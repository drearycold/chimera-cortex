import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app import app
from cortex.api.sources import (
    SourceCreate,
    _validate_source_config,
    api_create_source,
)
from cortex.core.connectors import WebConnector
from cortex.core.ingest import IngestManager
from cortex.core.scheduler import SourceScheduler


class PhaseTwoWebSourceTests(unittest.TestCase):
    def test_web_connector_normalizes_crawl_result(self):
        result = SimpleNamespace(
            success=True,
            url="https://docs.example.com/guide/getting-started",
            markdown=SimpleNamespace(raw_markdown="# Getting Started\n\nHello."),
            metadata={"title": "Getting Started", "depth": 1},
            status_code=200,
        )
        connector = WebConnector(
            2,
            7,
            {"url": "https://docs.example.com", "max_depth": 1},
            crawl_runner=lambda _config: [result],
        )

        document = connector.scan()[0]

        self.assertEqual("web", document.source_type)
        self.assertEqual(result.url, document.origin_path)
        self.assertEqual("Getting Started", document.title)
        self.assertEqual(64, len(document.content_hash))
        self.assertEqual(result.url, document.metadata["url"])
        self.assertEqual(1, document.metadata["crawl_depth"])
        self.assertTrue(document.filename.endswith(".md"))
        self.assertEqual(document.filename, connector.scan()[0].filename)

    def test_web_connector_rejects_empty_failed_crawl(self):
        result = SimpleNamespace(
            success=False,
            error_message="robots denied",
            url="https://docs.example.com",
        )
        connector = WebConnector(
            2,
            7,
            {"url": "https://docs.example.com"},
            crawl_runner=lambda _config: [result],
        )

        with self.assertRaisesRegex(RuntimeError, "robots denied"):
            connector.scan()

    def test_web_connector_rejects_partial_snapshot(self):
        successful = SimpleNamespace(
            success=True,
            url="https://docs.example.com/guide",
            markdown="# Guide\n\nAvailable page.",
            metadata={"title": "Guide"},
            status_code=200,
        )
        failed = SimpleNamespace(
            success=False,
            error_message="timeout",
            url="https://docs.example.com/missing",
        )
        connector = WebConnector(
            2,
            7,
            {"url": "https://docs.example.com"},
            crawl_runner=lambda _config: [successful, failed],
        )

        with self.assertRaisesRegex(RuntimeError, "missing.*timeout"):
            connector.scan()

    def test_source_validation_requires_web_url_and_scheduled_cron(self):
        with self.assertRaises(HTTPException):
            _validate_source_config("web", {"url": "relative"}, "manual", None)
        with self.assertRaises(HTTPException):
            _validate_source_config(
                "web",
                {"url": "https://docs.example.com"},
                "scheduled",
                None,
            )
        with self.assertRaises(HTTPException):
            _validate_source_config(
                "web",
                {"url": "https://docs.example.com"},
                "scheduled",
                "99 * * * *",
            )

        _validate_source_config(
            "web",
            {"url": "https://docs.example.com", "max_depth": 2, "max_pages": 20},
            "scheduled",
            "0 */6 * * *",
        )

    @patch("cortex.api.sources.source_scheduler")
    @patch("cortex.api.sources.create_source")
    @patch("cortex.api.sources.get_knowledge_base")
    def test_create_web_source_is_kb_scoped(
        self,
        get_kb_mock,
        create_source_mock,
        scheduler_mock,
    ):
        get_kb_mock.return_value = {"id": 3, "slug": "docs"}
        create_source_mock.return_value = {"id": 9, "type": "web"}
        scheduler_mock.running = False
        request = SourceCreate(
            type="web",
            name="Documentation",
            config={"url": "https://docs.example.com"},
        )

        result = api_create_source("docs", request)

        self.assertEqual(9, result["id"])
        create_source_mock.assert_called_once_with(3, request.model_dump())

    def test_source_routes_are_registered(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/api/kb/{slug}/sources", paths)
        self.assertIn("/api/kb/{slug}/sources/{source_id}/sync", paths)

    @patch("cortex.core.ingest.threading.Thread")
    def test_ingest_manager_starts_specific_source(self, thread_mock):
        thread_mock.return_value = Mock()
        manager = IngestManager()

        manager.start_source("docs", 9)

        self.assertEqual("docs", manager.get_status()["kb_slug"])
        self.assertEqual(9, manager.get_status()["source_id"])
        self.assertEqual(
            (None, False, "docs", 9),
            thread_mock.call_args.kwargs["args"],
        )
        self.assertTrue(thread_mock.call_args.kwargs["daemon"])

    @patch("cortex.core.database.list_watch_sources", return_value=[])
    @patch("cortex.core.database.list_scheduled_sources")
    def test_scheduler_registers_enabled_cron_sources(
        self,
        list_sources_mock,
        _list_watch_sources_mock,
    ):
        list_sources_mock.return_value = [
            {
                "id": 9,
                "kb_slug": "docs",
                "name": "Documentation",
                "sync_cron": "0 */6 * * *",
            }
        ]
        fake_scheduler = Mock()
        fake_scheduler.running = True
        fake_scheduler.get_jobs.return_value = [SimpleNamespace(id="source-sync-old")]
        scheduler = SourceScheduler()
        scheduler.scheduler = fake_scheduler

        scheduler.refresh()

        fake_scheduler.remove_job.assert_called_once_with("source-sync-old")
        kwargs = fake_scheduler.add_job.call_args.kwargs
        self.assertEqual("source-sync-9", kwargs["id"])
        self.assertEqual(["docs", 9], kwargs["args"])
        self.assertTrue(kwargs["coalesce"])

    def test_scheduler_job_status_exposes_next_run(self):
        next_run = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
        fake_scheduler = Mock()
        fake_scheduler.running = True
        fake_scheduler.get_job.return_value = SimpleNamespace(next_run_time=next_run)
        scheduler = SourceScheduler()
        scheduler.scheduler = fake_scheduler

        result = scheduler.job_status(9)

        self.assertTrue(result["scheduler_running"])
        self.assertTrue(result["scheduled"])
        self.assertEqual(next_run.isoformat(), result["next_run_at"])

    @patch("cortex.core.ingest.manager")
    def test_scheduled_sync_defers_until_ingestion_is_available(self, manager):
        manager.get_status.side_effect = [
            {"status": "running"},
            {"status": "completed"},
        ]
        fake_scheduler = Mock()
        fake_scheduler.running = True
        scheduler = SourceScheduler()
        scheduler.scheduler = fake_scheduler

        scheduler._run_source_sync("docs", 9)

        deferred = fake_scheduler.add_job.call_args
        self.assertEqual(scheduler._run_source_sync, deferred.args[0])
        self.assertEqual("source-deferred-sync-9", deferred.kwargs["id"])
        self.assertEqual(["docs", 9], deferred.kwargs["args"])
        self.assertTrue(deferred.kwargs["replace_existing"])
        manager.start_source.assert_not_called()

        deferred.args[0](*deferred.kwargs["args"])

        manager.start_source.assert_called_once_with("docs", 9)


if __name__ == "__main__":
    unittest.main()
