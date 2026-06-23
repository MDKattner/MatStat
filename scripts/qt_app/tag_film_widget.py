from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
    ChapterSequence,
    GetVidDuration,
    cfg_dir,
    taged_dir,
    tmp_dir,
    untaged_dir,
)
from scripts.qt_app.async_runner import AsyncFfmpegRunner
from scripts.qt_app.widgets import (
    ChapterPreviewDialog,
    ConfigComboBox,
    MultiSelector,
    TimeInput,
    VideoPreviewPanel,
)


class VideoSelectorWidget(QWidget):
    """A filterable list of untagged videos with click-to-select."""

    videoSelected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.search_box: QLineEdit = QLineEdit()
        self.search_box.setPlaceholderText("Type to filter videos...")
        self.search_box.textChanged.connect(self._filter_items)
        layout.addWidget(self.search_box)

        self.list_widget: QListWidget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_selected)
        layout.addWidget(self.list_widget)

        self.all_files: list[str] = []
        self._refresh()

    def _refresh(self) -> None:
        self.list_widget.clear()
        self.all_files.clear()
        try:
            files: list[str] = sorted(f for f in os.listdir(untaged_dir) if not f.startswith("."))
            self.all_files = files
            for f in files:
                item: QListWidgetItem = QListWidgetItem(f)
                self.list_widget.addItem(item)
        except FileNotFoundError:
            logging.error(f"Untagged videos directory not found: {untaged_dir}")

    def _filter_items(self, text: str) -> None:
        self.list_widget.clear()
        for f in self.all_files:
            if text.lower() in f.lower():
                item: QListWidgetItem = QListWidgetItem(f)
                self.list_widget.addItem(item)

    def _on_selected(self, item: QListWidgetItem) -> None:
        self.videoSelected.emit(item.text())



