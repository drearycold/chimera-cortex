# Decisions

## D-20260530-0819-retrieval-improvement-plan

- Status: ACCEPTED
- Date: 2026-05-30T08:19:00Z
- Decision owner: User (approved implementation plan)
- Context: Retrieval audit (conversation `2ffdfdd7`) showed audited score of 4.30/5 with specific failure modes (boundary loss, wrong entity retrieval, missing multi-entity coverage).
- Decision: Implement 5-phase retrieval improvement: (1) chunk overlap + expand top-K, (2) hybrid BM25+dense with RRF, (3) query decomposition for multi-hop, (4) parent-child chunking, (5) adaptive score threshold.
- Rationale: Each phase targets specific audit failures. Ordered by ROI. Each independently deployable and testable.
- Consequences: Phases 1–4 implemented and committed. Phase 5 deferred. Achieved C=4.75, F=5.00, R=4.80, Pass=95%.
- Evidence: Commits `e3c39e4`, `d46153d`, `8b1ffca`, `d0fe1c0`, `9d75712` on `main`. User-reported benchmark results.
- Supersedes: N/A
- Superseded by: N/A

## D-20260604-0523-multi-source-ingestion-architecture

- Status: ACCEPTED
- Date: 2026-06-04T05:23:00Z
- Decision owner: User (reviewed artifact, requested multi-DB extension)
- Context: After retrieval improvements, user requested research on comprehensive knowledge database and data source ingestion management architecture supporting directories, websites, cloud drives, and Calibre ebook libraries.
- Decision: Design a connector-based ingestion framework where all sources normalize to Markdown + metadata JSON, with SHA-256 content-hash deduplication, incremental sync, and a unified REST API.
- Rationale: Source-agnostic pipeline enables adding new source types without modifying downstream chunking/embedding. Store raw in MinIO for rebuild-without-refetch.
- Consequences: Architecture artifact produced. No code implemented.
- Evidence: Artifact `knowledge_base_ingestion_architecture.md` (initial version)
- Supersedes: N/A
- Superseded by: D-20260604-0532-multi-database-architecture

## D-20260604-0532-multi-database-architecture

- Status: ACCEPTED
- Date: 2026-06-04T05:32:00Z
- Decision owner: User (requested multi-DB support)
- Context: User asked to extend the architecture so the system supports multiple databases, each with its own data sources and ingest/generation configurations.
- Decision: Introduce `knowledge_bases` as top-level entity. Per-KB: ingestion config (embedding model, chunk size, overlap), generation config (LLM model, temperature, system prompt), vector table isolation (separate Infinity table per KB), MinIO path namespacing, Redis cache prefixing. All APIs scoped under `/api/kb/{slug}/*`.
- Rationale: Different domains need different chunking strategies, embedding models, and generation models. Per-table vector isolation supports different embedding dimensions. Enables A/B testing by pointing two KBs at the same source with different configs.
- Consequences: Architecture artifact rewritten. Roadmap has 5 implementation phases. No code implemented yet.
- Evidence: Artifact `knowledge_base_ingestion_architecture.md` (revised version)
- Supersedes: D-20260604-0523-multi-source-ingestion-architecture
- Superseded by: N/A

## D-20260604-0529-phase5-deferred

- Status: ACCEPTED
- Date: 2026-06-04T05:29:00Z
- Decision owner: User
- Context: User reported benchmark results after Phases 1–4: C=4.75, F=5.00, R=4.80, Pass=95%.
- Decision: Phase 5 (adaptive score threshold) is deferred as "not critical at this moment."
- Rationale: Current scores are excellent; diminishing returns from Phase 5.
- Consequences: Phase 5 remains in the plan as DEFERRED but will not be implemented unless revisited.
- Evidence: User statement in conversation.
- Supersedes: N/A
- Superseded by: N/A

## D-20260604-0520-crawl4ai-for-web

- Status: ACCEPTED
- Date: 2026-06-04T05:20:00Z
- Decision owner: Researcher (recommendation accepted by user)
- Context: Evaluated Crawl4AI, Firecrawl, and Spider for web scraping connector.
- Decision: Use Crawl4AI as the web scraping tool.
- Rationale: Free, open-source, runs locally (no API key needed), outputs LLM-ready Markdown, supports JavaScript rendering via Playwright.
- Consequences: Adds `crawl4ai` dependency. No managed API costs.
- Evidence: Research in `knowledge_base_ingestion_architecture.md` Section 3.2
- Supersedes: N/A
- Superseded by: N/A

## D-20260604-0520-calibre-sqlite-direct

