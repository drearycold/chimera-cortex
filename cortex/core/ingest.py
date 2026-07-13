"""
Chimera Cortex — Ingestion Core Module
======================================
Discovers corpus manuscripts, uploads raw Markdown documents to MinIO,
registers metadata in MySQL, chunks sections, generates embeddings,
and stores vector vectors inside Infinity DB.
"""

import os
import io
import json
import httpx
import threading
import time
from datetime import datetime, timezone

from .config import INFINITY_API_URL
from .connectors import (
    CalibreConnector,
    DirectoryConnector,
    DropboxConnector,
    GoogleDriveConnector,
    OneDriveConnector,
    WebConnector,
)
from .connectors.base import RawDocument
from .database import (
    get_knowledge_base,
    get_minio_client,
    get_mysql_connection,
    get_or_create_directory_source,
    get_source,
    record_ingestion_log,
)
from .kb_config import DEFAULT_KB_SLUG
from .external_documents import build_segment_chunks
from .kb_storage import (
    ensure_external_vector_columns,
    ensure_minio_bucket,
    ensure_vector_table,
    require_infinity_success,
)
from .rag import chunk_markdown, get_embeddings_batch, parse_document_title


def document_identity(raw_document: RawDocument) -> tuple[str, str]:
    """Return the stable database identity for a connector document."""
    if raw_document.source_type == "cloud_drive" and raw_document.origin_path:
        return "origin_path", raw_document.origin_path
    return "filename", raw_document.filename

