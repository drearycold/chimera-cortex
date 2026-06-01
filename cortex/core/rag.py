import os
import re
import math
import json
import httpx
from .config import (
    OLLAMA_EMBED_URL, OLLAMA_EMBED_MODEL, RERANKER_URL,
    OLLAMA_GENERATE_URL, OLLAMA_GEN_MODEL
)

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
    paragraphs = []
    current_para = []
    
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
    split_paragraphs = []
    for heading, text in paragraphs:
        prefix = f"[{doc_title} - {heading}] "
        if len(prefix) + len(text) > max_chars:
            lines_in_para = text.split("\n")
            current_sub = []
            current_sub_len = 0
            for l in lines_in_para:
                line_len = len(l)
                # Cap split sub-paragraphs to leave space for prefix and safe buffer
                if current_sub_len + line_len + (1 if current_sub else 0) > (max_chars - len(prefix) - 40):
                    if current_sub:
                        split_paragraphs.append((heading, "\n".join(current_sub)))
                    current_sub = [l]
                    current_sub_len = line_len
                else:
                    current_sub.append(l)
                    current_sub_len += line_len + 1
            if current_sub:
                split_paragraphs.append((heading, "\n".join(current_sub)))
        else:
            split_paragraphs.append((heading, text))
        
    chunks = []
    current_chunk = []
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
                overlap_elements = []
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

def get_embedding(text, is_query=False):
    """Query the local Ollama service to generate a dense vector embedding."""
    import unicodedata
    text = unicodedata.normalize('NFC', text)
    try:
        prefix = "search_query: " if is_query else "search_document: "
        prefixed_text = prefix + text
        embed_url = OLLAMA_EMBED_URL.replace("/api/embeddings", "/api/embed")
        r = httpx.post(
            embed_url,
            json={"model": OLLAMA_EMBED_MODEL, "input": prefixed_text},
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

def get_embeddings_batch(texts):
    """Query the local Ollama service to generate dense vector embeddings for a list of texts in one batch."""
    if not texts:
        return []
    try:
        prefixed_texts = [f"search_document: {t}" for t in texts]
        embed_url = OLLAMA_EMBED_URL.replace("/api/embeddings", "/api/embed")
        r = httpx.post(
            embed_url,
            json={"model": OLLAMA_EMBED_MODEL, "input": prefixed_texts},
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

def decompose_query(query: str) -> list[str]:
    """Use the generation LLM to decompose a complex query into atomic sub-queries."""
    import unicodedata
    query = unicodedata.normalize('NFC', query)
    prompt = (
        "You are a search query optimizer. Given a user question, determine if it asks about "
        "multiple entities or topics. If so, decompose it into 2-3 focused sub-queries, each "
        "targeting a single entity/topic. If the question is simple and targets one entity, "
        "return it unchanged.\n\n"
        "Return ONLY a JSON array of query strings. No explanation.\n\n"
        f"Question: {query}\n\nSub-queries:"
    )
    
    try:
        resp = httpx.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": OLLAMA_GEN_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0}
            },
            timeout=30.0
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
