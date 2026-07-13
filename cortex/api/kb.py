from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
from cortex.core.cache_management import (
    delete_cache_entry,
    get_cache_entry_detail,
    get_knowledge_base_cache,
)
from cortex.core.kb_storage import (
    clear_knowledge_base_cache,
    delete_knowledge_base_storage,
    ensure_minio_bucket,
    ensure_vector_table,
)
from cortex.core.scheduler import source_scheduler

router = APIRouter(prefix="/api/kb", tags=["Knowledge Bases"])


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=255)
    dimensions: int = Field(ge=1, le=65536)
    provider: str = Field(min_length=1, max_length=64)


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(min_length=1, max_length=64)
    max_chars: int = Field(ge=1, le=1_000_000)
    overlap_chars: int = Field(ge=0, le=999_999)

    @model_validator(mode="after")
    def overlap_is_smaller_than_chunk(self):
        if self.overlap_chars >= self.max_chars:
            raise ValueError("chunking.overlap_chars must be less than max_chars")
        return self


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bm25_enabled: bool
    initial_topn: int = Field(ge=1, le=10_000)
    rrf_k: int = Field(ge=1, le=100_000)
    context_window: int = Field(ge=0, le=100)


class IngestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding: EmbeddingConfig
    chunking: ChunkingConfig
    search: SearchConfig


class QueryRewriteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    model: str = Field(min_length=1, max_length=255)


class RerankerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class GenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=64)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1, le=32768)
    top_k_contexts: int = Field(ge=1, le=100)
    system_prompt: str = Field(min_length=1, max_length=100_000)
    query_rewrite: QueryRewriteConfig
    reranker: RerankerConfig


def _default_ingest_model() -> IngestConfig:
    return IngestConfig.model_validate(default_ingest_config())


def _default_generation_model() -> GenerationConfig:
    return GenerationConfig.model_validate(default_generation_config())


def _index_identity(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "embedding": config.get("embedding"),
        "chunking": config.get("chunking"),
    }


class KnowledgeBaseCreate(BaseModel):
    slug: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    ingest_config: IngestConfig = Field(default_factory=_default_ingest_model)
    generation_config: GenerationConfig = Field(default_factory=_default_generation_model)
    enabled: bool = True


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    ingest_config: IngestConfig | None = None
    generation_config: GenerationConfig | None = None
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
        current = get_knowledge_base(slug)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        ) from exc
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base '{slug}' not found.",
        )

    requested_ingest = changes.get("ingest_config")
    if (
        requested_ingest is not None
        and _index_identity(requested_ingest)
        != _index_identity(current["ingest_config"])
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Embedding or chunking configuration cannot be changed after knowledge "
                "base creation without an orchestrated rebuild."
            ),
        )

    generation_changed = (
        "generation_config" in changes
        and changes["generation_config"] != current["generation_config"]
    )
    if generation_changed:
        try:
            clear_knowledge_base_cache(slug)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not invalidate knowledge base cache: {exc}",
            ) from exc

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
        return {
            "cleared": cleared,
            "message": f"Cleared {cleared} cache entries for '{slug}'.",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cache clear failed: {exc}",
        ) from exc


@router.get("/{slug}/cache")
def api_list_knowledge_base_cache(
    slug: str,
    offset: int = 0,
    limit: int = 50,
    q: str | None = Query(default=None, max_length=500),
):
    if get_knowledge_base(slug) is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="offset must be >= 0 and limit must be 1-100.")
    try:
        return get_knowledge_base_cache(slug, offset, limit, q)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cache entries unavailable: {exc}") from exc


@router.get("/{slug}/cache/{digest}")
def api_get_knowledge_base_cache_entry(slug: str, digest: str):
    if get_knowledge_base(slug) is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    try:
        entry = get_cache_entry_detail(slug, digest)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cache entry unavailable: {exc}") from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Cache entry has already expired or was deleted.")
    return entry


@router.delete("/{slug}/cache/{digest}")
def api_delete_knowledge_base_cache_entry(slug: str, digest: str):
    if get_knowledge_base(slug) is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    try:
        deleted = delete_cache_entry(slug, digest)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cache deletion failed: {exc}") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Cache entry has already expired or was deleted.")
    return {"deleted": 1, "message": "Cache entry deleted."}


@router.get("/{slug}/ingestion-logs")
def api_list_ingestion_logs(slug: str, limit: int = 100):
    knowledge_base = get_knowledge_base(slug)
    if knowledge_base is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    try:
        return {"logs": list_ingestion_logs(knowledge_base["id"], limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
