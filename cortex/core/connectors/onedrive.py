import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .base import BaseConnector, RawDocument
from .cloud_common import SUPPORTED_EXTENSIONS, make_cloud_document


class OneDriveConnector(BaseConnector):
    def __init__(
        self,
        kb_id: int,
        source_id: int,
        config: dict[str, Any],
        client: httpx.Client | None = None,
    ):
        self.kb_id = kb_id
        self.source_id = source_id
        self.config = config
        self.drive_id = str(config["drive_id"])
        self.folder_id = str(config["folder_id"])
        self.is_full_snapshot = not bool(config.get("cursor"))
        self.next_cursor: str | None = None
        self.deleted_origin_paths: list[str] = []
        self.allow_empty = True
        token = os.getenv(str(config.get("token_env", "")), "")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url="https://graph.microsoft.com/v1.0",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
            timeout=60.0,
        )

    @staticmethod
    def _timestamp(value: str | None) -> float:
        return datetime.fromisoformat((value or "1970-01-01T00:00:00+00:00").replace("Z", "+00:00")).timestamp()

    def _delta_url(self) -> str:
        if self.config.get("cursor"):
            return str(self.config["cursor"])
        drive = quote(self.drive_id, safe="")
        folder = quote(self.folder_id, safe="")
        return f"/drives/{drive}/items/{folder}/delta"

    def _items(self) -> list[dict[str, Any]]:
        items = []
        url = self._delta_url()
        while url:
            response = self.client.get(url)
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("value", []):
                if "folder" in item:
                    continue
                if "deleted" in item:
                    self.deleted_origin_paths.append(f"onedrive:{item['id']}")
                else:
                    items.append(item)
            url = payload.get("@odata.nextLink")
            if not url:
                self.next_cursor = payload.get("@odata.deltaLink")
        return items

    def _download(self, item: dict[str, Any]) -> bytes:
        url = item.get("@microsoft.graph.downloadUrl")
        if url:
            response = self.client.get(url)
        else:
            drive = quote(self.drive_id, safe="")
            file_id = quote(item["id"], safe="")
            response = self.client.get(f"/drives/{drive}/items/{file_id}/content")
        response.raise_for_status()
        return response.content

    def scan(self) -> list[RawDocument]:
        documents = []
        errors = []
        for item in self._items():
            name = item.get("name", "")
            if Path(name).suffix.casefold() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                documents.append(
                    make_cloud_document(
                        kb_id=self.kb_id,
                        source_id=self.source_id,
                        provider="onedrive",
                        file_id=item["id"],
                        name=name,
                        data=self._download(item),
                        modified_at=self._timestamp(item.get("lastModifiedDateTime")),
                        metadata={
                            "name": name,
                            "web_url": item.get("webUrl"),
                            "mime_type": item.get("file", {}).get("mimeType"),
                        },
                    )
                )
            except Exception as exc:
                print(f"[ONEDRIVE] Failed to import '{name}': {exc}")
                errors.append(f"{name}: {exc}")
        if errors:
            raise RuntimeError("OneDrive sync could not import: " + "; ".join(errors))
        return documents

    def detect_changes(self, since: float) -> list[RawDocument]:
        return [item for item in self.scan() if item.source_modified_at > since]

    def detect_deletions(self, known_paths: set[str]) -> list[str]:
        return []

    def close(self):
        if self._owns_client:
            self.client.close()