- Status: SUPERSEDED
- Date: 2026-06-04T05:20:00Z
- Decision owner: Researcher (recommendation accepted by user)
- Context: Evaluated Calibre integration approaches (CLI, REST wrapper, direct SQLite, Python API).
- Decision: Read Calibre's `metadata.db` directly via SQLite with `?mode=ro`.
- Rationale: No dependency on Calibre being installed/running. Direct access to full metadata schema. Read-only mode avoids concurrent access issues.
- Consequences: Must not access `metadata.db` while Calibre GUI is writing. Calibre Python API not needed.
- Evidence: Research in `knowledge_base_ingestion_architecture.md` Section 3.4
- Supersedes: N/A
- Superseded by: D-20260711-dsreaderhelper-cortex-contract

## D-20260711-dsreaderhelper-cortex-contract

- Status: SUPERSEDED
- Date: 2026-07-11
- Decision owner: User
- Context: The initial Phase 3 implementation connected Cortex directly to Calibre `metadata.db`. The user clarified that DSReaderHelper is the required intermediary and supplied the advanced reader QA retrieval contract.
- Decision: DSReaderHelper owns Calibre access, ebook parsing, reader semantics, scope resolution, and opaque ID mapping. Cortex exposes generic external-document PUT/DELETE/batch APIs and accepts opaque document/source filters with per-document ordinal caps. Cortex code and tests must not depend on Calibre-specific fields.
- Rationale: This keeps trusted reader metadata and spoiler-scope decisions at the reader boundary while preserving Cortex as a generic ingestion and filtered-retrieval service.
- Consequences: Remove the direct SQLite connector and ebook parser dependencies. Add segment-aware storage, retrieval-stage filtering across dense/text/RRF, bounded adjacent expansion, filter-aware caching, and generic citations.
- Evidence: User-provided contract attachment, 2026-07-11.
- Supersedes: D-20260604-0520-calibre-sqlite-direct
- Superseded by: D-20260711-calibre-content-server-boundary

## D-20260711-calibre-content-server-boundary

- Status: ACCEPTED
- Date: 2026-07-11
- Decision owner: User
- Context: After defining the DSReaderHelper advanced QA contract, the user clarified that Chimera should still be able to import Calibre libraries independently, but never by reading `metadata.db`.
- Decision: Chimera imports books through the standard read-only Calibre Content Server HTTP API. DSReaderHelper continues to own reader-specific scope resolution, locators, permissions, and advanced QA orchestration, using opaque Cortex filters where needed.
- Rationale: Normal library ingestion remains available without coupling Chimera to Calibre's on-disk schema, while reader-specific semantics stay in DSReaderHelper.
- Consequences: Implement a Content Server connector with explicit authentication and bounded format downloads. Keep the generic segment/filter contract for advanced QA. No direct SQLite access is permitted.
- Evidence: User clarification, 2026-07-11.
- Supersedes: D-20260711-dsreaderhelper-cortex-contract
- Superseded by: N/A

## D-20260711-infinity-rest-schema-migration

- Status: ACCEPTED
- Date: 2026-07-11
- Decision owner: Codex (local compatibility adjustment)
- Context: Existing vector tables lacked the four reader-contract columns. The installed Infinity Thrift SDK and configured server rejected each other's protocol versions during additive migration.
- Decision: Use Infinity's standard REST columns endpoint for additive vector-table migrations and verify all registered KB tables during application startup.
- Rationale: The REST endpoint is supported by the installed SDK implementation, avoids protocol-version coupling, and updates existing tables without rebuilding 17,089 FGO chunks.
- Consequences: Startup requires Infinity schema access. Migration is idempotent because existing columns are inspected before POSTing only missing definitions.
- Evidence: Real `chunks_fgo_lore` migration from five to nine columns and successful grounded retrieval after the change.
- Supersedes: N/A
- Superseded by: N/A

## D-20260711-reader-scope-fail-closed-contract

- Status: ACCEPTED
- Date: 2026-07-11
- Decision owner: User plan, implemented by Codex
- Context: DSReaderHelper resolves reader scope into opaque Cortex constraints. A valid scope can resolve to zero allowed documents, while callers may also intentionally omit filtering for ordinary KB chat.
- Decision: Distinguish an omitted `retrieval_filter` from an explicitly empty filter. Omitted means unrestricted KB retrieval; explicit empty matches no indexed documents. Publish versioned Pydantic-derived JSON Schemas and keep frozen Cortex-owned fixtures.
- Rationale: Empty allowed sets must not widen into all-ingested retrieval, which would violate scope and spoiler guarantees. Versioned schemas make the repository boundary independently testable.
- Consequences: DSReaderHelper can safely send an empty resolved set. Contract consumers can discover request/response/external-document schemas at `/api/contracts/reader-qa/v1`; no Calibre-specific scope fields enter Cortex.
- Evidence: Unit fixtures and live empty-versus-omitted scope comparison on `fgo-lore`.
- Supersedes: N/A
- Superseded by: N/A

## D-20260711-google-drive-desktop-oauth

