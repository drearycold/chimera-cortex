from fastapi import APIRouter, HTTPException
from cortex.core.database import get_service_status, get_redis_client

router = APIRouter(prefix="/api", tags=["System"])

@router.get("/status")
async def api_status():
    return get_service_status()

@router.post("/cache/clear")
async def api_clear_cache():
    try:
        r_client = get_redis_client()
        r_client.flushdb()
        return {"message": "Redis generation cache has been successfully cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear Redis cache: {str(e)}")
