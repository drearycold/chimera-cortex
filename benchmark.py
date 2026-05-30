#!/usr/bin/env python3
"""
Chimera Cortex — RAG Benchmark with LLM-as-Judge
=================================================
Evaluates the RAG pipeline end-to-end by querying /api/chat for each QA pair,
then scoring the response with a separate LLM judge call on three dimensions:
  1. Answer Correctness (vs reference answer)
  2. Faithfulness (grounded in retrieved context, no hallucination)
  3. Retrieval Relevance (did retrieved chunks contain needed info)

Usage:
    python benchmark.py
    python benchmark.py --api-url http://127.0.0.1:8000
    python benchmark.py --judge-model qwen3:8b
    python benchmark.py --dataset benchmark_dataset.json
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime

import httpx

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
from cortex.config import (
    DEFAULT_API_URL, DEFAULT_DATASET, DEFAULT_OLLAMA_HOST,
    DEFAULT_JUDGE_MODEL, RESULTS_DIR
)

# ---------------------------------------------------------------------------
# Judge prompt
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
# Helpers
# ---------------------------------------------------------------------------
def flush_cache_via_api(api_url: str, timeout: float = 5.0):
    """Flush the RAG cache by calling POST /api/cache/clear on the live API."""
    try:
        resp = httpx.post(f"{api_url}/api/cache/clear", timeout=timeout)
        resp.raise_for_status()
        msg = resp.json().get("message", "OK")
        print(f"[INFO] Cache cleared via API: {msg}")
    except Exception as e:
        print(f"[WARN] Failed to clear cache via API: {e}")

def query_rag(api_url: str, question: str, timeout: float = 60.0) -> dict:
    """Send a question to the RAG /api/chat endpoint and return the full response."""
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
# Report generation
# ---------------------------------------------------------------------------
def generate_html_report(results: dict, output_path: str):
    """Generate an extremely premium, interactive glassmorphic HTML Audit Dashboard."""
    # Base64 or URL Encode the complete results dictionary to inject cleanly into JS
    encoded_data = urllib.parse.quote(json.dumps(results, ensure_ascii=False))
    
    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chimera Cortex — RAG Audit & Benchmarking Suite</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  * {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
  }}
  body {{
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    background-color: #070b13;
    color: #f1f5f9;
    min-height: 100vh;
    padding: 2.5rem;
    overflow-x: hidden;
  }}
  /* Custom Scrollbar */
  ::-webkit-scrollbar {{
    width: 8px;
    height: 8px;
  }}
  ::-webkit-scrollbar-track {{
    background: #070b13;
  }}
  ::-webkit-scrollbar-thumb {{
    background: #1e293b;
    border-radius: 4px;
  }}
  ::-webkit-scrollbar-thumb:hover {{
    background: #334155;
  }}
  header {{
    margin-bottom: 2.5rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    padding-bottom: 1.5rem;
  }}
  .logo-block {{
    display: flex;
    flex-direction: column;
  }}
  .logo {{
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: -0.025em;
    background: linear-gradient(135deg, #38bdf8 0%, #a855f7 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .subtitle {{
    font-size: 0.85rem;
    color: #64748b;
    margin-top: 0.25rem;
  }}
  .run-info {{
    text-align: right;
    font-size: 0.85rem;
    color: #94a3b8;
  }}
  .run-info code {{
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: monospace;
    color: #38bdf8;
  }}
  
  /* KPI Cards grid */
  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 1.25rem;
    margin-bottom: 2rem;
  }}
  .kpi-card {{
    background: rgba(17, 25, 40, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 16px;
    padding: 1.5rem;
    backdrop-filter: blur(12px);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
  }}
  .kpi-card::before {{
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 3px;
    background: transparent;
  }}
  .kpi-card.correctness::before {{ background: #10b981; }}
  .kpi-card.faithfulness::before {{ background: #c084fc; }}
  .kpi-card.retrieval::before {{ background: #ec4899; }}
  .kpi-card.passrate::before {{ background: #38bdf8; }}
  .kpi-card.latency::before {{ background: #f59e0b; }}
  
  .kpi-card:hover {{
    transform: translateY(-4px);
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 0 15px rgba(56, 189, 248, 0.1);
    border-color: rgba(56, 189, 248, 0.25);
  }}
  .kpi-label {{
    font-size: 0.8rem;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
  }}
  .kpi-value {{
    font-size: 2rem;
    font-weight: 700;
    margin-top: 0.5rem;
    display: flex;
    align-items: baseline;
  }}
  .kpi-suffix {{
    font-size: 0.9rem;
    color: #475569;
    margin-left: 0.25rem;
    font-weight: 500;
  }}
  
  /* Global Latency Breakdown */
  .latency-panel {{
    background: rgba(17, 25, 40, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 16px;
    padding: 1.5rem;
    margin-bottom: 2rem;
  }}
  .panel-title {{
    font-size: 0.9rem;
    color: #94a3b8;
    margin-bottom: 1rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    display: flex;
    justify-content: space-between;
  }}
  .latency-bar-container {{
    width: 100%;
    height: 24px;
    background: rgba(255, 255, 255, 0.03);
    border-radius: 8px;
    overflow: hidden;
    display: flex;
    border: 1px solid rgba(255, 255, 255, 0.06);
  }}
  .latency-segment {{
    height: 100%;
    transition: all 0.3s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    font-weight: 700;
    color: #070b13;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    position: relative;
    cursor: pointer;
  }}
  .latency-segment:hover::after {{
    content: attr(data-tooltip);
    position: absolute;
    bottom: 30px;
    background: #0f172a;
    color: #fff;
    padding: 4px 8px;
    border-radius: 4px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    font-size: 0.7rem;
    z-index: 10;
    pointer-events: none;
  }}
  .seg-embed {{ background: #c084fc; }}
  .seg-retrieval {{ background: #38bdf8; }}
  .seg-rerank {{ background: #f472b6; }}
  .seg-gen {{ background: #34d399; }}
  
  .latency-legend {{
    display: flex;
    gap: 1.5rem;
    margin-top: 1rem;
    flex-wrap: wrap;
    font-size: 0.8rem;
    color: #94a3b8;
  }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }}
  .legend-dot {{
    width: 10px;
    height: 10px;
    border-radius: 3px;
  }}
  
  /* Filter bar */
  .control-dock {{
    background: rgba(17, 25, 40, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 16px;
    padding: 1.25rem;
    backdrop-filter: blur(12px);
    margin-bottom: 2rem;
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    align-items: center;
    justify-content: space-between;
  }}
  .search-wrapper {{
    flex: 1;
    min-width: 280px;
    position: relative;
  }}
  .search-input {{
    width: 100%;
    background: rgba(7, 11, 19, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: #f1f5f9;
    padding: 0.65rem 1rem 0.65rem 2.5rem;
    border-radius: 10px;
    font-family: inherit;
    font-size: 0.9rem;
    transition: all 0.2s ease;
  }}
  .search-input:focus {{
    outline: none;
    border-color: #38bdf8;
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.15);
  }}
  .search-icon {{
    position: absolute;
    left: 0.9rem;
    top: 50%;
    transform: translateY(-50%);
    color: #475569;
    pointer-events: none;
  }}
  .filter-groups {{
    display: flex;
    flex-wrap: wrap;
    gap: 1.25rem;
  }}
  .filter-group {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }}
  .filter-label {{
    font-size: 0.8rem;
    color: #64748b;
    font-weight: 600;
    text-transform: uppercase;
  }}
  .pill-container {{
    display: flex;
    background: rgba(7, 11, 19, 0.5);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 2px;
  }}
  .filter-pill {{
    background: transparent;
    border: none;
    color: #94a3b8;
    padding: 6px 12px;
    font-size: 0.8rem;
    font-weight: 600;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: inherit;
  }}
  .filter-pill:hover {{
    color: #f1f5f9;
  }}
  .filter-pill.active {{
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
    box-shadow: inset 0 0 8px rgba(56, 189, 248, 0.1);
  }}
  
  /* Sort wrapper */
  .sort-select {{
    background: rgba(7, 11, 19, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: #f1f5f9;
    padding: 0.5rem 1.5rem 0.5rem 0.75rem;
    border-radius: 10px;
    font-size: 0.8rem;
    font-family: inherit;
    cursor: pointer;
    outline: none;
    -webkit-appearance: none;
  }}
  
  /* Questions container */
  .questions-list {{
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }}
  .qa-card {{
    background: rgba(17, 25, 40, 0.5);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 16px;
    backdrop-filter: blur(12px);
    overflow: hidden;
    transition: border-color 0.3s ease, box-shadow 0.3s ease;
  }}
  .qa-card:hover {{
    border-color: rgba(255, 255, 255, 0.12);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
  }}
  .qa-card.open {{
    border-color: rgba(56, 189, 248, 0.2);
  }}
  
  .qa-header {{
    padding: 1.25rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    user-select: none;
  }}
  .header-left {{
    display: flex;
    align-items: center;
    gap: 1rem;
    flex: 1;
    min-width: 0;
  }}
  .id-badge {{
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    color: #e2e8f0;
    font-weight: 700;
    font-size: 0.8rem;
    padding: 4px 10px;
    border-radius: 8px;
    font-family: monospace;
    flex-shrink: 0;
  }}
  .question-summary {{
    font-size: 0.95rem;
    font-weight: 600;
    color: #f1f5f9;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }}
  .header-right {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-shrink: 0;
  }}
  .badge {{
    padding: 4px 8px;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 700;
  }}
  .badge.diff-simple {{ background: rgba(16, 185, 129, 0.1); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.2); }}
  .badge.diff-cross {{ background: rgba(168, 85, 247, 0.1); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.2); }}
  .badge.cache-hit {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.2); }}
  .badge.cache-miss {{ background: rgba(100, 116, 139, 0.15); color: #94a3b8; border: 1px solid rgba(100, 116, 139, 0.15); }}
  
  .score-badge-group {{
    display: flex;
    gap: 2px;
    background: rgba(7, 11, 19, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    padding: 2px;
  }}
  .score-badge {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 6px;
    font-weight: 700;
    font-size: 0.8rem;
  }}
  .score-high {{ background: rgba(16, 185, 129, 0.2); color: #34d399; }}
  .score-mid {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; }}
  .score-low {{ background: rgba(239, 68, 68, 0.2); color: #f87171; }}
  
  .header-arrow {{
    color: #64748b;
    transition: transform 0.3s ease;
  }}
  .qa-card.open .header-arrow {{
    transform: rotate(180deg);
  }}
  
  /* Details expanded drawer */
  .qa-details {{
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    background: rgba(7, 11, 19, 0.25);
    padding: 1.5rem;
    display: none;
  }}
  .qa-card.open .qa-details {{
    display: block;
  }}
  
  .timing-chips {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin-bottom: 1.25rem;
  }}
  .timing-chip {{
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.06);
    padding: 4px 10px;
    border-radius: 8px;
    font-size: 0.75rem;
    font-weight: 600;
    color: #cbd5e1;
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }}
  .timing-chip.total {{
    background: rgba(245, 158, 11, 0.1);
    color: #f59e0b;
    border-color: rgba(245, 158, 11, 0.2);
  }}
  
  /* Answers Grid */
  .answers-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 1.25rem;
    margin-bottom: 1.5rem;
  }}
  .answer-panel {{
    border-radius: 12px;
    padding: 1.25rem;
    position: relative;
  }}
  .answer-panel.reference {{
    background: rgba(16, 185, 129, 0.03);
    border: 1px solid rgba(16, 185, 129, 0.1);
    border-left: 4px solid #10b981;
  }}
  .answer-panel.rag {{
    background: rgba(56, 189, 248, 0.03);
    border: 1px solid rgba(56, 189, 248, 0.1);
    border-left: 4px solid #38bdf8;
  }}
  .panel-hdr {{
    font-size: 0.8rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.75rem;
    display: flex;
    justify-content: space-between;
  }}
  .answer-panel.reference .panel-hdr {{ color: #10b981; }}
  .answer-panel.rag .panel-hdr {{ color: #38bdf8; }}
  .answer-body {{
    font-size: 0.9rem;
    line-height: 1.6;
    color: #cbd5e1;
    white-space: pre-wrap;
  }}
  
  /* Timings bar inside question details */
  .q-latency-container {{
    margin-bottom: 1.5rem;
  }}
  
  /* Rerank rankings shift table */
  .rankings-panel {{
    background: rgba(17, 25, 40, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 12px;
    padding: 1.25rem;
    margin-bottom: 1.5rem;
  }}
  .rankings-table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 0.75rem;
    font-size: 0.85rem;
  }}
  .rankings-table th {{
    background: rgba(255, 255, 255, 0.03);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    padding: 0.5rem 0.75rem;
    text-align: left;
    color: #94a3b8;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
  }}
  .rankings-table td {{
    padding: 0.65rem 0.75rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    vertical-align: middle;
  }}
  .rankings-table tr:hover {{
    background: rgba(255, 255, 255, 0.02);
  }}
  .shift-badge {{
    display: inline-flex;
    align-items: center;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 700;
    font-size: 0.75rem;
  }}
  .shift-up {{ background: rgba(16, 185, 129, 0.15); color: #34d399; }}
  .shift-down {{ background: rgba(239, 68, 68, 0.15); color: #f87171; }}
  .shift-none {{ background: rgba(255, 255, 255, 0.05); color: #94a3b8; }}
  
  .top-slice-badge {{
    background: linear-gradient(135deg, #0284c7 0%, #7c3aed 100%);
    color: #fff;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    box-shadow: 0 0 10px rgba(124, 58, 237, 0.4);
  }}
  .view-chunk-btn {{
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: #e2e8f0;
    padding: 4px 8px;
    border-radius: 6px;
    cursor: pointer;
    font-family: inherit;
    font-size: 0.75rem;
    transition: all 0.2s ease;
  }}
  .view-chunk-btn:hover {{
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
    border-color: rgba(56, 189, 248, 0.3);
  }}
  
  .chunk-drawer {{
    background: rgba(7, 11, 19, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 1rem;
    margin-top: 0.5rem;
    font-size: 0.85rem;
    line-height: 1.5;
    color: #cbd5e1;
    white-space: pre-wrap;
    display: none;
  }}
  
  /* Collapsible prompt trace section */
  .collapsible-section {{
    margin-bottom: 1rem;
  }}
  .sec-hdr {{
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.05);
    padding: 0.75rem 1rem;
    border-radius: 10px;
    font-size: 0.85rem;
    font-weight: 600;
    color: #94a3b8;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    user-select: none;
    transition: all 0.2s ease;
  }}
  .sec-hdr:hover {{
    background: rgba(255, 255, 255, 0.06);
    color: #f1f5f9;
  }}
  .sec-body {{
    display: none;
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-top: none;
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
    background: rgba(7, 11, 19, 0.6);
    padding: 1.25rem;
  }}
  .sec-body.open {{
    display: block;
  }}
  
  .prompt-pre {{
    font-family: monospace;
    font-size: 0.8rem;
    color: #38bdf8;
    white-space: pre-wrap;
    line-height: 1.5;
  }}
  
  .rationale-text {{
    font-size: 0.85rem;
    color: #94a3b8;
    line-height: 1.5;
    background: rgba(245, 158, 11, 0.03);
    border-left: 3px solid #f59e0b;
    padding: 0.75rem 1rem;
    border-radius: 8px;
  }}
  
  .footer {{
    text-align: center;
    color: #475569;
    font-size: 0.8rem;
    margin-top: 4rem;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    padding-top: 1.5rem;
  }}
</style>
</head>
<body>
  <header>
    <div class="logo-block">
      <h1 class="logo">Chimera Cortex Audit Suite</h1>
      <div class="subtitle">System Auditing & RAG Telemetry Dashboard</div>
    </div>
    <div class="run-info">
      Run ID: <code>{results['timestamp']}</code> &nbsp;|&nbsp;
      Judge Model: <code>{results['config']['judge_model']}</code><br>
      Total Latency: <code>{results.get('duration_seconds', 0):.1f}s</code> &nbsp;|&nbsp;
      RAG API: <code>{results['config']['api_url']}</code>
    </div>
  </header>

  <!-- Metric KPI Cards -->
  <div class="metrics-grid" id="kpis-container">
    <!-- Rendered dynamically by JS -->
  </div>

  <!-- Global Latency panel -->
  <div class="latency-panel">
    <div class="panel-title">
      <span>AVERAGE TELEMETRY LATENCY BREAKDOWN</span>
      <span id="avg-total-latency-text">0.0 ms</span>
    </div>
    <div class="latency-bar-container" id="avg-latency-bar">
      <!-- Rendered dynamically by JS -->
    </div>
    <div class="latency-legend">
      <div class="legend-item"><div class="legend-dot seg-embed"></div>Embedding (Ollama)</div>
      <div class="legend-item"><div class="legend-dot seg-retrieval"></div>Dense Vector Search (Infinity)</div>
      <div class="legend-item"><div class="legend-dot seg-rerank"></div>Cross-Encoder Reranker (Llama)</div>
      <div class="legend-item"><div class="legend-dot seg-gen"></div>LLM Text Synthesis (Ollama)</div>
    </div>
  </div>

  <!-- Control panel -->
  <div class="control-dock">
    <div class="search-wrapper">
      <span class="search-icon">🔍</span>
      <input type="text" class="search-input" id="search-box" placeholder="Search questions, reference answers or synthesized outputs...">
    </div>
    <div class="filter-groups">
      <div class="filter-group">
        <span class="filter-label">Difficulty</span>
        <div class="pill-container" id="filter-difficulty">
          <button class="filter-pill active" onclick="setFilter('difficulty', 'all')">All</button>
          <button class="filter-pill" onclick="setFilter('difficulty', 'simple')">Simple</button>
          <button class="filter-pill" onclick="setFilter('difficulty', 'cross-chunk')">Cross-Chunk</button>
        </div>
      </div>
      <div class="filter-group">
        <span class="filter-label">Cache</span>
        <div class="pill-container" id="filter-cache">
          <button class="filter-pill active" onclick="setFilter('cache', 'all')">All</button>
          <button class="filter-pill" onclick="setFilter('cache', 'hit')">Hits</button>
          <button class="filter-pill" onclick="setFilter('cache', 'miss')">Misses</button>
        </div>
      </div>
      <div class="filter-group">
        <span class="filter-label">Fidelity</span>
        <div class="pill-container" id="filter-fidelity">
          <button class="filter-pill active" onclick="setFilter('fidelity', 'all')">All</button>
          <button class="filter-pill" onclick="setFilter('fidelity', 'pass')">Pass (≥4)</button>
          <button class="filter-pill" onclick="setFilter('fidelity', 'fail')">Low (<4)</button>
        </div>
      </div>
      <div class="filter-group">
        <span class="filter-label">Sort</span>
        <select class="sort-select" id="sort-select" onchange="handleSortChange()">
          <option value="id">QID Order</option>
          <option value="slowest">Slowest Latency</option>
          <option value="fastest">Fastest Latency</option>
          <option value="correctness">Lowest Correctness</option>
        </select>
      </div>
    </div>
  </div>

  <!-- Accordions List -->
  <div class="questions-list" id="questions-container">
    <!-- Rendered dynamically by JS -->
  </div>

  <div class="footer">
    Chimera Cortex RAG Audit Engine · Developed by DeepMind Team
  </div>

  <script>
    // Safe injection of URL-encoded JSON payload
    const rawPayload = "{encoded_data}";
    const payload = JSON.parse(decodeURIComponent(rawPayload));
    
    // Global filter states
    const filterStates = {{
      difficulty: 'all',
      cache: 'all',
      fidelity: 'all',
      query: ''
    }};
    
    window.onload = function() {{
      computeAndRenderOverallStats();
      renderQuestions();
    }};
    
    function computeAndRenderOverallStats() {{
      const items = payload.results;
      const total = items.length;
      
      let sumCorrect = 0, sumFaith = 0, sumRetrieve = 0, countPass = 0, sumTotalMs = 0;
      let countHit = 0;
      
      let sumEmbed = 0, sumRetr = 0, sumRerank = 0, sumGen = 0;
      let timedCount = 0;
      
      items.forEach(item => {{
        sumCorrect += item.scores.answer_correctness;
        sumFaith += item.scores.faithfulness;
        sumRetrieve += item.scores.retrieval_relevance;
        
        if (item.scores.answer_correctness >= 4) countPass++;
        if (item.cache_hit) countHit++;
        
        const audit = item.audit || {{}};
        const timings = audit.timings_ms || {{}};
        if (timings.total) {{
          sumTotalMs += timings.total;
          sumEmbed += timings.embedding || 0;
          sumRetr += timings.retrieval || 0;
          sumRerank += timings.rerank || 0;
          sumGen += timings.generation || 0;
          timedCount++;
        }}
      }});
      
      const avgCorrect = sumCorrect / total;
      const avgFaith = sumFaith / total;
      const avgRetrieve = sumRetrieve / total;
      const passRate = (countPass / total) * 100;
      const cacheRate = (countHit / total) * 100;
      const avgLatencyMs = timedCount > 0 ? (sumTotalMs / timedCount) : 0;
      
      // Render KPI widgets
      const kpisHtml = 
        '<div class="kpi-card correctness">' +
          '<div class="kpi-label">Avg Correctness</div>' +
          '<div class="kpi-value" style="color:' + getScoreColor(avgCorrect) + '">' + avgCorrect.toFixed(2) + '<span class="kpi-suffix">/ 5</span></div>' +
        '</div>' +
        '<div class="kpi-card faithfulness">' +
          '<div class="kpi-label">Avg Faithfulness</div>' +
          '<div class="kpi-value" style="color:' + getScoreColor(avgFaith) + '">' + avgFaith.toFixed(2) + '<span class="kpi-suffix">/ 5</span></div>' +
        '</div>' +
        '<div class="kpi-card retrieval">' +
          '<div class="kpi-label">Avg Retrieval</div>' +
          '<div class="kpi-value" style="color:' + getScoreColor(avgRetrieve) + '">' + avgRetrieve.toFixed(2) + '<span class="kpi-suffix">/ 5</span></div>' +
        '</div>' +
        '<div class="kpi-card passrate">' +
          '<div class="kpi-label">Fidelity Pass Rate</div>' +
          '<div class="kpi-value" style="color:' + (passRate >= 80 ? '#10b981' : passRate >= 50 ? '#f59e0b' : '#ef4444') + '">' + passRate.toFixed(1) + '<span class="kpi-suffix">%</span></div>' +
        '</div>' +
        '<div class="kpi-card latency">' +
          '<div class="kpi-label">Avg Total Latency</div>' +
          '<div class="kpi-value" style="color:#f59e0b">' + avgLatencyMs.toFixed(1) + '<span class="kpi-suffix">ms</span></div>' +
        '</div>';
        
      document.getElementById('kpis-container').innerHTML = kpisHtml;
      
      // Render Global Latency Breakdown bar
      document.getElementById('avg-total-latency-text').innerText = avgLatencyMs.toFixed(1) + ' ms';
      
      if (timedCount > 0) {{
        const embedPct = (sumEmbed / sumTotalMs) * 100;
        const retrPct = (sumRetr / sumTotalMs) * 100;
        const rerankPct = (sumRerank / sumTotalMs) * 100;
        const genPct = (sumGen / sumTotalMs) * 100;
        
        const latencyBarHtml = 
          '<div class="latency-segment seg-embed" style="width:' + embedPct + '%" data-tooltip="Avg Embedding: ' + (sumEmbed / timedCount).toFixed(1) + 'ms (' + embedPct.toFixed(1) + '%)">Embedding</div>' +
          '<div class="latency-segment seg-retrieval" style="width:' + retrPct + '%" data-tooltip="Avg Vector Retrieval: ' + (sumRetr / timedCount).toFixed(1) + 'ms (' + retrPct.toFixed(1) + '%)">Retrieval</div>' +
          '<div class="latency-segment seg-rerank" style="width:' + rerankPct + '%" data-tooltip="Avg Rerank: ' + (sumRerank / timedCount).toFixed(1) + 'ms (' + rerankPct.toFixed(1) + '%)">Rerank</div>' +
          '<div class="latency-segment seg-gen" style="width:' + genPct + '%" data-tooltip="Avg LLM Synthesis: ' + (sumGen / timedCount).toFixed(1) + 'ms (' + genPct.toFixed(1) + '%)">Synthesis</div>';
          
        document.getElementById('avg-latency-bar').innerHTML = latencyBarHtml;
      }}
    }}
    
    function getScoreColor(val) {{
      if (val >= 4) return '#34d399';
      if (val >= 3) return '#fbbf24';
      return '#f87171';
    }}
    
    function getScoreClass(val) {{
      if (val >= 4) return 'score-high';
      if (val >= 3) return 'score-mid';
      return 'score-low';
    }}
    
    // Setup search listener
    document.getElementById('search-box').addEventListener('input', function(e) {{
      filterStates.query = e.target.value.toLowerCase().strip();
      renderQuestions();
    }});
    
    function setFilter(type, value) {{
      // Update filter UI pills
      const parent = document.getElementById('filter-' + type);
      const pills = parent.getElementsByClassName('filter-pill');
      for (let i = 0; i < pills.length; i++) {{
        pills[i].classList.remove('active');
        if (pills[i].innerText.toLowerCase() === value || (value === 'all' && pills[i].innerText === 'All')) {{
          pills[i].classList.add('active');
        }}
      }}
      
      filterStates[type] = value;
      renderQuestions();
    }}
    
    function handleSortChange() {{
      renderQuestions();
    }}
    
    function renderQuestions() {{
      const container = document.getElementById('questions-container');
      container.innerHTML = '';
      
      let items = [...payload.results];
      
      // Apply filters
      items = items.filter(item => {{
        // Difficulty filter
        if (filterStates.difficulty !== 'all' && item.difficulty !== filterStates.difficulty) return false;
        
        // Cache filter
        if (filterStates.cache === 'hit' && !item.cache_hit) return false;
        if (filterStates.cache === 'miss' && item.cache_hit) return false;
        
        // Fidelity filter
        if (filterStates.fidelity === 'pass' && item.scores.answer_correctness < 4) return false;
        if (filterStates.fidelity === 'fail' && item.scores.answer_correctness >= 4) return false;
        
        // Text search query filter
        if (filterStates.query) {{
          const questionText = item.question.toLowerCase();
          const refText = item.reference_answer.toLowerCase();
          const ragText = item.rag_answer.toLowerCase();
          const rationaleText = (item.scores.rationale || '').toLowerCase();
          if (!questionText.includes(filterStates.query) && 
              !refText.includes(filterStates.query) && 
              !ragText.includes(filterStates.query) &&
              !rationaleText.includes(filterStates.query)) return false;
        }}
        
        return true;
      }});
      
      // Apply sorts
      const sortVal = document.getElementById('sort-select').value;
      if (sortVal === 'slowest') {{
        items.sort((a, b) => {{
          const aMs = a.audit && a.audit.timings_ms ? a.audit.timings_ms.total : 0;
          const bMs = b.audit && b.audit.timings_ms ? b.audit.timings_ms.total : 0;
          return bMs - aMs;
        }});
      }} else if (sortVal === 'fastest') {{
        items.sort((a, b) => {{
          const aMs = a.audit && a.audit.timings_ms ? a.audit.timings_ms.total : 999999;
          const bMs = b.audit && b.audit.timings_ms ? b.audit.timings_ms.total : 999999;
          return aMs - bMs;
        }});
      }} else if (sortVal === 'correctness') {{
        items.sort((a, b) => a.scores.answer_correctness - b.scores.answer_correctness);
      }}
      
      if (items.length === 0) {{
        container.innerHTML = '<div style="text-align:center;color:#64748b;padding:3rem;">No audited results matches your active search / filters.</div>';
        return;
      }}
      
      items.forEach(item => {{
        const card = document.createElement('div');
        card.className = 'qa-card';
        card.id = 'card-' + item.id;
        
        const sc = item.scores;
        const audit = item.audit || {{}};
        const timings = audit.timings_ms || {{}};
        
        // Setup headers and badges
        const cacheBadge = item.cache_hit ? '<span class="badge cache-hit">CACHE HIT</span>' : '<span class="badge cache-miss">COLD RUN</span>';
        const diffBadge = item.difficulty === 'cross-chunk' ? '<span class="badge diff-cross">CROSS-CHUNK</span>' : '<span class="badge diff-simple">SIMPLE</span>';
        
        const correctClass = getScoreClass(sc.answer_correctness);
        const faithClass = getScoreClass(sc.faithfulness);
        const retrieveClass = getScoreClass(sc.retrieval_relevance);
        
        const totalLatencyText = timings.total ? timings.total.toFixed(0) + ' ms' : 'N/A';
        
        // Construct detailed row inside accordion
        card.innerHTML = 
          '<div class="qa-header" onclick="toggleAccordion(\'' + item.id + '\')">' +
            '<div class="header-left">' +
              '<span class="id-badge">' + item.id + '</span>' +
              '<span class="question-summary">' + item.question + '</span>' +
            '</div>' +
            '<div class="header-right">' +
              diffBadge +
              cacheBadge +
              '<span class="badge" style="background:rgba(245,158,11,0.1);color:#f59e0b;border:1px solid rgba(245,158,11,0.2);">' + totalLatencyText + '</span>' +
              '<div class="score-badge-group">' +
                '<div class="score-badge ' + correctClass + '" title="Answer Correctness: ' + sc.answer_correctness + '">C:' + sc.answer_correctness + '</div>' +
                '<div class="score-badge ' + faithClass + '" title="Faithfulness: ' + sc.faithfulness + '">F:' + sc.faithfulness + '</div>' +
                '<div class="score-badge ' + retrieveClass + '" title="Retrieval Relevance: ' + sc.retrieval_relevance + '">R:' + sc.retrieval_relevance + '</div>' +
              '</div>' +
              '<span class="header-arrow">▼</span>' +
            '</div>' +
          '</div>' +
          '<div class="qa-details" id="details-' + item.id + '">' +
            '<!-- Timings chips -->' +
            '<div class="timing-chips">' +
              '<div class="timing-chip"><b>Embedding:</b> ' + (timings.embedding || 0).toFixed(1) + ' ms</div>' +
              '<div class="timing-chip"><b>Retrieval:</b> ' + (timings.retrieval || 0).toFixed(1) + ' ms</div>' +
              '<div class="timing-chip"><b>Rerank:</b> ' + (timings.rerank || 0).toFixed(1) + ' ms</div>' +
              '<div class="timing-chip"><b>Synthesis:</b> ' + (timings.generation || 0).toFixed(1) + ' ms</div>' +
              '<div class="timing-chip total"><b>Total Pipeline:</b> ' + (timings.total || 0).toFixed(1) + ' ms</div>' +
            '</div>' +
            
            '<!-- Latency mini-bar -->' +
            (timings.total ? 
              '<div class="q-latency-container">' +
                '<div class="latency-bar-container" style="height:12px;border-radius:4px;">' +
                  '<div class="latency-segment seg-embed" style="width:' + ((timings.embedding||0)/timings.total*100) + '%" title="Embedding: ' + (timings.embedding||0).toFixed(1) + 'ms"></div>' +
                  '<div class="latency-segment seg-retrieval" style="width:' + ((timings.retrieval||0)/timings.total*100) + '%" title="Retrieval: ' + (timings.retrieval||0).toFixed(1) + 'ms"></div>' +
                  '<div class="latency-segment seg-rerank" style="width:' + ((timings.rerank||0)/timings.total*100) + '%" title="Rerank: ' + (timings.rerank||0).toFixed(1) + 'ms"></div>' +
                  '<div class="latency-segment seg-gen" style="width:' + ((timings.generation||0)/timings.total*100) + '%" title="Synthesis: ' + (timings.generation||0).toFixed(1) + 'ms"></div>' +
                '</div>' +
              '</div>' : '') +
            
            '<!-- Answers Grid -->' +
            '<div class="answers-grid">' +
              '<div class="answer-panel reference">' +
                '<div class="panel-hdr">Ground-Truth Reference Answer</div>' +
                '<div class="answer-body">' + item.reference_answer + '</div>' +
              '</div>' +
              '<div class="answer-panel rag">' +
                '<div class="panel-hdr">' +
                  '<span>Chimera Cortex Generated Answer</span>' +
                '</div>' +
                '<div class="answer-body">' + item.rag_answer + '</div>' +
              '</div>' +
            '</div>' +
            
            '<!-- Rationale -->' +
            '<div style="margin-bottom:1.5rem;">' +
              '<div class="kpi-label" style="font-size:0.75rem;margin-bottom:0.5rem;">LLM Judge Evaluation Rationale</div>' +
              '<div class="rationale-text">' + (sc.rationale || 'No explanation provided.') + '</div>' +
            '</div>' +
            
            '<!-- Dual Stage comparative table -->' +
            '<div class="rankings-panel">' +
              '<div class="panel-hdr" style="color:#f1f5f9;margin-bottom:0.25rem;">Candidate Rankings & Reranker Rank Shifts</div>' +
              '<table class="rankings-table">' +
                '<thead>' +
                  '<tr>' +
                    '<th>Candidate Chunk Source</th>' +
                    '<th>1st-Stage Vector Rank (Similarity)</th>' +
                    '<th>2nd-Stage Rerank Rank (Logit / Sigmoid)</th>' +
                    '<th>Rank Shift</th>' +
                    '<th style="text-align:right;">Text Inspection</th>' +
                  '</tr>' +
                '</thead>' +
                '<tbody>' +
                  buildRankingsTable(audit.first_stage_candidates, audit.second_stage_candidates, item.id) +
                '</tbody>' +
              '</table>' +
            '</div>' +
            
            '<!-- Audited Prompt Trace Drawer -->' +
            '<div class="collapsible-section">' +
              '<div class="sec-hdr" onclick="togglePromptTrace(\'' + item.id + '\')">' +
                '<span>RAW GENERATIVE PROMPT LOGS INGESTED BY OLLAMA</span>' +
                '<span id="prompt-arrow-' + item.id + '">▶</span>' +
              '</div>' +
              '<div class="sec-body" id="prompt-body-' + item.id + '">' +
                '<pre class="prompt-pre">' + escapeHtml(audit.llm_prompt || 'N/A') + '</pre>' +
              '</div>' +
            '</div>' +
          '</div>';
          
        container.appendChild(card);
      }});
    }}
    
    function toggleAccordion(id) {{
      const card = document.getElementById('card-' + id);
      const details = document.getElementById('details-' + id);
      
      if (card.classList.contains('open')) {{
        card.classList.remove('open');
        details.style.display = 'none';
      }} else {{
        card.classList.add('open');
        details.style.display = 'block';
      }}
    }}
    
    function togglePromptTrace(id) {{
      const body = document.getElementById('prompt-body-' + id);
      const arrow = document.getElementById('prompt-arrow-' + id);
      if (body.classList.contains('open')) {{
        body.classList.remove('open');
        arrow.innerText = '▶';
      }} else {{
        body.classList.add('open');
        arrow.innerText = '▼';
      }}
    }}
    
    function toggleChunkText(qid, index) {{
      const drawer = document.getElementById('chunk-text-' + qid + '-' + index);
      const btn = document.getElementById('btn-chunk-' + qid + '-' + index);
      if (drawer.style.display === 'block') {{
        drawer.style.display = 'none';
        btn.innerText = 'View Text';
      }} else {{
        drawer.style.display = 'block';
        btn.innerText = 'Close Text';
      }}
    }}
    
    function buildRankingsTable(firstStage, secondStage, qid) {{
      if (!firstStage || firstStage.length === 0) {{
        return '<tr><td colspan="5" style="text-align:center;color:#64748b;padding:1rem;">RAG Backend did not output dual-stage candidates for this run.</td></tr>';
      }}
      
      let rows = '';
      
      // Let's iterate through the 10 reranked candidates (secondStage)
      secondStage.forEach((candidate, idx) => {{
        const fn = candidate.filename;
        const chunkIdx = candidate.chunk_index;
        const r1 = candidate.first_stage_rank;
        const r2 = candidate.rank;
        const sim = candidate.first_stage_score;
        const logit = candidate.rerank_logit;
        const sigmoid = candidate.rerank_score;
        
        // Rank shift calculation
        const shiftVal = r1 - r2;
        let shiftBadge = '';
        if (shiftVal > 0) {{
          shiftBadge = '<span class="shift-badge shift-up">▲ +' + shiftVal + '</span>';
        }} else if (shiftVal < 0) {{
          shiftBadge = '<span class="shift-badge shift-down">▼ ' + shiftVal + '</span>';
        }} else {{
          shiftBadge = '<span class="shift-badge shift-none">•</span>';
        }}
        
        // Highlight sliced candidate chunks (Top 3)
        const sliceBadge = r2 <= 3 ? ' <span class="top-slice-badge">PROMPT INJECT</span>' : '';
        
        rows += 
          '<tr>' +
            '<td><b>' + fn + '</b> (Chunk ' + chunkIdx + ')' + sliceBadge + '</td>' +
            '<td>Rank ' + r1 + ' <span style="color:#64748b">(' + sim.toFixed(4) + ')</span></td>' +
            '<td>Rank ' + r2 + ' <span style="color:#64748b">(logit: ' + (logit !== null ? logit.toFixed(2) : 'N/A') + ' | sigmoid: ' + (sigmoid !== null ? sigmoid.toFixed(4) : 'N/A') + ')</span></td>' +
            '<td>' + shiftBadge + '</td>' +
            '<td style="text-align:right;">' +
              '<button class="view-chunk-btn" id="btn-chunk-' + qid + '-' + idx + '" onclick="toggleChunkText(\'' + qid + '\', ' + idx + ')">View Text</button>' +
            '</td>' +
          '</tr>' +
          '<tr>' +
            '<td colspan="5" style="padding:0;border:none;">' +
              '<div class="chunk-drawer" id="chunk-text-' + qid + '-' + idx + '">' + escapeHtml(candidate.content) + '</div>' +
            '</td>' +
          '</tr>';
      }});
      
      return rows;
    }}
    
    function escapeHtml(text) {{
      if (!text) return '';
      return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }}
  </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def compute_aggregates(items: list) -> dict:
    """Compute aggregate metrics from per-question results."""
    total = len(items)
    if total == 0:
        return {}
    correctness_scores = [r["scores"]["answer_correctness"] for r in items]
    faithfulness_scores = [r["scores"]["faithfulness"] for r in items]
    retrieval_scores = [r["scores"]["retrieval_relevance"] for r in items]
    pass_count = sum(1 for s in correctness_scores if s >= 4)
    
    # Breakdown by difficulty
    by_difficulty = {}
    for r in items:
        diff = r.get("difficulty", "unknown")
        if diff not in by_difficulty:
            by_difficulty[diff] = {"correctness": [], "faithfulness": [], "retrieval": []}
        by_difficulty[diff]["correctness"].append(r["scores"]["answer_correctness"])
        by_difficulty[diff]["faithfulness"].append(r["scores"]["faithfulness"])
        by_difficulty[diff]["retrieval"].append(r["scores"]["retrieval_relevance"])
        
    breakdown = {}
    for diff, data in by_difficulty.items():
        n = len(data["correctness"])
        breakdown[diff] = {
            "count": n,
            "avg_correctness": sum(data["correctness"]) / n,
            "avg_faithfulness": sum(data["faithfulness"]) / n,
            "avg_retrieval": sum(data["retrieval"]) / n,
            "pass_rate": sum(1 for s in data["correctness"] if s >= 4) / n * 100,
        }
        
    return {
        "total_questions": total,
        "avg_correctness": sum(correctness_scores) / total,
        "avg_faithfulness": sum(faithfulness_scores) / total,
        "avg_retrieval": sum(retrieval_scores) / total,
        "pass_rate": pass_count / total * 100,
        "by_difficulty": breakdown,
    }

def run_benchmark(args):
    """Main benchmark execution loop."""
    print("=" * 60)
    print("  Chimera Cortex — RAG Benchmark (LLM-as-Judge)")
    print("=" * 60)
    
    # Load dataset
    print(f"\n[1/5] Loading dataset: {args.dataset}")
    with open(args.dataset, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    print(f"       Loaded {len(dataset)} questions.")
    
    # Flush Redis cache via API (skipped if --reuse-cache)
    if args.reuse_cache:
        print(f"\n[2/5] Reusing existing cache (--reuse-cache set, skipping flush).")
    else:
        print(f"\n[2/5] Flushing cache via POST /api/cache/clear ...")
        flush_cache_via_api(args.api_url)
        
    # Check RAG API health
    print(f"\n[3/5] Checking RAG API at {args.api_url} ...")
    try:
        status_resp = httpx.get(f"{args.api_url}/api/status", timeout=5.0)
        status_resp.raise_for_status()
        status = status_resp.json()
        print(f"       Services: {json.dumps(status, indent=None)}")
    except Exception as e:
        print(f"[ERROR] Cannot reach RAG API: {e}")
        sys.exit(1)
        
    # Run each question
    print(f"\n[4/5] Running benchmark ({len(dataset)} questions)...\n")
    results = []
    start_time = time.time()
    
    for i, qa in enumerate(dataset, 1):
        qid = qa["id"]
        question = qa["question"]
        ref_answer = qa["reference_answer"]
        difficulty = qa.get("difficulty", "unknown")
        print(f"  [{i:2d}/{len(dataset)}] {qid}: {question[:70]}...")
        
        # Query RAG
        try:
            rag_resp = query_rag(args.api_url, question, timeout=args.timeout)
            rag_answer = rag_resp.get("answer", "")
            contexts = rag_resp.get("contexts", [])
            cache_hit = rag_resp.get("cache_hit", False)
            audit = rag_resp.get("audit")
        except Exception as e:
            print(f"         [ERROR] RAG query failed: {e}")
            rag_answer = f"[ERROR] {e}"
            contexts = []
            cache_hit = False
            audit = None
            
        if audit is None:
            audit = {
                "timings_ms": {
                    "embedding": 0.0,
                    "retrieval": 0.0,
                    "rerank": 0.0,
                    "generation": 0.0,
                    "total": 0.0
                },
                "first_stage_candidates": [],
                "second_stage_candidates": [],
                "llm_prompt": "N/A"
            }
            
        # Judge
        try:
            scores = call_judge(
                ollama_host=args.ollama_host,
                judge_model=args.judge_model,
                question=question,
                reference_answer=ref_answer,
                rag_answer=rag_answer,
                contexts=contexts,
                timeout=args.timeout,
            )
        except Exception as e:
            print(f"         [ERROR] Judge call failed: {e}")
            scores = {
                "answer_correctness": 1,
                "faithfulness": 1,
                "retrieval_relevance": 1,
                "rationale": f"Judge call error: {e}",
                "raw_judge_output": "",
            }
            
        c = scores["answer_correctness"]
        f_score = scores["faithfulness"]
        r = scores["retrieval_relevance"]
        print(f"         Scores → C:{c} F:{f_score} R:{r}  {'(cached)' if cache_hit else ''}")
        
        results.append({
            "id": qid,
            "question": question,
            "difficulty": difficulty,
            "reference_answer": ref_answer,
            "rag_answer": rag_answer,
            "retrieved_contexts": [
                {
                    "filename": ctx.get("filename", ""),
                    "content": ctx.get("content", ""),
                    "document_id": ctx.get("document_id"),
                    "chunk_index": ctx.get("chunk_index"),
                    "distance": ctx.get("distance"),
                    "rerank_logit": ctx.get("rerank_logit"),
                    "rerank_score": ctx.get("rerank_score")
                }
                for ctx in contexts
            ],
            "cache_hit": cache_hit,
            "scores": scores,
            "audit": audit,
        })
        
        # Small delay between questions to avoid overwhelming Ollama
        if i < len(dataset):
            time.sleep(args.delay)
            
    elapsed = time.time() - start_time
    
    # Compute aggregates
    print(f"\n[5/5] Computing aggregates and generating reports...")
    agg = compute_aggregates(results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    output = {
        "timestamp": timestamp,
        "config": {
            "api_url": args.api_url,
            "judge_model": args.judge_model,
            "dataset": args.dataset,
            "ollama_host": args.ollama_host,
        },
        "duration_seconds": elapsed,
        "aggregate": agg,
        "results": results,
    }
    
    # Write outputs
    os.makedirs(RESULTS_DIR, exist_ok=True)
    json_path = os.path.join(RESULTS_DIR, f"results_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"       JSON results: {json_path}")
    
    html_path = os.path.join(RESULTS_DIR, f"report_{timestamp}.html")
    generate_html_report(output, html_path)
    print(f"       HTML report:  {html_path}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("  BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"  Total Questions:    {agg['total_questions']}")
    print(f"  Duration:           {elapsed:.1f}s")
    print(f"  Avg Correctness:    {agg['avg_correctness']:.2f} / 5")
    print(f"  Avg Faithfulness:   {agg['avg_faithfulness']:.2f} / 5")
    print(f"  Avg Retrieval:      {agg['avg_retrieval']:.2f} / 5")
    print(f"  Pass Rate (≥4):     {agg['pass_rate']:.1f}%")
    print()
    for diff, data in agg.get("by_difficulty", {}).items():
        print(f"  [{diff}] n={data['count']}  C={data['avg_correctness']:.2f}  "
              f"F={data['avg_faithfulness']:.2f}  R={data['avg_retrieval']:.2f}  "
              f"Pass={data['pass_rate']:.1f}%")
    print("=" * 60)

def main():
    parser = argparse.ArgumentParser(
        description="Chimera Cortex RAG Benchmark — LLM-as-Judge Evaluator"
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"Base URL of the RAG API (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"Path to benchmark dataset JSON (default: benchmark_dataset.json)",
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"Ollama model to use as judge (default: {DEFAULT_JUDGE_MODEL})",
    )
    parser.add_argument(
        "--ollama-host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama host:port for judge calls (default: {DEFAULT_OLLAMA_HOST})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="HTTP timeout in seconds for RAG and judge calls (default: 90)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between questions (default: 1.0)",
    )
    parser.add_argument(
        "--reuse-cache",
        action="store_true",
        default=False,
        help="Skip cache flush and reuse existing Redis cache (useful when comparing judge models on identical RAG outputs)",
    )
    
    args = parser.parse_args()
    run_benchmark(args)

if __name__ == "__main__":
    main()
