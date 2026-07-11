# Approved Implementation Plan

## Plan Metadata

- Plan status: APPROVED
- Approval source: User reviewed and iterated on the architecture (2026-06-04)
- Last updated at: 2026-07-11T01:38:43Z
- Related objective: Build a multi-database knowledge platform with multi-source ingestion
- Related acceptance criteria: Inferred — see CURRENT.md

## Constraints

- No dedicated test framework (validate via live API and benchmark runner)
- External services (MySQL, Redis, MinIO, Infinity, Ollama, reranker) may fail independently — explicit error handling required
- Do not commit secrets or credentials
- Conventional Commit-style messages
- Keep API modules thin; business logic in `cortex/core/`
- Python 3, 4-space indent, snake_case functions, PascalCase classes

## Plan Steps

### 1. Multi-DB Core + Directory Connector — `COMPLETED`

**Objective:** Introduce `knowledge_bases` as top-level entity; refactor existing single-DB pipeline to be KB-scoped.

**Files:**
- `cortex/core/database.py` — Add `knowledge_bases` table (id, slug, name, description, ingest_config JSON, generation_config JSON, vector_table, minio_bucket, enabled, timestamps)
- `cortex/core/database.py` — Add `sources` table (id, kb_id FK, type, name, config JSON, sync_mode, sync_cron, enabled, last_synced_at)
- `cortex/core/database.py` — Add `kb_id` FK to existing document/chunk flows
- `cortex/core/database.py` — Add `ingestion_log` table
- New `cortex/api/kb.py` — KB CRUD endpoints (`/api/kb`)
- `cortex/api/chat.py` — Refactor to `/api/kb/{slug}/chat`, load per-KB generation config
- `cortex/core/rag.py` — Query per-KB vector table, use per-KB embedding model
- `cortex/core/ingest.py` — Use per-KB chunking config (max_chars, overlap), per-KB embedding model, per-KB vector table
- `index.py` — Accept `--kb` flag to target a specific knowledge base
- New `cortex/core/connectors/directory.py` — `DirectoryConnector` with `watchdog` filesystem watcher
- `requirements.txt` — Add `watchdog`

**Expected change:** Existing FGO lore pipeline works as a named KB (`fgo-lore`). New KBs can be created with different configs. Per-KB Infinity table isolation (separate table per KB). Per-KB MinIO path namespacing. Per-KB Redis cache prefix.

**Completion condition:** `POST /api/kb` creates a KB; `POST /api/kb/fgo-lore/chat` queries it; existing benchmark passes against the new structure.

**Validation:** `python benchmark.py --dataset benchmark_dataset.json` passes with retrieval ≥ 4.80

**Evidence:** Implementation and live integration are complete. All six services pass. `fgo-lore` contains 413 indexed documents and 17,089 chunks in an isolated Infinity table with 413 namespaced MinIO objects. KB CRUD/storage lifecycle, scoped chat/documents/ingestion/cache, compatibility routes, directory connector/watchdog, and `--kb` CLIs are implemented. A second temporary KB using `bge-m3` at 1024 dimensions passed lifecycle validation. Run 33 scored C/F/R 5.00 with 100% pass rate. Full Run 37 scored correctness 4.70, faithfulness 4.70, retrieval relevance 4.85, and 95% pass rate, satisfying the retrieval acceptance gate.

---

### 2. Web Connector + Scheduled Sync — `COMPLETED`

**Objective:** Add website crawling as a data source type.

**Files:**
- New `cortex/core/connectors/web.py` — `WebConnector` using `crawl4ai`
- `cortex/core/ingest.py` — Support async ingestion from connector output
- `requirements.txt` — Add `crawl4ai`
- Scheduler integration (`APScheduler` or similar)

**Expected change:** A web source can be added to any KB. Pages are crawled to Markdown, hashed, chunked, embedded per the KB's ingest_config. Scheduled re-crawl via cron expression.

**Completion condition:** Crawl a documentation site into a KB and query its content.

**Validation:** Manual — add a web source, sync, query.

**Evidence:** Implemented KB-scoped source CRUD/sync routes, source-aware ingestion, `WebConnector` on Crawl4AI 0.9, APScheduler 3.11 cron registration, observable next-run status, and scheduler refresh on source/KB lifecycle changes. Static validation passes with 32 tests. Live validation crawled the FastAPI First Steps documentation page, indexed 89 chunks, answered a documentation question correctly, skipped an unchanged second sync with zero new chunks, registered a cron job, and removed all temporary MySQL/Infinity/MinIO state on KB deletion.

---

### 3. Calibre Content Server + Reader Contract — `COMPLETED`

**Objective:** Import ebook libraries through the standard Calibre Content Server HTTP API while supporting DSReaderHelper's opaque, spoiler-safe advanced QA contract.

**Files:**
- New `cortex/api/external_documents.py` — PUT/DELETE/batch APIs under each KB
- New `cortex/core/connectors/calibre.py` — Read-only Calibre Content Server client; never access `metadata.db`
- `cortex/core/database.py` and `cortex/core/ingest.py` — Persist and ingest opaque external documents and ordered segments
- `cortex/core/rag.py` and `cortex/api/chat.py` — Apply document/source/ordinal filters during retrieval and bounded context expansion

**Expected change:** A KB can sync a Calibre source over HTTP, download supported formats, normalize content, and preserve book metadata. DSReaderHelper separately owns reader semantics and scope resolution, then calls Cortex with opaque document/source constraints. Chat enforces ordinal caps before dense/text/RRF ranking and during adjacent context expansion.

**Completion condition:** Sync and query a Calibre Content Server source without filesystem/database access, and push/query segmented external documents without returning content above any `max_ordinal` cap.

