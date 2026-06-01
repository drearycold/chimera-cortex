import mysql.connector
from minio import Minio
import redis
import httpx
import json
from .config import (
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

def init_db():
    """Create required tables directly in cortex_rag."""
    try:
        # Connect directly with a short timeout to prevent locking startup
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database="cortex_rag",
            connection_timeout=3
        )
        cursor = conn.cursor()
        
        # 1. documents table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INT AUTO_INCREMENT PRIMARY KEY,
            filename VARCHAR(255) UNIQUE NOT NULL,
            title VARCHAR(255) NOT NULL,
            size_bytes INT NOT NULL,
            chunk_count INT NOT NULL,
            content_hash VARCHAR(64)
        )
        """)

        # Ensure content_hash column exists for older database installations
        try:
            cursor.execute("ALTER TABLE documents ADD COLUMN content_hash VARCHAR(64)")
        except mysql.connector.Error as err:
            # 1060: Duplicate column name, meaning it already exists
            if err.errno != 1060:
                raise err


        # 2. benchmark_runs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_runs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dataset_name VARCHAR(255),
            judge_model VARCHAR(255),
            total_questions INT DEFAULT 0,
            duration_seconds FLOAT DEFAULT 0.0,
            avg_correctness FLOAT DEFAULT 0.0,
            avg_faithfulness FLOAT DEFAULT 0.0,
            avg_relevance FLOAT DEFAULT 0.0,
            pass_rate FLOAT DEFAULT 0.0,
            status VARCHAR(50) DEFAULT 'running',
            comment TEXT
        )
        """)

        # Ensure comment column exists for older database installations
        try:
            cursor.execute("ALTER TABLE benchmark_runs ADD COLUMN comment TEXT")
        except mysql.connector.Error as err:
            # 1060: Duplicate column name, meaning it already exists
            if err.errno != 1060:
                raise err

        # 3. benchmark_results table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_results (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_id INT NOT NULL,
            question_id VARCHAR(50),
            question TEXT,
            difficulty VARCHAR(50),
            reference_answer TEXT,
            rag_answer TEXT,
            cache_hit BOOLEAN,
            answer_correctness INT,
            faithfulness INT,
            retrieval_relevance INT,
            rationale TEXT,
            raw_judge_output TEXT,
            latency_embedding FLOAT,
            latency_retrieval FLOAT,
            latency_rerank FLOAT,
            latency_generation FLOAT,
            latency_total FLOAT,
            first_stage_candidates JSON,
            second_stage_candidates JSON,
            llm_prompt TEXT,
            FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE
        )
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("[DB] Tables verified/created successfully.")
    except Exception as e:
        print(f"[DB Warning] Database tables initialization bypassed or failed: {e}")

def save_benchmark_run(dataset_name: str, judge_model: str, total_questions: int, comment: str = None) -> int:
    """Insert a new benchmark run and return its run_id."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO benchmark_runs (dataset_name, judge_model, total_questions, status, comment)
        VALUES (%s, %s, %s, 'running', %s)
    """, (dataset_name, judge_model, total_questions, comment))
    conn.commit()
    run_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return run_id

def update_benchmark_run_status(
    run_id: int, 
    status: str, 
    duration_seconds: float = 0.0, 
    avg_correctness: float = 0.0, 
    avg_faithfulness: float = 0.0, 
    avg_relevance: float = 0.0, 
    pass_rate: float = 0.0
):
    """Update status and aggregated scores of a benchmark run."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE benchmark_runs
        SET status = %s, duration_seconds = %s, avg_correctness = %s, 
            avg_faithfulness = %s, avg_relevance = %s, pass_rate = %s
        WHERE id = %s
    """, (status, duration_seconds, avg_correctness, avg_faithfulness, avg_relevance, pass_rate, run_id))
    conn.commit()
    cursor.close()
    conn.close()

def save_benchmark_result(run_id: int, result: dict):
    """Insert a single question evaluation result."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    
    audit = result.get("audit") or {}
    timings = audit.get("timings_ms") or {}
    scores = result.get("scores") or {}
    
    first_stage = json.dumps(audit.get("first_stage_candidates") or [])
    second_stage = json.dumps(audit.get("second_stage_candidates") or [])
    
    cursor.execute("""
        INSERT INTO benchmark_results (
            run_id, question_id, question, difficulty, reference_answer, rag_answer, cache_hit,
            answer_correctness, faithfulness, retrieval_relevance, rationale, raw_judge_output,
            latency_embedding, latency_retrieval, latency_rerank, latency_generation, latency_total,
            first_stage_candidates, second_stage_candidates, llm_prompt
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s
        )
    """, (
        run_id,
        result.get("id"),
        result.get("question"),
        result.get("difficulty"),
        result.get("reference_answer"),
        result.get("rag_answer"),
        result.get("cache_hit", False),
        scores.get("answer_correctness", 1),
        scores.get("faithfulness", 1),
        scores.get("retrieval_relevance", 1),
        scores.get("rationale", ""),
        scores.get("raw_judge_output", ""),
        timings.get("embedding", 0.0),
        timings.get("retrieval", 0.0),
        timings.get("rerank", 0.0),
        timings.get("generation", 0.0),
        timings.get("total", 0.0),
        first_stage,
        second_stage,
        audit.get("llm_prompt", "")
    ))
    conn.commit()
    cursor.close()
    conn.close()

def get_benchmark_runs() -> list:
    """Fetch all benchmark runs ordered by creation time descending."""
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM benchmark_runs ORDER BY created_at DESC")
    runs = cursor.fetchall()
    # Format created_at to string for JSON serialization compatibility
    for r in runs:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    cursor.close()
    conn.close()
    return runs

def get_benchmark_run(run_id: int) -> dict:
    """Fetch a benchmark run and all its associated question results."""
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM benchmark_runs WHERE id = %s", (run_id,))
    run = cursor.fetchone()
    if not run:
        cursor.close()
        conn.close()
        return None
        
    if run.get("created_at"):
        run["created_at"] = run["created_at"].isoformat()
        
    cursor.execute("SELECT * FROM benchmark_results WHERE run_id = %s ORDER BY id ASC", (run_id,))
    results = cursor.fetchall()
    
    # Parse candidate lists from JSON string
    for res in results:
        for json_col in ["first_stage_candidates", "second_stage_candidates"]:
            val = res.get(json_col)
            if isinstance(val, str):
                try:
                    res[json_col] = json.loads(val)
                except Exception:
                    res[json_col] = []
            elif val is None:
                res[json_col] = []
                
    run["results"] = results
    cursor.close()
    conn.close()
    return run

def delete_benchmark_run(run_id: int) -> bool:
    """Delete a benchmark run from database."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM benchmark_runs WHERE id = %s", (run_id,))
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()
    return affected > 0
