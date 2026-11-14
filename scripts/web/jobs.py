from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from scripts.web.ffmpeg import JobCancelledError

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

# Finished jobs older than this (seconds) are pruned from memory on the next
# submit/finish call.  Prevents unbounded growth of _jobs / _cancel_events
# during long server uptimes.
_JOB_PRUNE_TTL: float = 300.0

# How often shutdown re-checks whether a cancelled job has returned.
_IDLE_POLL_INTERVAL: float = 0.05

# Event dicts published by the manager (see ws.py for the wire format):
#   {"type": "job_started", "id", "kind", "status", "message"}
#   {"type": "job_progress", "id", "progress", "message"}
#   {"type": "job_finished", "id", "status", "message", "error"}
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


@dataclass(frozen=True)
class RunningJob:
    """A job that is still executing, with how long it has been running."""

    id: str
    kind: str
    elapsed: float

    def describe(self) -> str:
        """Return a short human-readable label for logs."""
        return f"{self.id} ({self.kind}, {self.elapsed:.1f}s)"


class JobContext:
    """Passed to job functions so they can report progress and cancellation."""

    def __init__(self, manager: JobManager, job_id: str) -> None:
        self._manager: JobManager = manager
        self.job_id: str = job_id

    def report(self, progress: float, message: str = "") -> None:
        """Report progress (0-100) and an optional status message."""
        self._manager._report(self.job_id, progress, message)

    @property
    def cancelled(self) -> bool:
        """True if the job has been requested to cancel."""
        return self._manager._is_cancelled(self.job_id)

    @property
    def cancel_event(self) -> threading.Event:
        """The job's cancellation event (set by ``JobManager.cancel``).

        Pass this to ``run_ffmpeg`` so a cancel request aborts the running
        process instead of merely relabeling the job afterwards.
        """
        return self._manager._get_cancel_event(self.job_id)


