from copy import deepcopy


DEFAULT_KB_SLUG = "fgo-lore"

DEFAULT_INGEST_CONFIG = {
    "embedding": {
        "model": "nomic-embed-text:latest",
        "dimensions": 768,
        "provider": "ollama",
    },
    "chunking": {
        "strategy": "markdown_aware",
        "max_chars": 600,
        "overlap_chars": 120,
    },
    "search": {
        "bm25_enabled": True,
        "initial_topn": 20,
        "rrf_k": 60,
        "context_window": 2,
    },
}

DEFAULT_GENERATION_CONFIG = {
    "model": "qwen3:8b",
    "provider": "ollama",
    "temperature": 0.0,
    "max_tokens": 256,
    "top_k_contexts": 10,
    "system_prompt": (
        "You are Chimera Cortex, a strict document AI assistant. Answer based "
        "only on the provided context. If the context does not contain the "
        "answer, state that the documents do not contain sufficient information. "
        "Address every part of the question explicitly, and preserve contrasts "
        "between entities, forms, classes, time periods, or viewpoints. Give the "
        "shortest complete answer and include only facts needed to answer the "
        "question. Use one sentence for a single fact and at most one sentence per "
        "requested part, with a hard limit of three sentences and 120 words. For each "
        "comparison, state the exact evidence-backed property for both sides. Match "
        "every fact to the exact named subject and never transfer attributes from an "
        "adjacent entity. Do not mention filenames, source labels, retrieved context, "
        "or add examples, statistics, background, or interpretations unless they are "
        "needed to answer a requested part and explicitly supported."
    ),
    "query_rewrite": {
        "enabled": True,
        "model": "qwen3:8b",
    },
    "reranker": {
        "enabled": True,
    },
}


def default_ingest_config() -> dict:
    return deepcopy(DEFAULT_INGEST_CONFIG)


def default_generation_config() -> dict:
    return deepcopy(DEFAULT_GENERATION_CONFIG)
