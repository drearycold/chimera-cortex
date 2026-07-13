from datetime import datetime, timedelta, timezone
from typing import Any

from .connectors import DirectoryConnector


def cron_trigger(expression: str):
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    return CronTrigger.from_crontab(expression, timezone="UTC")


class SourceScheduler:
    def __init__(self):
        self.scheduler = None
        self.watchers: dict[int, Any] = {}

    @property
    def running(self) -> bool:
        return bool(self.scheduler and self.scheduler.running)

    def start(self):
        if self.running:
            return
        from apscheduler.schedulers.background import (  # type: ignore[import-untyped]
            BackgroundScheduler,
        )

        self.scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
        self.scheduler.start()
        self.refresh()

    def shutdown(self):
        self._stop_watchers()
        if self.running:
            self.scheduler.shutdown(wait=False)
        self.scheduler = None

    def refresh(self):
        if not self.running:
            return
        from .database import list_scheduled_sources, list_watch_sources

        for job in self.scheduler.get_jobs():
            if job.id.startswith(
                ("source-sync-", "source-watch-sync-", "source-deferred-sync-")
            ):
                self.scheduler.remove_job(job.id)
        for source in list_scheduled_sources():
            trigger = cron_trigger(source["sync_cron"])
            self.scheduler.add_job(
                self._run_source_sync,
                trigger=trigger,
                args=[source["kb_slug"], source["id"]],
                id=f"source-sync-{source['id']}",
                name=f"Sync {source['kb_slug']} / {source['name']}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=300,
            )
        self._stop_watchers()
        for source in list_watch_sources():
            try:
                connector = DirectoryConnector(
                    source["kb_id"],
                    source["id"],
                    source["config"]["path"],
                    source["config"].get("glob_patterns", ["*.md"]),
                )
                observer = connector.watch(
                    lambda event_type, path, source=source: self._schedule_watch_sync(
                        source["kb_slug"],
                        source["id"],
                        event_type,
                        path,
                    )
                )
                self.watchers[source["id"]] = observer
            except Exception as exc:
                print(
                    f"[SCHEDULER] Failed to watch source {source['id']}: {exc}"
                )

    def _stop_watchers(self):
        for observer in self.watchers.values():
            try:
                observer.stop()
                observer.join(timeout=2.0)
            except Exception as exc:
                print(f"[SCHEDULER] Failed to stop source watcher: {exc}")
        self.watchers.clear()

    def _schedule_watch_sync(
        self,
        kb_slug: str,
        source_id: int,
        event_type: str,
        path: str,
    ):
        if not self.running:
            return
        print(
            f"[SCHEDULER] Directory {event_type} for source {source_id}: {path}"
        )
        self.scheduler.add_job(
            self._run_watched_source_sync,
            trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=1),
            args=[kb_slug, source_id],
            id=f"source-watch-sync-{source_id}",
            name=f"Watch sync {kb_slug} / {source_id}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def _run_watched_source_sync(self, kb_slug: str, source_id: int):
        from .ingest import manager

        if manager.get_status()["status"] == "running":
            self.scheduler.add_job(
                self._run_watched_source_sync,
                trigger="date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=2),
                args=[kb_slug, source_id],
                id=f"source-watch-sync-{source_id}",
                name=f"Deferred watch sync {kb_slug} / {source_id}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            return
        self._run_source_sync(kb_slug, source_id)

    def job_status(self, source_id: int) -> dict:
        job = (
            self.scheduler.get_job(f"source-sync-{source_id}")
            if self.running
            else None
        )
        next_run = getattr(job, "next_run_time", None)
        return {
            "scheduler_running": self.running,
            "scheduled": job is not None,
            "watching": source_id in self.watchers,
            "next_run_at": next_run.isoformat() if next_run is not None else None,
        }

    def _defer_source_sync(self, kb_slug: str, source_id: int):
        if not self.running:
            print(
                f"[SCHEDULER] Cannot defer source {source_id}; scheduler is not running."
            )
            return
        self.scheduler.add_job(
            self._run_source_sync,
            trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=2),
            args=[kb_slug, source_id],
            id=f"source-deferred-sync-{source_id}",
            name=f"Deferred sync {kb_slug} / {source_id}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def _run_source_sync(self, kb_slug: str, source_id: int):
        from .ingest import manager

        if manager.get_status()["status"] == "running":
            print(
                f"[SCHEDULER] Deferring source {source_id}; ingestion is already active."
            )
            self._defer_source_sync(kb_slug, source_id)
            return
        try:
            manager.start_source(kb_slug, source_id)
        except Exception as exc:
            if manager.get_status()["status"] == "running":
                self._defer_source_sync(kb_slug, source_id)
                return
            print(f"[SCHEDULER] Failed to start source {source_id}: {exc}")


source_scheduler = SourceScheduler()
