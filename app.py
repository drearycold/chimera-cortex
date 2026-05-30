import os
import io
import time
import httpx
import redis
import mysql.connector
import math
from minio import Minio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Environment variables loader (.env parser)
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"').strip("'")
                    os.environ[key] = val

load_env()

# Configurations
INFINITY_HOST = os.getenv("INFINITY_HOST", "127.0.0.1")
INFINITY_HTTP_PORT = int(os.getenv("INFINITY_PORT", "23820"))
INFINITY_API_URL = f"http://{INFINITY_HOST}:{INFINITY_HTTP_PORT}"

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASS = os.getenv("MYSQL_PASS", "root")

MINIO_HOST = os.getenv("MINIO_HOST", "127.0.0.1:9000")
MINIO_USER = os.getenv("MINIO_USER", "minioadmin")
MINIO_PASS = os.getenv("MINIO_PASS", "minioadmin")

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1:11434")
OLLAMA_EMBED_URL = f"http://{OLLAMA_HOST}/api/embeddings"
OLLAMA_GENERATE_URL = f"http://{OLLAMA_HOST}/api/generate"
OLLAMA_EMBED_MODEL = "bge-m3:latest"
OLLAMA_GEN_MODEL = "qwen2.5:3b"

# Reranker configurations (llama-server in rerank mode)
RERANKER_HOST = os.getenv("RERANKER_HOST", "127.0.0.1")
RERANKER_PORT = int(os.getenv("RERANKER_PORT", "8082"))
RERANKER_URL = f"http://{RERANKER_HOST}:{RERANKER_PORT}/v1/rerank"

app = FastAPI(title="Chimera Cortex: An Omni-Context Knowledge Engine")

class ChatRequest(BaseModel):
    query: str

# Helper to verify services connectivity
def get_service_status():
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
        minio_client = Minio(
            MINIO_HOST,
            access_key=MINIO_USER,
            secret_key=MINIO_PASS,
            secure=False
        )
        minio_client.bucket_exists("cortex-documents")
        status["minio"] = True
    except Exception:
        pass

    # 3. Test Redis
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=2)
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

# Document Reranker Helper Function
def rerank_documents(query: str, candidates: list) -> list:
    if not candidates:
        return []
    
    try:
        # Extract the texts of the candidate chunks (remote batch size increased to 4096)
        documents = [c["content"] for c in candidates]
        
        # Query llama-server /v1/rerank endpoint
        resp = httpx.post(
            RERANKER_URL,
            json={
                "query": query,
                "documents": documents
            },
            timeout=10.0
        )
        resp.raise_for_status()
        res_data = resp.json()
        
        # Results contains list of results with indices and raw logit scores
        results = res_data.get("results", [])
        
        # Apply Sigmoid Activation Function to map raw logit scores to standard similarity [0.0, 1.0]
        for r in results:
            idx = r["index"]
            raw_score = r["relevance_score"]
            # Sigmoid: 1 / (1 + exp(-x))
            sigmoid_score = 1.0 / (1.0 + math.exp(-raw_score))
            candidates[idx]["distance"] = sigmoid_score
            candidates[idx]["rerank_logit"] = float(raw_score)
            candidates[idx]["rerank_score"] = float(sigmoid_score)
            
        # Sort descending by updated similarity score
        candidates.sort(key=lambda x: x["distance"], reverse=True)
        return candidates
    except Exception as e:
        print(f"[Warning] Reranker failed, falling back to original Infinity vector ordering: {e}")
        for c in candidates:
            c["rerank_logit"] = None
            c["rerank_score"] = None
        return candidates

# API Endpoints
@app.get("/api/status")
async def api_status():
    return get_service_status()

@app.get("/api/documents")
async def api_documents():
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database="cortex_rag"
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, filename, title FROM documents ORDER BY title ASC")
        documents = cursor.fetchall()
        cursor.close()
        conn.close()
        return {"documents": documents}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/api/document/{filename}")
