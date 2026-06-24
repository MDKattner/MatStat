from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from scripts.helpers import (
    GetVideoCodecs,
    MakeFormattedDataFrame,
    cfg_dir,
    clips_dir,
    csv_dir,
    taged_dir,
    tmp_dir,
)
from scripts.qt_app.widgets import CheckboxListWidget, ConfigComboBox, VideoPreviewPanel


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
        use_stream_copy: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._filtered_df: pd.DataFrame = filtered_df
        self._wrestler_name: str = wrestler_name
        self._filter_type: str = filter_type
        self._selected_item: str = selected_item
        self._use_stream_copy: bool = use_stream_copy

    def run(self) -> None:
        clip_gen_dir: Path = tmp_dir / f"clip_gen_{os.getpid()}"
        clip_gen_dir.mkdir(parents=True, exist_ok=True)

        segment_files: list[Path] = []
        has_errors: bool = False
        total: int = len(self._filtered_df)

        for i, (index, row) in enumerate(self._filtered_df.iterrows()):
            origin_video_name: str = str(index).split(":")[0]
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
            ]

            if self._use_stream_copy:
                codecs: dict[str, str] = GetVideoCodecs(input_video_path)
                vcodec: str = codecs.get("video", "")
                acodec: str = codecs.get("audio", "")
                if vcodec == "h264" and acodec == "aac":
                    extract_cmd += ["-c", "copy", "-avoid_negative_ts", "1"]
                    logging.debug(f"Using stream copy for {input_video_path.name}")
                else:
                    logging.info(
                        f"Codecs ({vcodec}/{acodec}) not compatible with stream copy "
                        f"for {input_video_path.name}; falling back to re-encode"
                    )
                    extract_cmd += [
                        "-vf", "scale=1280:720,setsar=1",
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "23",
                        "-c:a", "aac",
                        "-b:a", "128k",
                    ]
            else:
                extract_cmd += [
                    "-vf", "scale=1280:720,setsar=1",
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                ]

            extract_cmd += ["-y", str(segment_path)]

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


@dataclass
class _BatchCombo:
    wrestler: str
    filter_type: str
    filter_item: str


