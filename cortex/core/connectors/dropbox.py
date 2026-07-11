import os
from datetime import timezone
from pathlib import Path
from typing import Any

from .base import BaseConnector, RawDocument
from .cloud_common import SUPPORTED_EXTENSIONS, make_cloud_document


class DropboxConnector(BaseConnector):
    def __init__(self, kb_id: int, source_id: int, config: dict[str, Any], client=None):
        self.kb_id = kb_id
        self.source_id = source_id
        self.config = config
        self.path = str(config.get("path", ""))
        self.is_full_snapshot = not bool(config.get("cursor"))
        self.next_cursor: str | None = None
        self.deleted_origin_paths: list[str] = []
        self.allow_empty = True
        if client is None:
            import dropbox  # type: ignore[import-untyped]

            token = os.environ[str(config["token_env"])]
            client = dropbox.Dropbox(token, timeout=60)
        self.client = client

    @staticmethod
    def _timestamp(value) -> float:
        if value is None:
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    def _result(self):
        cursor = self.config.get("cursor")
        if cursor:
            return self.client.files_list_folder_continue(cursor)
        return self.client.files_list_folder(
            self.path,
            recursive=bool(self.config.get("recursive", True)),
            include_deleted=False,
            include_non_downloadable_files=False,
        )

    @staticmethod
    def _is_deleted(entry) -> bool:
        return entry.__class__.__name__ == "DeletedMetadata"

    @staticmethod
    def _is_file(entry) -> bool:
        return entry.__class__.__name__ == "FileMetadata"

    def _entries(self) -> list[Any]:
        entries = []
        result = self._result()
        while True:
            for entry in result.entries:
                if self._is_deleted(entry):
                    self.deleted_origin_paths.append(f"dropbox:{entry.path_lower}")
                elif self._is_file(entry):
                    entries.append(entry)
            self.next_cursor = result.cursor
            if not result.has_more:
                break
            result = self.client.files_list_folder_continue(result.cursor)
        return entries

    def scan(self) -> list[RawDocument]:
        documents = []
        errors = []
        for entry in self._entries():
            name = entry.name
            if Path(name).suffix.casefold() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                _, response = self.client.files_download(entry.path_lower)
                documents.append(
                    make_cloud_document(
                        kb_id=self.kb_id,
                        source_id=self.source_id,
                        provider="dropbox",
                        file_id=entry.path_lower,
                        name=name,
                        data=response.content,
                        modified_at=self._timestamp(entry.server_modified),
                        metadata={
                            "name": name,
                            "path": entry.path_display,
                            "rev": entry.rev,
                        },
                    )
                )
            except Exception as exc:
                print(f"[DROPBOX] Failed to import '{name}': {exc}")
                errors.append(f"{name}: {exc}")
        if errors:
            raise RuntimeError("Dropbox sync could not import: " + "; ".join(errors))
        return documents

    def detect_changes(self, since: float) -> list[RawDocument]:
        return [item for item in self.scan() if item.source_modified_at > since]

    def detect_deletions(self, known_paths: set[str]) -> list[str]:
        return []

    def close(self):
        close = getattr(self.client, "close", None)
        if close is not None:
            close()
