import os
import re
import math
import httpx
from .config import (
    OLLAMA_EMBED_URL, OLLAMA_EMBED_MODEL, RERANKER_URL
)

def parse_document_title(filename):
    """Clean and extract clear, human-readable document titles from filenames."""
    base = os.path.basename(filename)
    # Strip numeric prefixes and lore suffixes common in Fate dataset, e.g. 123_Gawain_lore.md -> Gawain
    match = re.match(r"^\d+_(.*)_lore\.md$", base)
    if match:
        name_str = match.group(1)
        return name_str.replace("_", " ")
    # Otherwise general clean up
    return base.replace("_", " ").replace(".md", "")

def chunk_markdown(content, doc_title, max_chars=800):
    """Split markdown text into semantically cohesive, heading-aware chunks under 800 characters."""
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
        
    chunks = []
    current_chunk = []
    current_len = 0
    current_head = "Overview"
    
    for heading, text in paragraphs:
        prefix = f"[{doc_title} - {heading}] "
        full_text = prefix + text
        
        if current_len + len(full_text) > max_chars and current_chunk:
            chunks.append((current_head, "\n\n".join(current_chunk)))
            current_chunk = [full_text]
            current_len = len(full_text)
            current_head = heading
        else:
            current_chunk.append(full_text)
            current_len += len(full_text) + 2
            
    if current_chunk:
        chunks.append((current_head, "\n\n".join(current_chunk)))
        
    return chunks

def get_embedding(text):
    """Query the local Ollama service to generate a 1024-dimensional dense vector embedding."""
    try:
        r = httpx.post(
            OLLAMA_EMBED_URL,
            json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
            timeout=30.0
        )
        r.raise_for_status()
        return r.json()["embedding"]
    except Exception as e:
        print(f"[ERROR] Failed calling Ollama embedding API: {e}")
        return None

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
