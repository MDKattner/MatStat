from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from scripts.helpers import (
    MakeFormatedDataFrame,
    cfg_dir,
    clips_dir,
    csv_dir,
    taged_dir,
    tmp_dir,
)
from scripts.qt_app.widgets import ConfigComboBox


class CombineClipsWorker(QThread):
    """Extract and concatenate clips in a background thread."""

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        filtered_df: pd.DataFrame,
        wrestler_name: str,
        filter_type: str,
        selected_item: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._filtered_df: pd.DataFrame = filtered_df
        self._wrestler_name: str = wrestler_name
        self._filter_type: str = filter_type
        self._selected_item: str = selected_item

    def run(self) -> None:
        clip_gen_dir: Path = tmp_dir / f"clip_gen_{os.getpid()}"
        clip_gen_dir.mkdir(parents=True, exist_ok=True)

        segment_files: list[Path] = []
        has_errors: bool = False
        total: int = len(self._filtered_df)

        for i, (index, row) in enumerate(self._filtered_df.iterrows()):
            origin_video_name: str = index.split(":")[0]
            input_video_path: Path = taged_dir / origin_video_name
            start_time: int = int(row["Start Time"])
            end_time: int = int(row["End Time"])

            self.progress.emit(
                i + 1, total,
                f"Extracting clip {i + 1}/{total} from {origin_video_name}"
            )

            if not input_video_path.exists():
                logging.warning(f"Source video not found: {input_video_path}")
                has_errors = True
                continue

            segment_path: Path = clip_gen_dir / f"segment_{i}.ts"

            extract_cmd: list[str] = [
                "ffmpeg",
                "-i", str(input_video_path),
                "-ss", str(start_time),
                "-to", str(end_time),
                "-vf", "scale=1280:720,setsar=1",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-y",
                str(segment_path),
            ]

            try:
                subprocess.run(extract_cmd, check=True, capture_output=True, text=True)
                segment_files.append(segment_path)
            except subprocess.CalledProcessError as e:
                logging.error(f"Clip extraction failed: {e.stderr}")
                has_errors = True

        if not segment_files:
            shutil.rmtree(clip_gen_dir, ignore_errors=True)
            self.finished.emit(False, "No clips were successfully extracted.")
            return

        self.progress.emit(total, total, "Concatenating clips...")

        sanitized_item: str = self._selected_item.replace(" ", "_").replace("/", "_")
        final_output: Path = (
            clips_dir
            / f"{self._wrestler_name.replace(' ', '_')}_{self._filter_type.replace(' ', '_')}_{sanitized_item}.mkv"
        )

        list_file: Path = clip_gen_dir / "mylist.txt"
        with open(list_file, "w") as f:
            for seg in segment_files:
                f.write(f"file '{seg.resolve()}'\n")

        concat_cmd: list[str] = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-y",
            str(final_output),
        ]

        try:
            subprocess.run(concat_cmd, check=True, capture_output=True, text=True)
            shutil.rmtree(clip_gen_dir, ignore_errors=True)

            msg: str = f"Created: {final_output.name}"
            if has_errors:
                msg += " (some clips had errors)"
            self.finished.emit(True, msg)
        except subprocess.CalledProcessError as e:
            shutil.rmtree(clip_gen_dir, ignore_errors=True)
            self.finished.emit(False, f"Concatenation failed: {e.stderr}")


