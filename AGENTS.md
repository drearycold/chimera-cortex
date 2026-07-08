# Repository Guidelines

## Project Structure & Module Organization

Chimera Cortex is a FastAPI-based local RAG application. `app.py` initializes database tables, registers API routers, and serves the static UI.

- `cortex/api/`: routers for chat, documents, ingestion, benchmarks, and system status.
- `cortex/core/`: business logic for configuration, storage clients, RAG retrieval, ingestion, and benchmark execution.
- `static/`: vanilla HTML/CSS/JS frontend served from `/`.
- `documents/`: Markdown source corpus used by ingestion.
- `benchmark_dataset.json` and `test_dataset.json`: evaluation datasets.
- `index.py` and `benchmark.py`: CLI wrappers around the running API.

## Build, Test, and Development Commands

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run the application locally:

```bash
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Run ingestion through the API wrapper:

```bash
python index.py --source-dir documents
python index.py status
```

Run or inspect benchmark jobs:

```bash
python benchmark.py --dataset benchmark_dataset.json
python benchmark.py list
```

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, descriptive `snake_case` functions and variables, and `PascalCase` classes. Keep API modules thin; put reusable behavior in `cortex/core/`. Prefer explicit error handling around external services because MySQL, Redis, MinIO, Infinity, Ollama, and the reranker may fail independently.

Frontend code in `static/app.js` is plain JavaScript. Keep DOM IDs stable because they are referenced directly from the HTML.

## Testing Guidelines

There is no dedicated test framework configured. Validate changes with targeted commands and live API checks. For backend changes, start `uvicorn` and exercise the affected endpoint. For ingestion or benchmark changes, use `python index.py status` or `python benchmark.py status`. When adding tests, prefer `pytest` under `tests/` with names like `test_rag.py`.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit-style prefixes such as `feat:`, `perf:`, and `fix:`. Use concise imperative messages, for example `fix: handle missing reranker scores`.

Pull requests should include a short problem statement, implementation summary, validation steps, and any configuration or service dependency changes. Include screenshots or recordings when changing the static UI.

## Security & Configuration Tips

Local configuration is loaded from `.env` by `cortex/core/config.py`. Do not commit secrets, credentials, generated benchmark output, or local virtual environments. Document any required service endpoints when changing defaults for MySQL, MinIO, Redis, Infinity, Ollama, or reranker settings.
