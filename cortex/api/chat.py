import time
import json
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from cortex.core.config import (
    INFINITY_API_URL, OLLAMA_EMBED_URL, OLLAMA_GENERATE_URL,
    OLLAMA_EMBED_MODEL, OLLAMA_GEN_MODEL
)
from cortex.core.database import get_mysql_connection, get_redis_client
from cortex.core.rag import rerank_documents, get_embedding, decompose_query, fetch_and_merge_chunk_range

router = APIRouter(prefix="/api", tags=["Chat"])

class ChatRequest(BaseModel):
    query: str

@router.post("/chat")
def api_chat(req: ChatRequest):
    t_start = time.time()
    import unicodedata
    query = unicodedata.normalize('NFC', req.query.strip())
    if not query:
        raise HTTPException(status_code=400, detail="Empty query.")

    # 1. Check Redis Cache
    r_client = None
    try:
        r_client = get_redis_client()
        cached_val = r_client.get(f"rag_cache:{query}")
        if cached_val:
            cached_data = json.loads(cached_val.decode("utf-8"))
            cached_data["cache_hit"] = True
            return cached_data
    except Exception as e:
        print(f"[Warning] Redis cache connection failed: {e}")

    # 2. Decompose Query
    t_decomp_start = time.time()
    sub_queries = decompose_query(query)
    decomp_ms = (time.time() - t_decomp_start) * 1000.0
    print(f"Decomposed query '{query}' into: {sub_queries} in {decomp_ms:.2f}ms")

    # 3. Get Query Embeddings and Retrieve Closest Chunks
    t_embed_start = time.time()
    
    # Setup Entity-Balanced Retrieval Slicing quotas
    queries_to_run = [(query, 5)]
    for sq in sub_queries:
        if sq.lower() != query.lower():
            queries_to_run.append((sq, 3))
            
    query_vectors = []
    for sq, quota in queries_to_run:
        sq_vector = get_embedding(sq, is_query=True)
        query_vectors.append((sq, sq_vector, quota))
    embedding_ms = (time.time() - t_embed_start) * 1000.0

    t_retrieval_start = time.time()
    all_contexts = []
    first_stage_candidates = []
    second_stage_candidates = []
    rerank_total_ms = 0.0
    
    # Establish connection once for the loop
    mysql_conn = None
    cursor = None
    try:
        mysql_conn = get_mysql_connection()
        cursor = mysql_conn.cursor()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        
        for sq, sq_vector, quota in query_vectors:
            if not sq_vector:
                print(f"[Warning] Failed to get embedding for sub-query: {sq}")
                continue
                
            search_payload = {
                "output": ["document_id", "chunk_index", "content", "document_title", "score()"],
                "search": [
                    {
                        "match_method": "dense",
                        "fields": "vec",
                        "query_vector": sq_vector,
                        "element_type": "float",
                        "metric_type": "ip",
                        "topn": 20
                    },
                    {
                        "match_method": "text",
                        "fields": "content",
                        "matching_text": sq,
                        "topn": 20
                    },
                    {
                        "fusion_method": "rrf",
                        "topn": 20
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
                
            sq_contexts = []
            for idx, item_list in enumerate(raw_results):
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
                distance = row.get("SIMILARITY") or row.get("similarity") or row.get("SCORE") or row.get("score") or (1.0 - (idx * 0.1))
                
                filename = "Unknown Source"
                if doc_id is not None:
                    cursor.execute("SELECT filename FROM documents WHERE id = %s", (doc_id,))
                    db_res = cursor.fetchone()
                    if db_res:
                        filename = db_res[0]
                        
                sq_contexts.append({
                    "document_id": int(doc_id) if doc_id is not None else None,
                    "chunk_index": int(row.get("chunk_index")) if row.get("chunk_index") is not None else None,
                    "filename": filename,
                    "servant_name": doc_title,
                    "content": content,
                    "distance": float(distance)
                })
                
            if sq_contexts:
                for i, c in enumerate(sq_contexts):
                    first_stage_candidates.append({
                        "document_id": c.get("document_id"),
                        "chunk_index": c.get("chunk_index"),
                        "filename": c["filename"],
                        "servant_name": c["servant_name"],
                        "content": c["content"],
                        "score": c["distance"],
                        "rank": i + 1,
                        "sub_query": sq
                    })
                    
                t_rerank_start = time.time()
                sq_contexts = rerank_documents(sq, sq_contexts)
                rerank_total_ms += (time.time() - t_rerank_start) * 1000.0
                
                # Store all reranked contexts for global quota allocation
                all_contexts.append({
                    "sub_query": sq,
                    "quota": quota,
                    "contexts": sq_contexts
                })
                
    except Exception as e:
        print(f"[Warning] Failed to retrieve context from Infinity/MySQL: {e}")
    finally:
        if cursor:
            cursor.close()
        if mysql_conn:
            mysql_conn.close()
            
    retrieval_ms = ((time.time() - t_retrieval_start) * 1000.0) - rerank_total_ms

    # Apply Entity-Balanced Retrieval Slicing (Quota Allocation & Deduplication)
    seen = set()
    contexts = []
    pool = []

    for item in all_contexts:
        sq = item["sub_query"]
        quota = item["quota"]
        sq_contexts = item["contexts"]
        
        selected_count = 0
        for c in sq_contexts:
            key = (c["document_id"], c["chunk_index"])
            if key not in seen:
                c_copy = dict(c)
                c_copy["sub_query"] = sq
                if selected_count < quota:
                    seen.add(key)
                    contexts.append(c_copy)
                    selected_count += 1
                else:
                    pool.append(c_copy)
                    
    # Fill remainder if we have fewer than 10 chunks total (e.g. queries were too similar)
    if len(contexts) < 10:
        pool.sort(key=lambda x: x.get("distance", 0.0), reverse=True)
        for c in pool:
            key = (c["document_id"], c["chunk_index"])
            if key not in seen:
                seen.add(key)
                contexts.append(c)
                if len(contexts) >= 10:
                    break
                    
    # Sort final selected contexts globally by reranked distance
    contexts.sort(key=lambda x: x.get("distance", 0.0), reverse=True)
    
    # 4.5. On-The-Fly Context Expansion (Parent-Child Chunking)
    doc_to_chunks = {}
    for c in contexts:
        doc_id = c.get("document_id")
        if doc_id is not None:
            if doc_id not in doc_to_chunks:
                doc_to_chunks[doc_id] = []
            doc_to_chunks[doc_id].append(c.get("chunk_index"))
            
    doc_ranges = {}
    for doc_id, chunk_indices in doc_to_chunks.items():
        ranges = sorted([[idx - 1, idx + 1] for idx in chunk_indices if idx is not None])
        merged_ranges = []
        for r in ranges:
            if not merged_ranges:
                merged_ranges.append(r)
            else:
                last_r = merged_ranges[-1]
                if r[0] <= last_r[1]:
                    last_r[1] = max(last_r[1], r[1])
                else:
                    merged_ranges.append(r)
        doc_ranges[doc_id] = merged_ranges
        
    doc_range_texts = {}
    for doc_id, merged_ranges in doc_ranges.items():
        doc_range_texts[doc_id] = []
        for start, end in merged_ranges:
            text = fetch_and_merge_chunk_range(doc_id, start, end)
            doc_range_texts[doc_id].append((start, end, text))
            
    for c in contexts:
        doc_id = c.get("document_id")
        chunk_idx = c.get("chunk_index")
        c["child_content"] = c["content"]  # preserve original child content
        if doc_id is not None and chunk_idx is not None:
            for start, end, text in doc_range_texts.get(doc_id, []):
                if start <= chunk_idx <= end and text:
                    c["content"] = text
                    break
    
    for i, c in enumerate(contexts):
        second_stage_candidates.append({
            "document_id": c.get("document_id"),
            "chunk_index": c.get("chunk_index"),
            "filename": c["filename"],
            "servant_name": c["servant_name"],
            "content": c["content"],  # Expanded parent content
            "child_content": c.get("child_content", c["content"]),  # Original child content
            "first_stage_score": next((f["score"] for f in first_stage_candidates if f["filename"] == c["filename"] and f["chunk_index"] == c["chunk_index"]), c["distance"]),
            "first_stage_rank": next((f["rank"] for f in first_stage_candidates if f["filename"] == c["filename"] and f["chunk_index"] == c["chunk_index"]), i + 1),
            "rerank_logit": c.get("rerank_logit"),
            "rerank_score": c.get("rerank_score"),
            "rank": i + 1,
            "sub_query": c.get("sub_query", "Unknown Query")
        })
        
    rerank_ms = rerank_total_ms

    # 5. Generate RAG Response via Ollama (qwen3:8b)
    t_gen_start = time.time()
    if contexts:
        unique_contents = []
        seen_contents = set()
        for c in contexts:
            if c['content'] not in seen_contents:
                seen_contents.add(c['content'])
                unique_contents.append(f"--- SOURCE: {c['filename']} ---\n{c['content']}")
        context_str = "\n\n".join(unique_contents)
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
                    "temperature": 0.3,
                    "num_ctx": 8192
                }
            },
            timeout=300.0
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
                "decomposition": round(decomp_ms, 2),
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
            r_client.setex(f"rag_cache:{query}", 3600, json.dumps(response_data))
        except Exception:
            pass

    return response_data
