"""End-to-end shutdown regression: an open /ws socket must not stall SIGTERM.

The WebSocket endpoint used to await only the event queue, so a client
disconnect (or uvicorn's shutdown close) was never observed. uvicorn tracked
the handler as a background task and, because its graceful timeout defaults to
``None``, waited for it forever. This test starts a real server, holds a real
socket open, sends SIGTERM, and asserts the process exits promptly.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from websockets.sync.client import connect

sys.path.append(str(Path(__file__).parent.parent))

REPO_ROOT: Path = Path(__file__).parent.parent


def _free_port() -> int:
    """Reserve and release a loopback port for the child server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_sigterm_with_open_websocket_exits(tmp_path) -> None:
    port: int = _free_port()
    log_file: Path = tmp_path / "MatStat.log"
    env: dict[str, str] = {
        **os.environ,
        "MATSTAT_ROOT": str(tmp_path),
        "MATSTAT_AUTH": "off",
        "MATSTAT_LOG_FILE": str(log_file),
        "MATSTAT_VISIBLE_LOGGING": "",
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])
        ),
    }
    proc: subprocess.Popen[bytes] = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "scripts.web.app:app",
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
    connection = None
    try:
        deadline: float = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"server exited during startup (code {proc.returncode})")
            try:
                connection = connect(f"ws://127.0.0.1:{port}/ws", open_timeout=1.0)
                break
            except Exception:
                time.sleep(0.1)

        assert connection is not None, "server never accepted a WebSocket connection"
        assert connection.recv(timeout=5.0) is not None

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
