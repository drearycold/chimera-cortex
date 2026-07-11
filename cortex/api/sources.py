from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from cortex.core.database import (
    create_source,
    delete_source,
    get_knowledge_base,
    get_source,
    list_sources,
    update_source,
)
from cortex.core.ingest import manager as ingest_manager
from cortex.core.kb_storage import delete_source_storage
from cortex.core.scheduler import cron_trigger, source_scheduler

router = APIRouter(prefix="/api/kb/{slug}/sources", tags=["Sources"])

SourceType = Literal["directory", "web", "calibre", "cloud_drive", "external"]
SyncMode = Literal["manual", "watch", "scheduled", "push"]


class SourceCreate(BaseModel):
    type: SourceType
    name: str = Field(min_length=1, max_length=255)
    config: dict[str, Any]
    sync_mode: SyncMode = "manual"
    sync_cron: str | None = Field(default=None, max_length=100)
    enabled: bool = True


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    sync_mode: SyncMode | None = None
    sync_cron: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None


def _knowledge_base_or_404(slug: str) -> dict:
    knowledge_base = get_knowledge_base(slug)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base '{slug}' not found.",
        )
    return knowledge_base


def _validate_source_config(
    source_type: str,
    config: dict[str, Any],
    sync_mode: str,
    sync_cron: str | None,
):
    if sync_mode == "scheduled":
        if not sync_cron or len(sync_cron.split()) != 5:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Scheduled sources require a five-field cron expression.",
            )
        try:
            cron_trigger(sync_cron)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid cron expression: {exc}",
            ) from exc
    if source_type == "directory":
        if not str(config.get("path", "")).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Directory sources require config.path.",
            )
        return

    if source_type == "calibre":
        url = str(config.get("base_url", "")).strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Calibre sources require an absolute HTTP(S) config.base_url.",
            )
        if not str(config.get("library_id", "")).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Calibre sources require config.library_id.",
            )
        if config.get("password"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Use config.password_env instead of storing a Calibre password.",
            )
        if str(config.get("auth_type", "digest")).casefold() not in {"basic", "digest"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Calibre auth_type must be basic or digest.",
            )
        formats = config.get("preferred_formats", ["EPUB", "PDF", "TXT", "MD"])
        supported = {"EPUB", "PDF", "TXT", "MD"}
        if not formats or any(str(value).upper() not in supported for value in formats):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Calibre preferred_formats supports EPUB, PDF, TXT, and MD.",
            )
        try:
            page_size = int(config.get("page_size", 500))
            max_books = int(config.get("max_books", 10000))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Calibre page_size and max_books must be integers.",
            ) from exc
        if not 1 <= page_size <= 1000 or not 1 <= max_books <= 100000:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Calibre page_size must be 1-1000 and max_books must be 1-100000.",
            )
        return

    if source_type == "external":
        if not str(config.get("source_key", "")).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="External sources require opaque config.source_key.",
            )
        if sync_mode != "push":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="External sources require sync_mode 'push'.",
            )
        return

    if source_type == "cloud_drive":
        provider = str(config.get("provider", "")).casefold()
        if provider not in {"google_drive", "onedrive", "dropbox"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Cloud drive provider must be google_drive, onedrive, or dropbox.",
            )
        forbidden = {"token", "access_token", "refresh_token", "service_account_json"}
        if forbidden.intersection(config):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Store cloud credentials in environment variables, not source config.",
            )
        if provider == "google_drive":
            if not str(config.get("folder_id", "")).strip():
                raise HTTPException(status_code=422, detail="Google Drive requires config.folder_id.")
            if not (config.get("token_env") or config.get("service_account_env")):
                raise HTTPException(
                    status_code=422,
                    detail="Google Drive requires config.token_env or config.service_account_env.",
                )
        elif provider == "onedrive":
            if not str(config.get("drive_id", "")).strip() or not str(config.get("folder_id", "")).strip():
                raise HTTPException(
                    status_code=422,
                    detail="OneDrive requires config.drive_id and config.folder_id.",
                )
            if not config.get("token_env"):
                raise HTTPException(status_code=422, detail="OneDrive requires config.token_env.")
        elif not config.get("token_env"):
            raise HTTPException(status_code=422, detail="Dropbox requires config.token_env.")
        return

    url = str(config.get("url", "")).strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Web sources require an absolute HTTP(S) config.url.",
        )
    try:
        max_depth = int(config.get("max_depth", 1))
        max_pages = int(config.get("max_pages", 25))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Web source max_depth and max_pages must be integers.",
        ) from exc
    if not 0 <= max_depth <= 5 or not 1 <= max_pages <= 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Web source max_depth must be 0-5 and max_pages must be 1-500.",
        )


