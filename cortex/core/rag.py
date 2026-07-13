import os
import re
import math
import json
from typing import Any

import httpx

from .config import (
    OLLAMA_EMBED_URL, OLLAMA_EMBED_MODEL, RERANKER_URL,
    OLLAMA_GENERATE_URL, OLLAMA_GEN_MODEL
)


class RetrievalBackendError(RuntimeError):
    """Raised when a retrieval service cannot return trustworthy results."""

def parse_document_title(filename):
    """Clean and extract clear, human-readable document titles from filenames."""
    import unicodedata
    filename = unicodedata.normalize('NFC', filename)
    base = os.path.basename(filename)
    # Strip numeric prefixes and lore suffixes common in Fate dataset, e.g. 123_Gawain_lore.md -> Gawain
    match = re.match(r"^\d+_(.*)_lore\.md$", base)
    if match:
        name_str = match.group(1)
        return name_str.replace("_", " ")
    # Otherwise general clean up
    return base.replace("_", " ").replace(".md", "")

def chunk_markdown(content, doc_title, max_chars=600, overlap_chars=120):
    """Split markdown text into semantically cohesive, heading-aware chunks with overlap."""
    import unicodedata
    content = unicodedata.normalize('NFC', content)
    doc_title = unicodedata.normalize('NFC', doc_title)
    lines = content.split("\n")
    current_heading = "Overview"
    paragraphs: list[tuple[str, str]] = []
    current_para: list[str] = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_para:
                paragraphs.append((current_heading, "\n".join(current_para)))
                current_para = []
            continue
        
        heading_match = re.match(r"^(#+)\s+(.*)$", stripped)
        if heading_match:
            if current_para:
                paragraphs.append((current_heading, "\n".join(current_para)))
                current_para = []
            current_heading = heading_match.group(2).strip()
            continue
            
        current_para.append(line)
        
    if current_para:
        paragraphs.append((current_heading, "\n".join(current_para)))
        
    # Split oversized paragraphs/tables into smaller sub-paragraphs
    split_paragraphs: list[tuple[str, str]] = []
    for heading, text in paragraphs:
        prefix = f"[{doc_title} - {heading}] "
        if len(prefix) + len(text) > max_chars:
            lines_in_para = text.split("\n")
            current_sub: list[str] = []
            current_sub_len = 0
            for line in lines_in_para:
                line_len = len(line)
                # Cap split sub-paragraphs to leave space for prefix and safe buffer
                if current_sub_len + line_len + (1 if current_sub else 0) > (max_chars - len(prefix) - 40):
                    if current_sub:
                        split_paragraphs.append((heading, "\n".join(current_sub)))
                    current_sub = [line]
                    current_sub_len = line_len
                else:
                    current_sub.append(line)
                    current_sub_len += line_len + 1
            if current_sub:
                split_paragraphs.append((heading, "\n".join(current_sub)))
        else:
            split_paragraphs.append((heading, text))
        
    chunks: list[tuple[str, str]] = []
    current_chunk: list[str] = []
    current_len = 0
    current_head = "Overview"
    
    for heading, text in split_paragraphs:
        prefix = f"[{doc_title} - {heading}] "
        full_text = prefix + text
        
        if current_len + len(full_text) > max_chars and current_chunk:
            chunk_content = "\n\n".join(current_chunk)
            chunks.append((current_head, chunk_content))
            
            # Construct overlap for the next chunk
            if overlap_chars > 0:
                overlap_elements: list[str] = []
                overlap_len = 0
                for item in reversed(current_chunk):
                    if item.startswith("..."):
                        continue
                    if overlap_len + len(item) + (2 if overlap_elements else 0) <= overlap_chars:
                        overlap_elements.insert(0, item)
                        overlap_len += len(item) + 2
                    else:
                        break
                
                if overlap_elements:
                    current_chunk = overlap_elements + [full_text]
                    current_len = sum(len(x) + 2 for x in current_chunk) - 2
                else:
                    # Fallback to character slicing of the last paragraph
                    last_item = current_chunk[-1]
                    if last_item.startswith("..."):
                        overlap_text = last_item
                    else:
                        overlap_text = last_item[-overlap_chars:]
                        first_space = overlap_text.find(" ")
                        if first_space != -1 and first_space < 30:
                            overlap_text = overlap_text[first_space + 1:]
                        if overlap_text:
                            overlap_text = f"... {overlap_text.strip()}"
                    
                    if overlap_text:
                        current_chunk = [overlap_text, full_text]
                        current_len = len(overlap_text) + 2 + len(full_text)
                    else:
                        current_chunk = [full_text]
                        current_len = len(full_text)
            else:
                current_chunk = [full_text]
                current_len = len(full_text)
            current_head = heading
        else:
            current_chunk.append(full_text)
            current_len += len(full_text) + 2
            
    if current_chunk:
        chunks.append((current_head, "\n\n".join(current_chunk)))
        
    return chunks

