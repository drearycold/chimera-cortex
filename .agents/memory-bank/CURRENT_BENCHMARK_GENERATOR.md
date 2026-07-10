# Current State

## Snapshot

- Updated at: 2026-07-10T15:05:00Z
- Repository root: `.` (chimera-cortex)
- Current branch: `main`
- Current HEAD: `d0ec3af` (`docs: add repository contributor guide`)
- Working-tree status: Staged memory-bank files and new docs; `clean_index.py` and `.agents/memory-bank/CURRENT_BENCHMARK_RUNNER.md` untracked.
- Outgoing agent role: Benchmark Evaluator (RAG Audit & LLM-as-Judge Validation)
- Expected next agent role: Implementer / Optimizer (Phase 5 or RAG optimizations based on audit)
- Memory-bank confidence: HIGH

## Objective

Evaluate the RAG pipeline end-to-end using the automated `benchmark.py` runner script with LLM-as-judge scoring, compare `qwen2.5:3b` and `qwen3:8b` judge models on identical RAG answers, and audit the soundness of the average retrieval relevance score.

## Acceptance Criteria

- [x] Read document base and generate 20 English QA pairs (10 simple + 10 cross-chunk) with required source chunks.
- [x] Implement the LLM-as-judge benchmark mechanism in `benchmark.py` with configurable judge models and delay.
- [x] Run evaluations using different Ollama models (`qwen2.5:3b`, `qwen3:8b`) with warm cache reuse.
- [x] Audit the soundness of the 4.6/5 average retrieval score.

## Approved Plan Reference

- `PLAN.md` contains the approved multi-database knowledge platform plan (all 5 phases are READY but not started).
- RAG optimizations are PROPOSED but code changes have not yet been approved or started.

## Progress

### Completed

1. **Benchmark QA Dataset** — Created [benchmark_dataset.json](file://benchmark_dataset.json) containing 20 English QA pairs (10 simple, 10 cross-chunk) with required source files and text segments.
2. **LLM-as-Judge Runner** — Implemented [benchmark.py](file://benchmark.py) to sequentially query the live `/api/chat` API, run Ollama judge calls, score correctness/faithfulness/retrieval on a 1-5 scale, parse raw JSON/regex responses, and export JSON and HTML reports.
3. **Judge Model Comparison Runs** — Run 2 (`qwen2.5:3b`, fresh cache, 20% pass rate) and Run 3 (`qwen3:8b`, reused cache, 80% pass rate) evaluated identical RAG answers, proving that smaller models (3B) suffer from severe grading noise and lack semantic awareness (e.g. penalizing concise answers).
4. **Retrieval Relevance Audit** — Conducted a full manual audit of the 20 queries from the `qwen3:8b` run. Verified that the reported average retrieval relevance of `4.60/5` was slightly inflated (fair audited score is `4.30/5`) due to judge rounding/hallucination on QA-04 (missing explanation of Altria's disguise), QA-13 (missing Bedivere lore file), and QA-19 (Prototype variant file mismatch).

### In Progress

- Handoff documentation and memory bank consolidation.

### Not Started

- Phase 1 implementation of the Multi-database knowledge platform (`PLAN.md`).
- Implementation of the 4 proposed RAG optimizations (Entity-Balanced Slicing, Metadata Prepends, Multi-Entity Expansion, Anchor Defeat).

## Working Tree

### Current Task Changes

- `benchmark_dataset.json` — Staged 20-question evaluation dataset.
- `benchmark.py` — Staged CLI benchmark runner.
- `benchmark_results/` — Staged visual HTML reports and raw JSON output files.
- `.agents/memory-bank/CURRENT.md` — [NEW] Authoritative task snapshot.
- `.agents/memory-bank/DECISIONS.md` — [NEW] Appendix of RAG decisions.
- `.agents/memory-bank/LOG.md` — [NEW] Append-only session logs.

### Pre-existing or Unrelated Changes

- `.DS_Store` — macOS metadata, untracked.
- `clean_index.py` — Untracked database/storage reset script.
- `.agents/memory-bank/CURRENT_BENCHMARK_RUNNER.md` — Untracked parallel agent snapshot.
- `.agents/memory-bank/CURRENT_ARCHITECT.md` & `CURRENT_RESEARCH.md` — Staged parallel agent snapshots.

## Key Files and Symbols

### Benchmark System
- [benchmark_dataset.json](file://benchmark_dataset.json) — Ground-truth QA pairs.
- [benchmark.py](file://benchmark.py) — CLI evaluation runner script.
- [benchmark_results/](file://benchmark_results/) — Reports directory.

### RAG Pipeline
- [cortex/core/rag.py](file://cortex/core/rag.py) — Core RAG logic.
- [cortex/api/chat.py](file://cortex/api/chat.py) — API chat handler.

## Validation

| Command | Result | Timestamp UTC | Scope | Notes |
| --- | --- | --- | --- | --- |
| `python3 benchmark.py --judge-model qwen3:8b --reuse-cache --delay 0.5` | PASS | 2026-05-29T16:57:26Z | Full run | Completed in 674.7s; Avg correctness 3.95/5, pass rate 80.0% |
| `python3 benchmark.py --judge-model qwen2.5:3b --delay 1.0` | PASS | 2026-05-29T16:24:42Z | Full run | Completed in 153.9s; Avg correctness 2.75/5, pass rate 20.0% |

## Decisions and Constraints

1. **Strict Grounding Constraint**: Prompt modifications to allow pronoun/nickname resolution are rejected to maintain strict document grounding and prevent hallucination (D-20260602-0316).
2. **Warm Cache Re-use**: Use `--reuse-cache` to evaluate judge model capability isolated from retrieval variance.
3. **Synchronous Execution**: The chat API executes synchronously to prevent Ollama context corruption.

## Open Questions and Blockers

1. **Strategic Priority**: Should the next agent focus on implementing the 4 RAG optimization fixes to close the generation-synthesis gap identified in the audit, or begin Multi-DB platform development?

## Next Actions

1. **Consult User on Priority** — Resolve whether to begin Multi-DB development or implement RAG optimizations.
2. **Implement Entity-Balanced Slicing** — Modify the context merger in [cortex/api/chat.py](file://cortex/api/chat.py) to partition contexts proportionally per sub-query.
3. **Prepend Tabular Attributes** — Extend the Markdown chunker in [cortex/core/rag.py](file://cortex/core/rag.py) to bind key attributes/names to each text chunk.

## Resume Point

- **Immediate next action:** Await user decision on next priority (RAG optimizations vs Multi-DB platform).

## Confidence and Uncertainty

- High confidence. The benchmark system is fully validated, and a detailed audit of the retrieval scores has been documented.
