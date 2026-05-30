import mysql.connector
from minio import Minio
import redis
import httpx
from cortex.config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASS,
    MINIO_HOST, MINIO_USER, MINIO_PASS,
    REDIS_HOST, REDIS_PORT,
    INFINITY_API_URL, OLLAMA_HOST, RERANKER_HOST, RERANKER_PORT
)

def get_mysql_connection(database="cortex_rag"):
    """Get a connection to the MySQL database."""
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=database
    )

def get_minio_client():
    """Get a MinIO client instance."""
    return Minio(
        MINIO_HOST,
        access_key=MINIO_USER,
        secret_key=MINIO_PASS,
        secure=False
    )

def get_redis_client(socket_timeout=2):
    """Get a Redis client instance."""
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        socket_timeout=socket_timeout
    )

def get_service_status():
    """Verify connectivity and health across all 6 core RAG services."""
    status = {
        "mysql": False,
        "minio": False,
        "redis": False,
        "infinity": False,
        "ollama": False,
        "reranker": False
    }
    
    # 1. Test MySQL
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database="cortex_rag",
            connection_timeout=2
        )
        if conn.is_connected():
            status["mysql"] = True
            conn.close()
    except Exception:
        pass

    # 2. Test MinIO
    try:
        minio_client = get_minio_client()
        minio_client.bucket_exists("cortex-documents")
        status["minio"] = True
    except Exception:
        pass

    # 3. Test Redis
    try:
        r = get_redis_client()
        if r.ping():
            status["redis"] = True
    except Exception:
        pass

    # 4. Test Infinity (HTTP REST API)
    try:
        r = httpx.get(f"{INFINITY_API_URL}/databases", timeout=2.0)
        if r.status_code == 200:
            status["infinity"] = True
    except Exception:
        pass

    # 5. Test Ollama
    try:
        r = httpx.get(f"http://{OLLAMA_HOST}/api/version", timeout=2.0)
        if r.status_code == 200:
            status["ollama"] = True
    except Exception:
        pass

    # 6. Test Reranker (llama-server)
    try:
        r = httpx.get(f"http://{RERANKER_HOST}:{RERANKER_PORT}/health", timeout=2.0)
        if r.status_code == 200 and r.json().get("status") == "ok":
            status["reranker"] = True
    except Exception:
        pass

    return status