class TagFilmWidget(QWidget):
    """Qt6 GUI equivalent of the 'Tag Film' script."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chap_list: list[ChapterSequence] = []
        self._old_end: int = 0
        self._vid_file_name: str = ""
        self._wrestler_name: str = ""
        self._is_tagging: bool = False

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        main_layout: QHBoxLayout = QHBoxLayout(self)

        left_scroll: QScrollArea = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(380)
        left_scroll.setMaximumWidth(500)

        left_panel: QWidget = QWidget()
        left_layout: QVBoxLayout = QVBoxLayout(left_panel)

        self._build_wrestler_section(left_layout)
        self._build_timing_section(left_layout)
        self._build_details_section(left_layout)
        self._build_action_section(left_layout)
        self._build_sequence_list_section(left_layout)

        left_scroll.setWidget(left_panel)

        self._video_panel: VideoPreviewPanel = VideoPreviewPanel()

        right_panel: QWidget = QWidget()
        right_layout: QVBoxLayout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._video_panel, stretch=1)
        self._build_video_section(right_layout)

        splitter: QSplitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setSizes([400, 600])

        main_layout.addWidget(splitter)

    def _build_video_section(self, layout: QVBoxLayout) -> None:
        group: QGroupBox = QGroupBox("Select Video")
        group.setMaximumHeight(250)
        gl: QVBoxLayout = QVBoxLayout(group)

        self.video_selector: VideoSelectorWidget = VideoSelectorWidget()
        gl.addWidget(self.video_selector)

        layout.addWidget(group)

    def _build_wrestler_section(self, layout: QVBoxLayout) -> None:
        self.wrestler_selector: ConfigComboBox = ConfigComboBox(
            cfg_dir / "Wrestlers.config",
            "Wrestler:"
        )
        layout.addWidget(self.wrestler_selector)

    def _build_timing_section(self, layout: QVBoxLayout) -> None:
        group: QGroupBox = QGroupBox("2. Timing")
        gl: QVBoxLayout = QVBoxLayout(group)

        self.start_time_input: TimeInput = TimeInput("Start:")
        self.start_time_input.mark_button.clicked.connect(self._mark_start)
        gl.addWidget(self.start_time_input)

        self.end_time_input: TimeInput = TimeInput("End:")
        self.end_time_input.mark_button.clicked.connect(self._mark_end)
        gl.addWidget(self.end_time_input)

        layout.addWidget(group)

    def _build_details_section(self, layout: QVBoxLayout) -> None:
        group: QGroupBox = QGroupBox("3. Sequence Details")
        gl: QVBoxLayout = QVBoxLayout(group)

        attack_group: QGroupBox = QGroupBox("Attack / Defense")
        ag: QHBoxLayout = QHBoxLayout(attack_group)
        self.attack_radio: QRadioButton = QRadioButton("Attacking")
        self.attack_radio.setChecked(True)
        self.defend_radio: QRadioButton = QRadioButton("Defending")
        ag.addWidget(self.attack_radio)
        ag.addWidget(self.defend_radio)
        gl.addWidget(attack_group)

        self.tie_selector: ConfigComboBox = ConfigComboBox(
            cfg_dir / "Ties.config",
            "Starting Tie/Position:"
        )
        gl.addWidget(self.tie_selector)

        self.moves_selector: MultiSelector = MultiSelector(
            cfg_dir / "Moves.config",
            "Your Wrestler's Moves:"
        )
        gl.addWidget(self.moves_selector)

        self.opp_moves_selector: MultiSelector = MultiSelector(
            cfg_dir / "Moves.config",
            "Opponent's Moves:"
        )
        gl.addWidget(self.opp_moves_selector)

        self.scores_selector: MultiSelector = MultiSelector(
            cfg_dir / "Outcomes.config",
            "Your Wrestler's Scores:"
        )
        gl.addWidget(self.scores_selector)

        self.opp_scores_selector: MultiSelector = MultiSelector(
            cfg_dir / "Outcomes.config",
            "Opponent's Scores:"
        )
        gl.addWidget(self.opp_scores_selector)

        layout.addWidget(group)

    def _build_action_section(self, layout: QVBoxLayout) -> None:
        group: QGroupBox = QGroupBox("Actions")
        gl: QHBoxLayout = QHBoxLayout(group)

        self.add_seq_btn: QPushButton = QPushButton("Add Sequence")
        self.add_seq_btn.setEnabled(False)
        gl.addWidget(self.add_seq_btn)

        self.finish_btn: QPushButton = QPushButton("Finish & Tag Video")
        self.finish_btn.setEnabled(False)
        gl.addWidget(self.finish_btn)

        layout.addWidget(group)

        self.progress_bar: QProgressBar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label: QLabel = QLabel("Select a video and wrestler to begin.")
        layout.addWidget(self.status_label)

    def _build_sequence_list_section(self, layout: QVBoxLayout) -> None:
        group: QGroupBox = QGroupBox("Tagged Sequences")
        gl: QVBoxLayout = QVBoxLayout(group)

        self.seq_count_label: QLabel = QLabel("Sequences tagged: 0")
        gl.addWidget(self.seq_count_label)

        self.seq_list: QListWidget = QListWidget()
        gl.addWidget(self.seq_list)

        layout.addWidget(group)

    def _connect_signals(self) -> None:
        self.video_selector.videoSelected.connect(self._on_video_selected)
        self.wrestler_selector.selectionMade.connect(self._on_wrestler_selected)
        self.add_seq_btn.clicked.connect(self._add_sequence)
        self.finish_btn.clicked.connect(self._finish_tagging)

    def _on_video_selected(self, file_name: str) -> None:
        self._vid_file_name = file_name
        video_path: Path = untaged_dir / file_name
        self._video_panel.load_video(str(video_path))
        self.status_label.setText(f"Selected video: {file_name}")
        logging.info(f"Selected video for tagging: {file_name}")
        self._update_buttons()

    def _on_wrestler_selected(self, name: str) -> None:
        self._wrestler_name = name
        self.status_label.setText(f"Selected wrestler: {name}")
        logging.info(f"Selected wrestler: {name}")
        self._update_buttons()

    def _update_buttons(self) -> None:
        can_tag: bool = bool(self._vid_file_name and self._wrestler_name)
        self.add_seq_btn.setEnabled(can_tag)
        self.finish_btn.setEnabled(bool(self._chap_list))

    def _mark_start(self) -> None:
        ms: int = self._video_panel.current_position_ms()
        secs: int = ms // 1000
        self.start_time_input.set_from_seconds(secs)
        logging.debug(f"Marked start time: {secs}s")

    def _mark_end(self) -> None:
        ms: int = self._video_panel.current_position_ms()
        secs: int = ms // 1000
        self.end_time_input.set_from_seconds(secs)
        logging.debug(f"Marked end time: {secs}s")

    def _add_sequence(self) -> None:
        start: int = self.start_time_input.seconds
        end: int = self.end_time_input.seconds

        if start >= end:
            QMessageBox.warning(self, "Invalid Times", "Start time must be before end time.")
            return

        attacking: bool = self.attack_radio.isChecked()
        tie: str = self.tie_selector.selected

        our_moves: list[str] = self.moves_selector.selected
        if not our_moves:
            our_moves = ["nothing"]

        opp_moves: list[str] = self.opp_moves_selector.selected
        if not opp_moves:
            opp_moves = ["nothing"]

        team_scores: list[str] = self.scores_selector.selected
        if not team_scores:
            team_scores = ["None"]

        opp_scores: list[str] = self.opp_scores_selector.selected
        if not opp_scores:
            opp_scores = ["None"]

        temp_chap: ChapterSequence = ChapterSequence(
            start_time=start,
            end_time=end,
            attack_defend=attacking,
            tie_up=tie,
            team_moves=our_moves,
            op_moves=opp_moves,
            team_scores=team_scores,
            op_scores=opp_scores,
        )

        dialog: ChapterPreviewDialog = ChapterPreviewDialog(temp_chap.PrettyChapter(), self)
        dialog.exec()

        code: int = dialog.result_code
        if code == 0:
            return

        if code != 0:
            self._chap_list.append(ChapterSequence.MakeEmptyChap(self._old_end, start))
            self._chap_list.append(temp_chap)
            self._old_end = end

            self.seq_list.addItem(
                f"[{start // 60}:{start % 60:02} - {end // 60}:{end % 60:02}] "
                f"{'A' if attacking else 'D'} | {tie}"
            )
            self.seq_count_label.setText(f"Sequences tagged: {len(self._chap_list) // 2}")

        self._update_buttons()
        self.status_label.setText(f"Added sequence. {len(self._chap_list) // 2} sequences tagged.")

        if code == 2:
            self._finish_tagging()

    def _finish_tagging(self) -> None:
        if not self._chap_list:
            QMessageBox.information(self, "No Sequences", "No sequences were added. Nothing to tag.")
            return

        if not self._vid_file_name or not self._wrestler_name:
            QMessageBox.warning(self, "Missing Info", "Select a video and wrestler first.")
            return

        try:
            video_duration: int = GetVidDuration(untaged_dir / self._vid_file_name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not get video duration: {e}")
            logging.error(f"Failed to get video duration: {e}")
            return

        self._chap_list.append(ChapterSequence.MakeEmptyChap(self._old_end, video_duration))

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.status_label.setText("Tagging video...")
        self.add_seq_btn.setEnabled(False)
        self.finish_btn.setEnabled(False)

        self._runner: AsyncFfmpegRunner = AsyncFfmpegRunner(self)
        self._runner.finished.connect(self._on_tag_done)

        vid_file: Path = Path(self._vid_file_name.strip())
        input_path: Path = untaged_dir / vid_file.name
        output_path: Path = taged_dir / vid_file.with_suffix(".mkv").name
        metadata_path: Path = tmp_dir / "metadata.txt"

        metadata_content: str = f";FFMETADATA1\ntitle={self._wrestler_name.strip()}\n\n"
        for chap in self._chap_list:
            metadata_content += chap.ToMetadata()

        try:
            with open(metadata_path, "w") as f:
                f.write(metadata_content)
        except IOError as e:
            QMessageBox.critical(self, "Error", f"Could not write metadata file: {e}")
            self.progress_bar.setVisible(False)
            self._update_buttons()
            return

        cmd: list[str] = [
            "ffmpeg",
            "-i", str(input_path),
            "-i", str(metadata_path),
            "-map_metadata", "1",
            "-codec", "copy",
            "-y",
            str(output_path),
        ]

        self._input_path = input_path
        self._vid_file_name_clean = vid_file.name
        self._metadata_path = metadata_path
        self._output_path = output_path

        self._runner.run(cmd, f"Tagging {self._vid_file_name}")

    def _on_tag_done(self, success: bool, message: str) -> None:
        self.progress_bar.setVisible(False)

        if success:
            try:
                os.remove(self._metadata_path)
                os.rename(
                    str(self._input_path),
                    str(self._input_path.parent / f".{self._vid_file_name_clean}"),
                )
            except OSError as e:
                logging.warning(f"Cleanup after tagging: {e}")

            QMessageBox.information(
                self,
                "Success",
                f"Video tagged successfully!\n\nOutput: {self._output_path}\nOriginal hidden.",
            )
            self.status_label.setText(f"Tagged: {self._output_path.name}")
        else:
            QMessageBox.critical(self, "Tagging Failed", f"ffmpeg error:\n{message}")
            self.status_label.setText("Tagging failed.")
            if hasattr(self, "_output_path") and self._output_path.exists():
                try:
                    os.remove(self._output_path)
                except OSError:
                    pass

        self._update_buttons()