class BatchCombineClipsWorker(QThread):
    """Process multiple wrestler × filter combinations in a background thread."""

    progress = pyqtSignal(int, int, str)
    combo_finished = pyqtSignal(str, bool, str)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        combos: list[_BatchCombo],
        use_stream_copy: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._combos: list[_BatchCombo] = combos
        self._use_stream_copy: bool = use_stream_copy

    def run(self) -> None:
        total: int = len(self._combos)
        successes: int = 0
        failures: int = 0

        for i, combo in enumerate(self._combos):
            self.progress.emit(i + 1, total, f"[{i+1}/{total}] {combo.wrestler} — {combo.filter_item}")

            csv_path: Path = csv_dir / f"{combo.wrestler}.csv"
            if not csv_path.exists():
                self.combo_finished.emit(combo.wrestler, False, "CSV not found")
                failures += 1
                continue

            try:
                df: pd.DataFrame = MakeFormattedDataFrame(csv_path)
            except Exception as e:
                self.combo_finished.emit(combo.wrestler, False, str(e))
                failures += 1
                continue

            if combo.filter_type == "Starting Tie":
                filtered: pd.DataFrame = df[df["Tie Up"] == combo.filter_item]  # type: ignore[assignment]
            elif combo.filter_type == "Move Used":
                filtered = df[df["Team Moves"].apply(lambda m: combo.filter_item in m)]  # type: ignore[assignment]
            else:
                filtered = df[df["Opponent Moves"].apply(lambda m: combo.filter_item in m)]  # type: ignore[assignment]

            if filtered.empty:
                self.combo_finished.emit(combo.wrestler, False, "No matches")
                failures += 1
                continue

            clip_gen_dir: Path = tmp_dir / f"clip_gen_{os.getpid()}_{i}"
            clip_gen_dir.mkdir(parents=True, exist_ok=True)
            segment_files: list[Path] = []
            has_errors: bool = False

            for j, (index, row) in enumerate(filtered.iterrows()):
                origin_video_name: str = str(index).split(":")[0]
                input_video_path: Path = taged_dir / origin_video_name
                start_time: int = int(row["Start Time"])
                end_time: int = int(row["End Time"])

                if not input_video_path.exists():
                    has_errors = True
                    continue

                segment_path: Path = clip_gen_dir / f"segment_{j}.ts"
                extract_cmd: list[str] = [
                    "ffmpeg", "-i", str(input_video_path),
                    "-ss", str(start_time), "-to", str(end_time),
                ]

                if self._use_stream_copy:
                    codecs: dict[str, str] = GetVideoCodecs(input_video_path)
                    if codecs.get("video") == "h264" and codecs.get("audio") == "aac":
                        extract_cmd += ["-c", "copy", "-avoid_negative_ts", "1"]
                    else:
                        extract_cmd += [
                            "-vf", "scale=1280:720,setsar=1",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                            "-c:a", "aac", "-b:a", "128k",
                        ]
                else:
                    extract_cmd += [
                        "-vf", "scale=1280:720,setsar=1",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                        "-c:a", "aac", "-b:a", "128k",
                    ]

                extract_cmd += ["-y", str(segment_path)]

                try:
                    subprocess.run(extract_cmd, check=True, capture_output=True, text=True)
                    segment_files.append(segment_path)
                except subprocess.CalledProcessError:
                    has_errors = True

            if not segment_files:
                shutil.rmtree(clip_gen_dir, ignore_errors=True)
                self.combo_finished.emit(combo.wrestler, False, "No clips extracted")
                failures += 1
                continue

            sanitized: str = combo.filter_item.replace(" ", "_").replace("/", "_")
            final_output: Path = (
                clips_dir
                / f"{combo.wrestler.replace(' ', '_')}_{combo.filter_type.replace(' ', '_')}_{sanitized}.mkv"
            )

            list_file: Path = clip_gen_dir / "mylist.txt"
            with open(list_file, "w") as f:
                for seg in segment_files:
                    f.write(f"file '{seg.resolve()}'\n")

            concat_cmd: list[str] = [
                "ffmpeg", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c", "copy", "-y", str(final_output),
            ]

            try:
                subprocess.run(concat_cmd, check=True, capture_output=True, text=True)
                msg: str = f"Created: {final_output.name}"
                if has_errors:
                    msg += " (some clips had errors)"
                successes += 1
                self.combo_finished.emit(combo.wrestler, True, msg)
            except subprocess.CalledProcessError as e:
                self.combo_finished.emit(combo.wrestler, False, f"Concat failed: {e.stderr}")
                failures += 1

            shutil.rmtree(clip_gen_dir, ignore_errors=True)

        summary: str = f"Batch complete. {successes} succeeded, {failures} failed."
        self.finished.emit(failures == 0, summary)


