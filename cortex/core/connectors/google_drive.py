import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import BaseConnector, RawDocument
from .cloud_common import SUPPORTED_EXTENSIONS, make_cloud_document

FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def save_oauth_credentials(credentials: Any, token_file: str | Path) -> None:
    path = Path(token_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(credentials.to_json())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def load_oauth_credentials(token_file: str | Path):
    from google.auth.transport.requests import Request  # type: ignore[import-untyped]
    from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]

    path = Path(token_file).expanduser()
    credentials = Credentials.from_authorized_user_file(path, GOOGLE_DRIVE_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_oauth_credentials(credentials, path)
    if not credentials.valid:
        raise RuntimeError(
            "Google OAuth credentials are invalid; run google_auth.py authorize again."
        )
    return credentials


class GoogleDriveConnector(BaseConnector):
    def __init__(self, kb_id: int, source_id: int, config: dict[str, Any], service=None):
        self.kb_id = kb_id
        self.source_id = source_id
        self.config = config
        self.folder_id = str(config["folder_id"])
        self.service = service or self._build_service()
        self.is_full_snapshot = not bool(config.get("cursor"))
        self.next_cursor: str | None = None
        self.deleted_origin_paths: list[str] = []
        self.allow_empty = True

    def _build_service(self):
        from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]
        from google.oauth2 import service_account  # type: ignore[import-untyped]
        from googleapiclient.discovery import build  # type: ignore[import-untyped]

        service_account_env = self.config.get("service_account_env")
        if service_account_env:
            value = os.environ[str(service_account_env)]
            if value.lstrip().startswith("{"):
                credentials = service_account.Credentials.from_service_account_info(
                    json.loads(value), scopes=GOOGLE_DRIVE_SCOPES
                )
            else:
                credentials = service_account.Credentials.from_service_account_file(
                    value, scopes=GOOGLE_DRIVE_SCOPES
                )
        elif self.config.get("oauth_token_file_env"):
            credentials = load_oauth_credentials(
                os.environ[str(self.config["oauth_token_file_env"])]
            )
        else:
            credentials = Credentials(token=os.environ[str(self.config["token_env"])])
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _timestamp(value: str | None) -> float:
        return datetime.fromisoformat((value or "1970-01-01T00:00:00+00:00").replace("Z", "+00:00")).timestamp()

    def _list_folder(self) -> tuple[list[dict[str, Any]], set[str]]:
        files = []
        folder_ids = {self.folder_id}
        pending = [self.folder_id]
        while pending:
            parent = pending.pop()
            token = None
            while True:
                response = self.service.files().list(
                    q=f"'{parent}' in parents and trashed = false",
                    fields="nextPageToken,files(id,name,mimeType,modifiedTime,parents,trashed)",
                    pageSize=1000,
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()
                for item in response.get("files", []):
                    if item.get("mimeType") == FOLDER_MIME:
                        if self.config.get("recursive", True):
                            folder_ids.add(item["id"])
                            pending.append(item["id"])
                    else:
                        files.append(item)
                token = response.get("nextPageToken")
                if not token:
                    break
        return files, folder_ids

    def _changes(self) -> tuple[list[dict[str, Any]], set[str]]:
        items = []
        folder_ids = set(self.config.get("folder_ids", [self.folder_id]))
        page_token = str(self.config["cursor"])
        while True:
            response = self.service.changes().list(
                pageToken=page_token,
                spaces="drive",
                fields="nextPageToken,newStartPageToken,changes(removed,fileId,file(id,name,mimeType,modifiedTime,parents,trashed))",
                includeRemoved=True,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for change in response.get("changes", []):
                item = change.get("file") or {}
                file_id = change.get("fileId") or item.get("id")
                if change.get("removed") or item.get("trashed"):
                    self.deleted_origin_paths.append(f"google_drive:{file_id}")
                    continue
                if item.get("mimeType") == FOLDER_MIME:
                    if set(item.get("parents", [])) & folder_ids:
                        folder_ids.add(item["id"])
                    continue
                if set(item.get("parents", [])) & folder_ids:
                    items.append(item)
                else:
                    self.deleted_origin_paths.append(f"google_drive:{file_id}")
            if response.get("nextPageToken"):
                page_token = response["nextPageToken"]
                continue
            self.next_cursor = response.get("newStartPageToken", page_token)
            break
        self.config["folder_ids"] = sorted(folder_ids)
        return items, folder_ids

    def _download(self, item: dict[str, Any]) -> tuple[str, bytes]:
        mime_type = item.get("mimeType", "")
        name = item["name"]
        if mime_type in GOOGLE_EXPORTS:
            export_mime, suffix = GOOGLE_EXPORTS[mime_type]
            data = self.service.files().export(fileId=item["id"], mimeType=export_mime).execute()
            return f"{name}{suffix}", bytes(data)
        data = self.service.files().get_media(fileId=item["id"]).execute()
        return name, bytes(data)

    def _documents(self, items: list[dict[str, Any]]) -> list[RawDocument]:
        documents = []
        errors = []
        for item in items:
            name = item.get("name", "")
            if item.get("mimeType") not in GOOGLE_EXPORTS and Path(name).suffix.casefold() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                normalized_name, data = self._download(item)
                documents.append(
                    make_cloud_document(
                        kb_id=self.kb_id,
                        source_id=self.source_id,
                        provider="google_drive",
                        file_id=item["id"],
                        name=normalized_name,
                        data=data,
                        modified_at=self._timestamp(item.get("modifiedTime")),
                        metadata={"name": name, "mime_type": item.get("mimeType")},
                    )
                )
            except Exception as exc:
                print(f"[GOOGLE DRIVE] Failed to import '{name}': {exc}")
                errors.append(f"{name}: {exc}")
        if errors:
            raise RuntimeError(
                "Google Drive sync could not import: " + "; ".join(errors)
            )
        return documents

    def scan(self) -> list[RawDocument]:
        if self.is_full_snapshot:
            items, folder_ids = self._list_folder()
            self.config["folder_ids"] = sorted(folder_ids)
            token = self.service.changes().getStartPageToken().execute()
            self.next_cursor = token["startPageToken"]
        else:
            items, _ = self._changes()
        return self._documents(items)

    def detect_changes(self, since: float) -> list[RawDocument]:
        return [item for item in self.scan() if item.source_modified_at > since]

    def detect_deletions(self, known_paths: set[str]) -> list[str]:
        return []
