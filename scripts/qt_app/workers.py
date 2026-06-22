from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from scripts.helpers import (
    ChapterSequence,
    MakeFormatedDataFrame,
    MakeNameAndCSV,
    NameProbe,
)


class FfprobeWorker(QThread):
    """Run ffprobe operations in a background thread."""

    resultReady = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, fn: Callable, *args: object, **kwargs: object) -> None:
        super().__init__()
        self._fn: Callable = fn
        self._args: tuple[object, ...] = args
        self._kwargs: dict[str, object] = kwargs

    def run(self) -> None:
        try:
            result: object = self._fn(*self._args, **self._kwargs)
            self.resultReady.emit(result)
        except Exception as e:
            logging.error(f"FfprobeWorker error: {e}")
            self.error.emit(str(e))


class CompileStatsWorker(QThread):
    """Run Compile Stats processing in a background thread."""

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict, list)

    def __init__(self, video_paths: list[Path], wrestler_names: list[str]) -> None:
        super().__init__()
        self.video_paths: list[Path] = video_paths
        self.wrestler_names: list[str] = wrestler_names

    def run(self) -> None:
        import multiprocessing

        wrestler_to_csv: dict[str, list[str]] = {name: [] for name in self.wrestler_names}
        processing_errors: list[str] = []
        total: int = len(self.video_paths)

        for i, vid_path in enumerate(self.video_paths):
            try:
                name: str | None
                data: str
                name, data = MakeNameAndCSV(vid_path)
                if name is None:
                    processing_errors.append(f"Error processing: {vid_path.name}")
                elif name not in wrestler_to_csv:
                    processing_errors.append(f"Unknown wrestler '{name}' in {vid_path.name}")
                else:
                    wrestler_to_csv[name].append(data)
            except Exception as e:
                processing_errors.append(f"{vid_path.name}: {e}")

            self.progress.emit(i + 1, total, vid_path.name)

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
            df = MakeFormatedDataFrame(self.csv_path)
            self.resultReady.emit(df)
        except Exception as e:
            logging.error(f"LoadDataWorker error: {e}")
            self.error.emit(str(e))
