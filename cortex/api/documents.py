import os
import httpx
from fastapi import APIRouter, HTTPException
from cortex.core.config import INFINITY_API_URL
from cortex.core.database import get_mysql_connection, get_minio_client, get_redis_client

router = APIRouter(prefix="/api", tags=["Documents"])

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

    # 5. Flush Redis Cache
    try:
        r_client = get_redis_client()
        r_client.flushdb()
        print("Redis cache flushed successfully.")
    except Exception as e:
        print(f"[Warning] Redis cache flush failed: {e}")

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
