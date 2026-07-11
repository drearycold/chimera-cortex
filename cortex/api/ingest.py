from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from cortex.core.ingest import manager as ingest_manager
from cortex.core.kb_config import DEFAULT_KB_SLUG

router = APIRouter(prefix="/api", tags=["Ingest"])

class IngestRequest(BaseModel):
    source_dir: str = "documents"
    force_rebuild: bool = False
    kb_slug: str = DEFAULT_KB_SLUG

@router.get("/ingest/status")
async def api_ingest_status():
    return ingest_manager.get_status()

@router.post("/ingest/run")
async def api_run_ingest(req: IngestRequest):
    status = ingest_manager.get_status()
    if status["status"] == "running":
        raise HTTPException(status_code=400, detail="An ingestion run is already in progress.")
    try:
        ingest_manager.start(
            source_dir=req.source_dir,
            force_rebuild=req.force_rebuild,
            kb_slug=req.kb_slug,
        )
        return {"message": "Ingestion started successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start ingestion: {str(e)}")

@router.post("/ingest/stop")
async def api_stop_ingest():
    stopped = ingest_manager.stop()
    if stopped:
        return {"message": "Ingestion cancellation signal sent successfully."}
    return {"message": "No active ingestion run found to cancel."}


@router.get("/kb/{slug}/ingest/status")
async def api_kb_ingest_status(slug: str):
    status = ingest_manager.get_status()
    status["requested_kb_slug"] = slug
    return status


@router.post("/kb/{slug}/ingest/run")
async def api_run_kb_ingest(slug: str, req: IngestRequest):
    req.kb_slug = slug
    return await api_run_ingest(req)


@router.post("/kb/{slug}/ingest/stop")
async def api_stop_kb_ingest(slug: str):
    status = ingest_manager.get_status()
    if status["status"] == "running" and status["kb_slug"] != slug:
        raise HTTPException(
            status_code=409,
            detail=f"The active ingestion belongs to knowledge base '{status['kb_slug']}'.",
        )
    return await api_stop_ingest()
