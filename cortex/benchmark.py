"""
Chimera Cortex — Benchmark Core Module
=======================================
Contains the evaluation pipeline logic: LLM-as-Judge scoring, RAG querying,
background execution manager, and metric aggregation.

This module is imported by:
  - app.py       (to run benchmarks via the web API)
  - benchmark.py (thin CLI wrapper that delegates to the HTTP API)
"""

import json
import os
import re
import time
import threading
import httpx

from cortex.config import DEFAULT_OLLAMA_HOST, DEFAULT_JUDGE_MODEL
from cortex.database import (
    save_benchmark_run, update_benchmark_run_status, save_benchmark_result
)

# ---------------------------------------------------------------------------
# Judge Prompts
# ---------------------------------------------------------------------------
JUDGE_SYSTEM_PROMPT = """\
You are an impartial judge evaluating a RAG (Retrieval-Augmented Generation) system.
You will receive:
  - QUESTION: the user's question
  - REFERENCE ANSWER: the ground-truth answer
  - RAG ANSWER: the answer produced by the RAG system
  - RETRIEVED CONTEXTS: the text chunks the RAG system retrieved

Score the RAG system on three dimensions using a 1-5 integer scale:
1. **answer_correctness** — Does the RAG answer convey the same key facts as the reference answer?
   - 5: All key facts present and accurate
   - 4: Most key facts present, minor omission
   - 3: Some key facts present, some missing
   - 2: Few key facts, significant errors or omissions
   - 1: Completely wrong or irrelevant
2. **faithfulness** — Is the RAG answer grounded in the retrieved contexts (no hallucination)?
   - 5: Every claim is supported by the retrieved contexts
   - 4: Almost all claims supported, trivial unsupported detail
   - 3: Mix of supported and unsupported claims
   - 2: Significant unsupported claims
   - 1: Mostly hallucinated
3. **retrieval_relevance** — Did the retrieved chunks contain the information needed to answer?
   - 5: Retrieved chunks contain all necessary information
   - 4: Most necessary information present
   - 3: Some relevant information, but key pieces missing
   - 2: Little relevant information
   - 1: Retrieved chunks are irrelevant

Respond ONLY with a JSON object in this exact format (no markdown, no extra text):
{"answer_correctness": <int>, "faithfulness": <int>, "retrieval_relevance": <int>, "rationale": "<brief explanation>"}
"""

JUDGE_USER_TEMPLATE = """\
QUESTION:
{question}
REFERENCE ANSWER:
{reference_answer}
RAG ANSWER:
{rag_answer}
RETRIEVED CONTEXTS:
{contexts}
"""

# ---------------------------------------------------------------------------
# Pipeline Helpers
# ---------------------------------------------------------------------------
def flush_cache_via_api(api_url: str, timeout: float = 5.0):
    """Flush the RAG cache by calling POST /api/cache/clear on the live API."""
    try:
        resp = httpx.post(f"{api_url}/api/cache/clear", timeout=timeout)
        resp.raise_for_status()
        print(f"[INFO] Cache cleared via API.")
    except Exception as e:
        print(f"[WARN] Failed to clear cache via API: {e}")

