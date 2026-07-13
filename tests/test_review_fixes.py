import hashlib
import json
import unittest
from datetime import datetime
from typing import Any
from unittest.mock import Mock, patch

from cortex.core.connectors.calibre import CalibreConnector
from cortex.core.connectors.base import RawDocument
from cortex.core.external_documents import (
    delete_external_document,
    upsert_external_document,
)
from cortex.core.ingest import IngestManager
from cortex.core.scheduler import SourceScheduler


class ReviewRegressionTests(unittest.TestCase):
    @staticmethod
    def _cloud_document() -> RawDocument:
        content = b"Cloud evidence."
        return RawDocument(
            kb_id=1,
            source_id=7,
            source_type="cloud_drive",
            origin_path="google_drive:file-1",
            filename="cloud.md",
            title="Cloud",
            format="md",
            raw_bytes=content,
            content_markdown=content.decode(),
            content_hash=hashlib.sha256(content).hexdigest(),
            source_modified_at=1_700_000_000.0,
            metadata={},
        )

    def _run_cloud_ingest(
        self,
        *,
        minio_error: Exception | None = None,
        embedding_error: Exception | None = None,
        cancelled: bool = False,
    ):
        knowledge_base = {
            "id": 1,
            "slug": "cloud",
            "enabled": True,
            "vector_table": "chunks_cloud",
            "minio_bucket": "cortex-documents",
            "ingest_config": {"embedding": {}, "chunking": {}},
        }
        source: dict[str, Any] = {
            "id": 7,
            "type": "cloud_drive",
            "enabled": True,
            "config": {
                "provider": "google_drive",
                "folder_id": "folder",
                "cursor": "cursor-old",
            },
        }
        connector = Mock()
        connector.scan.return_value = [self._cloud_document()]
        connector.is_full_snapshot = False
        connector.deleted_origin_paths = []
        connector.next_cursor = "cursor-new"
        connector.config = dict(source["config"])
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        cursor.lastrowid = 101
        minio = Mock()
        if minio_error is not None:
            minio.put_object.side_effect = minio_error
        manager = IngestManager()
        manager.status = "running"
        manager.is_running = True
        if cancelled:
            manager.cancel_event.set()

        with (
            patch("cortex.core.ingest.get_knowledge_base", return_value=knowledge_base),
            patch("cortex.core.ingest.get_source", return_value=source),
            patch("cortex.core.ingest.GoogleDriveConnector", return_value=connector),
            patch("cortex.core.ingest.ensure_minio_bucket"),
            patch("cortex.core.ingest.ensure_vector_table", return_value=False),
            patch("cortex.core.ingest.ensure_external_vector_columns"),
            patch("cortex.core.ingest.get_minio_client", return_value=minio),
            patch("cortex.core.ingest.get_mysql_connection", return_value=connection),
            patch(
                "cortex.core.ingest.chunk_markdown",
                return_value=[("", "Cloud evidence.")],
            ),
            patch(
                "cortex.core.ingest.get_embeddings_batch",
                side_effect=embedding_error,
                return_value=[[0.1, 0.2]],
            ),
            patch("cortex.core.ingest.record_ingestion_log") as record_log,
        ):
            manager._run_wrapper(None, False, "cloud", 7)

        return manager, cursor, record_log

    def test_cloud_cursor_is_preserved_after_document_failure(self):
        manager, cursor, record_log = self._run_cloud_ingest(
            minio_error=RuntimeError("upload failed")
        )

        self.assertEqual("failed", manager.status)
        self.assertEqual(0, manager.processed_files)
        self.assertEqual(1, manager.failed_files)
        self.assertFalse(
            any("UPDATE sources SET" in call.args[0] for call in cursor.execute.call_args_list)
        )
        self.assertEqual(1, record_log.call_args.kwargs["docs_failed"])

    def test_cloud_cursor_is_preserved_after_final_flush_failure(self):
        manager, cursor, record_log = self._run_cloud_ingest(
            embedding_error=RuntimeError("embedding batch failed")
        )

        self.assertEqual("failed", manager.status)
        self.assertEqual(0, manager.processed_files)
        self.assertEqual(1, manager.failed_files)
        self.assertFalse(
            any("UPDATE sources SET" in call.args[0] for call in cursor.execute.call_args_list)
        )
        self.assertIn("final chunk queue", manager.error_message)
        self.assertEqual(1, record_log.call_args.kwargs["docs_failed"])

    def test_cloud_cursor_is_preserved_after_cancellation(self):
        manager, cursor, record_log = self._run_cloud_ingest(cancelled=True)

        self.assertEqual("cancelled", manager.status)
        self.assertEqual(0, manager.processed_files)
        self.assertEqual(0, manager.failed_files)
        self.assertFalse(
            any("UPDATE sources SET" in call.args[0] for call in cursor.execute.call_args_list)
        )
        self.assertEqual(1, record_log.call_args.kwargs["docs_skipped"])

    def test_source_force_rebuild_does_not_drop_kb_vector_table(self):
        knowledge_base = {
            "id": 1,
            "slug": "multi-source",
            "enabled": True,
            "vector_table": "chunks_multi_source",
            "minio_bucket": "cortex-documents",
            "ingest_config": {"embedding": {}, "chunking": {}},
        }
        with (
            patch("cortex.core.ingest.get_knowledge_base", return_value=knowledge_base),
            patch("cortex.core.ingest.ensure_minio_bucket"),
            patch("cortex.core.ingest.ensure_vector_table", return_value=False) as ensure,
            patch("cortex.core.ingest.ensure_external_vector_columns"),
            patch("cortex.core.ingest.get_minio_client"),
            patch("cortex.core.ingest.get_source", side_effect=RuntimeError("stop")),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                IngestManager()._run_ingest(
                    None,
                    force_rebuild=True,
                    kb_slug="multi-source",
                    source_id=7,
                )

        ensure.assert_called_once_with(knowledge_base)

    def test_calibre_scan_fails_instead_of_returning_partial_snapshot(self):
        connector = object.__new__(CalibreConnector)
        connector._book_ids = Mock(return_value=[42])
        connector._metadata = Mock(return_value=[{"id": 42, "title": "Book"}])
        connector._document = Mock(side_effect=RuntimeError("download failed"))

        with self.assertRaisesRegex(RuntimeError, "42"):
            connector.scan()

    def test_scheduler_activates_and_debounces_directory_watch_sources(self):
        source = {
            "id": 9,
            "kb_id": 1,
            "kb_slug": "docs",
            "name": "Watched Docs",
            "type": "directory",
            "config": {"path": "documents", "glob_patterns": ["*.md"]},
        }
        fake_scheduler = Mock()
        fake_scheduler.running = True
        fake_scheduler.get_jobs.return_value = []
        observer = Mock()
        connector = Mock()
        connector.watch.return_value = observer
        scheduler = SourceScheduler()
        scheduler.scheduler = fake_scheduler
        with (
            patch(
                "cortex.core.database.list_scheduled_sources",
                return_value=[],
            ),
            patch(
                "cortex.core.database.list_watch_sources",
                return_value=[source],
            ),
            patch(
                "cortex.core.scheduler.DirectoryConnector",
                return_value=connector,
            ) as connector_type,
        ):
            scheduler.refresh()

        connector_type.assert_called_once_with(1, 9, "documents", ["*.md"])
        callback = connector.watch.call_args.args[0]
        callback("modified", "documents/guide.md")
        add_kwargs = fake_scheduler.add_job.call_args.kwargs
        self.assertEqual("source-watch-sync-9", add_kwargs["id"])
        self.assertEqual(["docs", 9], add_kwargs["args"])
        self.assertTrue(add_kwargs["replace_existing"])
        self.assertIsInstance(add_kwargs["run_date"], datetime)

    def test_external_delete_preserves_storage_on_infinity_business_error(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (7, "reader/external/doc.json")
        response = Mock()
        response.json.return_value = {
            "error_code": 3017,
            "error_msg": "delete rejected",
        }
        minio = Mock()
        knowledge_base = {
            "id": 1,
            "slug": "reader",
            "vector_table": "chunks_reader",
            "minio_bucket": "cortex-documents",
        }
        with (
            patch(
                "cortex.core.external_documents.get_mysql_connection",
                return_value=connection,
            ),
            patch(
                "cortex.core.external_documents.httpx.request",
                return_value=response,
            ),
            patch(
                "cortex.core.external_documents.get_minio_client",
                return_value=minio,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "delete rejected"):
                delete_external_document(knowledge_base, "opaque-book")

        minio.remove_object.assert_not_called()
        self.assertFalse(
            any(
                call.args[0].startswith("DELETE FROM documents")
                for call in cursor.execute.call_args_list
            )
        )

    def test_unchanged_external_put_returns_before_embedding_or_storage(self):
        document = {
            "title": "Book",
            "source_key": "opaque-library",
            "metadata": {},
            "segments": [
                {
                    "ordinal": 1,
                    "locator": {"type": "epub_cfi", "value": "epubcfi(/6/2)"},
                    "heading": "Chapter",
                    "text": "Already indexed evidence.",
                }
            ],
        }
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        content_hash = hashlib.sha256(canonical).hexdigest()
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (7, content_hash, 3, 11)
        knowledge_base = {
            "id": 1,
            "slug": "reader",
            "vector_table": "chunks_reader",
            "minio_bucket": "cortex-documents",
            "ingest_config": {"chunking": {}, "embedding": {}},
        }
        with (
            patch(
                "cortex.core.external_documents.get_mysql_connection",
                return_value=connection,
            ),
            patch("cortex.core.external_documents.get_embeddings_batch") as embeddings,
            patch("cortex.core.external_documents.ensure_minio_bucket") as ensure_bucket,
            patch("cortex.core.external_documents.ensure_vector_table") as ensure_table,
            patch("cortex.core.external_documents.get_or_create_external_source"),
        ):
            result = upsert_external_document(
                knowledge_base,
                "opaque-book",
                document,
            )

        self.assertEqual("unchanged", result["status"])
        self.assertEqual(3, result["chunk_count"])
        embeddings.assert_not_called()
        ensure_bucket.assert_not_called()
        ensure_table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
