from __future__ import annotations

import logging
import multiprocessing
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from scripts.helpers import MakeFormattedDataFrame, MakeNameAndCSV


def _ProcessVideoSafely(vid_path: Path) -> tuple[str | None, str]:
    """Safely extract a wrestler name and CSV data from a video.

    Module-level so it can be pickled by multiprocessing.Pool. Mirrors the
    `process_video_safely` wrapper used by the web Compile Stats job.

    Args:
        vid_path: The path to the tagged video file.

    Returns:
        A tuple of (name, data) on success, or (None, error_message) on failure.
    """
    try:
        name, data = MakeNameAndCSV(vid_path)
        return (name, data)
    except Exception as e:
        error_msg: str = f"Failed to process video '{vid_path.name}': {e}"
        logging.error(error_msg)
        return (None, error_msg)


class CompileStatsWorker(QThread):
    """Run Compile Stats processing in a background thread."""

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict, list)

    def __init__(self, video_paths: list[Path], wrestler_names: list[str]) -> None:
        super().__init__()
        self.video_paths: list[Path] = video_paths
        self.wrestler_names: list[str] = wrestler_names

    def run(self) -> None:
        wrestler_to_csv: dict[str, list[str]] = {name: [] for name in self.wrestler_names}
        processing_errors: list[str] = []
        total: int = len(self.video_paths)

        with multiprocessing.Pool() as pool:
            for i, (name, data) in enumerate(pool.imap(_ProcessVideoSafely, self.video_paths)):
                if self.isInterruptionRequested():
                    pool.terminate()
                    return
                if name is None:
                    processing_errors.append(data or f"Error processing: {self.video_paths[i].name}")
                elif name not in wrestler_to_csv:
                    processing_errors.append(f"Unknown wrestler '{name}' in {self.video_paths[i].name}")
                else:
                    wrestler_to_csv[name].append(data)
                self.progress.emit(i + 1, total, self.video_paths[i].name)

        result: dict[str, str] = {
            name: "\n".join(entries) for name, entries in wrestler_to_csv.items()
        }
        self.finished.emit(result, processing_errors)


class LoadDataWorker(QThread):
    """Load a wrestler CSV into a DataFrame in a background thread."""

    resultReady = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, csv_path: Path) -> None:
        super().__init__()
        self.csv_path: Path = csv_path

    def run(self) -> None:
        try:
            df = MakeFormattedDataFrame(self.csv_path)
            self.resultReady.emit(df)
        except Exception as e:
            logging.error(f"LoadDataWorker error: {e}")
            self.error.emit(str(e))