def get_embedding(text, is_query=False, model=OLLAMA_EMBED_MODEL):
    """Query the local Ollama service to generate a dense vector embedding."""
    import unicodedata
    text = unicodedata.normalize('NFC', text)
    try:
        prefix = "search_query: " if is_query else "search_document: "
        prefixed_text = prefix + text
        embed_url = OLLAMA_EMBED_URL.replace("/api/embeddings", "/api/embed")
        r = httpx.post(
            embed_url,
            json={"model": model, "input": prefixed_text},
            timeout=30.0
        )
        r.raise_for_status()
        res = r.json()
        if "embeddings" in res:
            return res["embeddings"][0]
        return res.get("embedding")
    except Exception as e:
        print(f"[ERROR] Failed calling Ollama embedding API: {e}")
        return None

def get_embeddings_batch(texts, model=OLLAMA_EMBED_MODEL):
    """Query the local Ollama service to generate dense vector embeddings for a list of texts in one batch."""
    if not texts:
        return []
    try:
        prefixed_texts = [f"search_document: {t}" for t in texts]
        embed_url = OLLAMA_EMBED_URL.replace("/api/embeddings", "/api/embed")
        r = httpx.post(
            embed_url,
            json={"model": model, "input": prefixed_texts},
            timeout=60.0
        )
        r.raise_for_status()
        res = r.json()
        return res.get("embeddings", [])
    except Exception as e:
        print(f"[ERROR] Failed calling Ollama batch embedding API: {e}")
        return [None] * len(texts)

def rerank_documents(query: str, candidates: list) -> list:
    """Rerank candidates using a cross-encoder, applying sigmoid normalization to logits."""
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

def decompose_query(query: str, model=OLLAMA_GEN_MODEL) -> list[str]:
    """Use the generation LLM to decompose a complex query into atomic sub-queries."""
    import unicodedata
    query = unicodedata.normalize('NFC', query)
    prompt = (
        "You are a search query optimizer. Decompose only when separate evidence is likely "
        "needed for a comparison between entities, forms, classes, time periods, or viewpoints, "
        "or when a premise and a contrasting conclusion must both be retrieved. For comparisons, "
        "produce one focused query for the shared premise and one for each side. Do not decompose "
        "a short compound fact question about one entity when the facts are likely stated together; "
        "return that question unchanged. Example unchanged: 'At what age did X leave home, and at "
        "what age did X die?' Example decomposed: 'What did X learn, and how is it used differently "
        "as a Lancer versus a Caster?' Return at most 3 atomic sub-queries.\n\n"
        "Return ONLY a JSON array of query strings. No explanation.\n\n"
        f"Question: {query}\n\nSub-queries:"
    )
    
    try:
        resp = httpx.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.0,
                    "num_ctx": 8192
                }
            },
            timeout=45.0
        )
        resp.raise_for_status()
        response_text = resp.json()["response"].strip()
        
        # Clean response if wrapped in markdown blocks
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            response_text = "\n".join(lines).strip()
            
        sub_queries = json.loads(response_text)
        if isinstance(sub_queries, list) and len(sub_queries) > 0:
            parsed_queries = []
            for q in sub_queries:
                if isinstance(q, dict):
                    # extract the first string value if it's a dict
                    val = next(iter(q.values()))
                    parsed_queries.append(str(val))
                else:
                    parsed_queries.append(str(q))
            return parsed_queries
    except Exception as e:
        print(f"[Warning] Query decomposition failed, falling back to original query: {e}")
        
    return [query]


