#!/usr/bin/env python3
import sys
import httpx
from cortex.core.database import get_mysql_connection, get_minio_client, get_redis_client
from cortex.core.config import INFINITY_API_URL

def clean_index():
    print("=== Cleaning Chimera Cortex Index ===")

    # 1. Clear MySQL 'documents' table
    print("\n1. Clearing MySQL 'documents' table...")
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE documents")
        conn.commit()
        cursor.close()
        conn.close()
        print("[SUCCESS] MySQL documents table truncated.")
    except Exception as e:
        print(f"[ERROR] Failed to clear MySQL documents: {e}")

    # 2. Clear MinIO 'cortex-documents' bucket objects
    print("\n2. Clearing MinIO 'cortex-documents' bucket...")
    try:
        minio_client = get_minio_client()
        bucket_name = "cortex-documents"
        if minio_client.bucket_exists(bucket_name):
            # List and delete all objects
            objects = minio_client.list_objects(bucket_name, recursive=True)
            for obj in objects:
                minio_client.remove_object(bucket_name, obj.object_name)
                print(f"Removed object {obj.object_name} from MinIO.")
            print("[SUCCESS] MinIO bucket cleared.")
        else:
            print("[INFO] MinIO bucket does not exist.")
    except Exception as e:
        print(f"[ERROR] Failed to clear MinIO: {e}")

    # 3. Drop and Recreate Infinity Table
    print("\n3. Dropping and Recreating Infinity Table 'chunks'...")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        # Drop table if exists
        res_del = httpx.request(
            "DELETE",
            f"{INFINITY_API_URL}/databases/default_db/tables/chunks",
            json={},
            headers=headers,
            timeout=10.0
        )
        print(f"Table drop response: {res_del.status_code} - {res_del.text.strip()}")
        
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
        res = httpx.post(
            f"{INFINITY_API_URL}/databases/default_db/tables/chunks",
            json=payload,
            headers=headers,
            timeout=10.0
        )
        res.raise_for_status()
        if res.json().get("error_code", 0) != 0:
            raise Exception(res.json().get("error_msg", "Unknown error"))
        print("[SUCCESS] Infinity table 'chunks' dropped and recreated.")
    except Exception as e:
        print(f"[ERROR] Failed to setup Infinity: {e}")

    # 4. Flush Redis Cache
    print("\n4. Flushing Redis Cache...")
    try:
        r_client = get_redis_client()
        r_client.flushdb()
        print("[SUCCESS] Redis cache flushed.")
    except Exception as e:
        print(f"[ERROR] Failed to flush Redis cache: {e}")

    print("\n=== Cleanup Complete ===")

if __name__ == "__main__":
    clean_index()
