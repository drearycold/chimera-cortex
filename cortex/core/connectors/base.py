from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RawDocument:
    kb_id: int
    source_id: int
    source_type: str
    origin_path: str
    filename: str
    title: str
    format: str
    raw_bytes: bytes
    content_markdown: str
    content_hash: str
    source_modified_at: float
    metadata: dict
    external_id: str = ""
    source_key: str = ""
    segments: list[dict[str, Any]] | None = None


class BaseConnector(ABC):
    @abstractmethod
    def scan(self) -> list[RawDocument]:
        """Return every document currently available from the source."""

    @abstractmethod
    def detect_changes(self, since: float) -> list[RawDocument]:
        """Return documents modified after the supplied Unix timestamp."""

    @abstractmethod
    def detect_deletions(self, known_paths: set[str]) -> list[str]:
        """Return known origin paths that no longer exist."""
