import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from cortex.api.chat import ChatResponse
from cortex.core.cache_management import (
    clear_cache,
    delete_cache_entry,
    get_cache_entry_detail,
    get_global_cache_stats,
    get_knowledge_base_cache,
)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def get(self, key):
        self.commands.append(("get", key))

    def ttl(self, key):
        self.commands.append(("ttl", key))

    def memory_usage(self, key):
        self.commands.append(("memory", key))

    def execute(self):
        values = []
        for command, key in self.commands:
            encoded = key.encode() if isinstance(key, str) else key
            item = self.redis.values.get(encoded)
            if command == "get":
                values.append(item[0] if item else None)
            elif command == "ttl":
                values.append(item[1] if item else -2)
            else:
                values.append(item[2] if item else None)
        return values


class FakeRedis:
    def __init__(self, values):
        self.values = values

    def scan_iter(self, pattern):
        prefix = pattern.removesuffix("*")
        return iter(key for key in self.values if key.decode().startswith(prefix))

    def pipeline(self, transaction=False):
        self.transaction = transaction
        return FakePipeline(self)

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            encoded = key.encode() if isinstance(key, str) else key
            if encoded in self.values:
                deleted += 1
                del self.values[encoded]
        return deleted


def cache_value(query, created_at=None):
    payload = {"audit": {"retrieval_query": f"retrieval: {query}"}}
    if created_at:
        payload["_cache_meta"] = {"query": query, "created_at": created_at}
    return json.dumps(payload).encode()


