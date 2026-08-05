from __future__ import annotations

import logging
import re

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


class AsyncFfmpegRunner(QObject):
    """Run ffmpeg commands asynchronously via QProcess."""

    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(int, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)
        self._description: str = ""
        self._stderr_buf: str = ""

    def run(self, cmd: list[str], description: str = "") -> None:
        """Start an ffmpeg process.

        Args:
            cmd: The command and arguments (e.g. ['ffmpeg', '-i', ...]).
            description: A human-readable label for this operation.
        """
        if self._process.state() != QProcess.ProcessState.NotRunning:
            logging.warning(f"AsyncFfmpegRunner is already running a process: {self._description}")
            return

        self._description = description or " ".join(cmd[:4])
        self._stderr_buf = ""
        logging.info(f"Starting ffmpeg: {' '.join(cmd)}")
        self._process.start(cmd[0], cmd[1:])

    def cancel(self) -> None:
        """Kill the running process."""
        if self._process.state() != QProcess.ProcessState.NotRunning:
            logging.info(f"Cancelling ffmpeg process: {self._description}")
            self._process.kill()
            self._process.waitForFinished(3000)

    def _on_stdout(self) -> None:
        data: bytes = self._process.readAllStandardOutput().data()
        decoded: str = data.decode("utf-8", errors="replace")
        for line in decoded.splitlines():
            if "time=" in line:
                self._parse_progress(line)

    def _on_stderr(self) -> None:
        data: bytes = self._process.readAllStandardError().data()
        self._stderr_buf += data.decode("utf-8", errors="replace")

    def _parse_progress(self, line: str) -> None:
        match = re.search(r"time=(\d+):(\d+):(\d+)\.(\d+)", line)
        if match:
            hours: int = int(match.group(1))
            minutes: int = int(match.group(2))
            seconds: int = int(match.group(3))
            total_seconds: float = hours * 3600 + minutes * 60 + seconds
            self.progress.emit(int(total_seconds), self._description)

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        success: bool = exit_code == 0
        stdout_output: str = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")

        if success:
            logging.info(f"ffmpeg completed: {self._description}")
            self.finished.emit(True, stdout_output)
        else:
            error_msg: str = self._stderr_buf.strip() or f"Exit code {exit_code}"
            logging.error(f"ffmpeg failed ({self._description}): {error_msg}")
            self.finished.emit(False, error_msg)

    @property
    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning
