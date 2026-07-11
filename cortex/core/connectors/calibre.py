import hashlib
import html
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from .base import BaseConnector, RawDocument


class CalibreConnector(BaseConnector):
    """Read books through Calibre's Content Server without filesystem access."""

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
        self.base_url = str(config["base_url"]).rstrip("/")
        self.library_id = str(config["library_id"])
        self.preferred_formats = [
            str(value).upper()
            for value in config.get("preferred_formats", ["EPUB", "PDF", "TXT", "MD"])
        ]
        self.page_size = min(1000, max(1, int(config.get("page_size", 500))))
        self.max_books = min(100000, max(1, int(config.get("max_books", 10000))))
        self.query = str(config.get("query", ""))
        self.source_key = str(config.get("source_key") or self._default_source_key())
        self._owns_client = client is None
        self.client = client or httpx.Client(
            auth=self._auth(),
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    def _default_source_key(self) -> str:
        identity = f"{self.base_url}\n{self.library_id}".encode("utf-8")
        return f"calibre-{hashlib.sha256(identity).hexdigest()[:20]}"

    def _auth(self):
        username = str(self.config.get("username", ""))
        password = str(self.config.get("password", ""))
        password_env = str(self.config.get("password_env", ""))
        if password_env:
            password = os.getenv(password_env, "")
        if not username:
            return None
        auth_type = str(self.config.get("auth_type", "digest")).casefold()
        if auth_type == "basic":
            return httpx.BasicAuth(username, password)
        return httpx.DigestAuth(username, password)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.client.get(self._url(path), params=params)
        response.raise_for_status()
        return response.json()

    def _book_ids(self) -> list[int]:
        library = quote(self.library_id, safe="")
        ids: list[int] = []
        offset = 0
        while len(ids) < self.max_books:
            payload = self._get_json(
                f"ajax/search/{library}",
                {
                    "query": self.query,
                    "offset": offset,
                    "num": min(self.page_size, self.max_books - len(ids)),
                    "sort": "last_modified",
                    "sort_order": "asc",
                },
            )
            page = [int(value) for value in payload.get("book_ids", [])]
            ids.extend(page)
            offset += len(page)
            total = int(payload.get("total_num", payload.get("num", offset)))
            if not page or offset >= total:
                break
        return ids[: self.max_books]

    def _metadata(self, book_ids: list[int]) -> list[dict[str, Any]]:
        library = quote(self.library_id, safe="")
        books = []
        for start in range(0, len(book_ids), 100):
            batch = book_ids[start : start + 100]
            payload = self._get_json(
                f"ajax/books/{library}",
                {"ids": ",".join(str(value) for value in batch)},
            )
            for book_id in batch:
                book = payload.get(str(book_id))
                if isinstance(book, dict):
                    book["id"] = book_id
                    books.append(book)
        return books

    @staticmethod
    def _html_to_text(value: bytes | str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()
        return "\n\n".join(
            text for text in (block.get_text(" ", strip=True) for block in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre"]))
            if text
        )

    def _extract_epub(self, data: bytes) -> list[dict[str, Any]]:
        import ebooklib  # type: ignore[import-untyped]
        from ebooklib import epub  # type: ignore[import-untyped]

        with tempfile.NamedTemporaryFile(suffix=".epub") as file:
            file.write(data)
            file.flush()
            book = epub.read_epub(file.name, options={"ignore_ncx": True})
        segments = []
        ordinal = 0
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            text = self._html_to_text(item.get_body_content())
            if not text:
                continue
            heading = Path(item.get_name()).stem
            segments.append(
                {
                    "ordinal": ordinal,
                    "locator": {"type": "epub_href", "value": item.get_name()},
                    "heading": heading,
                    "text": text,
                }
            )
            ordinal += 1
        return segments

    @staticmethod
    def _extract_pdf(data: bytes) -> list[dict[str, Any]]:
        import pymupdf

        segments = []
        with pymupdf.open(stream=data, filetype="pdf") as document:
            for index, page in enumerate(document):
                text = page.get_text("text").strip()
                if text:
                    segments.append(
                        {
                            "ordinal": index,
                            "locator": {"type": "pdf_page", "value": str(index + 1)},
                            "heading": f"Page {index + 1}",
                            "text": text,
                        }
                    )
        return segments

    def _extract(self, data: bytes, file_format: str) -> list[dict[str, Any]]:
        if file_format == "EPUB":
            return self._extract_epub(data)
        if file_format == "PDF":
            return self._extract_pdf(data)
        text = data.decode("utf-8", errors="replace").strip()
        return [
            {
                "ordinal": 0,
                "locator": {"type": "text", "value": "0"},
                "heading": "Text",
                "text": text,
            }
        ] if text else []

    @staticmethod
    def _timestamp(value: Any) -> float:
        if not value:
            return 0.0
        normalized = str(value).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _metadata_header(book: dict[str, Any]) -> str:
        fields = [
            ("Authors", ", ".join(book.get("authors", []))),
            ("Series", book.get("series")),
            ("Publisher", book.get("publisher")),
            ("Tags", ", ".join(book.get("tags", []))),
        ]
        lines = [f"# {book.get('title') or 'Untitled'}"]
        for label, value in fields:
            if value:
                lines.append(f"**{label}:** {html.escape(str(value))}")
        comments = book.get("comments")
        if comments:
            lines.extend(["", "## Description", "", CalibreConnector._html_to_text(comments)])
        return "\n\n".join(lines)

    def _document(self, book: dict[str, Any]) -> RawDocument | None:
        available = {str(value).upper() for value in book.get("formats", [])}
        file_format = next(
            (value for value in self.preferred_formats if value in available),
            None,
        )
        if file_format is None:
            return None
        library = quote(self.library_id, safe="")
        response = self.client.get(
            self._url(f"get/{quote(file_format, safe='')}/{book['id']}/{library}")
        )
        response.raise_for_status()
        segments = self._extract(response.content, file_format)
        if not segments:
            return None
        header = self._metadata_header(book)
        content = header + "\n\n" + "\n\n".join(
            f"## {segment['heading']}\n\n{segment['text']}" for segment in segments
        )
        raw_bytes = content.encode("utf-8")
        external_id = f"{self.source_key}:{book['id']}"
        metadata = {
            "book_id": book["id"],
            "uuid": book.get("uuid"),
            "authors": book.get("authors", []),
            "tags": book.get("tags", []),
            "series": book.get("series"),
            "series_index": book.get("series_index"),
            "publisher": book.get("publisher"),
            "identifiers": book.get("identifiers", {}),
            "source_format": file_format.lower(),
        }
        title = str(book.get("title") or f"Book {book['id']}")
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()[:80]
        return RawDocument(
            kb_id=self.kb_id,
            source_id=self.source_id,
            source_type="calibre",
            origin_path=f"{self.base_url}/get/{file_format}/{book['id']}/{self.library_id}",
            filename=f"calibre-{book['id']}-{slug or 'book'}.md",
            title=title,
            format=file_format.lower(),
            raw_bytes=raw_bytes,
            content_markdown=content,
            content_hash=hashlib.sha256(raw_bytes).hexdigest(),
            source_modified_at=self._timestamp(book.get("last_modified")),
            metadata=metadata,
            external_id=external_id,
            source_key=self.source_key,
            segments=segments,
        )

    def scan(self) -> list[RawDocument]:
        documents = []
        errors = []
        for book in self._metadata(self._book_ids()):
            try:
                document = self._document(book)
            except Exception as exc:
                print(f"[CALIBRE] Failed to import book {book.get('id')}: {exc}")
                errors.append(f"{book.get('id')}: {exc}")
                continue
            if document is not None:
                documents.append(document)
        if errors:
            raise RuntimeError("Calibre sync could not import books: " + "; ".join(errors))
        return documents

    def detect_changes(self, since: float) -> list[RawDocument]:
        return [item for item in self.scan() if item.source_modified_at > since]

    def detect_deletions(self, known_paths: set[str]) -> list[str]:
        current = {item.origin_path for item in self.scan()}
        return sorted(known_paths - current)

    def close(self):
        if self._owns_client:
            self.client.close()