class JobManager:
    """Run background jobs in daemon worker threads and broadcast progress.

    Each job runs in a worker thread and reports progress via a ``JobContext``.
    Event dicts are pushed to the ``publish`` callable, which the WebSocket hub
    subscribes to (see ``scripts.web.ws``).

    Workers are daemon threads on purpose: at interpreter exit Python joins
    non-daemon threads with no timeout, so a job stuck in a tool call would
    keep the process alive after uvicorn finished shutting down. Shutdown
    cancels what it can, waits a bounded grace period, then lets the daemons
    die with the process.
    """

    def __init__(self, publish: EventPublisher | None = None, max_workers: int = 4) -> None:
        self._publish: EventPublisher = publish or (lambda _event: None)
        self._max_workers: int = max_workers
        self._queue: queue.Queue[tuple[str, Callable[[JobContext], Any]] | None] = (
            queue.Queue()
        )
        self._workers: list[threading.Thread] = []
        self._workers_lock: threading.Lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._running: dict[str, float] = {}
        self._lock: threading.Lock = threading.Lock()
        self._closed: bool = False

    def submit(
        self,
        kind: str,
        fn: Callable[[JobContext], Any],
        message: str = "",
        job_id: str | None = None,
    ) -> str:
        """Queue a job that runs ``fn(ctx)`` in a worker thread.

        Args:
            kind: A stable machine-readable label (e.g. "transcode", "compile_stats").
            fn: The job body; receives a JobContext for progress/cancellation.
            message: Initial status message.
            job_id: Optional pre-generated job id. Lets the caller coordinate
                external state (e.g. the transcode in-flight map) *before* the
                worker can start, closing the race where a fast job finishes
                before the id is recorded.

        Returns:
            The job id (short hex), used to poll or cancel the job.

        Raises:
            RuntimeError: If the manager has been shut down (the app is exiting).
        """
        self._prune_finished()
        job_id = job_id or uuid.uuid4().hex[:8]
        job: Job = Job(id=job_id, kind=kind, message=message)
        with self._lock:
            if self._closed:
                raise RuntimeError("Job manager is shut down")
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()

        self._publish({
            "type": "job_started",
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "message": message,
        })
        self._ensure_workers()
        self._queue.put((job_id, fn))
        return job_id

    def _ensure_workers(self) -> None:
        """Start daemon workers up to ``max_workers`` (replacing dead ones)."""
        work_queue: queue.Queue[tuple[str, Callable[[JobContext], Any]] | None] = (
            self._queue
        )
        with self._workers_lock:
            self._workers = [t for t in self._workers if t.is_alive()]
            while len(self._workers) < self._max_workers:
                index: int = len(self._workers) + 1
                thread: threading.Thread = threading.Thread(
                    target=self._worker,
                    args=(work_queue,),
                    name=f"matstat-job-{index}",
                    daemon=True,
                )
                self._workers.append(thread)
                thread.start()

    def _worker(
        self,
        work_queue: queue.Queue[tuple[str, Callable[[JobContext], Any]] | None],
    ) -> None:
        """Consume queued jobs until a ``None`` sentinel tells the worker to stop.

        The queue is passed in so a worker keeps serving the queue it was
        started with even if ``reset`` swaps in a fresh one.
        """
        while True:
            item: tuple[str, Callable[[JobContext], Any]] | None = work_queue.get()
            if item is None:
                work_queue.task_done()
                return
            job_id, fn = item
            with self._lock:
                self._running[job_id] = time.monotonic()
            try:
                self._run(job_id, fn)
            finally:
                with self._lock:
                    self._running.pop(job_id, None)
                work_queue.task_done()

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
        except JobCancelledError:
            self._finish(job_id, status="cancelled", message="Cancelled")
        except Exception as e:
            logging.error(f"Job {job_id} failed: {e}")
            self._finish(job_id, status="failed", message=f"Failed: {e}", error=str(e))

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a running/queued job."""
        with self._lock:
            event: threading.Event | None = self._cancel_events.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    def request_cancel(self) -> None:
        """Request cancellation of every known job (used during shutdown)."""
        with self._lock:
            events: list[threading.Event] = list(self._cancel_events.values())
        for event in events:
            event.set()

    def running_jobs(self) -> list[RunningJob]:
        """Return running jobs, longest-running first.

        Queued jobs are not included; ``drop_queued`` cancels those instead.
        """
        now: float = time.monotonic()
        with self._lock:
            running: list[RunningJob] = [
                RunningJob(
                    id=job_id,
                    kind=self._jobs[job_id].kind if job_id in self._jobs else "unknown",
                    elapsed=now - started_at,
                )
                for job_id, started_at in self._running.items()
            ]
        return sorted(running, key=lambda job: job.elapsed, reverse=True)

    def drop_queued(self, *, notify: bool = True) -> int:
        """Remove queued jobs without running them.

        Args:
            notify: Publish a ``job_finished`` event for each dropped job so a
                connected browser marks it cancelled instead of waiting forever.

        Returns:
            The number of jobs dropped.
        """
        dropped: int = 0
        while True:
            try:
                item: tuple[str, Callable[[JobContext], Any]] | None = (
                    self._queue.get_nowait()
                )
            except queue.Empty:
                return dropped
            if item is not None:
                job_id, _fn = item
                dropped += 1
                if notify:
                    self._finish(job_id, status="cancelled", message="Cancelled")
            self._queue.task_done()

    def wait_idle(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for every running job to return.

        Returns:
            True when no job is running, False when the timeout expired first.
        """
        deadline: float = time.monotonic() + timeout
        while True:
            with self._lock:
                if not self._running:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_IDLE_POLL_INTERVAL)

    def stop_workers(self) -> None:
        """Ask idle workers to exit; running workers die with the process."""
        with self._workers_lock:
            workers: list[threading.Thread] = list(self._workers)
            self._workers = []
        for _ in workers:
            self._queue.put(None)
        for worker in workers:
            worker.join(timeout=0.2)

    def shutdown(self, grace: float = 10.0) -> list[RunningJob]:
        """Cancel outstanding jobs, wait up to ``grace``, then stop the pool.

        Returns:
            The jobs that were still running when the grace period expired.
        """
        self._closed = True
        self.request_cancel()
        self.drop_queued()
        idle: bool = self.wait_idle(grace)
        survivors: list[RunningJob] = [] if idle else self.running_jobs()
        for job in survivors:
            logging.warning(f"Abandoning running job {job.describe()}.")
        self.stop_workers()
        return survivors

    def reset(self) -> None:
        """Recreate the worker pool after a previous shutdown.

        Used by the app lifespan so the manager survives repeated
        startup/shutdown cycles (e.g. across test client instances). A fresh
        queue means sentinels left behind by a worker that is still finishing
        an abandoned job can never be consumed by the next pool.
        """
        self.drop_queued(notify=False)
        self.stop_workers()
        self._queue = queue.Queue()
        with self._lock:
            self._jobs.clear()
            self._cancel_events.clear()
            self._running.clear()
        self._closed = False

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            event: threading.Event | None = self._cancel_events.get(job_id)
        return event is not None and event.is_set()

    def _get_cancel_event(self, job_id: str) -> threading.Event:
        with self._lock:
            event: threading.Event | None = self._cancel_events.get(job_id)
        if event is None:
            event = threading.Event()
        return event

    def _prune_finished(self) -> None:
        """Remove jobs with terminal status older than ``_JOB_PRUNE_TTL``."""
        now: float = time.time()
        with self._lock:
            stale: list[str] = [
                jid for jid, job in self._jobs.items()
                if job.status in ("done", "failed", "cancelled")
                and now - job.created_at > _JOB_PRUNE_TTL
            ]
            for jid in stale:
                del self._jobs[jid]
                self._cancel_events.pop(jid, None)

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
        self._prune_finished()
