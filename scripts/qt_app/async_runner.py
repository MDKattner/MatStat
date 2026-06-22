from __future__ import annotations

import logging
from typing import ClassVar

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


class AsyncFfmpegRunner(QObject):
    """Run ffmpeg/ffprobe commands asynchronously via QProcess."""

    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(int, str)

    _cmd_type: ClassVar[str] = "ffmpeg"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_ready_read)
        self._process.finished.connect(self._on_finished)
        self._description: str = ""

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
        logging.info(f"Starting ffmpeg: {' '.join(cmd)}")
        self._process.start(cmd[0], cmd[1:])

    def cancel(self) -> None:
        """Kill the running process."""
        if self._process.state() != QProcess.ProcessState.NotRunning:
            logging.info(f"Cancelling ffmpeg process: {self._description}")
            self._process.kill()
            self._process.waitForFinished(3000)

    def _on_ready_read(self) -> None:
        data: bytes = self._process.readAllStandardOutput().data()
        decoded: str = data.decode("utf-8", errors="replace")

        for line in decoded.splitlines():
            if "time=" in line:
                self._parse_progress(line)

    def _parse_progress(self, line: str) -> None:
        import re

        match = re.search(r"time=(\d+):(\d+):(\d+)\.(\d+)", line)
        if match:
            hours: int = int(match.group(1))
            minutes: int = int(match.group(2))
            seconds: int = int(match.group(3))
            total_seconds: float = hours * 3600 + minutes * 60 + seconds
            self.progress.emit(int(total_seconds), self._description)

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        success: bool = exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit
        stderr_output: str = ""
        stdout_output: str = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        stderr_data: bytes = self._process.readAllStandardError().data()
        if stderr_data:
            stderr_output = stderr_data.decode("utf-8", errors="replace")

        if success:
            logging.info(f"ffmpeg completed: {self._description}")
            self.finished.emit(True, stdout_output)
        else:
            error_msg: str = stderr_output or f"Exit code {exit_code}"
            logging.error(f"ffmpeg failed ({self._description}): {error_msg}")
            self.finished.emit(False, error_msg)

    @property
    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning


class AsyncFfprobeRunner(AsyncFfmpegRunner):
    """Run ffprobe commands asynchronously via QProcess."""

    _cmd_type: ClassVar[str] = "ffprobe"

    def run(self, cmd: list[str], description: str = "") -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            logging.warning(f"AsyncFfprobeRunner is already running: {self._description}")
            return

        self._description = description or " ".join(cmd[:4])
        logging.info(f"Starting ffprobe: {' '.join(cmd)}")
        self._process.start(cmd[0], cmd[1:])
