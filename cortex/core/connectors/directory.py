import fnmatch
import hashlib
from pathlib import Path
from typing import Callable

from .base import BaseConnector, RawDocument


class DirectoryConnector(BaseConnector):
    def __init__(
        self,
        kb_id: int,
        source_id: int,
        path: str,
        glob_patterns: list[str] | None = None,
    ):
        self.kb_id = kb_id
        self.source_id = source_id
        self.path = Path(path).expanduser().resolve()
        self.glob_patterns = glob_patterns or ["*.md"]

    def _name_matches(self, path: Path) -> bool:
        return any(
            fnmatch.fnmatch(path.name, pattern) for pattern in self.glob_patterns
        )

    def _matches(self, path: Path) -> bool:
        return path.is_file() and self._name_matches(path)

    def _document(self, path: Path) -> RawDocument:
        raw_bytes = path.read_bytes()
        content = raw_bytes.decode("utf-8")
        return RawDocument(
            kb_id=self.kb_id,
            source_id=self.source_id,
            source_type="directory",
            origin_path=str(path),
            filename=path.name,
            title=path.stem.replace("_", " "),
            format=path.suffix.lstrip(".").lower() or "txt",
            raw_bytes=raw_bytes,
            content_markdown=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            source_modified_at=path.stat().st_mtime,
            metadata={"relative_path": str(path.relative_to(self.path))},
        )

    def scan(self) -> list[RawDocument]:
        if not self.path.is_dir():
            raise FileNotFoundError(f"Source directory '{self.path}' does not exist.")
        paths = {
            candidate
            for pattern in self.glob_patterns
            for candidate in self.path.glob(pattern)
            if self._matches(candidate)
        }
        return [self._document(path) for path in sorted(paths)]

    def detect_changes(self, since: float) -> list[RawDocument]:
        return [
            document
            for document in self.scan()
            if document.source_modified_at > since
        ]

    def detect_deletions(self, known_paths: set[str]) -> list[str]:
        current_paths = {document.origin_path for document in self.scan()}
        return sorted(known_paths - current_paths)

    def watch(self, callback: Callable[[str, str], None]):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        connector = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                paths = [Path(event.src_path)]
                destination = getattr(event, "dest_path", None)
                if destination:
                    paths.append(Path(destination))
                for path in paths:
                    if connector._name_matches(path):
                        callback(event.event_type, str(path))
                        break

        observer = Observer()
        observer.schedule(Handler(), str(self.path), recursive=False)
        observer.start()
        return observer
