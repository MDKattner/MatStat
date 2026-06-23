from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTime, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


def _load_config_items(config_path: Path) -> list[str]:
    """Load non-blank, non-comment lines from a config file.

    Strips inline comments (text after '#') and leading/trailing whitespace.

    Args:
        config_path: Path to the config file.

    Returns:
        A list of parsed item strings.
    """
    try:
        items: list[str] = []
        with open(config_path) as f:
            for raw in f:
                stripped: str = raw.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                items.append(stripped.split("#")[0].strip())
        return items
    except FileNotFoundError:
        logging.error(f"Config file not found: {config_path}")
        return []
    except Exception as e:
        logging.error(f"Error loading config {config_path}: {e}")
        return []


def _filter_list_widget(list_widget: QListWidget, text: str) -> None:
    """Filter a QListWidget's items by a case-insensitive substring match.

    Also manages visibility of per-item widgets set via ``setItemWidget``,
    since Qt does not cascade item-level hidden state to those widgets.

    Args:
        list_widget: The list widget whose items should be filtered.
        text: The filter text.
    """
    for i in range(list_widget.count()):
        item: QListWidgetItem | None = list_widget.item(i)
        if item is not None:
            match: bool = text.lower() in item.text().lower()
            item.setHidden(not match)
            widget: QWidget | None = list_widget.itemWidget(item)
            if widget is not None:
                widget.setVisible(match)


class MultiSelector(QWidget):
    """Multi-select widget backed by a config file with per-item count controls.

    Each row has a checkbox for selection and a spin box (1-99) for how many
    times that item occurred. The spin box is enabled only when the checkbox
    is checked.  The ``selected`` property returns an expanded list with
    duplicates according to each spin box value.
    """

    selectionMade = pyqtSignal(list)

    def __init__(self, config_path: Path, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_path: Path = config_path
        self.items: list[str] = _load_config_items(config_path)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if title:
            layout.addWidget(QLabel(title))

        self.search_box: QLineEdit = QLineEdit()
        self.search_box.setPlaceholderText("Type to filter...")
        layout.addWidget(self.search_box)

        self.list_widget: QListWidget = QListWidget()
        for item_text in self.items:
            item: QListWidgetItem = QListWidgetItem("")

            row: QWidget = QWidget()
            rl: QHBoxLayout = QHBoxLayout(row)
            rl.setContentsMargins(2, 2, 2, 2)

            cb: QCheckBox = QCheckBox(item_text)
            rl.addWidget(cb, stretch=1)

            spin: QSpinBox = QSpinBox()
            spin.setRange(1, 99)
            spin.setValue(1)
            spin.setEnabled(False)
            spin.setMinimumWidth(52)
            rl.addWidget(spin)

            cb.toggled.connect(spin.setEnabled)
            cb.toggled.connect(self._on_selection_changed)

            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row)
        layout.addWidget(self.list_widget)

        self.search_box.textChanged.connect(self._filter_items)

    def _filter_items(self, text: str) -> None:
        for i in range(self.list_widget.count()):
            item: QListWidgetItem | None = self.list_widget.item(i)
            if item is None:
                continue
            widget: QWidget | None = self.list_widget.itemWidget(item)
            if widget is None:
                continue
            cb: QCheckBox | None = widget.findChild(QCheckBox)
            label_text: str = cb.text() if cb is not None else ""
            match: bool = text.lower() in label_text.lower()
            item.setHidden(not match)
            widget.setVisible(match)

    def _on_selection_changed(self) -> None:
        self.selectionMade.emit(self.selected)

    @property
    def selected(self) -> list[str]:
        result: list[str] = []
        for i in range(self.list_widget.count()):
            item: QListWidgetItem | None = self.list_widget.item(i)
            if item is None:
                continue
            widget: QWidget | None = self.list_widget.itemWidget(item)
            if widget is None:
                continue
            cb: QCheckBox | None = widget.findChild(QCheckBox)
            spin: QSpinBox | None = widget.findChild(QSpinBox)
            if cb is not None and cb.isChecked() and spin is not None:
                result.extend([cb.text()] * spin.value())
        return result


