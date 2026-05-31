import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cortex.core.database import init_db
from cortex.api.system import router as system_router
from cortex.api.documents import router as documents_router
from cortex.api.chat import router as chat_router
from cortex.api.benchmarks import router as benchmarks_router
from cortex.api.ingest import router as ingest_router

app = FastAPI(title="Chimera Cortex: An Omni-Context Knowledge Engine")

@app.on_event("startup")
def startup_event():
    try:
        init_db()
        print("MySQL database and benchmark tables initialized successfully.")
    except Exception as e:
        print(f"[ERROR] Database initialization failed: {e}")

# Register modular API routers
app.include_router(system_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(benchmarks_router)
app.include_router(ingest_router)

# Mount static web directory for the UI portal
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
else:
    @app.get("/")
    async def index_fallback():
        return {"message": "RAG Portal APIs are running. Static folder missing."}
