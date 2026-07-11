import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import INFINITY_API_URL
from .database import (
    get_minio_client,
    get_mysql_connection,
    get_or_create_external_source,
)
from .kb_storage import (
    clear_knowledge_base_cache,
    ensure_external_vector_columns,
    ensure_minio_bucket,
    ensure_vector_table,
    require_infinity_success,
)
from .rag import chunk_markdown, get_embeddings_batch


def _opaque_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def build_segment_chunks(
    title: str,
    segments: list[dict[str, Any]],
    max_chars: int,
    overlap_chars: int,
) -> list[dict[str, Any]]:
    chunks = []
    chunk_index = 0
    for segment in sorted(segments, key=lambda item: item["ordinal"]):
        heading = segment.get("heading") or f"Segment {segment['ordinal']}"
        markdown = f"## {heading}\n\n{segment['text']}"
        for _, content in chunk_markdown(
            markdown,
            title,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        ):
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "content": content,
                    "segment_ordinal": segment["ordinal"],
                    "segment_locator": json.dumps(
                        segment["locator"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
            chunk_index += 1
    return chunks


def _canonical_document_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _find_external_document(kb_id: int, external_id: str) -> dict[str, Any] | None:
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT d.id, d.content_hash, d.chunk_count, d.source_id
            FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE s.kb_id = %s AND d.external_id = %s
            """,
            (kb_id, external_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "content_hash": row[1],
            "chunk_count": int(row[2] or 0),
            "source_id": row[3],
        }
    finally:
        cursor.close()
        conn.close()


def upsert_external_document(
    knowledge_base: dict,
    external_id: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    raw_bytes = _canonical_document_bytes(document)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    existing = _find_external_document(knowledge_base["id"], external_id)
    if existing and existing["content_hash"] == content_hash:
        return {
            "external_id": external_id,
            "document_id": existing["id"],
            "chunk_count": existing["chunk_count"],
            "status": "unchanged",
        }

    source_key = document["source_key"]
    source = get_or_create_external_source(knowledge_base["id"], source_key)
    ensure_minio_bucket(knowledge_base)
    table_created = ensure_vector_table(knowledge_base)
    if not table_created:
        ensure_external_vector_columns(knowledge_base)

    ingest_config = knowledge_base["ingest_config"]
    chunking = ingest_config.get("chunking", {})
    embedding = ingest_config.get("embedding", {})
    chunks = build_segment_chunks(
        document["title"],
        document["segments"],
        max_chars=int(chunking.get("max_chars", 600)),
        overlap_chars=int(chunking.get("overlap_chars", 120)),
    )
    vectors = get_embeddings_batch(
        [chunk["content"] for chunk in chunks],
        model=embedding.get("model", "nomic-embed-text:latest"),
    )
    if len(vectors) != len(chunks) or any(vector is None for vector in vectors):
        raise RuntimeError("Embedding generation failed for one or more external chunks.")

    filename = f"external-{_opaque_digest(external_id)}.json"
    minio_key = f"{knowledge_base['slug']}/external/{filename}"
    conn = get_mysql_connection()
    cursor = conn.cursor()
    document_id = None
    try:
        if existing:
            document_id = existing["id"]
            cursor.execute(
                """
                UPDATE documents
                SET source_id=%s, filename=%s, title=%s, format='external', size_bytes=%s,
                    chunk_count=%s, content_hash=NULL, status='pending', metadata=%s,
                    source_key=%s, minio_key=%s, source_modified_at=%s
                WHERE id=%s
                """,
                (
                    source["id"],
                    filename,
                    document["title"],
                    len(raw_bytes),
                    len(chunks),
                    json.dumps(document.get("metadata", {}), ensure_ascii=False),
                    source_key,
                    minio_key,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    document_id,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO documents (
                    source_id, filename, title, format, size_bytes, chunk_count,
                    content_hash, status, metadata, external_id, source_key,
                    minio_key, source_modified_at
                ) VALUES (%s, %s, %s, 'external', %s, %s, NULL, 'pending',
                          %s, %s, %s, %s, %s)
                """,
                (
                    source["id"],
                    filename,
                    document["title"],
                    len(raw_bytes),
                    len(chunks),
                    json.dumps(document.get("metadata", {}), ensure_ascii=False),
                    external_id,
                    source_key,
                    minio_key,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
            document_id = cursor.lastrowid
        conn.commit()

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        response = httpx.request(
            "DELETE",
            f"{INFINITY_API_URL}/databases/default_db/tables/{knowledge_base['vector_table']}/docs",
            json={"filter": f"document_id = {int(document_id)}"},
            headers=headers,
            timeout=10.0,
        )
        require_infinity_success(response, "external document replacement")
        rows = [
            {
                "document_id": document_id,
                "chunk_index": chunk["chunk_index"],
                "document_title": document["title"],
                "content": chunk["content"],
                "external_id": external_id,
                "source_key": source_key,
                "segment_ordinal": chunk["segment_ordinal"],
                "segment_locator": chunk["segment_locator"],
                "vec": vectors[index],
            }
            for index, chunk in enumerate(chunks)
        ]
        if rows:
            response = httpx.post(
                f"{INFINITY_API_URL}/databases/default_db/tables/{knowledge_base['vector_table']}/docs",
                json=rows,
                headers=headers,
                timeout=60.0,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("error_code", 0) != 0:
                raise RuntimeError(body.get("error_msg", "Infinity insert failed"))

        get_minio_client().put_object(
            knowledge_base["minio_bucket"],
            minio_key,
            io.BytesIO(raw_bytes),
            length=len(raw_bytes),
            content_type="application/json",
        )
        cursor.execute(
            """
            UPDATE documents
            SET content_hash=%s, status='indexed', indexed_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (content_hash, document_id),
        )
        conn.commit()
        clear_knowledge_base_cache(knowledge_base["slug"])
        return {
            "external_id": external_id,
            "document_id": document_id,
            "chunk_count": len(chunks),
            "status": "indexed",
        }
    except Exception:
        conn.rollback()
        if document_id is not None:
            cursor.execute("UPDATE documents SET status='error' WHERE id=%s", (document_id,))
            conn.commit()
        raise
    finally:
        cursor.close()
        conn.close()


def delete_external_document(knowledge_base: dict, external_id: str) -> bool:
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT d.id, d.minio_key
            FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE s.kb_id = %s AND d.external_id = %s
            """,
            (knowledge_base["id"], external_id),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        document_id, minio_key = row
        response = httpx.request(
            "DELETE",
            f"{INFINITY_API_URL}/databases/default_db/tables/{knowledge_base['vector_table']}/docs",
            json={"filter": f"document_id = {int(document_id)}"},
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        require_infinity_success(response, "external document deletion")
        if minio_key:
            get_minio_client().remove_object(knowledge_base["minio_bucket"], minio_key)
        cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        conn.commit()
        clear_knowledge_base_cache(knowledge_base["slug"])
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
