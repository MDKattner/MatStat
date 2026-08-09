from __future__ import annotations

import logging
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

# Matches ffmpeg's stderr progress marker: time=00:01:23.45
_TIME_RE: re.Pattern[str] = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")


@dataclass(frozen=True)
class FfmpegResult:
    """Result of a finished ffmpeg process."""

    success: bool
    message: str
    stderr: str = field(default="")


class JobCancelledError(Exception):
    """Raised by ``run_ffmpeg`` when the cancel event is set mid-process.

    Job bodies let this propagate to ``JobManager._run``, which records the
    job as ``cancelled`` instead of ``failed``.
    """


def _parse_progress(line: str) -> int | None:
    """Extract total elapsed seconds from an ffmpeg stderr progress line.

    Args:
        line: A single stderr line from ffmpeg (e.g. "frame= 120 time=00:01:23.45").

    Returns:
        The elapsed time in seconds, or None if the line has no progress marker.
    """
    match: re.Match[str] | None = _TIME_RE.search(line)
    if match is None:
        return None
    hours: int = int(match.group(1))
    minutes: int = int(match.group(2))
    seconds: int = int(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def run_ffmpeg(
    cmd: list[str],
    description: str = "",
    on_progress: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> FfmpegResult:
    """Run an ffmpeg command in the current thread, parsing progress from stderr.

    This is the blocking ffmpeg runner used by the web jobs. It is
    blocking (call it from a job thread) but reports ``time=`` progress via
    ``on_progress(seconds, description)`` and honours ``cancel_event`` by
    killing the process.

    Args:
        cmd: The command and arguments (e.g. ['ffmpeg', '-i', ...]).
        description: A human-readable label for this operation.
        on_progress: Optional callback receiving (elapsed_seconds, description).
        cancel_event: Optional event; when set, the process is killed.

    Returns:
        An FfmpegResult describing the outcome.

    Raises:
        JobCancelledError: If ``cancel_event`` is set while the process runs.
    """
    logging.debug(f"ffmpeg start ({description or 'unspecified'}): {' '.join(cmd)}")

    proc: subprocess.Popen[bytes] = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stderr_lines: list[str] = []
    stderr_lock: threading.Lock = threading.Lock()

    def _read_stderr() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line: str = raw.decode("utf-8", errors="replace").rstrip("\n")
            with stderr_lock:
                stderr_lines.append(line)
            if on_progress is not None:
                elapsed: int | None = _parse_progress(line)
                if elapsed is not None:
                    on_progress(elapsed, description)

    reader: threading.Thread = threading.Thread(target=_read_stderr, daemon=True)
    reader.start()

    while True:
        try:
            exit_code: int = proc.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                logging.info(f"Cancelling ffmpeg: {description or 'unspecified'}")
                proc.kill()
                proc.wait()
                reader.join(timeout=1.0)
                raise JobCancelledError(description or "ffmpeg cancelled")

    reader.join(timeout=1.0)

    with stderr_lock:
        full_stderr: str = "\n".join(stderr_lines)

    if exit_code == 0:
        logging.info(f"ffmpeg completed: {description or 'unspecified'}")
        return FfmpegResult(success=True, message=description or "Completed", stderr=full_stderr)

    error_msg: str = full_stderr.strip() or f"Exit code {exit_code}"
    logging.error(f"ffmpeg failed ({description or 'unspecified'}): {error_msg}")
    return FfmpegResult(success=False, message=error_msg, stderr=full_stderr)
