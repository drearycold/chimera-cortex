# Approved Implementation Plan

## Plan Metadata

- Plan status: APPROVED
- Approval source: User reviewed and iterated on the architecture (2026-06-04)
- Last updated at: 2026-07-10T14:37:00Z
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

### 1. Multi-DB Core + Directory Connector — `READY`

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

**Evidence:** Not started

---

### 2. Web Connector + Scheduled Sync — `READY`

**Objective:** Add website crawling as a data source type.

**Files:**
- New `cortex/core/connectors/web.py` — `WebConnector` using `crawl4ai`
- `cortex/core/ingest.py` — Support async ingestion from connector output
- `requirements.txt` — Add `crawl4ai`
- Scheduler integration (`APScheduler` or similar)

**Expected change:** A web source can be added to any KB. Pages are crawled to Markdown, hashed, chunked, embedded per the KB's ingest_config. Scheduled re-crawl via cron expression.

**Completion condition:** Crawl a documentation site into a KB and query its content.

**Validation:** Manual — add a web source, sync, query.

**Evidence:** Not started

---

### 3. Calibre Ebook Library Connector — `READY`

**Objective:** Ingest books from a Calibre library.

**Files:**
- New `cortex/core/connectors/calibre.py` — `CalibreConnector` reading SQLite `metadata.db`
- `requirements.txt` — Add `ebooklib`, `beautifulsoup4`

**Expected change:** Calibre books (EPUB, PDF, TXT) are extracted, normalized to Markdown, and ingested using the KB's config. Rich Calibre metadata (authors, tags, series) flows into chunk metadata.

**Completion condition:** Create a KB with a Calibre source, sync, query against book content.

**Validation:** Manual — add Calibre source, sync, query.

**Evidence:** Not started

---

### 4. Cloud Drive Connectors — `READY`

**Objective:** Connect to Google Drive, OneDrive, Dropbox.

**Files:**
- New `cortex/core/connectors/google_drive.py`
- New `cortex/core/connectors/onedrive.py`
- New `cortex/core/connectors/dropbox.py`
- `requirements.txt` — Add `google-api-python-client`, `msgraph-sdk-python`, `dropbox`

**Expected change:** Cloud drive folders can be synced as KB sources. Incremental sync via provider change detection APIs. OAuth2 or Service Account auth.

**Completion condition:** Sync a Google Drive folder into a KB and query its contents.

**Validation:** Manual — end-to-end sync and query.

**Evidence:** Not started

---

### 5. Management UI — `READY`

**Objective:** Admin interface for multi-DB platform management.

**Files:**
- `static/index.html`, `static/style.css`, `static/app.js` — KB management views

**Expected change:** UI for creating/editing KBs, managing sources, viewing sync status, browsing documents, and triggering syncs.

**Completion condition:** All KB/source CRUD operations available in the web UI.

**Validation:** Manual — visual inspection and functional testing.

**Evidence:** Not started

## Plan Deviations

- No deviations recorded. Implementation has not begun.
- The retrieval improvement plan (Phase 5 — adaptive score threshold) was deferred by the user as "not critical at this moment" on 2026-06-04.

## Architecture Reference

The full architecture specification is in the Antigravity conversation brain:
`~/.gemini/antigravity/brain/3c576cf9-4591-40f5-826a-277f596d571f/knowledge_base_ingestion_architecture.md`

Key architectural elements:
- `knowledge_bases` table with `ingest_config` (embedding model, chunk size, overlap) and `generation_config` (LLM model, temperature, system prompt) as JSON columns
- Per-KB Infinity DB vector table (named `chunks_{slug}`) — supports different embedding dimensions
- Per-KB MinIO path namespacing (`cortex-documents/{slug}/`)
- Per-KB Redis key prefixes (`rag_cache:{slug}:*`)
- All APIs scoped under `/api/kb/{slug}/*`
- `BaseConnector` abstract interface with `scan()`, `detect_changes()`, `detect_deletions()`
- `RawDocument` dataclass as the common connector output
- Rebuild-from-raw workflow: changing ingest config triggers re-processing from MinIO, never re-fetches from source
