import unittest
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

    @patch("cortex.api.kb.update_knowledge_base", return_value={"slug": "fgo-lore"})
    def test_update_passes_only_set_fields(self, update_mock):
        result = api_update_knowledge_base(
            "fgo-lore",
            KnowledgeBaseUpdate(name="Updated"),
        )

        update_mock.assert_called_once_with("fgo-lore", {"name": "Updated"})
        self.assertEqual("fgo-lore", result["slug"])

    @patch("cortex.api.kb.get_knowledge_base", return_value=None)
    def test_delete_missing_returns_not_found(self, _get_mock):
        with self.assertRaises(HTTPException) as raised:
            api_delete_knowledge_base("missing")
        self.assertEqual(404, raised.exception.status_code)


if __name__ == "__main__":
    unittest.main()
