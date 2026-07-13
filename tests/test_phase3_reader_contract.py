import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import app
from cortex.api.external_documents import ExternalDocument
from cortex.api.chat import (
    ChatRequest,
    ChatResponse,
    _run_chat,
    build_chat_cache_key,
    build_search_payload,
    build_text_search_query,
    search_infinity,
)
from cortex.api.sources import _validate_source_config
from cortex.core.connectors import CalibreConnector
from cortex.core.external_documents import build_segment_chunks
from cortex.core.rag import (
    RetrievalBackendError,
    build_context_windows,
    build_retrieval_filter_expression,
    fetch_and_merge_chunk_range,
    merge_chunk_contents,
)

FIXTURES = Path(__file__).parent / "fixtures"
client = TestClient(app)


class PhaseThreeReaderContractTests(unittest.TestCase):
    def test_reader_contract_fixtures_validate(self):
        reader_fixtures = FIXTURES / "reader_contract"
        capped = json.loads((reader_fixtures / "chat_capped_request.json").read_text())
        empty = json.loads((reader_fixtures / "chat_empty_scope_request.json").read_text())
        response = json.loads((reader_fixtures / "chat_response.json").read_text())

        self.assertEqual(126, ChatRequest.model_validate(capped).retrieval_filter.documents[0].max_ordinal)
        self.assertEqual("installation", ChatRequest.model_validate(capped).retrieval_query)
        self.assertEqual("zh-CN", ChatRequest.model_validate(capped).response_locale)
        self.assertEqual([], ChatRequest.model_validate(empty).retrieval_filter.documents)
        self.assertEqual(
            "opaque-book-a",
            ChatResponse.model_validate(response).citations[0].external_id,
        )

    def test_versioned_reader_contract_publishes_json_schemas(self):
        response = client.get("/api/contracts/reader-qa/v1")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("1", payload["version"])
        self.assertEqual(
            {"chat_request", "chat_response", "external_document"},
            set(payload["schemas"]),
        )
        request_properties = payload["schemas"]["chat_request"]["properties"]
        self.assertIn("retrieval_query", request_properties)
        self.assertIn("response_locale", request_properties)
        self.assertIn("retrieval_query", payload["semantics"])
        self.assertIn("response_locale", payload["semantics"])
        self.assertIn("query_only_compatibility", payload["semantics"])
        self.assertIn("locale_omission", payload["semantics"])
        self.assertIn("reader_selection_target", payload["semantics"])
        self.assertIn("context_compaction", payload["semantics"])

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

    def test_context_windows_merge_overlapping_and_contiguous_hits(self):
        contexts = [
            {"document_id": 423, "chunk_index": 8, "distance": 0.96},
            {"document_id": 423, "chunk_index": 10, "distance": 0.20},
            {"document_id": 423, "chunk_index": 11, "distance": 0.10},
            {"document_id": 7, "chunk_index": 0, "distance": 0.80},
            {"document_id": 423, "chunk_index": 30, "distance": 0.70},
        ]

        windows = build_context_windows(contexts, context_window=1)

        self.assertEqual(
            [(7, 0, 1), (423, 7, 12), (423, 29, 31)],
            [
                (window["document_id"], window["start"], window["end"])
                for window in windows
            ],
        )
        self.assertEqual(
            [8, 10, 11],
            [match["chunk_index"] for match in windows[1]["matches"]],
        )

    def test_chunk_merge_removes_only_verified_ellipsis_overlap(self):
        verified = merge_chunk_contents(
            [
                "Installation ends when you configure calibre.",
                "... configure calibre.\n\n[Guide - Next] Choose a device.",
            ]
        )
        unverified = merge_chunk_contents(
            [
                "Installation ends here.",
                "... configure calibre.\n\n[Guide - Next] Choose a device.",
            ]
        )

        self.assertEqual(1, verified.count("configure calibre."))
        self.assertIn("[Guide - Next] Choose a device.", verified)
        self.assertIn("... configure calibre.", unverified)

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

    def test_explicit_empty_scope_fails_closed(self):
        self.assertIsNone(build_retrieval_filter_expression(None))
        expression = build_retrieval_filter_expression(
            {"documents": [], "source_keys": []}
        )

        self.assertEqual("(document_id = -1)", expression)
        payload = build_search_payload("question", [0.1, 0.2], expression)
        self.assertEqual(expression, payload["filter"])

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
        retrieval = build_chat_cache_key(
            "reader",
            "question",
            {"documents": [{"external_id": "opaque-a", "max_ordinal": 126}]},
            [],
            10,
            retrieval_query="installation",
        )
        locale = build_chat_cache_key(
            "reader",
            "question",
            {"documents": [{"external_id": "opaque-a", "max_ordinal": 126}]},
            [],
            10,
            retrieval_query="installation",
            response_locale="zh-CN",
        )
        generation = build_chat_cache_key(
            "reader",
            "question",
            {"documents": [{"external_id": "opaque-a", "max_ordinal": 126}]},
            [],
            10,
            generation_config={"model": "new-generation-model"},
        )
        self.assertEqual(
            6,
            len({capped, uncapped, external, retrieval, locale, generation}),
        )

        version_two_identity = json.dumps(
            {
                "cache_schema_version": 2,
                "query": "question",
                "retrieval_query": None,
                "response_locale": None,
                "retrieval_filter": {
                    "documents": [{"external_id": "opaque-a", "max_ordinal": 126}]
                },
                "external_contexts": [],
                "top_k": 10,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        version_two_digest = hashlib.sha256(
            version_two_identity.encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(version_two_digest, capped.rsplit(":", 1)[1])

    def test_text_search_escapes_infinity_query_syntax(self):
        query = "Explain in en-CN.\n\nSelected text:\ninstallation (desktop)"

        escaped = build_text_search_query(query)
        payload = build_search_payload(query, [0.1, 0.2], None)

        self.assertEqual(
            r"Explain in en\-CN. Selected text\: installation \(desktop\)",
            escaped,
        )
        self.assertEqual(escaped, payload["search"][1]["matching_text"])

    def test_response_locale_requires_bcp47_tag(self):
        self.assertEqual(
            "zh-CN",
            ChatRequest(query="Explain", response_locale="zh_CN").response_locale,
        )
        with self.assertRaises(ValidationError):
            ChatRequest(query="Explain", response_locale="Chinese (Simplified)")

    @patch("cortex.api.chat.httpx.post")
    @patch("cortex.api.chat.search_infinity", return_value=[])
    @patch("cortex.api.chat.get_embedding", return_value=[0.1, 0.2])
    @patch("cortex.api.chat.get_mysql_connection")
    @patch("cortex.api.chat.get_redis_client", side_effect=RuntimeError("offline"))
    def test_structured_reader_query_separates_retrieval_and_generation(
        self,
        _redis_mock,
        mysql_mock,
        embedding_mock,
        search_mock,
        generation_mock,
    ):
        mysql_mock.return_value.cursor.return_value = Mock()
        generation_response = Mock()
        generation_response.json.return_value = {"response": "安装说明"}
        generation_mock.return_value = generation_response
        knowledge_base = {
            "slug": "dsreader-default",
            "vector_table": "chunks_dsreader_default",
            "ingest_config": {
                "embedding": {"model": "test-embedding"},
                "search": {"context_window": 0},
            },
            "generation_config": {
                "model": "test-generation",
                "temperature": 0.0,
                "max_tokens": 64,
                "top_k_contexts": 5,
                "query_rewrite": {"enabled": False},
                "reranker": {"enabled": False},
            },
        }
        request = ChatRequest(
            query="Explain the selected text clearly.",
            retrieval_query="installation",
            response_locale="zh-CN",
            external_contexts=[
                {"kind": "reader_selection", "text": "installation"}
            ],
        )

        result = _run_chat(request, knowledge_base)

        embedding_mock.assert_called_once_with(
            "installation",
            is_query=True,
            model="test-embedding",
        )
        search_payload = search_mock.call_args.args[1]
        self.assertEqual("installation", search_payload["search"][1]["matching_text"])
        prompt = generation_mock.call_args.kwargs["json"]["prompt"]
        self.assertIn("Task: Explain the selected text clearly.", prompt)
        self.assertIn('Target selection (verbatim data): "installation"', prompt)
        self.assertNotIn('"kind": "reader_selection"', prompt)
        self.assertIn("Never substitute a book, product, chapter", prompt)
        self.assertIn("BCP 47 locale 'zh-CN'", prompt)
        self.assertEqual("installation", result["audit"]["retrieval_query"])
        self.assertEqual("zh-CN", result["audit"]["response_locale"])

        embedding_mock.reset_mock()
        search_mock.reset_mock()
        generation_mock.reset_mock()

        legacy_result = _run_chat(ChatRequest(query="installation"), knowledge_base)

        embedding_mock.assert_called_once_with(
            "installation",
            is_query=True,
            model="test-embedding",
        )
        legacy_payload = search_mock.call_args.args[1]
        self.assertEqual("installation", legacy_payload["search"][1]["matching_text"])
        legacy_prompt = generation_mock.call_args.kwargs["json"]["prompt"]
        self.assertIn("User Question: installation", legacy_prompt)
        self.assertNotIn("BCP 47 locale", legacy_prompt)
        self.assertEqual("installation", legacy_result["audit"]["retrieval_query"])
        self.assertIsNone(legacy_result["audit"]["response_locale"])

    @patch("cortex.api.chat.fetch_and_merge_chunk_range", return_value="Expanded evidence")
    @patch("cortex.api.chat.httpx.post")
    @patch("cortex.api.chat.search_infinity")
    @patch("cortex.api.chat.get_embedding", return_value=[0.1, 0.2])
    @patch("cortex.api.chat.get_mysql_connection")
    @patch("cortex.api.chat.get_redis_client")
    def test_chat_compacts_nearby_hits_into_one_cached_evidence_window(
        self,
        redis_mock,
        mysql_mock,
        _embedding_mock,
        search_mock,
        generation_mock,
        expansion_mock,
    ):
        redis_client = Mock()
        redis_client.get.return_value = None
        redis_mock.return_value = redis_client
        cursor = mysql_mock.return_value.cursor.return_value
        cursor.fetchone.return_value = ("guide.json",)
        search_mock.return_value = [
            {
                "document_id": 423,
                "chunk_index": chunk_index,
                "content": f"Child {chunk_index}",
                "document_title": "Quick Start Guide",
                "external_id": "book-1",
                "source_key": "library-1",
                "segment_ordinal": 4 if chunk_index < 10 else 5,
                "segment_locator": json.dumps(
                    {"type": "epub_href", "value": f"chapter-{chunk_index // 10}"}
                ),
                "SCORE": 1.0 - ((chunk_index - 8) * 0.1),
            }
            for chunk_index in (8, 9, 10, 11)
        ]
        generation_response = Mock()
        generation_response.json.return_value = {"response": "Grounded answer"}
        generation_mock.return_value = generation_response
        knowledge_base = {
            "slug": "dsreader-default",
            "vector_table": "chunks_dsreader_default",
            "ingest_config": {
                "embedding": {"model": "test-embedding"},
                "search": {"context_window": 1},
            },
            "generation_config": {
                "model": "test-generation",
                "temperature": 0.0,
                "max_tokens": 64,
                "top_k_contexts": 4,
                "query_rewrite": {"enabled": False},
                "reranker": {"enabled": False},
            },
        }

        result = _run_chat(ChatRequest(query="installation processes"), knowledge_base)

        expansion_mock.assert_called_once_with(
            423,
            7,
            12,
            vector_table="chunks_dsreader_default",
            retrieval_filter=None,
        )
        self.assertEqual(1, len(result["contexts"]))
        context = result["contexts"][0]
        self.assertEqual(8, context["chunk_index"])
        self.assertEqual(
            (7, 12),
            (context["window_start_chunk"], context["window_end_chunk"]),
        )
        self.assertEqual(
            [8, 9, 10, 11],
            [match["chunk_index"] for match in context["matched_chunks"]],
        )
        self.assertEqual(2, len(result["citations"]))
        self.assertEqual(1, len(result["audit"]["second_stage_candidates"]))
        prompt = generation_mock.call_args.kwargs["json"]["prompt"]
        self.assertEqual(1, prompt.count("Expanded evidence"))

        cached_payload = json.loads(redis_client.setex.call_args.args[2])
        self.assertEqual(1, len(cached_payload["contexts"]))
        self.assertEqual(1, len(cached_payload["audit"]["second_stage_candidates"]))

    @patch("cortex.api.chat.httpx.request")
    def test_indexed_content_is_recalled_with_and_without_filter(self, request_mock):
        indexed_row = [
            {"document_id": 423},
            {"chunk_index": 8},
            {"content": "The installation process starts by downloading calibre."},
            {"external_id": "dsr-book-24653c"},
            {"SCORE": 1.0},
        ]
        request_mock.return_value = httpx.Response(
            200,
            json={"error_code": 0, "output": [indexed_row]},
            request=httpx.Request("GET", "http://infinity.test/docs"),
        )

        for expression in (None, "external_id = 'dsr-book-24653c'"):
            payload = build_search_payload(
                "installation calibre",
                [0.1, 0.2],
                expression,
            )
            results = search_infinity("chunks_dsreader_default", payload)

            self.assertEqual([indexed_row], results)
            self.assertFalse(request_mock.call_args.kwargs["trust_env"])
            if expression:
                self.assertEqual(expression, request_mock.call_args.kwargs["json"]["filter"])

    @patch("cortex.api.chat.httpx.request")
    def test_infinity_application_error_is_not_an_empty_result(self, request_mock):
        request_mock.return_value = httpx.Response(
            200,
            json={
                "error_code": 3001,
                "error_msg": "dense executor unavailable",
            },
            request=httpx.Request("GET", "http://infinity.test/docs"),
        )

        with self.assertRaisesRegex(RetrievalBackendError, "dense executor unavailable"):
            search_infinity(
                "chunks_dsreader_default",
                build_search_payload("installation", [0.1, 0.2], None),
            )

    @patch("cortex.api.chat.httpx.request")
    def test_infinity_http_error_surfaces_backend_message(self, request_mock):
        request = httpx.Request("GET", "http://infinity.test/docs")
        request_mock.return_value = httpx.Response(
            500,
            json={
                "error_code": 3013,
                "error_msg": 'Column "text" index does not exist',
            },
            request=request,
        )

        with self.assertRaisesRegex(RetrievalBackendError, "Column .* does not exist"):
            search_infinity(
                "chunks_dsreader_default",
                build_search_payload("Selected text: installation", [0.1], None),
            )

    @patch("cortex.api.chat.get_mysql_connection")
    @patch("cortex.api.chat.get_redis_client")
    @patch("cortex.api.chat.get_embedding", return_value=[0.1, 0.2])
    @patch("cortex.api.chat.httpx.request", side_effect=httpx.ReadTimeout("timed out"))
    @patch("cortex.api.chat.httpx.post")
    def test_infinity_timeout_returns_retrieval_error_without_generation(
        self,
        generation_post_mock,
        _request_mock,
        _embedding_mock,
        redis_mock,
        mysql_mock,
    ):
        redis_mock.side_effect = RuntimeError("Redis unavailable")
        mysql_mock.return_value.cursor.return_value = Mock()
        knowledge_base = {
            "slug": "dsreader-default",
            "vector_table": "chunks_dsreader_default",
            "ingest_config": {
                "embedding": {"model": "test-embedding"},
                "search": {"context_window": 0},
            },
            "generation_config": {
                "model": "test-generation",
                "temperature": 0.0,
                "max_tokens": 64,
                "top_k_contexts": 5,
                "query_rewrite": {"enabled": False},
                "reranker": {"enabled": False},
            },
        }

        with self.assertRaises(HTTPException) as raised:
            _run_chat(ChatRequest(query="installation calibre"), knowledge_base)

        self.assertEqual(503, raised.exception.status_code)
        self.assertIn("Retrieval backend error", raised.exception.detail)
        generation_post_mock.assert_not_called()

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
        self.assertFalse(request_mock.call_args.kwargs["trust_env"])

    @patch("cortex.core.rag.httpx.request", side_effect=httpx.ReadTimeout("timed out"))
    def test_adjacent_expansion_timeout_is_not_an_empty_result(self, _request_mock):
        with self.assertRaisesRegex(
            RetrievalBackendError,
            "adjacent chunk retrieval failed",
        ):
            fetch_and_merge_chunk_range(
                423,
                7,
                9,
                vector_table="chunks_dsreader_default",
            )

    @patch("cortex.core.rag.httpx.request")
    def test_adjacent_expansion_application_error_is_not_empty(self, request_mock):
        response = Mock()
        response.json.return_value = {
            "error_code": 3001,
            "error_msg": "filter executor unavailable",
        }
        request_mock.return_value = response

        with self.assertRaisesRegex(RetrievalBackendError, "filter executor unavailable"):
            fetch_and_merge_chunk_range(
                423,
                7,
                9,
                vector_table="chunks_dsreader_default",
            )


if __name__ == "__main__":
    unittest.main()
