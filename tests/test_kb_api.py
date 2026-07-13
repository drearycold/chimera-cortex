import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import app
from cortex.api.kb import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    api_create_knowledge_base,
    api_delete_knowledge_base,
    api_get_knowledge_base,
    api_update_knowledge_base,
)
from cortex.core.database import KnowledgeBaseAlreadyExistsError
from cortex.core.kb_config import default_generation_config, default_ingest_config

client = TestClient(app)


class KnowledgeBaseApiTests(unittest.TestCase):
    def test_router_is_registered(self):
        route_paths = {route.path for route in app.routes}
        self.assertIn("/api/kb", route_paths)
        self.assertIn("/api/kb/{slug}", route_paths)

    def test_slug_validation_rejects_unsafe_table_names(self):
        with self.assertRaises(ValidationError):
            KnowledgeBaseCreate(slug="Unsafe_Slug", name="Unsafe")

    @patch("cortex.api.kb.ensure_vector_table")
    @patch("cortex.api.kb.ensure_minio_bucket")
    @patch("cortex.api.kb.create_knowledge_base")
    def test_create_uses_defaults(
        self,
        create_mock,
        ensure_bucket_mock,
        ensure_table_mock,
    ):
        create_mock.side_effect = lambda data: {
            **data,
            "vector_table": "chunks_fgo_lore",
            "minio_bucket": "cortex-documents",
        }
        result = api_create_knowledge_base(
            KnowledgeBaseCreate(slug="fgo-lore", name="FGO Lore")
        )

        self.assertEqual("nomic-embed-text:latest", result["ingest_config"]["embedding"]["model"])
        self.assertEqual(768, result["ingest_config"]["embedding"]["dimensions"])
        self.assertEqual(2, result["ingest_config"]["search"]["context_window"])
        self.assertEqual("qwen3:8b", result["generation_config"]["model"])
        ensure_bucket_mock.assert_called_once_with(result)
        ensure_table_mock.assert_called_once_with(result)

    @patch("cortex.api.kb.create_knowledge_base")
    def test_duplicate_slug_returns_conflict(self, create_mock):
        create_mock.side_effect = KnowledgeBaseAlreadyExistsError("fgo-lore")

        with self.assertRaises(HTTPException) as raised:
            api_create_knowledge_base(
                KnowledgeBaseCreate(slug="fgo-lore", name="FGO Lore")
            )

        self.assertEqual(409, raised.exception.status_code)

    @patch("cortex.api.kb.list_knowledge_bases", return_value=[{"slug": "fgo-lore"}])
    def test_list_wraps_results(self, _list_mock):
        response = client.get("/api/kb")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"knowledge_bases": [{"slug": "fgo-lore"}]}, response.json())

    @patch("cortex.api.kb.get_knowledge_base", return_value=None)
    def test_get_missing_returns_not_found(self, _get_mock):
        with self.assertRaises(HTTPException) as raised:
            api_get_knowledge_base("missing")
        self.assertEqual(404, raised.exception.status_code)

    @patch("cortex.api.kb.get_knowledge_base")
    @patch("cortex.api.kb.update_knowledge_base", return_value={"slug": "fgo-lore"})
    def test_update_passes_only_set_fields(self, update_mock, get_mock):
        get_mock.return_value = {
            "slug": "fgo-lore",
            "name": "FGO Lore",
            "ingest_config": default_ingest_config(),
            "generation_config": default_generation_config(),
            "stats": {"document_count": 0},
        }
        result = api_update_knowledge_base(
            "fgo-lore",
            KnowledgeBaseUpdate(name="Updated"),
        )

        update_mock.assert_called_once_with("fgo-lore", {"name": "Updated"})
        self.assertEqual("fgo-lore", result["slug"])

    @patch("cortex.api.kb.update_knowledge_base")
    @patch("cortex.api.kb.get_knowledge_base")
    def test_populated_kb_rejects_index_affecting_config_change(
        self, get_mock, update_mock
    ):
        current_ingest = default_ingest_config()
        get_mock.return_value = {
            "slug": "fgo-lore",
            "ingest_config": current_ingest,
            "generation_config": default_generation_config(),
            "stats": {"document_count": 3},
        }
        changed_ingest = deepcopy(current_ingest)
        changed_ingest["embedding"]["dimensions"] = 1024

        with self.assertRaises(HTTPException) as raised:
            api_update_knowledge_base(
                "fgo-lore",
                KnowledgeBaseUpdate(ingest_config=changed_ingest),
            )

        self.assertEqual(409, raised.exception.status_code)
        update_mock.assert_not_called()

    @patch("cortex.api.kb.update_knowledge_base", return_value={"slug": "empty"})
    @patch("cortex.api.kb.get_knowledge_base")
    def test_empty_kb_allows_index_affecting_config_change(
        self, get_mock, update_mock
    ):
        current_ingest = default_ingest_config()
        get_mock.return_value = {
            "slug": "empty",
            "ingest_config": current_ingest,
            "generation_config": default_generation_config(),
            "stats": {"document_count": 0},
        }
        changed_ingest = deepcopy(current_ingest)
        changed_ingest["embedding"]["model"] = "bge-m3:latest"

        result = api_update_knowledge_base(
            "empty",
            KnowledgeBaseUpdate(ingest_config=changed_ingest),
        )

        self.assertEqual("empty", result["slug"])
        self.assertEqual(
            changed_ingest,
            update_mock.call_args.args[1]["ingest_config"],
        )

    @patch("cortex.api.kb.update_knowledge_base", return_value={"slug": "populated"})
    @patch("cortex.api.kb.get_knowledge_base")
    def test_populated_kb_allows_search_only_config_change(
        self, get_mock, update_mock
    ):
        current_ingest = default_ingest_config()
        get_mock.return_value = {
            "slug": "populated",
            "ingest_config": current_ingest,
            "generation_config": default_generation_config(),
            "stats": {"document_count": 3},
        }
        changed_ingest = deepcopy(current_ingest)
        changed_ingest["search"]["context_window"] = 4

        api_update_knowledge_base(
            "populated",
            KnowledgeBaseUpdate(ingest_config=changed_ingest),
        )

        self.assertEqual(
            changed_ingest,
            update_mock.call_args.args[1]["ingest_config"],
        )

    @patch("cortex.api.kb.clear_knowledge_base_cache", return_value=4)
    @patch("cortex.api.kb.update_knowledge_base", return_value={"slug": "fgo-lore"})
    @patch("cortex.api.kb.get_knowledge_base")
    def test_generation_config_change_clears_kb_cache(
        self, get_mock, update_mock, clear_cache_mock
    ):
        generation_config = default_generation_config()
        get_mock.return_value = {
            "slug": "fgo-lore",
            "ingest_config": default_ingest_config(),
            "generation_config": generation_config,
            "stats": {"document_count": 3},
        }
        changed_generation = deepcopy(generation_config)
        changed_generation["temperature"] = 0.2

        api_update_knowledge_base(
            "fgo-lore",
            KnowledgeBaseUpdate(generation_config=changed_generation),
        )

        clear_cache_mock.assert_called_once_with("fgo-lore")
        self.assertEqual(
            changed_generation,
            update_mock.call_args.args[1]["generation_config"],
        )

    @patch(
        "cortex.api.kb.clear_knowledge_base_cache",
        side_effect=RuntimeError("redis offline"),
    )
    @patch("cortex.api.kb.update_knowledge_base")
    @patch("cortex.api.kb.get_knowledge_base")
    def test_generation_config_update_fails_closed_when_cache_cannot_clear(
        self, get_mock, update_mock, _clear_cache_mock
    ):
        generation_config = default_generation_config()
        get_mock.return_value = {
            "slug": "fgo-lore",
            "ingest_config": default_ingest_config(),
            "generation_config": generation_config,
            "stats": {"document_count": 3},
        }
        changed_generation = deepcopy(generation_config)
        changed_generation["temperature"] = 0.2

        with self.assertRaises(HTTPException) as raised:
            api_update_knowledge_base(
                "fgo-lore",
                KnowledgeBaseUpdate(generation_config=changed_generation),
            )

        self.assertEqual(503, raised.exception.status_code)
        update_mock.assert_not_called()

    def test_kb_config_rejects_runtime_breaking_values(self):
        invalid_ingest = default_ingest_config()
        invalid_ingest["chunking"]["overlap_chars"] = 700
        with self.assertRaises(ValidationError):
            KnowledgeBaseUpdate(ingest_config=invalid_ingest)

        invalid_generation = default_generation_config()
        invalid_generation["top_k_contexts"] = 0
        with self.assertRaises(ValidationError):
            KnowledgeBaseUpdate(generation_config=invalid_generation)

    @patch("cortex.api.kb.get_knowledge_base", return_value=None)
    def test_delete_missing_returns_not_found(self, _get_mock):
        with self.assertRaises(HTTPException) as raised:
            api_delete_knowledge_base("missing")
        self.assertEqual(404, raised.exception.status_code)


if __name__ == "__main__":
    unittest.main()
