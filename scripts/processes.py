"""Track child processes so server shutdown can stop them.

ffmpeg, ffprobe, mkvmerge, and mkvpropedit all run as child processes of the
web app. A child stuck on a corrupt file or a slow mount would otherwise keep
running after the operator stops the server, or hold a job thread open while
the process waits for it. Every child started through this module is
registered in a process group, and ``terminate_active`` kills the groups when
the app shuts down.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from collections.abc import Sequence
from typing import Any

# Live children (real Popen objects, plus test doubles).
_ACTIVE: set[subprocess.Popen[Any]] = set()
_LOCK: threading.Lock = threading.Lock()


def register_process(proc: subprocess.Popen[Any]) -> None:
    """Record a live child process so shutdown can terminate it."""
    with _LOCK:
        _ACTIVE.add(proc)


def unregister_process(proc: subprocess.Popen[Any]) -> None:
    """Forget a child process once it has been reaped."""
    with _LOCK:
        _ACTIVE.discard(proc)


def active_count() -> int:
    """Return how many child processes are currently tracked."""
    with _LOCK:
        return len(_ACTIVE)


def _already_exited(proc: subprocess.Popen[Any]) -> bool:
    """Best-effort check that does not assume a real Popen (tests use stubs)."""
    poll: Any = getattr(proc, "poll", None)
    if poll is None:
        return False
    try:
        return poll() is not None
    except Exception:
        return False


def kill_process(proc: subprocess.Popen[Any]) -> None:
    """Kill a child and its process group, falling back to ``Popen.kill``.

    Children are started with ``start_new_session=True``, so killing the group
    also stops anything the tool itself spawned. Test doubles without a pid
    are handled by the ``kill`` fallback.
    """
    if _already_exited(proc):
        return
    pid: int | None = getattr(proc, "pid", None)
    if pid is not None:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def terminate_active() -> int:
    """Kill every tracked child process and return how many were signalled.

    Called at the start of application shutdown, before waiting for job
    threads, so a blocked ``subprocess`` call in a job returns promptly. Child
    processes are reaped by the thread that started them (``communicate`` or
    ``wait``); this function only signals them.
    """
    with _LOCK:
        procs: list[subprocess.Popen[Any]] = list(_ACTIVE)
    for proc in procs:
        kill_process(proc)
    if procs:
        logging.info(f"Stopped {len(procs)} child process(es) during shutdown.")
    return len(procs)


def run_tracked(
    cmd: Sequence[str],
    *,
    check: bool = False,
    description: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run a short-lived tool with captured text output and kill tracking.

    This is the drop-in replacement for ``subprocess.run(cmd, capture_output=
    True, text=True)`` in the helpers. Output is buffered in memory, so only
    use it for tools whose output is small (ffprobe, mkvmerge, mkvpropedit).

    Args:
        cmd: The command and arguments.
        check: Raise ``CalledProcessError`` when the exit code is non-zero.
        description: Human-readable label for debug logs.

    Returns:
        A ``CompletedProcess`` with captured ``stdout`` and ``stderr``.

    Raises:
        subprocess.CalledProcessError: If ``check`` is set and the tool fails.
    """
    argv: list[str] = list(cmd)
    logging.debug(f"run ({description or argv[0]}): {' '.join(argv)}")
    proc: subprocess.Popen[str] = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    register_process(proc)
    try:
        stdout, stderr = proc.communicate()
    finally:
        unregister_process(proc)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, argv, output=stdout, stderr=stderr
        )
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
