"""Tests for tracked child processes — registration, kill, and shutdown."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts import processes


class _FakeProc:
    """Stand-in for ``subprocess.Popen`` that records kill calls.

    ``poll`` returns a canned exit code so ``kill_process`` can tell a live
    child from a reaped one without spawning anything.
    """

    def __init__(self, returncode: int | None = None, pid: int | None = None) -> None:
        self.returncode: int | None = returncode
        if pid is not None:
            self.pid = pid
        self.killed: bool = False

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure tests never leak tracked processes into each other."""
    yield
    for proc in list(processes._ACTIVE):
        processes.unregister_process(proc)


class TestRegistry:
    def test_register_and_unregister(self) -> None:
        proc = _FakeProc()
        assert processes.active_count() == 0

        processes.register_process(proc)
        assert processes.active_count() == 1

        processes.unregister_process(proc)
        assert processes.active_count() == 0

    def test_unregister_unknown_process_is_harmless(self) -> None:
        processes.unregister_process(_FakeProc())


class TestKillProcess:
    def test_kills_live_child(self) -> None:
        proc = _FakeProc(returncode=None)
        processes.kill_process(proc)
        assert proc.killed is True

    def test_skips_already_exited_child(self) -> None:
        proc = _FakeProc(returncode=0)
        processes.kill_process(proc)
        assert proc.killed is False


class TestTerminateActive:
    def test_kills_every_registered_child(self) -> None:
        first = _FakeProc(returncode=None)
        second = _FakeProc(returncode=None)
        processes.register_process(first)
        processes.register_process(second)

        count: int = processes.terminate_active()

        assert count == 2
        assert first.killed and second.killed

    def test_noop_when_nothing_is_tracked(self) -> None:
        assert processes.terminate_active() == 0

    def test_real_child_is_killed(self) -> None:
        """A real ``sleep`` child in its own session dies on terminate_active."""
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        processes.register_process(proc)
        try:
            assert processes.active_count() == 1
            processes.terminate_active()
            assert proc.wait(timeout=5.0) != 0
        finally:
            processes.unregister_process(proc)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)


class TestRunTracked:
    def test_returns_captured_output(self) -> None:
        result: subprocess.CompletedProcess[str] = processes.run_tracked(
            [sys.executable, "-c", "print('hello')"],
            description="echo",
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "hello"
        assert processes.active_count() == 0

    def test_check_raises_on_failure(self) -> None:
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            processes.run_tracked(
                [sys.executable, "-c", "import sys; sys.exit(3)"],
                check=True,
                description="fail",
            )
        assert excinfo.value.returncode == 3
        assert processes.active_count() == 0

    def test_no_check_returns_failure(self) -> None:
        result: subprocess.CompletedProcess[str] = processes.run_tracked(
            [sys.executable, "-c", "import sys; sys.exit(4)"]
        )
        assert result.returncode == 4

    def test_child_is_registered_while_running(self, monkeypatch) -> None:
        """The registry holds the child for the whole call, not just at start."""
        seen: list[int] = []

        class _Probe:
            def __init__(self, real: subprocess.Popen[str]) -> None:
                self._real = real

            def communicate(self):
                seen.append(processes.active_count())
                return self._real.communicate()

            @property
            def returncode(self) -> int:
                return self._real.returncode

        real_popen = subprocess.Popen
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **k: _Probe(real_popen(*a, **k)),
        )

        processes.run_tracked([sys.executable, "-c", "pass"])
        assert seen == [1]
        assert processes.active_count() == 0

    def test_unregisters_even_when_communicate_raises(self, monkeypatch) -> None:
        class _Broken:
            returncode: int = 0

            def communicate(self):
                raise OSError("pipe exploded")

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Broken())
        with pytest.raises(OSError):
            processes.run_tracked(["true"])
        assert processes.active_count() == 0


class TestKillRealGroup:
    def test_kills_grandchild_too(self, tmp_path: Path) -> None:
        """Killing the group stops grandchildren the tool spawned itself."""
        pid_file: Path = tmp_path / "grandchild.pid"
        child_code: str = (
            "import pathlib, subprocess, sys, time;"
            "gc = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)']);"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(gc.pid));"
            "time.sleep(30)"
        )
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            [sys.executable, "-c", child_code], start_new_session=True
        )
        processes.register_process(proc)
        try:
            deadline: float = time.monotonic() + 10.0
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert pid_file.exists(), "grandchild never started"
            grandchild_pid: int = int(pid_file.read_text())

            processes.terminate_active()
            assert proc.wait(timeout=5.0) != 0

            # The grandchild shares the child's process group, so SIGKILL on
            # the group must take it down; zombies are reaped by init.
            gone: bool = False
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild_pid, 0)
                except ProcessLookupError:
                    gone = True
                    break
                time.sleep(0.05)
            assert gone, "grandchild survived the process-group kill"
        finally:
            processes.unregister_process(proc)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)