def should_decompose_query(query: str) -> bool:
    """Return whether a question contains an explicit multi-part retrieval need."""
    normalized = f" {re.sub(r'\s+', ' ', query.casefold()).strip()} "
    if query.count("?") > 1:
        return True
    markers = (
        " compare ",
        " compared ",
        " versus ",
        " vs. ",
        " difference ",
        " differ ",
        " despite ",
        " whereas ",
        " respectively ",
        " and how ",
        " and why ",
        " and what ",
        " and who ",
        " and when ",
    )
    return any(marker in normalized for marker in markers)


def allocate_query_quotas(
    query: str,
    sub_queries: list[str],
    total_contexts: int,
) -> list[tuple[str, int]]:
    """Allocate the full context budget without dropping any atomic sub-query."""
    unique_sub_queries = []
    seen = {query.casefold()}
    for sub_query in sub_queries:
        normalized = sub_query.strip()
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        unique_sub_queries.append(normalized)

    if len(unique_sub_queries) < 2:
        return [(query, total_contexts)]

    unique_sub_queries = unique_sub_queries[: max(1, total_contexts - 1)]
    original_quota = max(1, total_contexts // 3)
    remaining = total_contexts - original_quota
    base_quota, extra = divmod(remaining, len(unique_sub_queries))
    allocations = [(query, original_quota)]
    for index, sub_query in enumerate(unique_sub_queries):
        allocations.append((sub_query, base_quota + (1 if index < extra else 0)))
    return allocations


def select_context_window(configured_window: int, query_count: int) -> int:
    """Keep simple lookups compact while allowing comparisons broader evidence."""
    safe_window = max(0, configured_window)
    if query_count <= 1:
        return min(safe_window, 1)
    return safe_window


def build_context_windows(
    contexts: list[dict[str, Any]],
    context_window: int,
) -> list[dict[str, Any]]:
    """Group selected child chunks into unique contiguous evidence windows."""
    safe_window = max(0, context_window)
    grouped: dict[int, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []
    for context in contexts:
        document_id = context.get("document_id")
        chunk_index = context.get("chunk_index")
        if document_id is None or chunk_index is None:
            standalone.append(
                {
                    "document_id": document_id,
                    "start": chunk_index,
                    "end": chunk_index,
                    "matches": [context],
                }
            )
            continue
        grouped.setdefault(int(document_id), []).append(context)

    windows: list[dict[str, Any]] = []
    for document_id in sorted(grouped):
        ranges = [
            (
                max(0, int(context["chunk_index"]) - safe_window),
                int(context["chunk_index"]) + safe_window,
                context,
            )
            for context in grouped[document_id]
        ]
        ranges.sort(key=lambda item: (item[0], item[1]))
        for start, end, context in ranges:
            if (
                windows
                and windows[-1]["document_id"] == document_id
                and start <= windows[-1]["end"] + 1
            ):
                windows[-1]["end"] = max(windows[-1]["end"], end)
                windows[-1]["matches"].append(context)
            else:
                windows.append(
                    {
                        "document_id": document_id,
                        "start": start,
                        "end": end,
                        "matches": [context],
                    }
                )
    windows.extend(standalone)
    return windows


def merge_chunk_contents(contents: list[str]) -> str:
    """Merge ordered chunks while removing only overlaps verified at boundaries."""
    if not contents:
        return ""

    merged = contents[0]
    for current in contents[1:]:
        overlap_length = 0
        max_overlap = min(len(merged), len(current), 250)
        for candidate_length in range(max_overlap, 10, -1):
            if merged[-candidate_length:] == current[:candidate_length]:
                overlap_length = candidate_length
                break
        if overlap_length:
            merged += current[overlap_length:]
            continue

        first_block, separator, remainder = current.partition("\n\n")
        if first_block.startswith("... "):
            overlap_text = first_block[4:].strip()
            if overlap_text and merged.rstrip().endswith(overlap_text):
                if separator and remainder:
                    merged = merged.rstrip() + "\n\n" + remainder
                continue

        merged += "\n\n" + current
    return merged


def _quote_filter_value(value: str) -> str:
    if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
        raise ValueError("Retrieval filter values must be 1-512 printable characters.")
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def build_retrieval_filter_expression(retrieval_filter: dict | None) -> str | None:
    """Compile opaque document/source constraints into an Infinity row filter."""
    if retrieval_filter is None:
        return None
    clauses = []
    for document in retrieval_filter.get("documents", []):
        clause = f"external_id = {_quote_filter_value(document['external_id'])}"
        max_ordinal = document.get("max_ordinal")
        if max_ordinal is not None:
            if not isinstance(max_ordinal, int) or max_ordinal < 0:
                raise ValueError("max_ordinal must be a non-negative integer.")
            clause = f"({clause} AND segment_ordinal <= {max_ordinal})"
        clauses.append(clause)
    source_keys = retrieval_filter.get("source_keys", [])
    if source_keys:
        values = ", ".join(_quote_filter_value(value) for value in source_keys)
        clauses.append(f"source_key IN ({values})")
    if not clauses:
        return "(document_id = -1)"
    return "(" + " OR ".join(clauses) + ")"

def fetch_and_merge_chunk_range(
    doc_id: int,
    start_idx: int,
    end_idx: int,
    vector_table: str = "chunks",
    retrieval_filter: str | None = None,
) -> str:
    """
    Fetch a contiguous range of chunks from Infinity DB and fuse them cleanly by identifying and resolving overlaps.
    """
    from .config import INFINITY_API_URL

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,254}", vector_table):
        raise ValueError(f"Invalid Infinity table name: {vector_table}")
    
    filter_expr = f"document_id = {doc_id} AND chunk_index >= {start_idx} AND chunk_index <= {end_idx}"
    if retrieval_filter:
        filter_expr += f" AND {retrieval_filter}"
    search_payload = {
        "output": ["chunk_index", "content"],
        "filter": filter_expr
    }
    
    try:
        resp = httpx.request(
            "GET",
            f"{INFINITY_API_URL}/databases/default_db/tables/{vector_table}/docs",
            json=search_payload,
            headers={"Content-Type": "application/json"},
            timeout=10.0,
            trust_env=False,
        )
        resp.raise_for_status()
        res = resp.json()
        if res.get("error_code", 0) != 0:
            raise RetrievalBackendError(
                res.get("error_msg") or "Infinity adjacent chunk retrieval failed"
            )
        
        raw_results = []
        if "output" in res:
            raw_results = res["output"]
        elif "docs" in res:
            raw_results = res["docs"]
        elif "rows" in res:
            raw_results = res["rows"]
                
        # Parse and sort by chunk_index
        parsed_chunks = []
        for item_list in raw_results:
            row = {}
            if isinstance(item_list, list):
                for item in item_list:
                    if isinstance(item, dict):
                        row.update(item)
            elif isinstance(item_list, dict):
                row = item_list
                
            c_idx = row.get("chunk_index")
            content = row.get("content")
            if c_idx is not None and content:
                parsed_chunks.append({"chunk_index": int(c_idx), "content": content})
                
        parsed_chunks.sort(key=lambda x: x["chunk_index"])
        
        if not parsed_chunks:
            return ""
            
        return merge_chunk_contents([chunk["content"] for chunk in parsed_chunks])
        
    except Exception as exc:
        raise RetrievalBackendError(
            "Infinity adjacent chunk retrieval failed for "
            f"document_id={doc_id}, range={start_idx}-{end_idx}: {exc}"
        ) from exc
