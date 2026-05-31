"""
Chimera Cortex — Ingestion Core Module
======================================
Discovers corpus manuscripts, uploads raw Markdown documents to MinIO,
registers metadata in MySQL, chunks sections, generates embeddings,
and stores vector vectors inside Infinity DB.
"""

import os
import sys
import glob
import io
import httpx
import threading
from concurrent.futures import ThreadPoolExecutor

from .config import INFINITY_API_URL
from .database import get_mysql_connection, get_minio_client
from .rag import parse_document_title, chunk_markdown, get_embedding, get_embeddings_batch

class IngestManager:
    """Thread-safe manager ensuring at most one ingestion run is in progress."""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.is_running = False
        self.status = "idle"  # "idle", "running", "completed", "failed", "cancelled"
        self.processed_files = 0
        self.total_files = 0
        self.current_file = ""
        self.error_message = ""
        self.total_chunks_indexed = 0
        self.thread = None

    def start(self, source_dir="documents"):
        """Start document ingestion asynchronously in a background thread."""
        with self.lock:
            if self.is_running:
                raise ValueError("An ingestion run is already in progress.")
            self.cancel_event.clear()
            self.is_running = True
            self.status = "running"
            self.processed_files = 0
            self.total_files = 0
            self.current_file = ""
            self.error_message = ""
            self.total_chunks_indexed = 0
            
        self.thread = threading.Thread(
            target=self._run_wrapper,
            args=(source_dir,)
        )
        self.thread.daemon = True
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
                "total_files": self.total_files,
                "current_file": self.current_file,
                "error_message": self.error_message,
                "total_chunks_indexed": self.total_chunks_indexed
            }

    def clear_active(self):
        """Reset manager execution state but keep the final status."""
        with self.lock:
            self.is_running = False
            self.thread = None

    def _run_wrapper(self, source_dir):
        try:
            self._run_ingest(source_dir)
        except Exception as e:
            print(f"[ERROR] Ingestion background thread crashed: {e}")
            with self.lock:
                self.status = "failed"
                self.error_message = str(e)
        finally:
            self.clear_active()

    def _run_ingest(self, source_dir):
        print(f"[INGEST] Starting ingestion from '{source_dir}'")
        
        # 1. Connect to MinIO
        print("[INGEST] Connecting to MinIO...")
        try:
            minio_client = get_minio_client()
            bucket_name = "cortex-documents"
            if not minio_client.bucket_exists(bucket_name):
                minio_client.make_bucket(bucket_name)
                print(f"[INGEST] Created MinIO bucket '{bucket_name}'")
            else:
                print(f"[INGEST] MinIO bucket '{bucket_name}' already exists.")
        except Exception as e:
            raise Exception(f"FAILED to connect to MinIO: {e}")
            
        # 2. Connect to MySQL & Setup Relational DB
        print("[INGEST] Connecting to MySQL...")
        try:
            from .config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASS
            import mysql.connector
            mysql_conn = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PASS
            )
            cursor = mysql_conn.cursor()
            cursor.execute("CREATE DATABASE IF NOT EXISTS cortex_rag")
            cursor.execute("USE cortex_rag")
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INT AUTO_INCREMENT PRIMARY KEY,
                filename VARCHAR(255) UNIQUE NOT NULL,
                title VARCHAR(255) NOT NULL,
                size_bytes INT NOT NULL,
                chunk_count INT NOT NULL
            )
            """)
            mysql_conn.commit()
            cursor.close()
            mysql_conn.close()
            print("[INGEST] MySQL Database & Tables set up successfully.")
        except Exception as e:
            raise Exception(f"FAILED to connect/setup MySQL: {e}")
            
        # 3. Connect to Infinity & Setup Vector DB (HTTP REST API)
        print("[INGEST] Connecting to Infinity Vector DB (HTTP)...")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            # Drop table if exists
            try:
                res_del = httpx.request(
                    "DELETE",
                    f"{INFINITY_API_URL}/databases/default_db/tables/chunks",
                    json={},
                    headers=headers,
                    timeout=5.0
                )
                if res_del.status_code == 200:
                    print("[INGEST] Dropped existing Infinity table 'chunks'.")
                else:
                    print(f"[INGEST] Table drop info: {res_del.text}")
            except Exception as e:
                print(f"[INGEST] Ignore drop table warning: {e}")
                
            # Create Table chunks
            payload = {
                "fields": [
                    {"name": "document_id", "type": "integer"},
                    {"name": "chunk_index", "type": "integer"},
                    {"name": "document_title", "type": "varchar"},
                    {"name": "content", "type": "varchar"},
                    {"name": "vec", "type": "vector, 768, float"}
                ]
            }
            res = httpx.post(f"{INFINITY_API_URL}/databases/default_db/tables/chunks", json=payload, headers=headers, timeout=5.0)
            res.raise_for_status()
            if res.json().get("error_code", 0) != 0:
                raise Exception(res.json().get("error_msg", "Unknown error"))
                
            print("[INGEST] Infinity table 'chunks' created successfully.")
        except Exception as e:
            raise Exception(f"FAILED to connect/setup Infinity: {e}")
            
        # 4. Search and Process Markdown Files
        target_dir = source_dir
        if not os.path.exists(target_dir):
            # Fallback to servant_lore_md_v3 if documents doesn't exist
            if source_dir == "documents" and os.path.exists("servant_lore_md_v3"):
                target_dir = "servant_lore_md_v3"
            else:
                raise Exception(f"Source directory '{source_dir}' does not exist.")
                
        search_path = os.path.join(target_dir, "*.md")
        files = glob.glob(search_path)
        
        if not files:
            raise Exception(f"No markdown files found in '{target_dir}'.")
            
        print(f"[INGEST] Found {len(files)} markdown files to process from '{target_dir}'.")
        
        with self.lock:
            self.total_files = len(files)
            
        doc_success_count = 0
        total_chunks_indexed = 0
        
        # Establish unified MySQL connection
        mysql_conn = get_mysql_connection()
        cursor = mysql_conn.cursor()
        
        for file_idx, filepath in enumerate(files, 1):
            # Graceful cancellation check
            if self.cancel_event.is_set():
                print(f"[INGEST] Ingestion cancelled by user request.")
                with self.lock:
                    self.status = "cancelled"
                break
                
            filename = os.path.basename(filepath)
            doc_title = parse_document_title(filename)
            
            with self.lock:
                self.current_file = filename
                
            print(f"[INGEST] [{file_idx}/{len(files)}] Processing '{filename}' as '{doc_title}'...")
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                content_bytes = content.encode("utf-8")
                size_bytes = len(content_bytes)
                
                # A. Upload raw file to MinIO
                minio_client.put_object(
                    bucket_name,
                    filename,
                    io.BytesIO(content_bytes),
                    length=size_bytes,
                    content_type="text/markdown"
                )
                
                # B. Chunk markdown
                chunks = chunk_markdown(content, doc_title)
                chunk_count = len(chunks)
                print(f"   -> Split into {chunk_count} semantic chunks.")
                
                # C. Register document in MySQL
                cursor.execute(
                    "INSERT INTO documents (filename, title, size_bytes, chunk_count) VALUES (%s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE title=%s, size_bytes=%s, chunk_count=%s",
                    (filename, doc_title, size_bytes, chunk_count, doc_title, size_bytes, chunk_count)
                )
                mysql_conn.commit()
                
                cursor.execute("SELECT id FROM documents WHERE filename = %s", (filename,))
                doc_id = cursor.fetchone()[0]
                
                # D. Generate embeddings in one batch request & insert into Infinity (HTTP)
                infinity_batch = []
                chunk_texts = [text for head, text in chunks]
                
                # Retrieve all embeddings in one single batch HTTP call
                embeddings = get_embeddings_batch(chunk_texts)
                
                for chunk_idx, (head, chunk_text) in enumerate(chunks):
                    emb = embeddings[chunk_idx] if chunk_idx < len(embeddings) else None
                    if emb is None:
                        print(f"      [Warning] Failed to generate embedding for chunk {chunk_idx}. Skipping.")
                        continue
                    
                    infinity_batch.append({
                        "document_id": doc_id,
                        "chunk_index": chunk_idx,
                        "document_title": doc_title,
                        "content": chunk_text,
                        "vec": emb
                    })
                    
                if infinity_batch:
                    # Direct HTTP POST to table/docs
                    docs_res = httpx.post(
                        f"{INFINITY_API_URL}/databases/default_db/tables/chunks/docs",
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
                    
                doc_success_count += 1
                
                with self.lock:
                    self.processed_files = doc_success_count
                    
            except Exception as e:
                print(f"   [ERROR] Failed to ingest '{filename}': {e}")
                mysql_conn.rollback()
                
        cursor.close()
        mysql_conn.close()
        
        with self.lock:
            if self.status != "cancelled":
                self.status = "completed"
                
        print(f"\n[INGEST] Ingestion Complete")
        print(f"[INGEST] Successfully processed {doc_success_count}/{len(files)} files.")
        print(f"[INGEST] Indexed a total of {total_chunks_indexed} chunks into Infinity Vector DB.")

# Singleton manager instance
manager = IngestManager()
