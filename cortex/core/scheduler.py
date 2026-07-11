def cron_trigger(expression: str):
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    return CronTrigger.from_crontab(expression, timezone="UTC")


class SourceScheduler:
    def __init__(self):
        self.scheduler = None

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
        if self.running:
            self.scheduler.shutdown(wait=False)
        self.scheduler = None

    def refresh(self):
        if not self.running:
            return
        from .database import list_scheduled_sources

        for job in self.scheduler.get_jobs():
            if job.id.startswith("source-sync-"):
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
            "next_run_at": next_run.isoformat() if next_run is not None else None,
        }

    @staticmethod
    def _run_source_sync(kb_slug: str, source_id: int):
        from .ingest import manager

        if manager.get_status()["status"] == "running":
            print(
                f"[SCHEDULER] Skipping source {source_id}; ingestion is already active."
            )
            return
        try:
            manager.start_source(kb_slug, source_id)
        except Exception as exc:
            print(f"[SCHEDULER] Failed to start source {source_id}: {exc}")


source_scheduler = SourceScheduler()