- Status: ACCEPTED
- Date: 2026-07-11
- Decision owner: User request, implemented by Codex
- Context: Official Google Drive acceptance required personal Drive authorization. The available credential was an installed-application OAuth client, not a service account or access token.
- Decision: Support Desktop OAuth through a dedicated local CLI. Store refreshable authorized-user credentials in a caller-selected file outside the repository; source configuration stores only the environment-variable name that points to that file. Refresh expired access tokens at connector startup and atomically rewrite the token file with `0600` permissions.
- Rationale: Personal Drive access is naturally user-authorized, while keeping secrets out of MySQL, source JSON, tracked files, and command output. Persisted refresh tokens avoid repeated interactive authorization.
- Consequences: `google-auth-oauthlib` is required for initial authorization. Runtime Drive access remains read-only. Revoked or invalid credentials require rerunning the authorization CLI.
- Evidence: Real Desktop OAuth authorization, direct Drive API call, automatic-refresh unit tests, and official two-document sync/query/cleanup acceptance.
- Supersedes: N/A
- Superseded by: N/A

## D-20260602-0323-overlap-merge-guardrail

- Status: ACCEPTED
- Date: 2026-06-02T03:23:00Z
- Decision owner: User (approved approach: "i think its better to find and merge overlapping parent chunks")
- Context: During Phase 4 parent-child chunking, adjacent children (e.g., chunk 2 and chunk 3 from the same document) would each independently expand to overlapping parent ranges (1-3 and 2-4), producing massive text duplication in the LLM context window.
- Decision: Consolidate overlapping `[idx-1, idx+1]` expansion ranges per document using interval merging before fetching. Fetch each merged range once with `fetch_and_merge_chunk_range()`. Deduplicate identical expanded content blocks in the prompt assembler via `seen_contents` set.
- Rationale: Eliminates redundant Infinity DB queries, prevents duplicated text from wasting the 8k context window, and improved Faithfulness from 4.80 to 5.00 (Run 32).
- Consequences: Renamed `fetch_and_merge_context` → `fetch_and_merge_chunk_range` (takes start/end instead of single index). Added interval-merge logic and `seen_contents` dedup in `chat.py`.
- Evidence: Commit `9d75712`. Benchmark Run 32: C=4.75, F=5.00, R=4.80, Pass=95%.
- Supersedes: N/A
- Superseded by: N/A

## D-20260602-0316-reject-nickname-prompt-change

- Status: ACCEPTED
- Date: 2026-06-02T03:16:00Z
- Decision owner: User (explicitly rejected and instructed revert)
- Context: QA-17 fails because the model refuses to identify "the golden man" as Gilgamesh under the strict grounding prompt. A prompt modification was attempted to allow "reasonable pronoun, synonym, and nickname/alias resolution."
- Decision: Revert the prompt change. Keep the strict grounding system prompt unchanged.
- Rationale: User preference to maintain strict document grounding over resolving one edge-case benchmark question.
- Consequences: QA-17 remains the sole failing question (1/1/1 scores). Pass rate stays at 95%.
- Evidence: User instruction "revert change to system prompt" during session.
- Supersedes: N/A
- Superseded by: N/A

## D-20260710-1500-rag-general-optimizations

- Status: PROPOSED
- Date: 2026-07-10T15:00:00Z
- Decision owner: Benchmarker / Evaluator
- Context: After analyzing Run 19, we identified 4 specific retrieval and reasoning shortfalls.
- Decision: Propose four generalized RAG optimization patterns (Entity-Balanced Retrieval Slicing, Parent Metadata & Tabular Prepends, Multi-Entity Co-occurrence Query Expansion, and General Knowledge Anchor Defeat) rather than domain-specific overrides.
- Rationale: General patterns keep the codebase clean and maintainable while solving semantic gaps in complex or comparative query synthesis.
- Consequences: Future agent will implement these patterns in `cortex/core/` and `cortex/api/`.
- Evidence: Recommendations in `.agents/docs/analysis_results.md`.
- Supersedes: N/A
- Superseded by: N/A

## D-20260710-1505-retrieval-audit-verification

- Status: ACCEPTED
- Date: 2026-07-10T15:05:00Z
- Decision owner: Outgoing Benchmark Evaluator
- Context: Audited the 4.60/5 average retrieval score reported by the qwen3:8b judge in Run 3.
- Decision: Establish that the true audited average retrieval relevance is 4.30/5 rather than the judge-reported 4.60/5. Document the precise discrepancies (QA-03, QA-04, QA-13, QA-19) in `retrieval_audit.md`.
- Rationale: Smaller judge models (3B) suffer from severe noise, while larger judge models (8B) occasionally over-score and round up retrieval relevance. Meticulous verification is required to identify exact synthesis and retrieval failures.
- Consequences: Found that retrieval is still highly effective (4.30/5), but the main pipeline bottleneck is the generative synthesis phase.
- Evidence: Analysis in `retrieval_audit.md`.
- Supersedes: N/A
- Superseded by: N/A
