"""End-to-end shutdown regressions: stopping the server must always return.

Two failure modes are covered:

* the WebSocket handler used to await only the event queue, so uvicorn's
  graceful shutdown waited for it forever (it now watches for disconnects);
* a job stuck in a step that ignores cancellation used to hold the lifespan
  shutdown open, because uvicorn never bounds ``lifespan.shutdown()``. The app
  now cancels, waits a bounded grace, and abandons what is left.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from websockets.sync.client import connect

sys.path.append(str(Path(__file__).parent.parent))

REPO_ROOT: Path = Path(__file__).parent.parent

# Test-only ASGI wrapper: the real app, plus a job that ignores cancellation.
STUCK_APP_SOURCE: str = '''\
"""Start a job that never checks for cancellation, then serve the real app."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from scripts.web import app as webapp

_original = webapp.app.router.lifespan_context


@asynccontextmanager
async def _lifespan(app):
    async with _original(app):
        webapp.job_manager.submit("stuck", lambda ctx: time.sleep(120))
        yield


webapp.app.router.lifespan_context = _lifespan
app = webapp.app
'''


def _free_port() -> int:
    """Reserve and release a loopback port for the child server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a child server rooted in ``tmp_path``."""
    env: dict[str, str] = {
        **os.environ,
        "MATSTAT_ROOT": str(tmp_path),
        "MATSTAT_AUTH": "off",
        "MATSTAT_LOG_FILE": str(tmp_path / "MatStat.log"),
        "MATSTAT_VISIBLE_LOGGING": "",
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])
        ),
    }
    env.update(extra or {})
    return env


def _start_server(port: int, env: dict[str, str], target: str) -> subprocess.Popen[bytes]:
    """Launch uvicorn with ``target`` on ``port``."""
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            target,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready(port: int, proc: subprocess.Popen[bytes]):
    """Block until the child accepts a WebSocket connection, then return it.

    The caller owns the connection; keeping it open during SIGTERM is what
    exercises the handler's disconnect path.
    """
    deadline: float = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited during startup (code {proc.returncode})")
        try:
            connection = connect(f"ws://127.0.0.1:{port}/ws", open_timeout=1.0)
        except Exception:
            time.sleep(0.1)
            continue
        try:
            assert connection.recv(timeout=5.0) is not None
        except AssertionError:
            connection.close()
            raise
        return connection
    raise AssertionError("server never accepted a WebSocket connection")


def test_sigterm_with_open_websocket_exits(tmp_path) -> None:
    port: int = _free_port()
    log_file: Path = tmp_path / "MatStat.log"
    proc: subprocess.Popen[bytes] = _start_server(
        port, _server_env(tmp_path), "scripts.web.app:app"
    )
    connection = None
    try:
        connection = _wait_ready(port, proc)

        proc.terminate()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            pytest.fail("server did not exit within 10s of SIGTERM with an open /ws socket")

        # uvicorn's shutdown diagnostics must reach the app log, otherwise a
        # slow stop looks like a silent freeze.
        assert "Shutting down" in log_file.read_text()
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10.0)


def test_sigterm_with_stuck_job_exits(tmp_path) -> None:
    """A job that ignores cancellation must not hold the process open."""
    port: int = _free_port()
    app_dir: Path = tmp_path / "appmod"
    app_dir.mkdir()
    (app_dir / "stuck_app.py").write_text(STUCK_APP_SOURCE)
    env: dict[str, str] = _server_env(tmp_path, {"MATSTAT_SHUTDOWN_GRACE": "0.5"})
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(app_dir), env["PYTHONPATH"]])
    )
    log_file: Path = tmp_path / "MatStat.log"
    proc: subprocess.Popen[bytes] = _start_server(port, env, "stuck_app:app")
    connection = None
    try:
        connection = _wait_ready(port, proc)

        begin: float = time.monotonic()
        proc.terminate()
        try:
            proc.wait(timeout=20.0)
        except subprocess.TimeoutExpired:
            pytest.fail("server did not exit within 20s of SIGTERM with a stuck job")
        elapsed: float = time.monotonic() - begin

        log: str = log_file.read_text()
        # uvicorn re-raises the captured SIGTERM after a graceful stop, so the
        # exit status is -SIGTERM on a clean shutdown (0 if it exits directly).
        assert proc.returncode in (0, -signal.SIGTERM)
        assert elapsed < 15.0
        assert "abandoning 1 running job(s)" in log
        assert "Shutdown did not finish" not in log
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10.0)


def test_sigterm_with_no_jobs_is_quick(tmp_path) -> None:
    """The idle path stays fast: no grace is paid when nothing is running."""
    port: int = _free_port()
    proc: subprocess.Popen[bytes] = _start_server(
        port,
        _server_env(tmp_path, {"MATSTAT_SHUTDOWN_GRACE": "10"}),
        "scripts.web.app:app",
    )
    connection = None
    try:
        connection = _wait_ready(port, proc)
        begin: float = time.monotonic()
        proc.terminate()
        proc.wait(timeout=10.0)
        assert time.monotonic() - begin < 5.0
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10.0)
