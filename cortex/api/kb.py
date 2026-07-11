from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from cortex.core.database import (
    KnowledgeBaseAlreadyExistsError,
    create_knowledge_base,
    delete_knowledge_base,
    get_knowledge_base,
    list_ingestion_logs,
    list_knowledge_bases,
    update_knowledge_base,
)
from cortex.core.kb_config import default_generation_config, default_ingest_config
from cortex.core.kb_storage import (
    clear_knowledge_base_cache,
    delete_knowledge_base_storage,
    ensure_minio_bucket,
    ensure_vector_table,
)
from cortex.core.scheduler import source_scheduler

router = APIRouter(prefix="/api/kb", tags=["Knowledge Bases"])

class KnowledgeBaseCreate(BaseModel):
    slug: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    ingest_config: dict[str, Any] = Field(default_factory=default_ingest_config)
    generation_config: dict[str, Any] = Field(default_factory=default_generation_config)
    enabled: bool = True


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    ingest_config: dict[str, Any] | None = None
    generation_config: dict[str, Any] | None = None
    enabled: bool | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def api_create_knowledge_base(req: KnowledgeBaseCreate):
    try:
        knowledge_base = create_knowledge_base(req.model_dump())
        try:
            ensure_minio_bucket(knowledge_base)
            ensure_vector_table(knowledge_base)
        except Exception:
            delete_knowledge_base(req.slug)
            raise
        return knowledge_base
    except KnowledgeBaseAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Knowledge base '{req.slug}' already exists.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        ) from exc


@router.get("")
def api_list_knowledge_bases():
    try:
        return {"knowledge_bases": list_knowledge_bases()}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        ) from exc


@router.get("/{slug}")
def api_get_knowledge_base(slug: str):
    try:
        knowledge_base = get_knowledge_base(slug)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        ) from exc
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base '{slug}' not found.",
        )
    return knowledge_base


@router.put("/{slug}")
def api_update_knowledge_base(slug: str, req: KnowledgeBaseUpdate):
    changes = req.model_dump(exclude_unset=True)
    try:
        knowledge_base = update_knowledge_base(slug, changes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        ) from exc
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base '{slug}' not found.",
        )
    if source_scheduler.running:
        source_scheduler.refresh()
    return knowledge_base


@router.delete("/{slug}")
def api_delete_knowledge_base(slug: str):
    knowledge_base = get_knowledge_base(slug)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base '{slug}' not found.",
        )
    try:
        delete_knowledge_base_storage(knowledge_base)
        deleted = delete_knowledge_base(slug)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base '{slug}' not found.",
        )
    if source_scheduler.running:
        source_scheduler.refresh()
    return {"message": f"Knowledge base '{slug}' deleted."}


@router.post("/{slug}/cache/clear")
def api_clear_knowledge_base_cache(slug: str):
    if get_knowledge_base(slug) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base '{slug}' not found.",
        )
    try:
        cleared = clear_knowledge_base_cache(slug)
        return {"message": f"Cleared {cleared} cache entries for '{slug}'."}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cache clear failed: {exc}",
        ) from exc


@router.get("/{slug}/ingestion-logs")
def api_list_ingestion_logs(slug: str, limit: int = 100):
    knowledge_base = get_knowledge_base(slug)
    if knowledge_base is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    try:
        return {"logs": list_ingestion_logs(knowledge_base["id"], limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
