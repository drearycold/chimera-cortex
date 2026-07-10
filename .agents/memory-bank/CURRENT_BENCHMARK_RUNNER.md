# Current State

## Snapshot

- Updated at: 2026-07-10T14:59:00Z
- Repository root: `.` (chimera-cortex)
- Current branch: `main`
- Current HEAD: `d0ec3af` (`docs: add repository contributor guide`)
- Working-tree status: Staged memory-bank files and new docs; `clean_index.py` untracked.
- Outgoing agent role: Benchmark Runner / Evaluator (RAG audit, database purge & rebuild, query analysis)
- Expected next agent role: Implementer / Optimizer (Phase 5 retrieval tuning, generalized RAG optimization fixes)
- Memory-bank confidence: HIGH

## Objective

Run database purging and clean rebuilds under the modular `cortex` framework, execute the evaluation suite, analyze the latest benchmark execution results (Runs 8, 9, 10, 14, 18, 19), identify RAG retrieval shortfalls, and formulate generalized engineering fixes.

## Acceptance Criteria

- [x] Clear existing MySQL databases, MinIO objects, Infinity vector indexes, and Redis generation cache completely.
- [x] Rebuild index from 413 files (6,452 chunks) successfully using the new `cortex.core` modular layout.
- [x] Execute and monitor benchmark runs via the `benchmark.py` runner script.
- [x] Track correctness, faithfulness, and relevance metrics across runs.
- [x] Dig deep into low-scoring or failing questions in Run 19 to identify concrete failures.
- [x] Update analysis recommendations in [analysis_results.md](file://.agents/docs/analysis_results.md) to be generalized and domain-agnostic.

## Approved Plan Reference

- No approved code implementation plan currently exists for the next RAG optimizations.
- The approved Multi-database knowledge platform plan in `PLAN.md` is READY but not started.
- Retrieval tuning Phase 5 (adaptive score threshold) is DEFERRED (see `DECISIONS.md`).

## Progress

### Completed

1. **Purged & Reset Indexes** — Created and executed [clean_index.py](file://clean_index.py) using the refactored `cortex.core.database` and `cortex.core.config` modules. Purged MySQL, MinIO, Infinity, and Redis.
2. **Rebuilt Knowledge Base** — Indexed all 413 documents from `documents/` directory using 768-dimensional embeddings (`nomic-embed-text:latest`).
3. **Executed Benchmark Suite** — Monitored execution of multiple benchmark evaluations (Runs 8, 9, 10, 14, 18, 19).
4. **Analyzed Run 19 Failures** — Diagnosed the specific causes of semantic search loss, tabular block separation, post-merge slicing cutoff, and relational constraints.
5. **Generalized Recommendations** — Formulated domain-agnostic RAG design patterns (Entity-Balanced Retrieval Slicing, Parent Metadata & Tabular Attribute Prepends, Multi-Entity Co-occurrence Query Expansion, and General Knowledge Anchor Defeat) in [analysis_results.md](file://.agents/docs/analysis_results.md).

### In Progress

- Handoff documentation and memory bank consolidation.

### Not Started

- Implementation of the 4 generalized RAG optimizations.
- Multi-database knowledge platform (`PLAN.md`).

## Working Tree

### Current Task Changes

- `clean_index.py` — Untracked reset script.
- `.agents/memory-bank/CURRENT.md` — [NEW] Canonical snapshot.
- `.agents/memory-bank/PLAN.md` — Staged approved multi-DB plan.
- `.agents/memory-bank/DECISIONS.md` — Staged decisions log.
- `.agents/memory-bank/LOG.md` — Staged session logs.
- `.agents/docs/analysis_results.md` — [NEW] Generalized analysis report.

### Pre-existing or Unrelated Changes

- `.DS_Store` — macOS metadata.
- `.agents/docs/` — Staged or untracked research papers/architecture docs.

## Key Files and Symbols

### Ingestion & CLI Wrappers
- [clean_index.py](file://clean_index.py) — Purges all databases/storages.
- [index.py](file://index.py) — Rebuilds knowledge indexes from markdown source files.
- [benchmark.py](file://benchmark.py) — CLI runner and evaluator script.

### RAG Pipeline
- [cortex/core/rag.py](file://cortex/core/rag.py) — Business logic for hybrid retrieval, RRF, and embedding.
- [cortex/api/chat.py](file://cortex/api/chat.py) — API router orchestrating query decomposition, context merging, and LLM prompt generation.

### Diagnostics
- [.agents/docs/analysis_results.md](file://.agents/docs/analysis_results.md) — Comprehensive breakdown of Run 19 shortfalls.

## Validation

| Command | Result | Timestamp UTC | Scope | Notes |
|---|---|---|---|---|
| `./venv/bin/python clean_index.py` | PASS | 2026-07-10T14:48:30Z | Full purge | Clears MySQL, MinIO, Infinity DB, and Redis |
| `./venv/bin/python index.py` | PASS | 2026-07-10T14:50:00Z | Index rebuild | Successfully chunked and embedded 413 files |
| `./venv/bin/python benchmark.py` | PASS | 2026-07-10T14:55:12Z | Evaluation | Run 19 completed successfully with 4.25 average correctness |

## Decisions and Constraints

1. **Use 768-dimension vectors** — Infinity DB chunk table uses `vector, 768, float` to accommodate `nomic-embed-text` embeddings.
2. **Synchronous evaluation** — API chat runs synchronously to avoid thread safety issues with Ollama and the DB drivers.
3. **Ignore cache for testing** — Purge the Redis cache or use `--reuse-cache=False` when validating retrieval adjustments.

## Open Questions and Blockers

1. **Strategic Workstream Choice** — Should the next agent focus on implementing the 4 RAG optimization fixes to maximize correctness score, or start building the multi-DB platform in `PLAN.md`?

## Next Actions

1. **Select Objective** — Consult the user to choose between the RAG optimization fixes (Entity-Balanced Slicing, Metadata Prepends) and the Multi-database knowledge platform (`PLAN.md`).
2. **Implement Entity-Balanced Slicing** — Modify the merging loop in [cortex/api/chat.py](file://cortex/api/chat.py) to partition contexts proportionally per sub-query.
3. **Prepend Tabular Attributes** — Extend the Markdown chunker in [cortex/core/rag.py](file://cortex/core/rag.py) to bind key attributes/names to each text chunk.

## Resume Point

- **Immediate next action:** Await user decision on next priority.
- **RAG tuning next step:** Modify the context merging and slicing routine in `cortex/api/chat.py`.

## Confidence and Uncertainty

- High confidence. The indexes are fully rebuilt and verified, and services are fully operational.
