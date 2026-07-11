import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from cortex.api.chat import (
    ChatRequest,
    api_chat,
    api_kb_chat,
    build_generation_payload,
)
from cortex.core.benchmark import query_rag
from cortex.core.connectors import DirectoryConnector
from cortex.core.ingest import IngestManager
from cortex.core.kb_storage import ensure_vector_table
from cortex.core.kb_config import default_generation_config
from cortex.core.rag import (
    allocate_query_quotas,
    decompose_query,
    fetch_and_merge_chunk_range,
    get_embedding,
    select_context_window,
    should_decompose_query,
)


class PhaseOneKnowledgeBaseTests(unittest.TestCase):
    def test_generation_payload_disables_thinking_with_token_limit(self):
        payload = build_generation_payload("qwen3:8b", "prompt", 0.1, 256)

        self.assertFalse(payload["think"])
        self.assertEqual(256, payload["options"]["num_predict"])

    def test_directory_connector_scans_changes_and_deletions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            first = source / "first.md"
            first.write_text("# First\nBody", encoding="utf-8")
            (source / "ignored.txt").write_text("ignored", encoding="utf-8")
            connector = DirectoryConnector(1, 2, directory)

            documents = connector.scan()

            self.assertEqual(["first.md"], [document.filename for document in documents])
            self.assertEqual(64, len(documents[0].content_hash))
            self.assertEqual(documents, connector.detect_changes(0))
            self.assertEqual(
                [str(source / "missing.md")],
                connector.detect_deletions(
                    {documents[0].origin_path, str(source / "missing.md")}
                ),
            )

    @patch("cortex.api.chat._run_chat", return_value={"knowledge_base": "fgo-lore"})
    @patch("cortex.api.chat.get_knowledge_base")
    def test_kb_chat_loads_requested_configuration(self, get_kb_mock, run_mock):
        knowledge_base = {"slug": "fgo-lore", "enabled": True}
        get_kb_mock.return_value = knowledge_base

        result = api_kb_chat("fgo-lore", ChatRequest(query="Who is Mash?"))

        run_mock.assert_called_once_with(
            ChatRequest(query="Who is Mash?"),
            knowledge_base,
        )
        self.assertEqual("fgo-lore", result["knowledge_base"])

    @patch("cortex.api.chat._run_chat", return_value={"knowledge_base": "fgo-lore"})
    @patch("cortex.api.chat.get_knowledge_base")
    def test_legacy_chat_defaults_to_fgo_lore(self, get_kb_mock, run_mock):
        knowledge_base = {"slug": "fgo-lore", "enabled": True}
        get_kb_mock.return_value = knowledge_base
        request = ChatRequest(query="Who is Mash?")

        result = api_chat(request)

        run_mock.assert_called_once_with(request, knowledge_base)
        self.assertEqual("fgo-lore", result["knowledge_base"])

    @patch("cortex.api.chat.get_knowledge_base", return_value=None)
    def test_kb_chat_rejects_unknown_slug(self, _get_kb_mock):
        with self.assertRaises(HTTPException) as raised:
            api_kb_chat("missing", ChatRequest(query="test"))
        self.assertEqual(404, raised.exception.status_code)

    @patch("cortex.core.benchmark.httpx.post")
    def test_benchmark_queries_kb_scoped_chat(self, post_mock):
        response = Mock()
        response.json.return_value = {"answer": "ok"}
        post_mock.return_value = response

        query_rag("http://localhost:8000", "question", kb_slug="fgo-lore")

        post_mock.assert_called_once_with(
            "http://localhost:8000/api/kb/fgo-lore/chat",
            json={"query": "question"},
            timeout=60.0,
        )

    @patch("cortex.core.rag.httpx.post")
    def test_embedding_uses_kb_model(self, post_mock):
        response = Mock()
        response.json.return_value = {"embeddings": [[0.1, 0.2]]}
        post_mock.return_value = response

        result = get_embedding("query", is_query=True, model="bge-m3:latest")

        self.assertEqual([0.1, 0.2], result)
        self.assertEqual(
            "bge-m3:latest",
            post_mock.call_args.kwargs["json"]["model"],
        )

    def test_chunk_fetch_rejects_unsafe_table_name(self):
        with self.assertRaises(ValueError):
            fetch_and_merge_chunk_range(1, 0, 2, vector_table="chunks;drop")

    def test_query_quotas_preserve_every_comparison_part(self):
        allocations = allocate_query_quotas(
            "Compare Lancer and Caster teachings",
            ["Shared teachings", "Lancer use", "Caster use"],
            10,
        )

        self.assertEqual(10, sum(quota for _, quota in allocations))
        self.assertEqual(
            {
                "Compare Lancer and Caster teachings",
                "Shared teachings",
                "Lancer use",
                "Caster use",
            },
            {query for query, _ in allocations},
        )
        self.assertTrue(all(quota > 0 for _, quota in allocations))

    def test_query_quotas_ignore_a_single_redundant_sub_query(self):
        allocations = allocate_query_quotas(
            "At what age did Jeanne leave and die?",
            ["At what age did Jeanne die?"],
            10,
        )

        self.assertEqual([("At what age did Jeanne leave and die?", 10)], allocations)

    def test_query_decomposition_gate_only_opens_for_explicit_multi_part_needs(self):
        self.assertFalse(should_decompose_query("What fraction of Gilgamesh is divine?"))
        self.assertFalse(
            should_decompose_query(
                "At what age did Jeanne leave, and at what age did she die?"
            )
        )
        self.assertTrue(
            should_decompose_query(
                "What did Cú Chulainn learn, and how does Lancer differ versus Caster?"
            )
        )
        self.assertTrue(
            should_decompose_query(
                "Who created Mordred and why? What was her actual attitude?"
            )
        )

    def test_context_window_stays_compact_for_single_query(self):
        self.assertEqual(1, select_context_window(2, 1))
        self.assertEqual(2, select_context_window(2, 3))

    def test_generation_defaults_require_focused_grounded_answers(self):
        config = default_generation_config()

        self.assertEqual(0.0, config["temperature"])
        self.assertEqual(256, config["max_tokens"])
        self.assertEqual("qwen3:8b", config["query_rewrite"]["model"])
        self.assertIn("shortest complete answer", config["system_prompt"])
        self.assertIn("at most one sentence per requested part", config["system_prompt"])
        self.assertIn("exact named subject", config["system_prompt"])
        self.assertIn("Do not mention filenames", config["system_prompt"])

    @patch("cortex.core.rag.httpx.post")
    def test_query_decomposition_disables_thinking(self, post_mock):
        response = Mock()
        response.json.return_value = {
            "response": '["Lancer rune use", "Caster rune use"]'
        }
        post_mock.return_value = response

        result = decompose_query("Compare Lancer and Caster rune use")

        self.assertEqual(["Lancer rune use", "Caster rune use"], result)
        self.assertFalse(post_mock.call_args.kwargs["json"]["think"])
        prompt = post_mock.call_args.kwargs["json"]["prompt"]
        self.assertIn("Do not decompose a short compound fact", prompt)

    @patch("cortex.core.kb_storage.httpx.post")
    @patch("cortex.core.kb_storage.httpx.get")
    def test_vector_table_uses_kb_embedding_dimensions(self, get_mock, post_mock):
        missing = Mock(status_code=404)
        get_mock.return_value = missing
        created = Mock()
        created.json.return_value = {"error_code": 0}
        post_mock.return_value = created
        knowledge_base = {
            "vector_table": "chunks_tech_library",
            "ingest_config": {"embedding": {"dimensions": 1024}},
        }

        created_table = ensure_vector_table(knowledge_base)

        create_payload = post_mock.call_args_list[0].kwargs["json"]
        self.assertEqual(
            "vector, 1024, float",
            create_payload["fields"][-1]["type"],
        )
        self.assertTrue(created_table)

    @patch("cortex.core.ingest.threading.Thread")
    def test_ingest_manager_tracks_target_kb(self, thread_mock):
        thread_mock.return_value = Mock()
        manager = IngestManager()

        manager.start(kb_slug="tech-library")

        self.assertEqual("tech-library", manager.get_status()["kb_slug"])
        self.assertEqual(
            ("documents", False, "tech-library"),
            thread_mock.call_args.kwargs["args"],
        )


if __name__ == "__main__":
    unittest.main()