def query_rag(api_url: str, question: str, timeout: float = 60.0) -> dict:
    """Send a question to the RAG /api/chat endpoint."""
    resp = httpx.post(
        f"{api_url}/api/chat",
        json={"query": question},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()

def call_judge(
    ollama_host: str,
    judge_model: str,
    question: str,
    reference_answer: str,
    rag_answer: str,
    contexts: list,
    timeout: float = 60.0,
) -> dict:
    """Ask the LLM judge to score a single QA result. Returns parsed scores dict."""
    ctx_text = "\n---\n".join(
        [f"[{c.get('filename', 'unknown')}] {c.get('content', '')}" for c in contexts]
    ) if contexts else "(no contexts retrieved)"
    
    user_prompt = JUDGE_USER_TEMPLATE.format(
        question=question,
        reference_answer=reference_answer,
        rag_answer=rag_answer,
        contexts=ctx_text,
    )
    full_prompt = f"System: {JUDGE_SYSTEM_PROMPT}\n\nUser:\n{user_prompt}\n\nJudge:"
    
    resp = httpx.post(
        f"http://{ollama_host}/api/generate",
        json={
            "model": judge_model,
            "prompt": full_prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()
    return parse_judge_response(raw)

def parse_judge_response(raw: str) -> dict:
    """Parse the judge LLM's JSON response with regex fallback."""
    defaults = {
        "answer_correctness": 1,
        "faithfulness": 1,
        "retrieval_relevance": 1,
        "rationale": "Failed to parse judge response.",
        "raw_judge_output": raw,
    }
    
    # Clean markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    
    try:
        parsed = json.loads(cleaned)
        for key in ["answer_correctness", "faithfulness", "retrieval_relevance"]:
            val = parsed.get(key)
            if isinstance(val, (int, float)) and 1 <= val <= 5:
                defaults[key] = int(val)
        defaults["rationale"] = parsed.get("rationale", defaults["rationale"])
        defaults["raw_judge_output"] = raw
        return defaults
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Regex fallback
    for key in ["answer_correctness", "faithfulness", "retrieval_relevance"]:
        match = re.search(rf'"{key}"\s*:\s*(\d)', raw)
        if match:
            val = int(match.group(1))
            if 1 <= val <= 5:
                defaults[key] = val
    rationale_match = re.search(r'"rationale"\s*:\s*"([^"]*)"', raw, re.DOTALL)
    if rationale_match:
        defaults["rationale"] = rationale_match.group(1)
    defaults["raw_judge_output"] = raw
    return defaults

# ---------------------------------------------------------------------------
# Background Execution & Concurrency Manager
# ---------------------------------------------------------------------------
class BenchmarkManager:
    """Thread-safe manager ensuring at most one benchmark runs at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.active_run_id = None
        self.thread = None

    def start(self, run_id, dataset_path, judge_model, api_url, ollama_host,
              reuse_cache=False, delay=1.0, timeout=90.0):
        """Start benchmark asynchronously in a background thread."""
        with self.lock:
            if self.active_run_id is not None:
                raise ValueError("A benchmark run is already in progress.")
            self.cancel_event.clear()
            self.active_run_id = run_id
            
        self.thread = threading.Thread(
            target=self._run_wrapper,
            args=(run_id, dataset_path, judge_model, api_url, ollama_host,
                  reuse_cache, delay, timeout)
        )
        self.thread.daemon = True
        self.thread.start()
        
    def stop(self):
        """Signal the current run to cancel gracefully."""
        with self.lock:
            if self.active_run_id is None:
                return False
            self.cancel_event.set()
            return True

    def get_status(self):
        """Get current execution status."""
        with self.lock:
            if self.active_run_id is not None:
                return {
                    "status": "running",
                    "run_id": self.active_run_id
                }
            return {
                "status": "idle",
                "run_id": None
            }

    def clear_active(self):
        """Reset manager state."""
        with self.lock:
            self.active_run_id = None
            self.thread = None

    def _run_wrapper(self, run_id, dataset_path, judge_model, api_url,
                     ollama_host, reuse_cache, delay, timeout):
        try:
            run_benchmark_internal(
                self, run_id, dataset_path, judge_model, api_url,
                ollama_host, reuse_cache, delay, timeout
            )
        except Exception as e:
            print(f"[ERROR] Benchmark background thread crashed: {e}")
            try:
                update_benchmark_run_status(run_id, "failed")
            except Exception:
                pass
        finally:
            self.clear_active()

# Singleton manager instance — imported by app.py
manager = BenchmarkManager()

# ---------------------------------------------------------------------------
# Internal Core Runner (Checks for Cancellation)
# ---------------------------------------------------------------------------
def run_benchmark_internal(
    mgr: BenchmarkManager,
    run_id: int,
    dataset_path: str,
    judge_model: str,
    api_url: str,
    ollama_host: str,
    reuse_cache: bool,
    delay: float,
    timeout: float
):
    """Executes the benchmark questions, evaluates them, and writes results to MySQL."""
    print(f"[RUNNER] Starting benchmark run_id={run_id}")
    
    # 1. Load dataset
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
        
    # 2. Flush Redis cache unless reuse_cache is requested
    if not reuse_cache:
        print("[RUNNER] Flushing generation cache via API...")
        flush_cache_via_api(api_url)
        
    # 3. Main execution loop
    results_list = []
    start_time = time.time()
    
    for idx, qa in enumerate(dataset, 1):
        # Graceful cancellation check
        if mgr.cancel_event.is_set():
            print(f"[RUNNER] Graceful cancellation received. Aborting run_id={run_id}")
            elapsed = time.time() - start_time
            update_completed_metrics(run_id, results_list, elapsed, status="cancelled")
            return
            
        qid = qa["id"]
        question = qa["question"]
        ref_answer = qa["reference_answer"]
        difficulty = qa.get("difficulty", "unknown")
        
        print(f"[RUNNER] [{idx}/{len(dataset)}] Processing {qid}...")
        
        # Query RAG
        try:
            rag_resp = query_rag(api_url, question, timeout=timeout)
            rag_answer = rag_resp.get("answer", "")
            contexts = rag_resp.get("contexts", [])
            cache_hit = rag_resp.get("cache_hit", False)
            audit = rag_resp.get("audit")
        except Exception as e:
            print(f"[RUNNER] RAG Query failed for {qid}: {e}")
            rag_answer = f"[ERROR] RAG Query Failed: {e}"
            contexts = []
            cache_hit = False
            audit = None
            
        if audit is None:
            audit = {
                "timings_ms": {"embedding": 0.0, "retrieval": 0.0, "rerank": 0.0, "generation": 0.0, "total": 0.0},
                "first_stage_candidates": [],
                "second_stage_candidates": [],
                "llm_prompt": "N/A"
            }
            
        # Call LLM Judge
        try:
            scores = call_judge(
                ollama_host=ollama_host,
                judge_model=judge_model,
                question=question,
                reference_answer=ref_answer,
                rag_answer=rag_answer,
                contexts=contexts,
                timeout=timeout
            )
        except Exception as e:
            print(f"[RUNNER] Judge call failed for {qid}: {e}")
            scores = {
                "answer_correctness": 1,
                "faithfulness": 1,
                "retrieval_relevance": 1,
                "rationale": f"[ERROR] Judge call failed: {e}",
                "raw_judge_output": ""
            }
            
        result_item = {
            "id": qid,
            "question": question,
            "difficulty": difficulty,
            "reference_answer": ref_answer,
            "rag_answer": rag_answer,
            "cache_hit": cache_hit,
            "scores": scores,
            "audit": audit
        }
        
        results_list.append(result_item)
        
        # Save to database immediately
        save_benchmark_result(run_id, result_item)
        
        # Small sleep between questions
        if idx < len(dataset):
            time.sleep(delay)
            
    elapsed = time.time() - start_time
    print(f"[RUNNER] Completed all {len(dataset)} questions. Updating aggregates.")
    update_completed_metrics(run_id, results_list, elapsed, status="completed")


def update_completed_metrics(run_id: int, results: list, elapsed: float, status: str):
    """Calculates aggregates and updates database run status."""
    total = len(results)
    if total == 0:
        update_benchmark_run_status(
            run_id=run_id,
            status=status,
            duration_seconds=elapsed,
            avg_correctness=0.0,
            avg_faithfulness=0.0,
            avg_relevance=0.0,
            pass_rate=0.0
        )
        return
        
    correctness_scores = [r["scores"]["answer_correctness"] for r in results]
    faithfulness_scores = [r["scores"]["faithfulness"] for r in results]
    retrieval_scores = [r["scores"]["retrieval_relevance"] for r in results]
    
    avg_c = sum(correctness_scores) / total
    avg_f = sum(faithfulness_scores) / total
    avg_r = sum(retrieval_scores) / total
    
    pass_count = sum(1 for s in correctness_scores if s >= 4)
    pass_rate = (pass_count / total) * 100.0
    
    update_benchmark_run_status(
        run_id=run_id,
        status=status,
        duration_seconds=elapsed,
        avg_correctness=avg_c,
        avg_faithfulness=avg_f,
        avg_relevance=avg_r,
        pass_rate=pass_rate
    )