**Validation:** Contract fixtures plus focused dense/text/RRF/context-expansion/cache tests and a live push/query/delete smoke test.

**Evidence:** The user supplied the authoritative DSReaderHelper-to-Cortex contract, then clarified that Chimera should also support direct import through the standard Calibre Content Server API. The direct-SQLite prototype was removed. Content Server connector, external-document APIs, segment-aware vectors, pre-retrieval filters, bounded expansion, filter-aware cache keys, generic citations, and frozen fixtures are implemented. Explicit empty retrieval scopes fail closed while omitted filters remain unrestricted. Versioned request/response/external-document JSON Schemas are published at `/api/contracts/reader-qa/v1`. Live external cap and empty-scope validation passed. A real Content Server at `192.168.11.65:8080` imported the Calibre Quick Start Guide as 78 chunks, answered how to add books, skipped all chunks on an unchanged second sync, and passed API/Infinity cleanup.

---

### 4. Cloud Drive Connectors — `IN_PROGRESS`

**Objective:** Connect to Google Drive, OneDrive, Dropbox.

**Files:**
- New `cortex/core/connectors/google_drive.py`
- New `cortex/core/connectors/onedrive.py`
- New `cortex/core/connectors/dropbox.py`
- `requirements.txt` — Add `google-api-python-client`, `msgraph-sdk-python`, `dropbox`

**Expected change:** Cloud drive folders can be synced as KB sources. Incremental sync via provider change detection APIs. OAuth2 or Service Account auth.

**Completion condition:** Sync a Google Drive folder into a KB and query its contents.

**Validation:** Manual — end-to-end sync and query.

**Evidence:** Implemented Google Drive, OneDrive, and Dropbox connectors, provider exports/downloads, credential-by-environment validation, full and incremental cursors, opaque origin-path deletion, and cloud format normalization. Fixture tests cover Google exports/change cursors, move-out deletion, retry-safe download failures, OneDrive delta links, and Dropbox cursors/deletions. Cloud filenames and ingestion identity are provider-ID based, so renames do not duplicate documents or objects. Live pipelines against actual MySQL, MinIO, Infinity, Ollama, and reranker proved initial/incremental updates, persisted cursors, grounded query, rename-in-place, opaque deletion, and cleanup. Official-provider live acceptance remains pending because no cloud credential variables are configured locally.

---

### 5. Management UI — `COMPLETED`

**Objective:** Admin interface for multi-DB platform management.

**Files:**
- `static/index.html`, `static/style.css`, `static/app.js` — KB management views

**Expected change:** UI for creating/editing KBs, managing sources, viewing sync status, browsing documents, and triggering syncs.

**Completion condition:** All KB/source CRUD operations available in the web UI.

**Validation:** Manual — visual inspection and functional testing.

**Evidence:** Added operational Chat/Manage/Audit navigation, KB selector, aggregate stats/comparison, KB and source CRUD forms, source sync controls, document browse/view/delete, cache clear, ingestion activity, and config inspection. Desktop 1440x900 and mobile 390x844 browser checks passed with no viewport overflow; large tables and tabs use bounded scrolling. A live UI query returned Gawain's B+ Strength from 10 contexts and opened the source document. Startup now migrates legacy Infinity tables through the REST columns API; the real FGO table moved from five to nine columns and retrieval recovered. All 46 tests and static gates pass.

## Plan Deviations

- User-approved architecture change (2026-07-11): Cortex must never access Calibre `metadata.db`. It may import through the standard Calibre Content Server API. DSReaderHelper remains responsible for reader-specific scope, locator, and advanced QA orchestration through generic opaque Cortex constraints.
- Local adjustment (2026-07-10): preserve legacy API routes while introducing KB-scoped routes, then migrate direct consumers (`static/app.js`, `cortex/core/benchmark.py`, `cortex/api/documents.py`, and `clean_index.py`) before removing compatibility paths.
- Local adjustment (2026-07-10): the architecture artifact is already tracked at `.agents/docs/knowledge_base_ingestion_architecture.md`; no copy step is required.
- Local adjustment (2026-07-10): separate per-KB query rewrite and answer-generation models, gate decomposition to explicit multi-part questions, and disable Qwen thinking when token-limited. This preserves the approved per-KB generation configuration while meeting the existing retrieval gate.
- Local adjustment (2026-07-11): use Crawl4AI 0.9 `AsyncWebCrawler`/BFS APIs and APScheduler 3.11 `BackgroundScheduler`; pin NumPy `<2` and SciPy `<1.17` to preserve Infinity SDK compatibility under Python 3.14.
- Local adjustment (2026-07-11): migrate pre-reader-contract Infinity tables through the REST columns endpoint at startup. The installed Thrift SDK and server reject each other's protocol versions, while the standard REST API supports additive columns.
- The retrieval improvement plan (Phase 5 — adaptive score threshold) was deferred by the user as "not critical at this moment" on 2026-06-04.

## Architecture Reference

The full architecture specification is tracked in the repository at:
`.agents/docs/knowledge_base_ingestion_architecture.md`

Key architectural elements:
- `knowledge_bases` table with `ingest_config` (embedding model, chunk size, overlap) and `generation_config` (LLM model, temperature, system prompt) as JSON columns
- Per-KB Infinity DB vector table (named `chunks_{slug}`) — supports different embedding dimensions
- Per-KB MinIO path namespacing (`cortex-documents/{slug}/`)
- Per-KB Redis key prefixes (`rag_cache:{slug}:*`)
- All APIs scoped under `/api/kb/{slug}/*`
- `BaseConnector` abstract interface with `scan()`, `detect_changes()`, `detect_deletions()`
- `RawDocument` dataclass as the common connector output
- Rebuild-from-raw workflow: changing ingest config triggers re-processing from MinIO, never re-fetches from source
