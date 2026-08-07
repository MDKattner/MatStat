"""Tests for the web JobManager — background jobs, progress, cancellation."""

import sys
import time
from pathlib import Path
from typing import Any, Callable

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

    def test_list_returns_all_jobs(self) -> None:
        manager: JobManager = JobManager()
        id_a: str = manager.submit("test", lambda ctx: 1)
        id_b: str = manager.submit("test", lambda ctx: 2)
        ids: set[str] = {j.id for j in manager.list()}
        assert ids == {id_a, id_b}

    def test_publish_defaults_to_noop(self) -> None:
        manager: JobManager = JobManager()
        job_id: str = manager.submit("test", lambda ctx: "ok")
        job: Job = _wait_for_terminal(job_id, manager)
        assert job.status == "done"