class IngestManager:
    """Thread-safe manager ensuring at most one ingestion run is in progress."""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.is_running = False
        self.status = "idle"  # "idle", "running", "completed", "failed", "cancelled"
        self.processed_files = 0
        self.failed_files = 0
        self.total_files = 0
        self.current_file = ""
        self.error_message = ""
        self.total_chunks_indexed = 0
        self.kb_slug = None
        self.source_id = None
        self.thread = None

    def start(
        self,
        source_dir="documents",
        force_rebuild=False,
        kb_slug=DEFAULT_KB_SLUG,
    ):
        """Start document ingestion asynchronously in a background thread."""
        with self.lock:
            if self.is_running:
                raise ValueError("An ingestion run is already in progress.")
            self.cancel_event.clear()
            self.is_running = True
            self.status = "running"
            self.processed_files = 0
            self.failed_files = 0
            self.total_files = 0
            self.current_file = ""
            self.error_message = ""
            self.total_chunks_indexed = 0
            self.kb_slug = kb_slug
            self.source_id = None
            
        self.thread = threading.Thread(
            target=self._run_wrapper,
            args=(source_dir, force_rebuild, kb_slug)
        )
        self.thread.daemon = True
        self.thread.start()

    def start_source(
        self,
        kb_slug: str,
        source_id: int,
        force_rebuild: bool = False,
    ):
        with self.lock:
            if self.is_running:
                raise ValueError("An ingestion run is already in progress.")
            self.cancel_event.clear()
            self.is_running = True
            self.status = "running"
            self.processed_files = 0
            self.failed_files = 0
            self.total_files = 0
            self.current_file = ""
            self.error_message = ""
            self.total_chunks_indexed = 0
            self.kb_slug = kb_slug
            self.source_id = source_id

        self.thread = threading.Thread(
            target=self._run_wrapper,
            args=(None, force_rebuild, kb_slug, source_id),
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        """Signal the current run to cancel gracefully."""
        with self.lock:
            if not self.is_running:
                return False
            self.cancel_event.set()
            return True

    def get_status(self):
        """Get current execution status and metrics."""
        with self.lock:
            return {
                "status": self.status,
                "processed_files": self.processed_files,
                "failed_files": self.failed_files,
                "total_files": self.total_files,
                "current_file": self.current_file,
                "error_message": self.error_message,
                "total_chunks_indexed": self.total_chunks_indexed,
                "kb_slug": self.kb_slug,
                "source_id": self.source_id,
            }

    def is_active(self) -> bool:
        """Return whether a worker still owns the global ingestion slot."""
        with self.lock:
            return self.is_running

    def clear_active(self):
        """Reset manager execution state but keep the final status."""
        with self.lock:
            self.is_running = False
            self.thread = None

    def _run_wrapper(self, source_dir, force_rebuild, kb_slug, source_id=None):
        started_at = time.monotonic()
        try:
            self._run_ingest(source_dir, force_rebuild, kb_slug, source_id)
        except Exception as e:
            print(f"[ERROR] Ingestion background thread crashed: {e}")
            with self.lock:
                self.status = "failed"
                self.error_message = str(e)
        finally:
            try:
                record_ingestion_log(
                    kb_slug=kb_slug,
                    source_id=source_id,
                    action="force_rebuild" if force_rebuild else "sync",
                    docs_processed=self.processed_files,
                    docs_skipped=max(
                        0,
                        self.total_files - self.processed_files - self.failed_files,
                    ),
                    docs_failed=self.failed_files,
                    duration_seconds=time.monotonic() - started_at,
                    error_detail=self.error_message or None,
                )
            except Exception as log_error:
                print(f"[Warning] Failed to record ingestion log: {log_error}")
            self.clear_active()

    def _run_ingest(
        self,
        source_dir,
        force_rebuild=False,
        kb_slug=DEFAULT_KB_SLUG,
        source_id=None,
    ):
        print(
            f"[INGEST] Starting ingestion for KB '{kb_slug}' "
            f"source={source_id or source_dir!r} (force_rebuild={force_rebuild})"
        )

        knowledge_base = get_knowledge_base(kb_slug)
        if knowledge_base is None or not knowledge_base["enabled"]:
            raise ValueError(f"Knowledge base '{kb_slug}' does not exist or is disabled.")

        ingest_config = knowledge_base["ingest_config"]
        embedding_config = ingest_config.get("embedding", {})
        chunking_config = ingest_config.get("chunking", {})
        embedding_model = embedding_config.get("model", "nomic-embed-text:latest")
        max_chars = int(chunking_config.get("max_chars", 600))
        overlap_chars = int(chunking_config.get("overlap_chars", 120))
        vector_table = knowledge_base["vector_table"]
        bucket_name = knowledge_base["minio_bucket"]
        # 1. Ensure KB-specific storage exists
        print("[INGEST] Connecting to KB storage...")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            ensure_minio_bucket(knowledge_base)
            vector_table_created = ensure_vector_table(knowledge_base)
            if not vector_table_created:
                ensure_external_vector_columns(knowledge_base)
            minio_client = get_minio_client()
        except Exception as exc:
            raise Exception(f"FAILED to connect/setup KB storage: {exc}") from exc
            
        # 2. Resolve the requested source and normalize it through a connector.
        if source_id is None:
            target_dir = source_dir
            if not os.path.exists(target_dir):
                if source_dir == "documents" and os.path.exists("servant_lore_md_v3"):
                    target_dir = "servant_lore_md_v3"
                else:
                    raise Exception(f"Source directory '{source_dir}' does not exist.")
            source = get_or_create_directory_source(knowledge_base["id"], target_dir)
        else:
            source = get_source(knowledge_base["id"], int(source_id))
            if source is None or not source["enabled"]:
                raise ValueError(
                    f"Enabled source {source_id} does not exist in KB '{kb_slug}'."
                )

        if source["type"] == "directory":
            target_dir = source["config"]["path"]
            connector = DirectoryConnector(
                knowledge_base["id"],
                source["id"],
                target_dir,
                source["config"].get("glob_patterns", ["*.md"]),
            )
        elif source["type"] == "web":
            connector = WebConnector(
                knowledge_base["id"],
                source["id"],
                source["config"],
            )
        elif source["type"] == "calibre":
            connector = CalibreConnector(
                knowledge_base["id"],
                source["id"],
                source["config"],
            )
        elif source["type"] == "cloud_drive":
            provider = source["config"]["provider"]
            connector_types = {
                "google_drive": GoogleDriveConnector,
                "onedrive": OneDriveConnector,
                "dropbox": DropboxConnector,
            }
            try:
                connector_type = connector_types[provider]
            except KeyError as exc:
                raise ValueError(f"Unsupported cloud drive provider '{provider}'.") from exc
            connector = connector_type(
                knowledge_base["id"],
                source["id"],
                source["config"],
            )
        else:
            raise ValueError(f"Unsupported source type '{source['type']}'.")
        object_prefix = (
            f"{kb_slug}/sources/{source['id']}/"
            if source["type"] in {"web", "calibre", "cloud_drive"}
            else f"{kb_slug}/"
        )
        try:
            raw_documents = connector.scan()
        finally:
            close_connector = getattr(connector, "close", None)
            if close_connector is not None:
                close_connector()

        is_full_snapshot = bool(getattr(connector, "is_full_snapshot", True))
        deleted_origin_paths = set(getattr(connector, "deleted_origin_paths", []))
        next_cursor = getattr(connector, "next_cursor", None)
        connector_config = dict(getattr(connector, "config", source["config"]))

        if not raw_documents and not bool(getattr(connector, "allow_empty", False)):
            raise Exception(f"Source {source['id']} returned no documents.")
            
        print(
            f"[INGEST] Found {len(raw_documents)} documents to process "
            f"from source {source['id']} ({source['type']})."
        )
        
        with self.lock:
            self.total_files = len(raw_documents)
            
        doc_success_count = 0
        total_chunks_indexed = 0
        chunk_queue: list[dict] = []
        successful_documents: set[str] = set()
        failed_documents: set[str] = set()
        run_errors: list[str] = []
        
        # Track chunk completion: { doc_id: {"total": total, "flushed": 0, "hash": file_hash} }
        chunk_progress: dict[int, dict] = {}

        def mark_document_success(filename: str):
            nonlocal doc_success_count
            if filename in successful_documents or filename in failed_documents:
                return
            successful_documents.add(filename)
            doc_success_count += 1
            with self.lock:
                self.processed_files = doc_success_count

        def mark_document_failed(filename: str, detail: str):
            if filename in failed_documents or filename in successful_documents:
                return
            failed_documents.add(filename)
            with self.lock:
                self.failed_files = len(failed_documents)
            run_errors.append(f"{filename}: {detail}")

        def mark_queued_documents_failed(detail: str):
            queued_ids = {item["document_id"] for item in chunk_queue}
            for queued_id in queued_ids:
                progress = chunk_progress.get(queued_id)
                if progress is not None:
                    mark_document_failed(progress["filename"], detail)

        # Establish unified MySQL connection
        mysql_conn = get_mysql_connection()
        cursor = mysql_conn.cursor()

        if force_rebuild or vector_table_created:
            cursor.execute("""
                UPDATE documents
                SET content_hash = NULL, status = 'pending', indexed_at = NULL
                WHERE source_id = %s
            """, (source["id"],))
            mysql_conn.commit()

        current_filenames = {document.filename for document in raw_documents}
        cursor.execute(
            "SELECT id, filename, minio_key, origin_path FROM documents WHERE source_id = %s",
            (source["id"],),
        )
        for document_id, filename, minio_key, origin_path in cursor.fetchall():
            should_delete = (
                filename not in current_filenames
                if is_full_snapshot
                else origin_path in deleted_origin_paths
            )
            if not should_delete:
                continue
            try:
                response = httpx.request(
                    "DELETE",
                    f"{INFINITY_API_URL}/databases/default_db/tables/{vector_table}/docs",
                    json={"filter": f"document_id = {document_id}"},
                    headers=headers,
                    timeout=10.0,
                )
                require_infinity_success(response, "deleted-source chunk deletion")
                minio_client.remove_object(bucket_name, minio_key)
                cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
                mysql_conn.commit()
                print(f"[INGEST] Removed deleted source document '{filename}'.")
            except Exception as exc:
                mysql_conn.rollback()
                mark_document_failed(filename, f"reconciliation failed: {exc}")
                print(
                    f"[Warning] Preserved deleted-source metadata for '{filename}' "
                    f"because storage cleanup failed: {exc}"
                )
        
        def flush_chunk_queue():
            nonlocal total_chunks_indexed
            if not chunk_queue:
                return
            
            print(f"   [INGEST] Flushing batch of {len(chunk_queue)} chunks to Ollama & Infinity DB...")
            chunk_texts = [item["content"] for item in chunk_queue]
            embeddings = get_embeddings_batch(chunk_texts, model=embedding_model)
            if len(embeddings) != len(chunk_queue) or any(
                embedding is None for embedding in embeddings
            ):
                raise RuntimeError("Embedding batch returned incomplete results.")
            
            infinity_batch = []
            for idx, item in enumerate(chunk_queue):
                emb = embeddings[idx] if idx < len(embeddings) else None
                infinity_batch.append({
                    "document_id": item["document_id"],
                    "chunk_index": item["chunk_index"],
                    "document_title": item["document_title"],
                    "content": item["content"],
                    "external_id": item["external_id"],
                    "source_key": item["source_key"],
                    "segment_ordinal": item["segment_ordinal"],
                    "segment_locator": item["segment_locator"],
                    "vec": emb
                })
            
            if infinity_batch:
                # Direct HTTP POST to table/docs
                docs_res = httpx.post(
                    f"{INFINITY_API_URL}/databases/default_db/tables/{vector_table}/docs",
                    json=infinity_batch,
                    headers=headers,
                    timeout=30.0
                )
                docs_res.raise_for_status()
                if docs_res.json().get("error_code", 0) != 0:
                    raise Exception(docs_res.json().get("error_msg"))
                
                batch_len = len(infinity_batch)
                total_chunks_indexed += batch_len
                with self.lock:
                    self.total_chunks_indexed = total_chunks_indexed
                
                # Update chunk completion status and commit content_hash when fully completed
                for item in infinity_batch:
                    doc_id = item["document_id"]
                    if doc_id in chunk_progress:
                        chunk_progress[doc_id]["flushed"] += 1
                        if chunk_progress[doc_id]["flushed"] == chunk_progress[doc_id]["total"]:
                            print(f"   [INGEST] Document ID {doc_id} completely indexed. Committing content_hash to MySQL...")
                            cursor.execute(
                                "UPDATE documents SET content_hash = %s, status = 'indexed', "
                                "indexed_at = CURRENT_TIMESTAMP WHERE id = %s",
                                (chunk_progress[doc_id]["hash"], doc_id)
                            )
                            mysql_conn.commit()
                            mark_document_success(chunk_progress[doc_id]["filename"])
            
            chunk_queue.clear()
            
        for file_idx, raw_document in enumerate(raw_documents, 1):
            # Graceful cancellation check
            if self.cancel_event.is_set():
                print("[INGEST] Ingestion cancelled by user request.")
                with self.lock:
                    self.status = "cancelled"
                break
                
            filename = raw_document.filename
            doc_title = raw_document.title or parse_document_title(filename)
            doc_id = None
            
            with self.lock:
                self.current_file = filename
                
            print(
                f"[INGEST] [{file_idx}/{len(raw_documents)}] "
                f"Checking '{filename}'..."
            )
            
            try:
                content = raw_document.content_markdown
                file_hash = raw_document.content_hash
                content_bytes = raw_document.raw_bytes
                size_bytes = len(content_bytes)
                minio_key = f"{object_prefix}{filename}"
                source_modified_at = datetime.fromtimestamp(
                    raw_document.source_modified_at,
                    tz=timezone.utc,
                ).replace(tzinfo=None)
                
                # Match cloud files by provider identity so renames do not duplicate them.
                identity_column, identity_value = document_identity(raw_document)
                if identity_column == "origin_path":
                    cursor.execute(
                        "SELECT id, content_hash, filename, minio_key FROM documents "
                        "WHERE source_id = %s AND origin_path = %s",
                        (source["id"], identity_value),
                    )
                else:
                    cursor.execute(
                        "SELECT id, content_hash, filename, minio_key FROM documents "
                        "WHERE source_id = %s AND filename = %s",
                        (source["id"], identity_value),
                    )
                existing = cursor.fetchone()
                previous_minio_key = None
                
                if existing:
                    doc_id, db_hash, previous_filename, previous_minio_key = existing
                    if (
                        not force_rebuild
                        and db_hash == file_hash
                        and previous_filename == filename
                    ):
                        cursor.execute(
                            "UPDATE documents SET title=%s, format=%s, metadata=%s, "
                            "source_modified_at=%s WHERE id=%s",
                            (
                                doc_title,
                                raw_document.format,
                                json.dumps(raw_document.metadata),
                                source_modified_at,
                                doc_id,
                            ),
                        )
                        mysql_conn.commit()
                        print("   -> Unchanged. Skipping.")
                        mark_document_success(filename)
                        continue
                    
                    # File exists but hash differs or rebuild is forced. Clean old chunks.
                    print(
                        "   -> Outdated, incomplete, or rebuild forced. "
                        "Deleting existing chunks from Infinity DB..."
                    )
                    try:
                        delete_payload = {
                            "filter": f"document_id = {doc_id}"
                        }
                        resp = httpx.request(
                            "DELETE",
                            f"{INFINITY_API_URL}/databases/default_db/tables/{vector_table}/docs",
                            json=delete_payload,
                            headers=headers,
                            timeout=10.0
                        )
                        require_infinity_success(resp, "document chunk deletion")
                    except Exception as clean_err:
                        raise RuntimeError(
                            f"Failed to delete existing chunks for document ID "
                            f"{doc_id}: {clean_err}"
                        ) from clean_err
                else:
                    doc_id = None
                
                # A. Upload raw file to MinIO
                minio_client.put_object(
                    bucket_name,
                    minio_key,
                    io.BytesIO(content_bytes),
                    length=size_bytes,
                    content_type="text/markdown"
                )
                
                # B. Chunk markdown
                if raw_document.segments:
                    segment_chunks = build_segment_chunks(
                        doc_title,
                        raw_document.segments,
                        max_chars=max_chars,
                        overlap_chars=overlap_chars,
                    )
                    chunks = [
                        ("", item["content"])
                        for item in segment_chunks
                    ]
                else:
                    segment_chunks = []
                    chunks = chunk_markdown(
                        content,
                        doc_title,
                        max_chars=max_chars,
                        overlap_chars=overlap_chars,
                    )
                chunk_count = len(chunks)
                print(f"   -> Split into {chunk_count} semantic chunks.")
                
                # C. Register/Update document in MySQL. Set content_hash = NULL initially
                if doc_id:
                    cursor.execute(
                        "UPDATE documents SET filename=%s, title=%s, format=%s, size_bytes=%s, "
                        "chunk_count=%s, content_hash=NULL, status='pending', metadata=%s, "
                        "external_id=%s, source_key=%s, origin_path=%s, minio_key=%s, "
                        "source_modified_at=%s WHERE id=%s",
                        (
                            filename,
                            doc_title,
                            raw_document.format,
                            size_bytes,
                            chunk_count,
                            json.dumps(raw_document.metadata),
                            raw_document.external_id or None,
                            raw_document.source_key or None,
                            raw_document.origin_path,
                            minio_key,
                            source_modified_at,
                            doc_id,
                        )
                    )
                else:
                    cursor.execute(
                        "INSERT INTO documents (source_id, filename, title, format, size_bytes, "
                        "chunk_count, content_hash, status, metadata, external_id, source_key, origin_path, "
                        "minio_key, source_modified_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, NULL, 'pending', %s, %s, %s, %s, %s, %s)",
                        (
                            source["id"],
                            filename,
                            doc_title,
                            raw_document.format,
                            size_bytes,
                            chunk_count,
                            json.dumps(raw_document.metadata),
                            raw_document.external_id or None,
                            raw_document.source_key or None,
                            raw_document.origin_path,
                            minio_key,
                            source_modified_at,
                        )
                    )
                    mysql_conn.commit()
                    doc_id = cursor.lastrowid
                
                mysql_conn.commit()

                if previous_minio_key and previous_minio_key != minio_key:
                    try:
                        minio_client.remove_object(bucket_name, previous_minio_key)
                    except Exception as exc:
                        print(
                            f"      [Warning] Failed to remove replaced object "
                            f"'{previous_minio_key}': {exc}"
                        )
                
                # Initialize progress tracking
                chunk_progress[doc_id] = {
                    "total": chunk_count,
                    "flushed": 0,
                    "hash": file_hash,
                    "filename": filename,
                }
                
                if chunk_count == 0:
                    # Empty file gets marked completed immediately
                    cursor.execute(
                        "UPDATE documents SET content_hash = %s, status = 'indexed', "
                        "indexed_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (file_hash, doc_id)
                    )
                    mysql_conn.commit()
                    mark_document_success(filename)
                
                # D. Add chunks to global queue and flush when threshold reached
                for chunk_idx, (head, chunk_text) in enumerate(chunks):
                    segment = segment_chunks[chunk_idx] if segment_chunks else None
                    chunk_queue.append({
                        "document_id": doc_id,
                        "chunk_index": chunk_idx,
                        "document_title": doc_title,
                        "content": chunk_text,
                        "external_id": raw_document.external_id,
                        "source_key": raw_document.source_key,
                        "segment_ordinal": (
                            segment["segment_ordinal"] if segment else -1
                        ),
                        "segment_locator": (
                            segment["segment_locator"] if segment else ""
                        ),
                    })
                    
                    if len(chunk_queue) >= 64:
                        try:
                            flush_chunk_queue()
                        except Exception as exc:
                            mark_queued_documents_failed(f"chunk batch failed: {exc}")
                            raise
                    
            except Exception as e:
                print(f"   [ERROR] Failed to ingest '{filename}': {e}")
                mysql_conn.rollback()
                mark_document_failed(filename, str(e))
                if doc_id:
                    try:
                        cursor.execute(
                            "UPDATE documents SET status = 'error' WHERE id = %s",
                            (doc_id,),
                        )
                        mysql_conn.commit()
                    except Exception:
                        mysql_conn.rollback()
                if chunk_queue:
                    break
                
        # E. Flush any remaining chunks in the queue
        if self.cancel_event.is_set():
            print("[INGEST] Ingestion cancelled by user request.")
            with self.lock:
                self.status = "cancelled"
                self.error_message = "Cancelled by user request."
        if chunk_queue and self.status != "cancelled" and not run_errors:
            try:
                flush_chunk_queue()
            except Exception as e:
                detail = f"Failed to flush final chunk queue: {e}"
                print(f"   [ERROR] {detail}")
                mark_queued_documents_failed(detail)
                
        cursor.close()
        mysql_conn.close()

        if self.status == "cancelled":
            print(
                f"[INGEST] Cancelled after processing {doc_success_count}/"
                f"{len(raw_documents)} files; source cursor was preserved."
            )
            return

        if run_errors:
            raise RuntimeError("Ingestion failed: " + "; ".join(run_errors))

        sync_conn = get_mysql_connection()
        sync_cursor = sync_conn.cursor()
        try:
            if next_cursor:
                connector_config["cursor"] = next_cursor
                sync_cursor.execute(
                    "UPDATE sources SET config = %s, last_synced_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (json.dumps(connector_config), source["id"]),
                )
            else:
                sync_cursor.execute(
                    "UPDATE sources SET last_synced_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (source["id"],),
                )
            sync_conn.commit()
        except Exception:
            sync_conn.rollback()
            raise
        finally:
            sync_cursor.close()
            sync_conn.close()
        
        with self.lock:
            if self.status != "cancelled":
                self.status = "completed"
                
        print("\n[INGEST] Ingestion Complete")
        print(
            f"[INGEST] Successfully processed {doc_success_count}/"
            f"{len(raw_documents)} files."
        )
        print(f"[INGEST] Indexed a total of {total_chunks_indexed} chunks into Infinity Vector DB.")

# Singleton manager instance
manager = IngestManager()
