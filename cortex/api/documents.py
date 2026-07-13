import json
import os
import httpx
from fastapi import APIRouter, HTTPException
from cortex.core.cache_management import clear_cache
from cortex.core.config import INFINITY_API_URL
from cortex.core.database import (
    get_knowledge_base,
    get_minio_client,
    get_mysql_connection,
    get_redis_client,
)
from cortex.core.kb_storage import require_infinity_success

router = APIRouter(prefix="/api", tags=["Documents"])


def _get_kb_document(slug: str, document_id: int) -> tuple[dict, dict]:
    knowledge_base = get_knowledge_base(slug)
    if knowledge_base is None:
        raise HTTPException(
            status_code=404,
            detail=f"Knowledge base '{slug}' not found.",
        )

    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT d.* FROM documents d
            JOIN sources s ON d.source_id = s.id
            WHERE d.id = %s AND s.kb_id = %s
        """, (document_id, knowledge_base["id"]))
        document = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    if document is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document {document_id} not found in knowledge base '{slug}'.",
        )
    for field in ("created_at", "updated_at", "indexed_at", "source_modified_at"):
        value = document.get(field)
        if value is not None and hasattr(value, "isoformat"):
            document[field] = value.isoformat()
    if isinstance(document.get("metadata"), str):
        document["metadata"] = json.loads(document["metadata"])
    return knowledge_base, document


@router.get("/kb/{slug}/documents")
def api_kb_documents(slug: str):
    knowledge_base = get_knowledge_base(slug)
    if knowledge_base is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT d.id, d.filename, d.title, d.format, d.status,
                   d.chunk_count, d.size_bytes, d.indexed_at, s.id AS source_id,
                   s.name AS source_name
            FROM documents d
            JOIN sources s ON d.source_id = s.id
            WHERE s.kb_id = %s
            ORDER BY d.title ASC
        """, (knowledge_base["id"],))
        documents = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()
    for document in documents:
        if document.get("indexed_at"):
            document["indexed_at"] = document["indexed_at"].isoformat()
    return {"knowledge_base": slug, "documents": documents}


@router.get("/kb/{slug}/documents/{document_id}")
def api_kb_document(slug: str, document_id: int):
    _, document = _get_kb_document(slug, document_id)
    return document


@router.get("/kb/{slug}/documents/{document_id}/content")
def api_kb_document_content(slug: str, document_id: int):
    knowledge_base, document = _get_kb_document(slug, document_id)
    try:
        minio_client = get_minio_client()
        response = minio_client.get_object(
            knowledge_base["minio_bucket"],
            document["minio_key"],
        )
        content = response.read().decode("utf-8")
        response.close()
        response.release_conn()
        return {
            "id": document_id,
            "filename": document["filename"],
            "content": content,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Document content is unavailable: {exc}",
        ) from exc


@router.delete("/kb/{slug}/documents/{document_id}")
def api_delete_kb_document(slug: str, document_id: int):
    knowledge_base, document = _get_kb_document(slug, document_id)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        response = httpx.request(
            "DELETE",
            f"{INFINITY_API_URL}/databases/default_db/tables/"
            f"{knowledge_base['vector_table']}/docs",
            json={"filter": f"document_id = {document_id}"},
            headers=headers,
            timeout=10.0,
        )
        require_infinity_success(response, "document deletion")
        get_minio_client().remove_object(
            knowledge_base["minio_bucket"],
            document["minio_key"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Storage cleanup failed; metadata was preserved: {exc}",
        ) from exc

    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    redis_client = get_redis_client()
    keys = list(redis_client.scan_iter(f"rag_cache:{slug}:*"))
    if keys:
        redis_client.delete(*keys)
    return {"message": f"Document {document_id} deleted from '{slug}'."}

@router.get("/documents")
async def api_documents():
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, filename, title FROM documents ORDER BY title ASC")
        documents = cursor.fetchall()
        cursor.close()
        conn.close()
        return {"documents": documents}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/document/{filename}")
async def api_document(filename: str):
    try:
        minio_client = get_minio_client()
        response = minio_client.get_object("cortex-documents", filename)
        content = response.read().decode("utf-8")
        response.close()
        response.release_conn()
        return {"filename": filename, "content": content}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found in MinIO: {str(e)}")

@router.delete("/document/{filename}")
async def api_delete_document(filename: str):
    # 1. Connect to MySQL and retrieve document_id
    try:
        mysql_conn = get_mysql_connection()
        cursor = mysql_conn.cursor()
        cursor.execute("SELECT id FROM documents WHERE filename = %s", (filename,))
        res = cursor.fetchone()
        if not res:
            cursor.close()
            mysql_conn.close()
            raise HTTPException(status_code=404, detail=f"Document '{filename}' not found in MySQL database.")
        doc_id = res[0]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Database lookup failed: {str(e)}")

    # 2. Delete from Infinity Vector DB (matching document_id)
    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        delete_payload = {
            "filter": f"document_id = {doc_id}"
        }
        resp = httpx.request(
            "DELETE",
            f"{INFINITY_API_URL}/databases/default_db/tables/chunks/docs",
            json=delete_payload,
            headers=headers,
            timeout=10.0
        )
        resp.raise_for_status()
        res_del = resp.json()
        if res_del.get("error_code", 0) != 0:
            print(f"[Warning] Infinity chunk deletion returned error: {res_del.get('error_msg')}")
    except Exception as e:
        print(f"[Warning] Failed to delete chunks from Infinity: {e}")

    # 3. Delete from MinIO Object Storage
    try:
        minio_client = get_minio_client()
        minio_client.remove_object("cortex-documents", filename)
    except Exception as e:
        print(f"[Warning] Failed to delete object from MinIO: {e}")

    # 4. Delete from MySQL
    try:
        cursor.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        mysql_conn.commit()
        cursor.close()
        mysql_conn.close()
    except Exception as e:
        print(f"[Warning] Failed to delete document from MySQL: {e}")

    # 5. Clear only Chimera RAG cache keys.
    try:
        cleared = clear_cache()
        print(f"Cleared {cleared} Redis RAG cache entries.")
    except Exception as e:
        print(f"[Warning] Redis RAG cache clear failed: {e}")

    # 6. Delete from local disk if it exists in servant_lore_md_v3 or documents
    local_paths = [
        os.path.join("documents", filename),
        os.path.join("servant_lore_md_v3", filename)
    ]
    for path in local_paths:
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"Removed local file: {path}")
            except Exception as e:
                print(f"[Warning] Failed to remove local file {path}: {e}")

    return {"message": f"Document '{filename}' has been successfully deleted from MySQL, MinIO, Infinity DB, Redis, and local disk."}
