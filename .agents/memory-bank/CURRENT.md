# Current State

## Snapshot

- Updated at: 2026-07-11T13:30:00Z
- Repository root: `.` (`chimera-cortex`)
- Branch: `main`
- HEAD: `610627c` (`fix: harden source synchronization`)
- Working tree: Google Drive Desktop OAuth implementation, documentation, tests, and memory-bank updates are uncommitted
- Memory status: `VERIFIED_WITH_DRIFT`

## Objective and Acceptance Criteria

Implement the approved five-phase multi-database knowledge platform in `PLAN.md`. All five phases and official-provider acceptance are complete.

## Verified Progress

- Phase 1 is complete. MySQL has KB/source/log schema and all 413 documents are assigned to the `fgo-lore` directory source.
- `fgo-lore` has 17,089 chunks in `chunks_fgo_lore` and 413 namespaced MinIO objects. Legacy storage was preserved.
- KB CRUD, isolated Infinity/MinIO/Redis lifecycle, KB-scoped chat/documents/ingestion/benchmark/cache, compatibility routes, directory connector/watchdog, and KB-aware CLIs are implemented.
- A temporary 1024-dimensional `bge-m3` KB passed create/read/delete lifecycle validation.
- RAG generation now uses per-KB generation and rewrite models, deterministic non-thinking output, explicit token limits, guarded query decomposition, adaptive parent windows, and balanced query quotas.
- Benchmark Run 37 completed with correctness 4.70, faithfulness 4.70, retrieval relevance 4.85, and 95% pass rate. This passes the Phase 1 retrieval gate. QA-17 remains the intentionally grounded nickname edge case.
- Phase 2 is complete. KB-scoped source CRUD/manual sync, source-aware ingestion, Crawl4AI 0.9 web crawling, APScheduler 3.11 cron registration, scheduler status, and source/KB lifecycle refresh are implemented.
- Live Phase 2 validation crawled the FastAPI `First Steps` documentation page into a temporary KB, indexed 89 chunks, answered the Swagger UI `/docs` and ReDoc `/redoc` question correctly, skipped all chunks on a second unchanged sync, exposed a scheduled job and next run, and removed MySQL/Infinity/MinIO state on deletion.
- Crawl4AI setup and doctor passed. Dependency constraints keep Crawl4AI and Infinity compatible on NumPy 1.26 / SciPy 1.16.
- Phase 3 is active under the latest dual-boundary decision: Calibre Content Server handles normal library import; DSReaderHelper handles reader-specific advanced QA semantics. The rejected direct-SQLite prototype was removed before commit.
- The Calibre connector now paginates `/ajax/search/{library_id}`, fetches `/ajax/books/{library_id}`, downloads `/get/{format}/{book_id}/{library_id}`, supports Basic/Digest credentials via `password_env`, and normalizes EPUB spine items, PDF pages, and text into ordered segments.
- Generic external-document PUT/DELETE/batch APIs, opaque source/document IDs, segment ordinal/locator vector fields, retrieval-stage document/source/cap filters, bounded adjacent expansion, cache identity, external contexts, and generic citations are implemented.
- Reader contract v1 publishes JSON Schemas at `/api/contracts/reader-qa/v1` and is covered by frozen request/response fixtures. An omitted filter remains unrestricted; an explicitly empty filter now matches no documents, preventing empty DSReaderHelper scopes from widening to the whole KB.
- Live reader-contract validation indexed ordinals 120 and 127. With `max_ordinal: 126`, contexts, first-stage audit, expanded context, prompt, and citation contained only ordinal 120; the uncapped control query returned ordinal 127. External delete and temporary KB/Infinity cleanup passed.
- Live Calibre validation used the local Content Server at `192.168.11.65:8080`: imported the Quick Start Guide over HTTP as 78 chunks, answered the documented Add Books workflow, skipped all chunks on a second unchanged sync, and removed the temporary KB/vector table successfully.
- Phase 4 is complete. Google Drive, OneDrive, and Dropbox connectors support exports/downloads, credential-safe configs, provider cursors, incremental deletions by opaque origin path, and cloud document normalization. Google Drive Desktop OAuth now stores refreshable authorized-user credentials outside the repository, refreshes expired access tokens, and atomically persists them with `0600` permissions.
- Official Google Drive acceptance used a real OAuth user and a two-document Drive folder. The public source API indexed both native Google Docs as 42 chunks, and KB chat correctly answered that Robert Weaver edited *Canadian Short Stories, Third Series* in 1978. The temporary KB returned 404 after cleanup.
- Phase 5 is complete. The operational UI manages KBs, sources, documents, activity, cache, and config; Chat is KB-scoped. Desktop and mobile browser QA pass, including a real query and source document view.
- Legacy Infinity tables are migrated at startup through the REST columns API. The real `chunks_fgo_lore` table moved from five to nine columns, resolving an Infinity 500 caused by requesting reader-contract columns from the old schema.

## Working Tree Ownership

- All modified and untracked files belong to the active multi-database task.
- `.env` is ignored and locally points Ollama and the judge to the available LAN service; it contains no tracked change.
- No unrelated or unknown working-tree changes are present.

## Validation

- `venv/bin/ruff check app.py index.py benchmark.py clean_index.py cortex tests` — PASS.
- `venv/bin/bandit -r app.py index.py benchmark.py clean_index.py cortex -ll -q` — PASS (no medium/high findings).
- `venv/bin/mypy app.py index.py benchmark.py clean_index.py cortex tests` — PASS, 33 source files.
- `venv/bin/python -m compileall -q app.py index.py benchmark.py clean_index.py cortex tests` — PASS.
- `PYTHONPATH=. venv/bin/pytest -q` — PASS, 61 tests.
- `venv/bin/pip check` — PASS.
- `git diff --check` — PASS.
- Live Phase 1/2 API/storage validation, Run 37, Phase 3 cap/empty-scope/Calibre validation, Phase 4 update/query and rename/delete pipelines, and Phase 5 browser/API validation — PASS.

## Active Step and Next Action

All approved plan steps are `COMPLETED`. The next safe action is to review and commit the uncommitted Google Drive Desktop OAuth changes.

## Blockers and Uncertainty

- No known blockers remain. Local OAuth client and token paths are configured only in ignored `.env`; credentials are not tracked.
