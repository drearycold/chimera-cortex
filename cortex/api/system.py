from fastapi import APIRouter, HTTPException
from cortex.core.cache_management import clear_cache, get_global_cache_stats
from cortex.core.database import get_service_status

router = APIRouter(prefix="/api", tags=["System"])

@router.get("/status")
async def api_status():
    return get_service_status()

@router.post("/cache/clear")
async def api_clear_cache():
    try:
        cleared = clear_cache()
        return {
            "cleared": cleared,
            "message": f"Cleared {cleared} RAG cache entries.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear Redis cache: {str(e)}")


@router.get("/cache/stats")
async def api_cache_stats():
    try:
        return get_global_cache_stats()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cache stats unavailable: {exc}") from exc
