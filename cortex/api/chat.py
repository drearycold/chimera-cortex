import time
import json
import hashlib
import httpx
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from cortex.core.config import (
    INFINITY_API_URL, OLLAMA_GENERATE_URL,
    OLLAMA_EMBED_MODEL, OLLAMA_GEN_MODEL
)
from cortex.core.database import get_knowledge_base, get_mysql_connection, get_redis_client
from cortex.core.kb_config import (
    DEFAULT_KB_SLUG,
    default_generation_config,
    default_ingest_config,
)
from cortex.core.rag import (
    allocate_query_quotas,
    build_retrieval_filter_expression,
    decompose_query,
    fetch_and_merge_chunk_range,
    get_embedding,
    rerank_documents,
    select_context_window,
    should_decompose_query,
)

router = APIRouter(prefix="/api", tags=["Chat"])

class ChatRequest(BaseModel):
    query: str
    retrieval_filter: "RetrievalFilter | None" = None
    external_contexts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    top_k: int | None = Field(default=None, ge=1, le=100)


class DocumentConstraint(BaseModel):
    external_id: str = Field(min_length=1, max_length=512)
    max_ordinal: int | None = Field(default=None, ge=0)


class RetrievalFilter(BaseModel):
    documents: list[DocumentConstraint] = Field(default_factory=list, max_length=1000)
    source_keys: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("documents")
    @classmethod
    def unique_documents(cls, documents: list[DocumentConstraint]):
        ids = [document.external_id for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieval_filter.documents contains duplicate external_id values")
        return documents

    @field_validator("source_keys")
    @classmethod
    def valid_source_keys(cls, source_keys: list[str]):
        if any(not value or len(value) > 512 for value in source_keys):
            raise ValueError("source_keys must contain 1-512 character values")
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("retrieval_filter.source_keys contains duplicates")
        return source_keys


def build_search_payload(
    query: str,
    query_vector: list[float],
    retrieval_filter: str | None,
    topn: int = 20,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "output": [
            "document_id",
            "chunk_index",
            "content",
            "document_title",
            "external_id",
            "source_key",
            "segment_ordinal",
            "segment_locator",
            "score()",
        ],
        "search": [
            {
                "match_method": "dense",
                "fields": "vec",
                "query_vector": query_vector,
                "element_type": "float",
                "metric_type": "ip",
                "topn": topn,
            },
            {
                "match_method": "text",
                "fields": "content",
                "matching_text": query,
                "topn": topn,
            },
            {"fusion_method": "rrf", "topn": topn},
        ],
    }
    if retrieval_filter:
        payload["filter"] = retrieval_filter
    return payload


def build_chat_cache_key(
    kb_slug: str | None,
    query: str,
    retrieval_filter: dict | None,
    external_contexts: list[dict[str, Any]],
    top_k: int,
) -> str:
    identity = json.dumps(
        {
            "query": query,
            "retrieval_filter": retrieval_filter,
            "external_contexts": external_contexts,
            "top_k": top_k,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"rag_cache:{kb_slug}:{digest}" if kb_slug else f"rag_cache:{digest}"


def build_generation_payload(
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 8192,
            "num_predict": max_tokens,
        },
    }

@router.post("/chat")
def api_chat(req: ChatRequest):
    try:
        default_kb = get_knowledge_base(DEFAULT_KB_SLUG)
    except Exception:
        default_kb = None
    return _run_chat(req, default_kb)


@router.post("/kb/{slug}/chat")
def api_kb_chat(slug: str, req: ChatRequest):
    try:
        knowledge_base = get_knowledge_base(slug)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    if knowledge_base is None or not knowledge_base["enabled"]:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    return _run_chat(req, knowledge_base)


def _run_chat(req: ChatRequest, knowledge_base: dict | None = None):
    t_start = time.time()
    import unicodedata
    query = unicodedata.normalize('NFC', req.query.strip())
    if not query:
        raise HTTPException(status_code=400, detail="Empty query.")

    kb_slug = knowledge_base["slug"] if knowledge_base else None
    ingest_config = knowledge_base["ingest_config"] if knowledge_base else default_ingest_config()
    generation_config = (
        knowledge_base["generation_config"]
        if knowledge_base
        else default_generation_config()
    )
    vector_table = knowledge_base["vector_table"] if knowledge_base else "chunks"
    embedding_model = ingest_config.get("embedding", {}).get("model", OLLAMA_EMBED_MODEL)
    configured_context_window = max(
        0,
        int(ingest_config.get("search", {}).get("context_window", 1)),
    )
    generation_model = generation_config.get("model", OLLAMA_GEN_MODEL)
    temperature = float(generation_config.get("temperature", 0.3))
    max_tokens = max(64, int(generation_config.get("max_tokens", 256)))
    top_k_contexts = req.top_k or max(
        1,
        int(generation_config.get("top_k_contexts", 10)),
    )
    rewrite_config = generation_config.get("query_rewrite", {})
    rewrite_enabled = rewrite_config.get("enabled", True)
    rewrite_model = rewrite_config.get("model", OLLAMA_GEN_MODEL)
    reranker_enabled = generation_config.get("reranker", {}).get("enabled", True)
    filter_data = req.retrieval_filter.model_dump() if req.retrieval_filter else None
    try:
        retrieval_filter = build_retrieval_filter_expression(filter_data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cache_key = build_chat_cache_key(
        kb_slug,
        query,
        filter_data,
        req.external_contexts,
        top_k_contexts,
    )

    # 1. Check Redis Cache
    r_client = None
    try:
        r_client = get_redis_client()
        cached_val = r_client.get(cache_key)
        if cached_val:
            cached_data = json.loads(cached_val.decode("utf-8"))
            cached_data["cache_hit"] = True
            return cached_data
    except Exception as e:
        print(f"[Warning] Redis cache connection failed: {e}")

    # 2. Decompose Query
    t_decomp_start = time.time()
    sub_queries = (
        decompose_query(query, model=rewrite_model)
        if rewrite_enabled and should_decompose_query(query)
        else [query]
    )
    decomp_ms = (time.time() - t_decomp_start) * 1000.0
    print(f"Decomposed query '{query}' into: {sub_queries} in {decomp_ms:.2f}ms")

    # 3. Get Query Embeddings and Retrieve Closest Chunks
    t_embed_start = time.time()
    
    # Setup Entity-Balanced Retrieval Slicing quotas within the final context budget.
    queries_to_run = allocate_query_quotas(query, sub_queries, top_k_contexts)
    context_window = select_context_window(
        configured_context_window,
        len(queries_to_run),
    )
            
    query_vectors = []
    for sq, quota in queries_to_run:
        sq_vector = get_embedding(sq, is_query=True, model=embedding_model)
        query_vectors.append((sq, sq_vector, quota))
    embedding_ms = (time.time() - t_embed_start) * 1000.0

    t_retrieval_start = time.time()
    all_contexts: list[dict[str, Any]] = []
    first_stage_candidates: list[dict[str, Any]] = []
    second_stage_candidates: list[dict[str, Any]] = []
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
                
            search_payload = build_search_payload(sq, sq_vector, retrieval_filter)
            
            resp = httpx.request(
                "GET",
                f"{INFINITY_API_URL}/databases/default_db/tables/{vector_table}/docs",
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
                
            sq_contexts: list[dict[str, Any]] = []
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
                        
                chunk_index_value = row.get("chunk_index")
                locator_value = row.get("segment_locator", "")
                try:
                    locator = json.loads(locator_value) if locator_value else None
                except (TypeError, json.JSONDecodeError):
                    locator = None
                sq_contexts.append({
                    "document_id": int(doc_id) if doc_id is not None else None,
                    "chunk_index": (
                        int(chunk_index_value)
                        if chunk_index_value is not None
                        else None
                    ),
                    "filename": filename,
                    "servant_name": doc_title,
                    "content": content,
                    "distance": float(distance),
                    "external_id": row.get("external_id") or None,
                    "source_key": row.get("source_key") or None,
                    "ordinal": (
                        int(row["segment_ordinal"])
                        if row.get("segment_ordinal") is not None
                        and int(row["segment_ordinal"]) >= 0
                        else None
                    ),
                    "locator": locator,
                })
                
            if sq_contexts:
                for i, c in enumerate(sq_contexts):
                    first_stage_candidates.append({
                        "document_id": c.get("document_id"),
                        "chunk_index": c.get("chunk_index"),
                        "filename": c["filename"],
                        "servant_name": c["servant_name"],
                        "content": c["content"],
                        "external_id": c.get("external_id"),
                        "source_key": c.get("source_key"),
                        "ordinal": c.get("ordinal"),
                        "locator": c.get("locator"),
                        "score": c["distance"],
                        "rank": i + 1,
                        "sub_query": sq
                    })
                    
                t_rerank_start = time.time()
                if reranker_enabled:
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
    seen: set[tuple[Any, Any]] = set()
    contexts: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []

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
    if len(contexts) < top_k_contexts:
        pool.sort(key=lambda x: x.get("distance", 0.0), reverse=True)
        for c in pool:
            key = (c["document_id"], c["chunk_index"])
            if key not in seen:
                seen.add(key)
                contexts.append(c)
                if len(contexts) >= top_k_contexts:
                    break
                    
    # Sort final selected contexts globally by reranked distance
    contexts.sort(key=lambda x: x.get("distance", 0.0), reverse=True)
    contexts = contexts[:top_k_contexts]
    
    # 4.5. On-The-Fly Context Expansion (Parent-Child Chunking)
    doc_to_chunks: dict[Any, list[Any]] = {}
    for c in contexts:
        doc_id = c.get("document_id")
        if doc_id is not None:
            if doc_id not in doc_to_chunks:
                doc_to_chunks[doc_id] = []
            doc_to_chunks[doc_id].append(c.get("chunk_index"))
            
    doc_ranges: dict[Any, list[list[int]]] = {}
    for doc_id, chunk_indices in doc_to_chunks.items():
        ranges = sorted(
            [
                [idx - context_window, idx + context_window]
                for idx in chunk_indices
                if idx is not None
            ]
        )
        merged_ranges: list[list[int]] = []
        for chunk_range in ranges:
            if not merged_ranges:
                merged_ranges.append(chunk_range)
            else:
                last_r = merged_ranges[-1]
                if chunk_range[0] <= last_r[1]:
                    last_r[1] = max(last_r[1], chunk_range[1])
                else:
                    merged_ranges.append(chunk_range)
        doc_ranges[doc_id] = merged_ranges
        
    doc_range_texts: dict[Any, list[tuple[int, int, str]]] = {}
    for doc_id, merged_ranges in doc_ranges.items():
        doc_range_texts[doc_id] = []
        for start, end in merged_ranges:
            text = fetch_and_merge_chunk_range(
                doc_id,
                start,
                end,
                vector_table=vector_table,
                retrieval_filter=retrieval_filter,
            )
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
            "external_id": c.get("external_id"),
            "source_key": c.get("source_key"),
            "ordinal": c.get("ordinal"),
            "locator": c.get("locator"),
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
        base_prompt = generation_config.get(
            "system_prompt",
            default_generation_config()["system_prompt"],
        )
        system_prompt = f"{base_prompt}\n\nHere is the retrieved context:\n{context_str}"
    else:
        system_prompt = (
            f"{generation_config.get('system_prompt', default_generation_config()['system_prompt'])} "
            "No matching document context was found in this knowledge base."
        )

    if req.external_contexts:
        external_context_text = json.dumps(req.external_contexts, ensure_ascii=False)
        system_prompt += f"\n\nAdditional external evidence:\n{external_context_text}"
    full_prompt = f"System: {system_prompt}\n\nUser Question: {query}\n\nAnswer:"
    
    try:
        generation_response = httpx.post(
            OLLAMA_GENERATE_URL,
            json=build_generation_payload(
                generation_model,
                full_prompt,
                temperature,
                max_tokens,
            ),
            timeout=300.0
        )
        generation_response.raise_for_status()
        answer = generation_response.json()["response"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama generation failed: {str(e)}")
    generation_ms = (time.time() - t_gen_start) * 1000.0
    total_ms = (time.time() - t_start) * 1000.0

    citations = []
    seen_citations = set()
    for context in contexts:
        if not context.get("external_id"):
            continue
        citation_key = (
            context["external_id"],
            context.get("ordinal"),
            json.dumps(context.get("locator"), sort_keys=True),
        )
        if citation_key in seen_citations:
            continue
        seen_citations.add(citation_key)
        citations.append(
            {
                "external_id": context["external_id"],
                "title": context["servant_name"],
                "ordinal": context.get("ordinal"),
                "locator": context.get("locator"),
            }
        )

    response_data = {
        "answer": answer,
        "contexts": contexts,
        "citations": citations,
        "cache_hit": False,
        "knowledge_base": kb_slug,
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
            r_client.setex(cache_key, 3600, json.dumps(response_data))
        except Exception:
            pass

    return response_data