async def api_document(filename: str):
    try:
        minio_client = Minio(
            MINIO_HOST,
            access_key=MINIO_USER,
            secret_key=MINIO_PASS,
            secure=False
        )
        response = minio_client.get_object("cortex-documents", filename)
        content = response.read().decode("utf-8")
        response.close()
        response.release_conn()
        return {"filename": filename, "content": content}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found in MinIO: {str(e)}")

@app.delete("/api/document/{filename}")
async def api_delete_document(filename: str):
    # 1. Connect to MySQL and retrieve document_id
    try:
        mysql_conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database="cortex_rag"
        )
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
        minio_client = Minio(
            MINIO_HOST,
            access_key=MINIO_USER,
            secret_key=MINIO_PASS,
            secure=False
        )
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
        r_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=2)
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

@app.post("/api/cache/clear")
async def api_clear_cache():
    try:
        r_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=2)
        r_client.flushdb()
        return {"message": "Redis generation cache has been successfully cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear Redis cache: {str(e)}")

@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    t_start = time.time()
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Empty query.")

    # 1. Check Redis Cache
    r_client = None
    try:
        r_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=2)
        cached_val = r_client.get(f"rag_cache:{query}")
        if cached_val:
            import json
            cached_data = json.loads(cached_val.decode("utf-8"))
            cached_data["cache_hit"] = True
            return cached_data
    except Exception as e:
        print(f"[Warning] Redis cache connection failed: {e}")

    # 2. Get Query Embedding from Ollama (bge-m3)
    t_embed_start = time.time()
    try:
        resp = httpx.post(OLLAMA_EMBED_URL, json={"model": OLLAMA_EMBED_MODEL, "prompt": query}, timeout=15.0)
        resp.raise_for_status()
        query_vector = resp.json()["embedding"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate query embedding via Ollama: {str(e)}")
    embedding_ms = (time.time() - t_embed_start) * 1000.0

    # 3. Retrieve Closest Chunks from Infinity DB (HTTP) - Get topn=10 for Reranking
    t_retrieval_start = time.time()
    contexts = []
    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        search_payload = {
            "output": ["document_id", "chunk_index", "content", "document_title", "similarity()"],
            "search": [
                {
                    "match_method": "dense",
                    "fields": "vec",
                    "query_vector": query_vector,
                    "element_type": "float",
                    "metric_type": "ip",
                    "topn": 10
                }
            ]
        }
        resp = httpx.request(
            "GET",
            f"{INFINITY_API_URL}/databases/default_db/tables/chunks/docs",
            json=search_payload,
            headers=headers,
            timeout=10.0
        )
        resp.raise_for_status()
        search_res = resp.json()
        
        # Robustly parse output list
        raw_results = []
        if search_res.get("error_code", 0) == 0:
            if "output" in search_res:
                raw_results = search_res["output"]
            elif "docs" in search_res:
                raw_results = search_res["docs"]
            elif "rows" in search_res:
                raw_results = search_res["rows"]
        else:
            print(f"[Warning] Infinity HTTP search returned error: {search_res.get('error_msg')}")
        
        # Resolve document_id to filename using MySQL
        mysql_conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database="cortex_rag"
        )
        cursor = mysql_conn.cursor()
        
        for idx, item_list in enumerate(raw_results):
            # item_list is a list of dicts from Infinity, e.g. [{"document_id": 28}, {"chunk_index": 0}, ...]
            row = {}
            if isinstance(item_list, list):
                for item in item_list:
                    if isinstance(item, dict):
                        row.update(item)
            elif isinstance(item_list, dict):
                row = item_list
                
            doc_id = row.get("document_id")
            content = row.get("content", "")
            doc_title = row.get("document_title", "")
            distance = row.get("SIMILARITY") or row.get("similarity") or row.get("score") or (1.0 - (idx * 0.1))
            
            filename = "Unknown Source"
            if doc_id is not None:
                cursor.execute("SELECT filename FROM documents WHERE id = %s", (doc_id,))
                db_res = cursor.fetchone()
                if db_res:
                    filename = db_res[0]
                    
            contexts.append({
                "document_id": int(doc_id) if doc_id is not None else None,
                "chunk_index": int(row.get("chunk_index")) if row.get("chunk_index") is not None else None,
                "filename": filename,
                "servant_name": doc_title,
                "content": content,
                "distance": float(distance)
            })
            
        cursor.close()
        mysql_conn.close()
    except Exception as e:
        print(f"[Warning] Failed to retrieve context from Infinity/MySQL: {e}")
        contexts = []
    retrieval_ms = (time.time() - t_retrieval_start) * 1000.0

    # 4. Rerank Chunks via Remote Llama-Server
    t_rerank_start = time.time()
    first_stage_candidates = []
    second_stage_candidates = []
    if contexts:
        # Capture first-stage candidates
        for i, c in enumerate(contexts):
            first_stage_candidates.append({
                "document_id": c.get("document_id"),
                "chunk_index": c.get("chunk_index"),
                "filename": c["filename"],
                "servant_name": c["servant_name"],
                "content": c["content"],
                "score": c["distance"],
                "rank": i + 1
            })
            
        contexts = rerank_documents(query, contexts)
        
        # Capture second-stage candidates
        for i, c in enumerate(contexts):
            second_stage_candidates.append({
                "document_id": c.get("document_id"),
                "chunk_index": c.get("chunk_index"),
                "filename": c["filename"],
                "servant_name": c["servant_name"],
                "content": c["content"],
                "first_stage_score": next((f["score"] for f in first_stage_candidates if f["filename"] == c["filename"] and f["chunk_index"] == c["chunk_index"]), c["distance"]),
                "first_stage_rank": next((f["rank"] for f in first_stage_candidates if f["filename"] == c["filename"] and f["chunk_index"] == c["chunk_index"]), i + 1),
                "rerank_logit": c.get("rerank_logit"),
                "rerank_score": c.get("rerank_score"),
                "rank": i + 1
            })
            
        # Select top_k = 3 most relevant chunks after rerank scoring
        contexts = contexts[:3]
    rerank_ms = (time.time() - t_rerank_start) * 1000.0

    # 5. Generate RAG Response via Ollama (qwen2.5:3b)
    t_gen_start = time.time()
    if contexts:
        context_str = "\n\n".join([f"--- SOURCE: {c['filename']} ---\n{c['content']}" for c in contexts])
        system_prompt = (
            "You are Chimera Cortex, a strict document AI assistant. "
            "You must answer the user query based ONLY on the provided document context below. "
            "Do NOT use any external or general knowledge. If the provided context does not contain "
            "the answer to the query, respond by stating that the provided documents do not contain "
            "sufficient information to answer the question.\n\n"
            f"Here is the retrieved context:\n{context_str}"
        )
    else:
        system_prompt = (
            "You are Chimera Cortex, a strict document AI assistant. No matching document context was found in the knowledge base. "
            "Because you are configured to answer based ONLY on the provided context, "
            "respond by stating that you cannot answer the query because no relevant documents "
            "were found in the knowledge base."
        )

    full_prompt = f"System: {system_prompt}\n\nUser Question: {query}\n\nAnswer:"
    
    try:
        r = httpx.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": OLLAMA_GEN_MODEL,
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3
                }
            },
            timeout=45.0
        )
        r.raise_for_status()
        answer = r.json()["response"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama generation failed: {str(e)}")
    generation_ms = (time.time() - t_gen_start) * 1000.0
    total_ms = (time.time() - t_start) * 1000.0

    response_data = {
        "answer": answer,
        "contexts": contexts,
        "cache_hit": False,
        "audit": {
            "timings_ms": {
                "embedding": round(embedding_ms, 2),
                "retrieval": round(retrieval_ms, 2),
                "rerank": round(rerank_ms, 2),
                "generation": round(generation_ms, 2),
                "total": round(total_ms, 2)
            },
            "first_stage_candidates": first_stage_candidates,
            "second_stage_candidates": second_stage_candidates,
            "llm_prompt": full_prompt
        }
    }

    # 6. Save to Cache in Redis
    if r_client:
        try:
            import json
            r_client.setex(f"rag_cache:{query}", 3600, json.dumps(response_data))
        except Exception:
            pass

    return response_data

# Mount static web directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
else:
    @app.get("/")
    async def index_fallback():
        return {"message": "RAG Portal APIs are running. Static folder missing."}
