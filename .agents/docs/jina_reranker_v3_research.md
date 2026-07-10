# Research: Upgrading to Jina Reranker v3 GGUF

This document summarizes the research on replacing our current reranker (`bge-reranker-v2-m3` running via `llama-server`) with **Jina Reranker v3** (`jinaai/jina-reranker-v3-GGUF`).

## 1. Feasibility & Compatibility

**Is Jina Reranker v3 compatible with our stack?**
**No, not natively.** You are entirely correct—while Jina Reranker **v2** support is merged in the mainline `llama.cpp`, support for **v3** remains an open issue/PR (e.g., Issue #17189). 

Because the `llama.cpp` mainline repository does not yet support the v3 cross-encoder architecture natively, we **cannot** use our standard `llama-server` binary to host it. To deploy v3, we would have to compile and maintain a custom, unofficial fork of `llama.cpp` (often provided by Jina AI or community members) on our `192.168.11.40` host, which introduces significant maintenance overhead and potential instability.

## 2. Model Overview

*   **Model:** [jinaai/jina-reranker-v3](https://huggingface.co/jinaai/jina-reranker-v3) (GGUF version available as `jinaai/jina-reranker-v3-GGUF`)
*   **Parameters:** 0.6B (Slightly larger than our current ~567M parameter BGE model, but still very lightweight)
*   **Architecture:** Cross-Encoder
*   **Strengths:** Highly optimized for multilingual retrieval, long-context handling, and high accuracy out-of-the-box compared to BGE v2.

## 3. Required Steps to Migrate

To switch the active reranker on our remote host (`192.168.11.40`), we would need to:

1.  **Download the GGUF File:** 
    Download the appropriate quantization of the model (e.g., `Q4_K_M` or `Q8_0` for better precision since the model is small) onto the remote host `192.168.11.40`.
2.  **Update the `llama-server` Startup Command:**
    Change the systemd service or startup script that currently launches `llama-server` on `192.168.11.40:8082` to point to the new Jina GGUF file:
    ```bash
    ./llama-server -m /path/to/jina-reranker-v3-Q8_0.gguf --port 8082 --host 0.0.0.0
    ```
3.  **Codebase Adjustments:**
    *   No changes are required in `cortex/core/config.py` since the API URL and port (`http://192.168.11.40:8082/v1/rerank`) will remain exactly the same.
    *   No changes are required in `cortex/core/rag.py` because `llama-server` abstracts the model architecture behind the standard `/v1/rerank` endpoint, and we already apply sigmoid normalization if necessary.

## 4. Evaluation & Benchmarking

Once the model is swapped on the server, we must run our benchmark suite (`python benchmark.py --judge-model qwen3:8b`) to:
1.  **Compare Retrieval Relevance:** Ensure that the Jina model scores equal or higher than the baseline `bge-reranker-v2-m3`.
2.  **Verify Latency:** The Jina model is slightly larger (0.6B), so we need to verify that reranking 10-20 context chunks per query does not introduce unacceptable latency overhead compared to BGE.

---

> [!TIP]
> Since this is a drop-in replacement on the `llama-server` side and requires zero code changes in our repository, it is very low-risk to test.

If you would like to proceed with this upgrade, you can swap the model on `192.168.11.40` and we can immediately run a benchmark to evaluate its performance.