class CacheManagementTests(unittest.TestCase):
    def setUp(self):
        self.digest_a = "a" * 64
        self.digest_b = "b" * 64
        self.redis = FakeRedis(
            {
                f"rag_cache:alpha:{self.digest_a}".encode(): (
                    cache_value("Alpha question", "2026-07-13T00:00:00+00:00"),
                    240,
                    128,
                ),
                f"rag_cache:beta:{self.digest_b}".encode(): (
                    cache_value("Beta legacy"),
                    1800,
                    256,
                ),
                b"unrelated:key": (b"keep", -1, 20),
            }
        )

    @patch("cortex.core.cache_management.get_redis_client")
    def test_stats_batch_entries_and_legacy_query_fallback(self, get_redis):
        get_redis.return_value = self.redis

        stats = get_global_cache_stats()
        page = get_knowledge_base_cache("beta", 0, 50)

        self.assertEqual(2, stats["entry_count"])
        self.assertEqual(384, stats["size_bytes"])
        self.assertEqual(1, stats["expiring_soon_count"])
        self.assertEqual("retrieval: Beta legacy", page["entries"][0]["query"])
        self.assertIsNotNone(page["entries"][0]["created_at"])
        self.assertFalse(self.redis.transaction)

    @patch("cortex.core.cache_management.get_redis_client")
    def test_pagination_and_expired_race(self, get_redis):
        expired_key = f"rag_cache:alpha:{'c' * 64}".encode()
        self.redis.values[expired_key] = (None, -2, None)
        get_redis.return_value = self.redis

        page = get_knowledge_base_cache("alpha", 1, 50)

        self.assertEqual(1, page["summary"]["entry_count"])
        self.assertEqual([], page["entries"])

    @patch("cortex.core.cache_management.get_redis_client")
    def test_query_search_filters_before_pagination_and_is_case_insensitive(
        self, get_redis
    ):
        second_digest = "c" * 64
        self.redis.values[f"rag_cache:alpha:{second_digest}".encode()] = (
            cache_value("Another ALPHA Question", "2026-07-13T00:01:00+00:00"),
            300,
            100,
        )
        get_redis.return_value = self.redis

        page = get_knowledge_base_cache("alpha", 1, 1, "alpha QUESTION")
        unfiltered = get_knowledge_base_cache("alpha", 0, 1, "   ")

        self.assertEqual(2, page["summary"]["entry_count"])
        self.assertEqual(2, page["filtered_count"])
        self.assertEqual(1, len(page["entries"]))
        self.assertEqual(2, unfiltered["filtered_count"])

    @patch("cortex.core.cache_management.get_redis_client")
    def test_cache_detail_whitelists_response_fields(self, get_redis):
        payload = {
            "answer": "Detailed answer",
            "contexts": [{"filename": "guide.md", "content": "Evidence"}],
            "citations": [{"title": "Guide", "external_id": "doc-1"}],
            "external_contexts": [{"secret": "hidden"}],
            "audit": {
                "timings_ms": {"total": 12.5},
                "llm_prompt": "hidden prompt",
                "first_stage_candidates": [{"content": "hidden"}],
            },
            "_cache_meta": {
                "query": "Original query",
                "created_at": "2026-07-13T00:00:00+00:00",
            },
        }
        self.redis.values[f"rag_cache:alpha:{self.digest_a}".encode()] = (
            json.dumps(payload).encode(),
            200,
            512,
        )
        get_redis.return_value = self.redis

        detail = get_cache_entry_detail("alpha", self.digest_a)

        self.assertEqual("Original query", detail["query"])
        self.assertEqual("Detailed answer", detail["answer"])
        self.assertEqual({"total": 12.5}, detail["timings"])
        serialized = json.dumps(detail)
        self.assertNotIn("llm_prompt", serialized)
        self.assertNotIn("first_stage_candidates", serialized)
        self.assertNotIn("external_contexts", serialized)
        self.assertNotIn("_cache_meta", serialized)

    @patch("cortex.core.cache_management.get_redis_client")
    def test_cache_detail_handles_expired_cross_kb_and_malformed_entries(
        self, get_redis
    ):
        malformed_digest = "d" * 64
        self.redis.values[f"rag_cache:alpha:{malformed_digest}".encode()] = (
            b"not-json",
            100,
            50,
        )
        get_redis.return_value = self.redis

        malformed = get_cache_entry_detail("alpha", malformed_digest)

        self.assertEqual("Query unavailable", malformed["query"])
        self.assertIsNone(malformed["answer"])
        self.assertEqual([], malformed["contexts"])
        self.assertIsNone(get_cache_entry_detail("alpha", self.digest_b))
        with self.assertRaises(ValueError):
            get_cache_entry_detail("alpha", "../beta")

    @patch("cortex.core.cache_management.get_redis_client")
    def test_scoped_and_global_clear_preserve_unrelated_keys(self, get_redis):
        get_redis.return_value = self.redis

        self.assertTrue(delete_cache_entry("alpha", self.digest_a))
        with self.assertRaises(ValueError):
            delete_cache_entry("beta", "../../unrelated:key")
        self.assertEqual(1, clear_cache())
        self.assertIn(b"unrelated:key", self.redis.values)

    @patch("cortex.api.kb.get_knowledge_base", return_value={"slug": "alpha"})
    @patch("cortex.api.kb.get_knowledge_base_cache", side_effect=RuntimeError("offline"))
    def test_redis_offline_returns_service_unavailable(self, _cache, _kb):
        response = TestClient(app).get("/api/kb/alpha/cache")

        self.assertEqual(503, response.status_code)

    @patch("cortex.api.kb.get_knowledge_base", return_value={"slug": "alpha"})
    @patch("cortex.api.kb.get_knowledge_base_cache")
    def test_cache_search_api_validates_and_forwards_query(self, cache, _kb):
        cache.return_value = {
            "knowledge_base": "alpha",
            "summary": {"entry_count": 0},
            "filtered_count": 0,
            "entries": [],
        }
        api = TestClient(app)

        response = api.get("/api/kb/alpha/cache?q=Needle&offset=50&limit=25")
        too_long = api.get(f"/api/kb/alpha/cache?q={'x' * 501}")

        self.assertEqual(200, response.status_code)
        cache.assert_called_once_with("alpha", 50, 25, "Needle")
        self.assertEqual(422, too_long.status_code)

    def test_cache_ui_contract_includes_search_and_detail_interactions(self):
        root = Path(__file__).parents[1]
        html = (root / "static/index.html").read_text()
        javascript = (root / "static/app.js").read_text()

        self.assertIn('id="cache-search"', html)
        self.assertIn('id="cache-detail-drawer"', html)
        self.assertIn("setTimeout(() =>", javascript)
        self.assertIn('data-cache-entry-digest="${entry.digest}"', javascript)
        self.assertIn("event.stopPropagation()", javascript)
        self.assertIn('event.key === "Escape"', javascript)

    def test_chat_response_filters_internal_cache_metadata(self):
        response = ChatResponse.model_validate(
            {
                "answer": "Grounded answer",
                "contexts": [],
                "citations": [],
                "cache_hit": True,
                "knowledge_base": "alpha",
                "audit": {},
                "_cache_meta": {"query": "private", "created_at": "now"},
            }
        )

        self.assertNotIn("_cache_meta", response.model_dump())


if __name__ == "__main__":
    unittest.main()