class CheckboxListWidget(QWidget):
    """A filterable list of checkboxes backed by a config file.

    Each row has a checkbox only (no spin box). The ``selected`` property
    returns a deduplicated list of checked item labels. Suitable for
    multi-select where quantities don't apply (e.g. wrestler names).
    """

    selectionChanged = pyqtSignal(list)

    def __init__(self, config_path: Path, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_path: Path = config_path
        self.items: list[str] = _load_config_items(config_path)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if title:
            layout.addWidget(QLabel(title))

        self.search_box: QLineEdit = QLineEdit()
        self.search_box.setPlaceholderText("Type to filter...")
        layout.addWidget(self.search_box)

        self.list_widget: QListWidget = QListWidget()
        for item_text in self.items:
            item: QListWidgetItem = QListWidgetItem(item_text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        self.search_box.textChanged.connect(self._filter_items)

    def _filter_items(self, text: str) -> None:
        _filter_list_widget(self.list_widget, text)

    def _on_selection_changed(self) -> None:
        self.selectionChanged.emit(self.selected)

    @property
    def selected(self) -> list[str]:
        result: list[str] = []
        for i in range(self.list_widget.count()):
            item: QListWidgetItem | None = self.list_widget.item(i)
            if item is not None and item.isHidden():
                continue
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                result.append(item.text())
        return result


class ConfigComboBox(QWidget):
    """A QComboBox with auto-complete backed by a config file."""

    selectionMade = pyqtSignal(str)

    def __init__(self, config_path: Path, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_path: Path = config_path
        self.items: list[str] = _load_config_items(config_path)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if title:
            layout.addWidget(QLabel(title))

        self.combo: QComboBox = QComboBox()
        self.combo.setEditable(True)
        self.combo.addItems(self.items)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        completer: QCompleter = QCompleter(self.items)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.combo.setCompleter(completer)

        self.combo.currentTextChanged.connect(self.selectionMade.emit)
        layout.addWidget(self.combo)

    @property
    def selected(self) -> str:
        return self.combo.currentText()


class TimeInput(QWidget):
    """Time input widget (minutes:seconds) with optional 'Mark' button."""

    timeChanged = pyqtSignal(int)

    def __init__(self, label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout: QHBoxLayout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if label:
            layout.addWidget(QLabel(label))

        self.time_edit: QTimeEdit = QTimeEdit()
        self.time_edit.setDisplayFormat("mm:ss")
        self.time_edit.setMinimumTime(QTime(0, 0))
        self.time_edit.setMaximumTime(QTime(23, 59, 59))
        self.time_edit.timeChanged.connect(self._on_time_changed)
        layout.addWidget(self.time_edit)

        self.mark_btn: QPushButton = QPushButton("Mark")
        self.mark_btn.setToolTip("Set from video player current position")
        layout.addWidget(self.mark_btn)

    def _on_time_changed(self) -> None:
        total_seconds: int = (
            self.time_edit.time().minute() * 60 + self.time_edit.time().second()
        )
        self.timeChanged.emit(total_seconds)

    def set_from_seconds(self, seconds: int) -> None:
        clamped: int = max(0, seconds)
        self.time_edit.setTime(QTime(0, clamped // 60, clamped % 60))

    @property
    def seconds(self) -> int:
        return self.time_edit.time().minute() * 60 + self.time_edit.time().second()

    @property
    def mark_button(self) -> QPushButton:
        return self.mark_btn


class VideoPlayerControls(QWidget):
    """Playback controls for the video preview."""

    def __init__(self, player: QMediaPlayer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._player: QMediaPlayer = player

        layout: QHBoxLayout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.play_btn: QPushButton = QPushButton("Play")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(self._toggle_play)
        layout.addWidget(self.play_btn)

        self.skip_back_btn: QPushButton = QPushButton("-5s")
        self.skip_back_btn.clicked.connect(lambda: self._seek_relative(-5000))
        layout.addWidget(self.skip_back_btn)

        self.skip_fwd_btn: QPushButton = QPushButton("+5s")
        self.skip_fwd_btn.clicked.connect(lambda: self._seek_relative(5000))
        layout.addWidget(self.skip_fwd_btn)

        self.position_slider: QSlider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self._player.setPosition)
        layout.addWidget(self.position_slider)

        self.time_label: QLabel = QLabel("0:00 / 0:00")
        layout.addWidget(self.time_label)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _seek_relative(self, ms: int) -> None:
        new_pos: int = max(0, self._player.position() + ms)
        self._player.setPosition(new_pos)

    def _on_position_changed(self, pos: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(pos)
        dur: int = self._player.duration()
        self.time_label.setText(
            f"{pos // 60000}:{(pos // 1000) % 60:02} / "
            f"{dur // 60000}:{(dur // 1000) % 60:02}"
        )

    def _on_duration_changed(self, dur: int) -> None:
        self.position_slider.setRange(0, dur)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_btn.setChecked(state == QMediaPlayer.PlaybackState.PlayingState)
        self.play_btn.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")


class VideoPreviewPanel(QWidget):
    """Panel with video player and controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._player: QMediaPlayer = QMediaPlayer(self)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.video_widget: QVideoWidget = QVideoWidget()
        self.video_widget.setMinimumSize(320, 240)
        self._player.setVideoOutput(self.video_widget)
        layout.addWidget(self.video_widget, stretch=1)

        self.controls: VideoPlayerControls = VideoPlayerControls(self._player, self)
        layout.addWidget(self.controls)

    def load_video(self, file_path: str) -> None:
        url: QUrl = QUrl.fromLocalFile(str(file_path))
        self._player.setSource(url)
        self._player.pause()

    def current_position_ms(self) -> int:
        return self._player.position()

    def duration_ms(self) -> int:
        return self._player.duration()


class ChapterPreviewDialog(QDialog):
    """Preview a ChapterSequence before confirming."""

    def __init__(self, chapter_preview: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chapter Preview")
        self.setMinimumSize(400, 300)

        layout: QVBoxLayout = QVBoxLayout(self)

        preview: QTextEdit = QTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(chapter_preview)
        layout.addWidget(preview)

        self.button_box: QDialogButtonBox = QDialogButtonBox()
        self.re_do_btn: QPushButton = QPushButton("Re-do Sequence")
        self.new_seq_btn: QPushButton = QPushButton("New Sequence")
        self.done_btn: QPushButton = QPushButton("Done")

        self.button_box.addButton(self.re_do_btn, QDialogButtonBox.ButtonRole.ActionRole)
        self.button_box.addButton(self.new_seq_btn, QDialogButtonBox.ButtonRole.ActionRole)
        self.button_box.addButton(self.done_btn, QDialogButtonBox.ButtonRole.AcceptRole)

        self.re_do_btn.clicked.connect(lambda: self.done(0))
        self.new_seq_btn.clicked.connect(lambda: self.done(1))
        self.done_btn.clicked.connect(lambda: self.done(2))

        layout.addWidget(self.button_box)

        self._result_code: int = 2

    def done(self, result_code: int) -> None:
        self._result_code = result_code
        super().done(result_code)

    @property
    def result_code(self) -> int:
        return self._result_code