class CombineClipsWidget(QWidget):
    """Qt6 GUI equivalent of the 'Combine Clips' script."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filtered_df: pd.DataFrame | None = None
        self._reel_path: Path | None = None
        self._batch_results: list[str] = []
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        main_layout: QVBoxLayout = QVBoxLayout(self)

        header: QLabel = QLabel("Combine Clips")
        header.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        main_layout.addWidget(header)

        desc: QLabel = QLabel(
            "Create a highlight reel by filtering a wrestler's tagged sequences "
            "by starting tie-up, move used, or move defended."
        )
        desc.setWordWrap(True)
        main_layout.addWidget(desc)

        splitter: QSplitter = QSplitter(Qt.Orientation.Horizontal)

        left_scroll: QScrollArea = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(300)

        left_panel: QWidget = QWidget()
        left_layout: QVBoxLayout = QVBoxLayout(left_panel)

        self._build_controls(left_layout)
        left_scroll.setWidget(left_panel)

        right_panel: QWidget = QWidget()
        right_layout: QVBoxLayout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._build_preview(right_layout)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 550])

        main_layout.addWidget(splitter, stretch=1)

    def _build_controls(self, layout: QVBoxLayout) -> None:
        self.wrestler_selector: CheckboxListWidget = CheckboxListWidget(
            cfg_dir / "Wrestlers.config",
            "Wrestlers (check one or more for batch):"
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

        self.stream_copy_cb: QCheckBox = QCheckBox("Fast extraction (stream copy)")
        self.stream_copy_cb.setChecked(True)
        self.stream_copy_cb.setToolTip(
            "Use stream copy when source codecs allow (2-5x faster). "
            "Disable if you need consistent re-encoded output."
        )
        layout.addWidget(self.stream_copy_cb)

        btn_layout: QHBoxLayout = QHBoxLayout()
        self.find_btn: QPushButton = QPushButton("Find Clips")
        self.find_btn.clicked.connect(self._find_clips)
        btn_layout.addWidget(self.find_btn)

        self.generate_btn: QPushButton = QPushButton("Generate Reel")
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self._generate_reel)
        btn_layout.addWidget(self.generate_btn)

        self.batch_gen_btn: QPushButton = QPushButton("Batch Generate All")
        self.batch_gen_btn.setEnabled(False)
        self.batch_gen_btn.clicked.connect(self._batch_generate)
        btn_layout.addWidget(self.batch_gen_btn)

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

        self.play_reel_btn: QPushButton = QPushButton("Play Reel")
        self.play_reel_btn.setEnabled(False)
        self.play_reel_btn.clicked.connect(self._play_reel)
        rl.addWidget(self.play_reel_btn)

        self.open_btn: QPushButton = QPushButton("Open Folder")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        rl.addWidget(self.open_btn)

        layout.addWidget(result_group)
        layout.addStretch()

    def _build_preview(self, layout: QVBoxLayout) -> None:
        preview_label: QLabel = QLabel("Preview")
        preview_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(preview_label)

        self._video_panel: VideoPreviewPanel = VideoPreviewPanel()
        layout.addWidget(self._video_panel, stretch=1)

        seq_label: QLabel = QLabel("Matched Sequences")
        seq_label.setStyleSheet("font-size: 14px; font-weight: bold; margin-top: 8px;")
        layout.addWidget(seq_label)

        self.seq_list: QListWidget = QListWidget()
        self.seq_list.setAlternatingRowColors(True)
        self.seq_list.itemClicked.connect(self._on_sequence_selected)
        self.seq_list.setFixedHeight(180)
        layout.addWidget(self.seq_list)

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

    def _get_selected_wrestlers(self) -> list[str]:
        return self.wrestler_selector.selected

    def _find_clips(self) -> None:
        wrestlers: list[str] = self._get_selected_wrestlers()
        if not wrestlers:
            self.status_label.setText("Select at least one wrestler.")
            return
        wrestler_name: str = wrestlers[0]

        filter_type: str = self._get_filter_type()
        selected_item: str = self._get_selected_item()

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
            df: pd.DataFrame = MakeFormattedDataFrame(csv_path)
        except Exception as e:
            self.status_label.setText("Failed to load data.")
            QMessageBox.critical(self, "Error", f"Could not load data: {e}")
            return

        if filter_type == "Starting Tie":
            filtered: pd.DataFrame = df[df["Tie Up"] == selected_item]  # type: ignore[assignment]
        elif filter_type == "Move Used":
            filtered = df[df["Team Moves"].apply(lambda m: selected_item in m)]  # type: ignore[assignment]
        else:
            filtered = df[df["Opponent Moves"].apply(lambda m: selected_item in m)]  # type: ignore[assignment]

        if filtered.empty:
            self.status_label.setText(
                f"No sequences found for '{wrestler_name}' / {filter_type} = '{selected_item}'."
            )
            self.generate_btn.setEnabled(False)
            self.batch_gen_btn.setEnabled(False)
            self.output_label.setText("")
            self.seq_list.clear()
            self._video_panel._player.setSource(None)
            self._filtered_df = None
            self._reel_path = None
            return

        self._reel_path = None
        self.play_reel_btn.setEnabled(False)
        self._filtered_df = filtered
        self._wrestler_name = wrestler_name
        self._filter_type = filter_type
        self._selected_item = selected_item

        count: int = len(filtered)
        self.status_label.setText(f"Found {count} matching sequences.")
        self.output_label.setText(
            f"Matching sequences: {count}\n"
            f"Wrestler: {wrestler_name}\n"
            f"Filter: {filter_type} = {selected_item}\n"
            f"Batch will process {len(wrestlers)} wrestler(s)."
        )
        self.generate_btn.setEnabled(True)
        self.batch_gen_btn.setEnabled(len(wrestlers) > 1)

        self._populate_sequence_list()

    def _populate_sequence_list(self) -> None:
        self.seq_list.clear()
        if self._filtered_df is None:
            return

        for row_idx, (index, row) in enumerate(self._filtered_df.iterrows()):
            video_name: str = str(index).split(":")[0]
            start_time: int = int(row["Start Time"])
            end_time: int = int(row["End Time"])
            attacking: str = "A" if bool(row["Attacking"]) else "D"
            tie_up: str = str(row["Tie Up"]) or ""
            team_moves_list = row["Team Moves"]
            team_moves: list[str] = team_moves_list if isinstance(team_moves_list, list) else []
            moves_str: str = ", ".join(team_moves[:2])
            if len(team_moves) > 2:
                moves_str += "..."

            text: str = (
                f"{video_name}  [{start_time // 60}:{start_time % 60:02}"
                f"-{end_time // 60}:{end_time % 60:02}]  "
                f"{attacking}  {tie_up}  {moves_str}"
            )
            item: QListWidgetItem = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, row_idx)
            self.seq_list.addItem(item)

    def _on_sequence_selected(self, item: QListWidgetItem) -> None:
        if self._filtered_df is None:
            return

        row_idx: int = item.data(Qt.ItemDataRole.UserRole)
        row = self._filtered_df.iloc[row_idx]
        video_name: str = str(row.name).split(":")[0]
        start_time: int = int(row["Start Time"])
        video_path: Path = taged_dir / video_name

        if video_path.exists():
            self._video_panel.load_video(str(video_path))
            self._video_panel._player.setPosition(start_time * 1000)
            self.status_label.setText(
                f"Previewing: {video_name}  [{start_time // 60}:{start_time % 60:02}]"
            )
        else:
            self.status_label.setText(f"Source video not found: {video_name}")
            logging.warning(f"Source video not found for preview: {video_path}")

    def _generate_reel(self) -> None:
        if self._filtered_df is None or self._filtered_df.empty:
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
            use_stream_copy=self.stream_copy_cb.isChecked(),
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
            sanitized_item: str = self._selected_item.replace(" ", "_").replace("/", "_")
            self._reel_path = (
                clips_dir
                / f"{self._wrestler_name.replace(' ', '_')}_{self._filter_type.replace(' ', '_')}_{sanitized_item}.mkv"
            )
            self.output_label.setText(message)
            self.open_btn.setEnabled(True)
            self.play_reel_btn.setEnabled(True)
            self.status_label.setText("Highlight reel created.")
        else:
            self._reel_path = None
            QMessageBox.critical(self, "Error", message)
            self.status_label.setText("Failed to create highlight reel.")

    def _batch_generate(self) -> None:
        wrestlers: list[str] = self._get_selected_wrestlers()
        filter_type: str = self._get_filter_type()
        selected_item: str = self._get_selected_item()

        if not wrestlers or not selected_item:
            return

        combos: list[_BatchCombo] = [
            _BatchCombo(w, filter_type, selected_item) for w in wrestlers
        ]

        self.generate_btn.setEnabled(False)
        self.batch_gen_btn.setEnabled(False)
        self.find_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(combos))
        self.progress_bar.setValue(0)
        self.status_label.setText(f"Batch generating {len(combos)} reel(s)...")

        clips_dir.mkdir(parents=True, exist_ok=True)

        self._batch_results = []
        self._batch_worker = BatchCombineClipsWorker(
            combos, use_stream_copy=self.stream_copy_cb.isChecked()
        )
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.combo_finished.connect(self._on_batch_combo)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.start()

    def _on_batch_progress(self, current: int, total: int, message: str) -> None:
        self.progress_bar.setValue(current)
        self.progress_bar.setMaximum(total)
        self.status_label.setText(message)

    def _on_batch_combo(self, wrestler: str, success: bool, message: str) -> None:
        icon: str = "✓" if success else "✗"
        self._batch_results.append(f"{icon} {wrestler}: {message}")
        self.output_label.setText("\n".join(self._batch_results[-20:]))

    def _on_batch_finished(self, success: bool, summary: str) -> None:
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.batch_gen_btn.setEnabled(True)
        self.find_btn.setEnabled(True)
        self.status_label.setText(summary)
        self.output_label.setText("\n".join(self._batch_results))

    def _play_reel(self) -> None:
        if self._reel_path is None or not self._reel_path.exists():
            self.status_label.setText("Reel file not found.")
            return
        self._video_panel.load_video(str(self._reel_path))
        self._video_panel._player.play()
        self.status_label.setText(f"Playing: {self._reel_path.name}")

    def _open_folder(self) -> None:
        clips_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["xdg-open", str(clips_dir)], check=False)
        except FileNotFoundError:
            self.status_label.setText("Could not open folder (xdg-open not found).")