class CombineClipsWidget(QWidget):
    """Qt6 GUI equivalent of the 'Combine Clips' script."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout: QVBoxLayout = QVBoxLayout(self)

        header: QLabel = QLabel("Combine Clips")
        header.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)

        desc: QLabel = QLabel(
            "Create a highlight reel by filtering a wrestler's tagged sequences "
            "by starting tie-up, move used, or move defended."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.wrestler_selector: ConfigComboBox = ConfigComboBox(
            cfg_dir / "Wrestlers.config",
            "Wrestler:"
        )
        layout.addWidget(self.wrestler_selector)

        filter_group: QGroupBox = QGroupBox("Filter Type")
        fl: QHBoxLayout = QHBoxLayout(filter_group)
        self.tie_radio: QRadioButton = QRadioButton("Starting Tie")
        self.tie_radio.setChecked(True)
        self.move_radio: QRadioButton = QRadioButton("Move Used")
        self.defend_radio: QRadioButton = QRadioButton("Move Defended")
        fl.addWidget(self.tie_radio)
        fl.addWidget(self.move_radio)
        fl.addWidget(self.defend_radio)
        layout.addWidget(filter_group)

        self._item_selector_ties: ConfigComboBox = ConfigComboBox(
            cfg_dir / "Ties.config",
            "Filter Item:"
        )
        layout.addWidget(self._item_selector_ties)

        self._item_selector_moves: ConfigComboBox = ConfigComboBox(
            cfg_dir / "Moves.config",
            "Filter Item:"
        )
        self._item_selector_moves.setVisible(False)
        layout.addWidget(self._item_selector_moves)

        btn_layout: QHBoxLayout = QHBoxLayout()
        self.find_btn: QPushButton = QPushButton("Find Clips")
        self.find_btn.clicked.connect(self._find_clips)
        btn_layout.addWidget(self.find_btn)

        self.generate_btn: QPushButton = QPushButton("Generate Reel")
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self._generate_reel)
        btn_layout.addWidget(self.generate_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.progress_bar: QProgressBar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label: QLabel = QLabel("Select a wrestler and filter to begin.")
        layout.addWidget(self.status_label)

        result_group: QGroupBox = QGroupBox("Output")
        rl: QVBoxLayout = QVBoxLayout(result_group)

        self.output_label: QLabel = QLabel("")
        self.output_label.setWordWrap(True)
        rl.addWidget(self.output_label)

        self.open_btn: QPushButton = QPushButton("Open Folder")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        rl.addWidget(self.open_btn)

        layout.addWidget(result_group)

    def _connect_signals(self) -> None:
        self.tie_radio.toggled.connect(self._on_filter_type_changed)
        self.move_radio.toggled.connect(self._on_filter_type_changed)
        self.defend_radio.toggled.connect(self._on_filter_type_changed)

    def _on_filter_type_changed(self) -> None:
        ties_visible: bool = self.tie_radio.isChecked()
        self._item_selector_ties.setVisible(ties_visible)
        self._item_selector_moves.setVisible(not ties_visible)

    def _get_filter_type(self) -> str:
        if self.tie_radio.isChecked():
            return "Starting Tie"
        if self.move_radio.isChecked():
            return "Move Used"
        return "Move Defended"

    def _get_selected_item(self) -> str:
        if self.tie_radio.isChecked():
            return self._item_selector_ties.selected
        return self._item_selector_moves.selected

    def _find_clips(self) -> None:
        wrestler_name: str = self.wrestler_selector.selected
        filter_type: str = self._get_filter_type()
        selected_item: str = self._get_selected_item()

        if not wrestler_name:
            self.status_label.setText("Select a wrestler.")
            return

        if not selected_item:
            self.status_label.setText(f"Select a {filter_type.lower()} item.")
            return

        csv_path: Path = csv_dir / f"{wrestler_name}.csv"
        if not csv_path.exists():
            QMessageBox.warning(
                self,
                "Data Not Found",
                f"No compiled data for {wrestler_name}.\nRun 'Compile Stats' first.",
            )
            return

        self.status_label.setText(f"Loading data for {wrestler_name}...")

        try:
            df: pd.DataFrame = MakeFormatedDataFrame(csv_path)
        except Exception as e:
            self.status_label.setText("Failed to load data.")
            QMessageBox.critical(self, "Error", f"Could not load data: {e}")
            return

        if filter_type == "Starting Tie":
            filtered: pd.DataFrame = df[df["Tie Up"] == selected_item]
        elif filter_type == "Move Used":
            filtered = df[df["Team Moves"].apply(lambda m: selected_item in m)]
        else:
            filtered = df[df["Opponent Moves"].apply(lambda m: selected_item in m)]

        if filtered.empty:
            self.status_label.setText(
                f"No sequences found for '{wrestler_name}' / {filter_type} = '{selected_item}'."
            )
            self.generate_btn.setEnabled(False)
            self.output_label.setText("")
            return

        self._filtered_df = filtered
        self._wrestler_name = wrestler_name
        self._filter_type = filter_type
        self._selected_item = selected_item

        count: int = len(filtered)
        self.status_label.setText(f"Found {count} matching sequences.")
        self.output_label.setText(
            f"Matching sequences: {count}\n"
            f"Wrestler: {wrestler_name}\n"
            f"Filter: {filter_type} = {selected_item}"
        )
        self.generate_btn.setEnabled(True)

    def _generate_reel(self) -> None:
        if not hasattr(self, "_filtered_df") or self._filtered_df.empty:
            return

        self.generate_btn.setEnabled(False)
        self.find_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(self._filtered_df))
        self.progress_bar.setValue(0)
        self.status_label.setText("Generating clips...")

        clips_dir.mkdir(parents=True, exist_ok=True)

        self._worker = CombineClipsWorker(
            self._filtered_df,
            self._wrestler_name,
            self._filter_type,
            self._selected_item,
        )
        self._worker.progress.connect(self._on_clip_progress)
        self._worker.finished.connect(self._on_clip_finished)
        self._worker.start()

    def _on_clip_progress(self, current: int, total: int, message: str) -> None:
        self.progress_bar.setValue(current)
        self.progress_bar.setMaximum(total)
        self.status_label.setText(message)

    def _on_clip_finished(self, success: bool, message: str) -> None:
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.find_btn.setEnabled(True)

        if success:
            self.output_label.setText(message)
            self.open_btn.setEnabled(True)
            self.status_label.setText("Highlight reel created.")
        else:
            QMessageBox.critical(self, "Error", message)
            self.status_label.setText("Failed to create highlight reel.")

    def _open_folder(self) -> None:
        clips_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["xdg-open", str(clips_dir)], check=False)
        except FileNotFoundError:
            self.status_label.setText("Could not open folder (xdg-open not found).")
