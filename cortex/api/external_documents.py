from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from cortex.core.database import get_knowledge_base
from cortex.core.external_documents import (
    delete_external_document,
    upsert_external_document,
)

router = APIRouter(
    prefix="/api/kb/{slug}/external-documents",
    tags=["External Documents"],
)


class SegmentLocator(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=4096)


class ExternalSegment(BaseModel):
    ordinal: int = Field(ge=0)
    locator: SegmentLocator
    heading: str | None = Field(default=None, max_length=1000)
    text: str = Field(min_length=1)


class ExternalDocument(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    source_key: str = Field(min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)
    segments: list[ExternalSegment] = Field(min_length=1)

    @field_validator("segments")
    @classmethod
    def validate_ordinals(cls, segments: list[ExternalSegment]):
        ordinals = [segment.ordinal for segment in segments]
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise ValueError("segment ordinals must be unique and monotonically increasing")
        return segments


class BatchExternalDocument(ExternalDocument):
    external_id: str = Field(min_length=1, max_length=512)


class ExternalDocumentBatch(BaseModel):
    documents: list[BatchExternalDocument] = Field(min_length=1, max_length=100)


def _knowledge_base(slug: str) -> dict:
    knowledge_base = get_knowledge_base(slug)
    if knowledge_base is None or not knowledge_base["enabled"]:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{slug}' not found.")
    return knowledge_base


@router.put("/{external_id}")
def api_put_external_document(slug: str, external_id: str, req: ExternalDocument):
    if not external_id or len(external_id) > 512:
        raise HTTPException(status_code=422, detail="external_id must be 1-512 characters.")
    try:
        return upsert_external_document(
            _knowledge_base(slug),
            external_id,
            req.model_dump(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"External ingestion failed: {exc}") from exc


@router.delete("/{external_id}")
def api_delete_external_document(slug: str, external_id: str):
    try:
        deleted = delete_external_document(_knowledge_base(slug), external_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"External deletion failed: {exc}") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"External document '{external_id}' not found.")
    return {"external_id": external_id, "status": "deleted"}


@router.post("/batch", status_code=status.HTTP_200_OK)
def api_batch_external_documents(slug: str, req: ExternalDocumentBatch):
    knowledge_base = _knowledge_base(slug)
    results = []
    for document in req.documents:
        payload = document.model_dump()
        external_id = payload.pop("external_id")
        try:
            results.append(upsert_external_document(knowledge_base, external_id, payload))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Batch failed for external_id '{external_id}': {exc}",
            ) from exc
    return {"documents": results}