def _refresh_scheduler():
    if source_scheduler.running:
        source_scheduler.refresh()


@router.post("", status_code=status.HTTP_201_CREATED)
def api_create_source(slug: str, req: SourceCreate):
    knowledge_base = _knowledge_base_or_404(slug)
    _validate_source_config(req.type, req.config, req.sync_mode, req.sync_cron)
    try:
        source = create_source(knowledge_base["id"], req.model_dump())
        _refresh_scheduler()
        return source
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Source creation failed: {exc}",
        ) from exc


@router.get("")
def api_list_sources(slug: str):
    knowledge_base = _knowledge_base_or_404(slug)
    return {"sources": list_sources(knowledge_base["id"])}


@router.get("/{source_id}")
def api_get_source(slug: str, source_id: int):
    knowledge_base = _knowledge_base_or_404(slug)
    source = get_source(knowledge_base["id"], source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found in '{slug}'.",
        )
    return source


@router.put("/{source_id}")
def api_update_source(slug: str, source_id: int, req: SourceUpdate):
    knowledge_base = _knowledge_base_or_404(slug)
    current = get_source(knowledge_base["id"], source_id)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found in '{slug}'.",
        )
    changes = req.model_dump(exclude_unset=True)
    config = changes.get("config", current["config"])
    sync_mode = changes.get("sync_mode", current["sync_mode"])
    sync_cron = changes.get("sync_cron", current["sync_cron"])
    _validate_source_config(current["type"], config, sync_mode, sync_cron)
    try:
        source = update_source(knowledge_base["id"], source_id, changes)
        _refresh_scheduler()
        return source
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Source update failed: {exc}",
        ) from exc


@router.delete("/{source_id}")
def api_delete_source(slug: str, source_id: int):
    knowledge_base = _knowledge_base_or_404(slug)
    if get_source(knowledge_base["id"], source_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found in '{slug}'.",
        )
    try:
        delete_source_storage(knowledge_base, source_id)
        deleted = delete_source(knowledge_base["id"], source_id)
        _refresh_scheduler()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Source deletion failed: {exc}",
        ) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return {"message": f"Source {source_id} deleted from '{slug}'."}


@router.post("/{source_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def api_sync_source(slug: str, source_id: int):
    knowledge_base = _knowledge_base_or_404(slug)
    source = get_source(knowledge_base["id"], source_id)
    if source is None or not source["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Enabled source {source_id} not found in '{slug}'.",
        )
    if source["sync_mode"] == "push":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Push sources are updated through the external-documents API.",
        )
    if ingest_manager.get_status()["status"] == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another ingestion run is already in progress.",
        )
    ingest_manager.start_source(slug, source_id)
    return {"message": f"Source {source_id} sync started."}


@router.get("/{source_id}/sync/status")
def api_source_sync_status(slug: str, source_id: int):
    knowledge_base = _knowledge_base_or_404(slug)
    if get_source(knowledge_base["id"], source_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found in '{slug}'.",
        )
    current = ingest_manager.get_status()
    current["requested_source_id"] = source_id
    current["schedule"] = source_scheduler.job_status(source_id)
    return current
