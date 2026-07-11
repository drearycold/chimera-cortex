import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from cortex.api.external_documents import ExternalDocument
from cortex.api.chat import build_chat_cache_key, build_search_payload
from cortex.api.sources import _validate_source_config
from cortex.core.connectors import CalibreConnector
from cortex.core.external_documents import build_segment_chunks
from cortex.core.rag import (
    build_retrieval_filter_expression,
    fetch_and_merge_chunk_range,
)

FIXTURES = Path(__file__).parent / "fixtures"


class PhaseThreeReaderContractTests(unittest.TestCase):
    def test_calibre_connector_uses_content_server_routes(self):
        search = (FIXTURES / "calibre_content_server/search.json").read_bytes()
        books = (FIXTURES / "calibre_content_server/books.json").read_bytes()
        requested_paths = []

        def handler(request: httpx.Request):
            requested_paths.append(request.url.path)
            if request.url.path == "/ajax/search/main":
                return httpx.Response(200, content=search, request=request)
            if request.url.path == "/ajax/books/main":
                return httpx.Response(200, content=books, request=request)
            if request.url.path == "/get/TXT/42/main":
                return httpx.Response(
                    200,
                    content=b"Evidence comes from the Content Server.",
                    request=request,
                )
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        connector = CalibreConnector(
            1,
            2,
            {
                "base_url": "http://calibre.test",
                "library_id": "main",
                "source_key": "opaque-main",
                "preferred_formats": ["TXT"],
            },
            client=client,
        )

        documents = connector.scan()

        self.assertEqual(1, len(documents))
        document = documents[0]
        self.assertEqual("opaque-main:42", document.external_id)
        self.assertEqual("opaque-main", document.source_key)
        self.assertEqual(0, document.segments[0]["ordinal"])
        self.assertIn("Content Server", document.content_markdown)
        self.assertEqual(
            ["/ajax/search/main", "/ajax/books/main", "/get/TXT/42/main"],
            requested_paths,
        )

    def test_calibre_source_validation_forbids_filesystem_and_plain_password(self):
        with self.assertRaises(HTTPException):
            _validate_source_config(
                "calibre",
                {"library_path": "/books"},
                "manual",
                None,
            )
        with self.assertRaises(HTTPException):
            _validate_source_config(
                "calibre",
                {
                    "base_url": "http://calibre.test",
                    "library_id": "main",
                    "username": "reader",
                    "password": "secret",
                },
                "manual",
                None,
            )
        _validate_source_config(
            "calibre",
            {
                "base_url": "https://calibre.test",
                "library_id": "main",
                "username": "reader",
                "password_env": "CALIBRE_PASSWORD",
                "auth_type": "basic",
            },
            "manual",
            None,
        )

    def test_external_contract_preserves_monotonic_segment_ordinals(self):
        payload = json.loads((FIXTURES / "external_document.json").read_text())
        document = ExternalDocument.model_validate(payload)

        chunks = build_segment_chunks(
            document.title,
            [segment.model_dump() for segment in document.segments],
            max_chars=600,
            overlap_chars=120,
        )

        self.assertEqual([120, 127], [chunk["segment_ordinal"] for chunk in chunks])
        self.assertIn("epub_cfi", chunks[0]["segment_locator"])

        payload["segments"].reverse()
        with self.assertRaises(ValidationError):
            ExternalDocument.model_validate(payload)

    def test_filter_expression_applies_per_document_caps_and_source_union(self):
        expression = build_retrieval_filter_expression(
            {
                "documents": [
                    {"external_id": "opaque-a", "max_ordinal": 126},
                    {"external_id": "opaque-b", "max_ordinal": None},
                ],
                "source_keys": ["opaque-library"],
            }
        )

        self.assertIn("external_id = 'opaque-a' AND segment_ordinal <= 126", expression)
        self.assertIn("external_id = 'opaque-b'", expression)
        self.assertIn("source_key IN ('opaque-library')", expression)
        self.assertIn(" OR ", expression)

    def test_hybrid_search_and_cache_identity_include_scope(self):
        expression = "(external_id = 'opaque-a' AND segment_ordinal <= 126)"
        payload = build_search_payload("question", [0.1, 0.2], expression)

        self.assertEqual(expression, payload["filter"])
        self.assertEqual(
            ["dense", "text", "rrf"],
            [
                item.get("match_method") or item.get("fusion_method")
                for item in payload["search"]
            ],
        )
        capped = build_chat_cache_key(
            "reader",
            "question",
            {"documents": [{"external_id": "opaque-a", "max_ordinal": 126}]},
            [],
            10,
        )
        uncapped = build_chat_cache_key(
            "reader",
            "question",
            {"documents": [{"external_id": "opaque-a", "max_ordinal": None}]},
            [],
            10,
        )
        external = build_chat_cache_key(
            "reader",
            "question",
            {"documents": [{"external_id": "opaque-a", "max_ordinal": 126}]},
            [{"text": "dictionary evidence"}],
            10,
        )
        self.assertEqual(3, len({capped, uncapped, external}))

    @patch("cortex.core.rag.httpx.request")
    def test_adjacent_expansion_reuses_retrieval_filter(self, request_mock):
        response = Mock()
        response.json.return_value = {"error_code": 0, "output": []}
        request_mock.return_value = response
        retrieval_filter = "(external_id = 'opaque-a' AND segment_ordinal <= 126)"

        fetch_and_merge_chunk_range(
            7,
            2,
            4,
            vector_table="chunks_reader",
            retrieval_filter=retrieval_filter,
        )

        sent_filter = request_mock.call_args.kwargs["json"]["filter"]
        self.assertIn("document_id = 7", sent_filter)
        self.assertIn(retrieval_filter, sent_filter)


if __name__ == "__main__":
    unittest.main()
