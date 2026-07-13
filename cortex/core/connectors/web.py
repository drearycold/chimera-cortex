import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .base import BaseConnector, RawDocument


class WebConnector(BaseConnector):
    def __init__(
        self,
        kb_id: int,
        source_id: int,
        config: dict[str, Any],
        crawl_runner: Callable[[dict[str, Any]], list[Any]] | None = None,
    ):
        self.kb_id = kb_id
        self.source_id = source_id
        self.config = config
        self.crawl_runner = crawl_runner

    @staticmethod
    def _markdown(result: Any) -> str:
        markdown = getattr(result, "markdown", "")
        if isinstance(markdown, str):
            return markdown.strip()
        return str(getattr(markdown, "raw_markdown", "")).strip()

    @staticmethod
    def _filename(url: str) -> str:
        parsed = urlsplit(url)
        path_name = parsed.path.strip("/").replace("/", "-")
        base = re.sub(r"[^a-zA-Z0-9._-]+", "-", path_name).strip("-.")
        if not base:
            base = parsed.netloc.replace(":", "-") or "page"
        suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        return f"{base[:120]}-{suffix}.md"

    def _document(self, result: Any) -> RawDocument | None:
        if not getattr(result, "success", True):
            return None
        markdown = self._markdown(result)
        if not markdown:
            return None
        url = str(getattr(result, "url", self.config["url"]))
        result_metadata = getattr(result, "metadata", {}) or {}
        title = str(result_metadata.get("title") or urlsplit(url).path or url)
        raw_bytes = markdown.encode("utf-8")
        return RawDocument(
            kb_id=self.kb_id,
            source_id=self.source_id,
            source_type="web",
            origin_path=url,
            filename=self._filename(url),
            title=title,
            format="md",
            raw_bytes=raw_bytes,
            content_markdown=markdown,
            content_hash=hashlib.sha256(raw_bytes).hexdigest(),
            source_modified_at=time.time(),
            metadata={
                "url": url,
                "title": title,
                "crawl_depth": int(result_metadata.get("depth", 0) or 0),
                "status_code": getattr(result, "status_code", None),
            },
        )

    async def _crawl(self) -> list[Any]:
        from crawl4ai import (  # type: ignore[import-untyped]
            AsyncWebCrawler,
            BrowserConfig,
            CacheMode,
            CrawlerRunConfig,
        )
        from crawl4ai.deep_crawling import (  # type: ignore[import-untyped]
            BFSDeepCrawlStrategy,
        )
        from crawl4ai.deep_crawling.filters import (  # type: ignore[import-untyped]
            FilterChain,
            URLPatternFilter,
        )

        max_depth = int(self.config.get("max_depth", 1))
        max_pages = int(self.config.get("max_pages", 25))
        filter_chain = None
        patterns = self.config.get("include_patterns") or []
        if patterns:
            filter_chain = FilterChain([URLPatternFilter(patterns=patterns)])
        deep_strategy = None
        if max_depth > 0:
            deep_strategy = BFSDeepCrawlStrategy(
                max_depth=max_depth,
                max_pages=max_pages,
                include_external=False,
                filter_chain=filter_chain,
            )
        run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            check_robots_txt=bool(self.config.get("respect_robots_txt", True)),
            deep_crawl_strategy=deep_strategy,
            excluded_tags=self.config.get("excluded_tags", ["nav", "footer"]),
            word_count_threshold=int(self.config.get("word_count_threshold", 10)),
            stream=False,
        )
        browser_config = BrowserConfig(headless=True, verbose=False)
        async with AsyncWebCrawler(config=browser_config) as crawler:
            results = await crawler.arun(url=self.config["url"], config=run_config)
        if isinstance(results, list):
            return results
        return [results]

    def scan(self) -> list[RawDocument]:
        results = (
            self.crawl_runner(self.config)
            if self.crawl_runner is not None
            else asyncio.run(self._crawl())
        )
        documents = []
        errors = []
        for result in results:
            document = self._document(result)
            if document is not None:
                documents.append(document)
                continue
            url = str(getattr(result, "url", self.config["url"]))
            detail = (
                str(getattr(result, "error_message", "crawl failed"))
                if not getattr(result, "success", True)
                else "crawl returned no Markdown"
            )
            errors.append(f"{url}: {detail}")
        if errors or not documents:
            detail = "; ".join(errors) or "crawl returned no pages"
            raise RuntimeError(f"Web crawl failed: {detail}")
        return documents

    def detect_changes(self, since: float) -> list[RawDocument]:
        # Most web pages do not expose reliable modification times; ingestion hashes
        # normalized Markdown and skips unchanged pages.
        return self.scan()

    def detect_deletions(self, known_paths: set[str]) -> list[str]:
        current_paths = {document.origin_path for document in self.scan()}
        return sorted(known_paths - current_paths)
