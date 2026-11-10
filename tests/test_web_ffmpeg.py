"""Tests for the blocking ffmpeg runner — progress parsing and cancellation."""

import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web.ffmpeg import FfmpegResult, JobCancelledError, _parse_progress, run_ffmpeg


class _FakeProc:
    """A fake subprocess that yields canned stderr lines and an exit code.

    When ``hang`` is True, ``wait(timeout=...)`` always times out so the
    caller's cancellation check runs (mirrors a long-running ffmpeg).
    """

    def __init__(self, stderr_lines: list[bytes], exit_code: int = 0, hang: bool = False) -> None:
        self.stderr = iter(stderr_lines)
        self._exit_code: int = exit_code
        self._hang: bool = hang
        self.killed: bool = False

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            return self._exit_code
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        return self._exit_code

    def kill(self) -> None:
        self.killed = True


class TestParseProgress:
    """Tests for _parse_progress — extracting elapsed seconds from stderr."""

    def test_parses_time_marker(self) -> None:
        assert _parse_progress("frame= 120 time=00:01:23.45 bitrate=1234kbits/s") == 83

    def test_parses_hours(self) -> None:
        assert _parse_progress("time=01:02:03.99") == 3723

    def test_no_marker_returns_none(self) -> None:
        assert _parse_progress("frame= 120 bitrate=1234kbits/s") is None

    def test_empty_line_returns_none(self) -> None:
        assert _parse_progress("") is None


class TestRunFfmpeg:
    """Tests for run_ffmpeg — success, failure, progress, and cancellation."""

    def test_success_reports_progress(self, monkeypatch) -> None:
        proc = _FakeProc(
            [
                b"frame= 10 time=00:00:03.00 bitrate=1kbits/s\n",
                b"frame= 20 time=00:00:05.00 bitrate=1kbits/s\n",
            ],
            exit_code=0,
        )
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
        seen: list[int] = []
        result: FfmpegResult = run_ffmpeg(
            ["ffmpeg", "-i", "in"], description="Test op", on_progress=lambda s, _d: seen.append(s)
        )
        assert result.success is True
        assert result.message == "Test op"
        assert seen == [3, 5]

    def test_failure_returns_stderr_message(self, monkeypatch) -> None:
        proc = _FakeProc([b"Error: no such file\n"], exit_code=1)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
        result: FfmpegResult = run_ffmpeg(["ffmpeg", "-i", "missing"], description="Test op")
        assert result.success is False
        assert "Error: no such file" in result.message

    def test_failure_without_stderr_reports_exit_code(self, monkeypatch) -> None:
        proc = _FakeProc([], exit_code=2)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
        result: FfmpegResult = run_ffmpeg(["ffmpeg"], description="Test op")
        assert result.success is False
        assert "Exit code 2" in result.message

    def test_cancel_event_raises_job_cancelled(self, monkeypatch) -> None:
        proc = _FakeProc([b"time=00:00:01.00\n"], exit_code=0, hang=True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
        cancel_event: threading.Event = threading.Event()
        cancel_event.set()
        with pytest.raises(JobCancelledError):
            run_ffmpeg(["ffmpeg"], description="Test op", cancel_event=cancel_event)
        assert proc.killed is True
