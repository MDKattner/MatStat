"""Tests for the web JobManager — background jobs, progress, cancellation."""

import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web.jobs import Job, JobContext, JobManager


def _wait_for_terminal(job_id: str, manager: JobManager, timeout: float = 5.0) -> Job:
    """Poll until a job reaches a terminal state (done/failed/cancelled)."""
    deadline: float = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job: Job | None = manager.get(job_id)
        assert job is not None
        if job.status in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish in {timeout}s")


class TestJobManager:
    """Tests for JobManager submit/get/cancel and event publishing."""

    def test_submit_runs_job_and_stores_result(self) -> None:
        events: list[dict[str, Any]] = []
        manager: JobManager = JobManager(publish=events.append)

        def work(ctx: JobContext) -> int:
            ctx.report(50, "halfway")
            return 42

        job_id: str = manager.submit("test", work)

        job: Job = _wait_for_terminal(job_id, manager)
        assert job.status == "done"
        assert job.result == 42
        assert job.progress == 100.0

        types: list[str] = [e["type"] for e in events]
        assert types == ["job_started", "job_progress", "job_finished"]
        assert events[0]["kind"] == "test"

    def test_job_failure_reports_error(self) -> None:
        manager: JobManager = JobManager()

        def boom(ctx: JobContext) -> None:
            raise ValueError("kaboom")

        job_id: str = manager.submit("test", boom)
        job: Job = _wait_for_terminal(job_id, manager)
        assert job.status == "failed"
        assert "kaboom" in job.error

    def test_progress_reporting_reaches_publisher(self) -> None:
        events: list[dict[str, Any]] = []
        manager: JobManager = JobManager(publish=events.append)

        def work(ctx: JobContext) -> None:
            ctx.report(25, "quarter")
            ctx.report(50, "half")

        job_id: str = manager.submit("test", work)
        _wait_for_terminal(job_id, manager)

        progress_events: list[dict[str, Any]] = [
            e for e in events if e["type"] == "job_progress"
        ]
        assert [e["progress"] for e in progress_events] == [25.0, 50.0]
        assert [e["message"] for e in progress_events] == ["quarter", "half"]

    def test_cancel_stops_running_job(self) -> None:
        manager: JobManager = JobManager()

        def long_job(ctx: JobContext) -> str:
            while not ctx.cancelled:
                time.sleep(0.01)
            return "stopped"

        job_id: str = manager.submit("test", long_job)

        # Wait until the job is actually running before cancelling.
        deadline: float = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            job: Job | None = manager.get(job_id)
            if job is not None and job.status == "running":
                break
            time.sleep(0.01)

        assert manager.cancel(job_id) is True
        job = _wait_for_terminal(job_id, manager)
        assert job.status == "cancelled"

    def test_cancel_unknown_job_returns_false(self) -> None:
        manager: JobManager = JobManager()
        assert manager.cancel("nope") is False

    def test_get_unknown_job_returns_none(self) -> None:
        manager: JobManager = JobManager()
        assert manager.get("missing") is None

    def test_publish_defaults_to_noop(self) -> None:
        manager: JobManager = JobManager()
        job_id: str = manager.submit("test", lambda ctx: "ok")
        job: Job = _wait_for_terminal(job_id, manager)
        assert job.status == "done"

    def test_shutdown_cancels_running_job(self) -> None:
        """Shutdown must cancel jobs instead of joining them indefinitely.

        Worker threads are daemons, so even a job that ignores cancellation
        cannot keep the process alive after the app lifespan returns.
        """
        manager: JobManager = JobManager()
        started: threading.Event = threading.Event()

        def long_job(ctx: JobContext) -> str:
            started.set()
            while not ctx.cancelled:
                time.sleep(0.01)
            return "stopped"

        job_id: str = manager.submit("test", long_job)
        assert started.wait(timeout=5.0)

        manager.shutdown()

        job: Job | None = manager.get(job_id)
        assert job is not None
        assert job.status == "cancelled"

    def test_shutdown_is_bounded_when_job_ignores_cancel(self, caplog) -> None:
        """A job stuck in an uninterruptible unit must not hold shutdown open."""
        manager: JobManager = JobManager()
        started: threading.Event = threading.Event()

        def stuck(ctx: JobContext) -> str:
            started.set()
            time.sleep(1.0)  # no cancel checkpoint
            return "late"

        job_id: str = manager.submit("compile_stats", stuck)
        assert started.wait(timeout=5.0)

        begin: float = time.monotonic()
        survivors = manager.shutdown(grace=0.2)
        elapsed: float = time.monotonic() - begin

        assert elapsed < 0.9
        assert [job.id for job in survivors] == [job_id]
        assert survivors[0].kind == "compile_stats"
        assert any("Abandoning running job" in r.message for r in caplog.records)

    def test_workers_are_daemon_threads(self) -> None:
        """Daemon workers cannot stall interpreter exit after uvicorn stops."""
        manager: JobManager = JobManager()
        started: threading.Event = threading.Event()
        release: threading.Event = threading.Event()

        def wait_job(ctx: JobContext) -> None:
            started.set()
            release.wait(timeout=5.0)

        manager.submit("test", wait_job)
        assert started.wait(timeout=5.0)

        workers: list[threading.Thread] = [
            t for t in threading.enumerate() if t.name.startswith("matstat-job-")
        ]
        assert workers
        assert all(worker.daemon for worker in workers)

        release.set()
        manager.shutdown(grace=2.0)

    def test_shutdown_drops_queued_jobs(self) -> None:
        """Queued work is cancelled rather than run during shutdown."""
        manager: JobManager = JobManager(max_workers=1)
        started: threading.Event = threading.Event()
        release: threading.Event = threading.Event()

        def blocker(ctx: JobContext) -> None:
            started.set()
            release.wait(timeout=5.0)

        manager.submit("blocker", blocker)
        assert started.wait(timeout=5.0)
        queued_id: str = manager.submit("queued", lambda ctx: "never")

        manager.request_cancel()
        dropped: int = manager.drop_queued()

        assert dropped == 1
        queued: Job | None = manager.get(queued_id)
        assert queued is not None
        assert queued.status == "cancelled"

        release.set()
        manager.shutdown(grace=2.0)

    def test_running_jobs_reports_kind_and_elapsed(self) -> None:
        """Shutdown diagnostics name the job that is blocking the wait."""
        manager: JobManager = JobManager()
        started: threading.Event = threading.Event()
        release: threading.Event = threading.Event()

        def blocker(ctx: JobContext) -> None:
            started.set()
            release.wait(timeout=5.0)

        job_id: str = manager.submit("team_eval", blocker)
        assert started.wait(timeout=5.0)

        running = manager.running_jobs()
        assert [(job.id, job.kind) for job in running] == [(job_id, "team_eval")]
        assert running[0].elapsed >= 0.0
        assert job_id in running[0].describe()

        release.set()
        manager.shutdown(grace=2.0)

    def test_submit_after_shutdown_raises(self) -> None:
        """No new work is accepted once the app is shutting down."""
        manager: JobManager = JobManager()
        manager.shutdown(grace=0.1)

        with pytest.raises(RuntimeError):
            manager.submit("test", lambda ctx: None)

    def test_reset_reopens_manager(self) -> None:
        """A fresh lifespan can reuse the manager after shutdown."""
        manager: JobManager = JobManager()
        manager.shutdown(grace=0.1)
        manager.reset()

        job_id: str = manager.submit("test", lambda ctx: "ok")
        job: Job = _wait_for_terminal(job_id, manager)
        assert job.status == "done"

        manager.shutdown(grace=0.5)

    def test_lifespan_shutdown_is_bounded(self, monkeypatch) -> None:
        """The real app lifespan returns even with a stuck job."""
        import asyncio

        from scripts.web import app as webapp

        monkeypatch.setattr(webapp, "SHUTDOWN_GRACE_SECONDS", 0.2)
        # Skip the auth fail-fast: this test only exercises shutdown.
        monkeypatch.setattr(webapp.auth, "check_configured", lambda: None)
        started: threading.Event = threading.Event()

        def stuck(ctx: JobContext) -> None:
            started.set()
            time.sleep(2.0)

        async def run_lifespan() -> float:
            begin: float = time.monotonic()
            async with webapp.lifespan(webapp.app):
                webapp.job_manager.submit("stuck", stuck)
                assert started.wait(timeout=5.0)
            return time.monotonic() - begin

        elapsed: float = asyncio.run(run_lifespan())
        assert elapsed < 1.5
