from __future__ import annotations

import asyncio
import logging
from typing import Any

# Wire format (JSON dicts pushed to every connected browser):
#   {"type": "log", "level": "INFO", "message": "..."}
#   {"type": "job_started", "id", "kind", "status", "message"}
#   {"type": "job_progress", "id", "progress", "message"}
#   {"type": "job_finished", "id", "status", "message", "error"}


class EventHub:
    """Fan-out hub that publishes event dicts to subscribed asyncio queues.

    ``publish`` is thread-safe: job workers and logging handlers may call it
    from any thread; events are marshalled onto the event loop via
    ``call_soon_threadsafe``. This is the web equivalent of the Qt
    ``LogBridge``/signal pattern.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the hub to the running event loop (call during startup)."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a new client queue and return it."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """Queue an event for every connected client (thread-safe)."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        for queue in list(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event so a slow client never blocks the app.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)


class WebLogHandler(logging.Handler):
    """A logging handler that forwards formatted records to the EventHub.

    Mirrors the Qt ``QtLogHandler`` → ``LogBridge`` pattern: every log record
    is pushed to the browser as a ``log`` event.
    """

    def __init__(self, hub: EventHub) -> None:
        super().__init__()
        self._hub: EventHub = hub
        self.setFormatter(logging.Formatter("%(asctime)s:%(levelname)s:%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._hub.publish({
                "type": "log",
                "level": record.levelname,
                "message": self.format(record),
            })
        except Exception:
            pass
