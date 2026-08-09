from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

# Event dicts published by the manager (see ws.py for the wire format):
#   {"type": "job_started", "id", "kind", "status"}
#   {"type": "job_progress", "id", "progress", "message"}
#   {"type": "job_finished", "id", "status", "message"}
EventPublisher = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class Job:
    """Immutable snapshot of a background job's current state."""

    id: str
    kind: str
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)


class JobContext:
    """Passed to job functions so they can report progress and cancellation."""

    def __init__(self, manager: "JobManager", job_id: str) -> None:
        self._manager: JobManager = manager
        self.job_id: str = job_id

    def report(self, progress: float, message: str = "") -> None:
        """Report progress (0-100) and an optional status message."""
        self._manager._report(self.job_id, progress, message)

    @property
    def cancelled(self) -> bool:
        """True if the job has been requested to cancel."""
        return self._manager._is_cancelled(self.job_id)


class JobManager:
    """Run background jobs in a thread pool and broadcast progress events.

    Each job runs in a worker thread and reports progress via a ``JobContext``.
    Event
    dicts are pushed to the ``publish`` callable, which the WebSocket hub
    subscribes to (see ``scripts.web.ws``).
    """

    def __init__(self, publish: EventPublisher | None = None, max_workers: int = 4) -> None:
        self._publish: EventPublisher = publish or (lambda _event: None)
        self._max_workers: int = max_workers
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, Job] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock: threading.Lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[JobContext], Any], message: str = "") -> str:
        """Queue a job that runs ``fn(ctx)`` in a worker thread.

        Args:
            kind: A stable machine-readable label (e.g. "transcode", "compile_stats").
            fn: The job body; receives a JobContext for progress/cancellation.
            message: Initial status message.

        Returns:
            The job id (short hex), used to poll or cancel the job.
        """
        job_id: str = uuid.uuid4().hex[:8]
        job: Job = Job(id=job_id, kind=kind, message=message)
        with self._lock:
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()

        self._publish({
            "type": "job_started",
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "message": message,
        })
        self._executor.submit(self._run, job_id, fn)
        return job_id

    def _run(self, job_id: str, fn: Callable[[JobContext], Any]) -> None:
        ctx: JobContext = JobContext(self, job_id)
        try:
            if not self._is_cancelled(job_id):
                self._set(job_id, status="running", message="Running...")
            result: Any = fn(ctx)
            if self._is_cancelled(job_id):
                self._finish(job_id, status="cancelled", message="Cancelled")
            else:
                self._finish(job_id, status="done", message="Done", result=result)
        except Exception as e:
            logging.error(f"Job {job_id} failed: {e}")
            self._finish(job_id, status="failed", message=f"Failed: {e}", error=str(e))

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a running/queued job."""
        with self._lock:
            event: threading.Event | None = self._cancel_events.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            event: threading.Event | None = self._cancel_events.get(job_id)
        return event is not None and event.is_set()

    def _set(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job: Job | None = self._jobs.get(job_id)
            if job is None:
                return
            self._jobs[job_id] = Job(**{**job.__dict__, **updates})

    def _report(self, job_id: str, progress: float, message: str) -> None:
        self._set(job_id, progress=float(progress), message=message)
        self._publish({
            "type": "job_progress",
            "id": job_id,
            "progress": float(progress),
            "message": message,
        })

    def _finish(
        self,
        job_id: str,
        *,
        status: JobStatus,
        message: str,
        error: str = "",
        result: Any = None,
    ) -> None:
        current: Job | None = self.get(job_id)
        current_progress: float = current.progress if current is not None else 0.0
        progress: float = 100.0 if status == "done" else current_progress
        self._set(job_id, status=status, progress=progress,
                  message=message, error=error, result=result)
        self._publish({
            "type": "job_finished",
            "id": job_id,
            "status": status,
            "message": message,
            "error": error,
        })

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def reset(self) -> None:
        """Recreate the thread pool after a previous shutdown.

        Used by the app lifespan so the manager survives repeated
        startup/shutdown cycles (e.g. across test client instances).
        """
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
