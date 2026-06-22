from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QStringListModel, pyqtSignal
from PyQt6.QtWidgets import (
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
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


class ConfigSelector(QWidget):
    """Single-select widget backed by a config file (one entry per line, # comments)."""

    selectionMade = pyqtSignal(str)

    def __init__(self, config_path: Path, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_path: Path = config_path
        self.items: list[str] = self._load_items()
        self._current_selection: str = ""

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if title:
            layout.addWidget(QLabel(title))

        self.search_box: QLineEdit = QLineEdit()
        self.search_box.setPlaceholderText("Type to filter...")
        layout.addWidget(self.search_box)

        self.list_widget: QListWidget = QListWidget()
        self.list_widget.addItems(self.items)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

        self.search_box.textChanged.connect(self._filter_items)

    def _load_items(self) -> list[str]:
        try:
            with open(self.config_path) as f:
                lines: list[str] = []
                for raw in f:
                    stripped: str = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    lines.append(stripped.split("#")[0].strip())
                return lines
        except FileNotFoundError:
            logging.error(f"Config file not found: {self.config_path}")
            return []
        except Exception as e:
            logging.error(f"Error loading config {self.config_path}: {e}")
            return []

    def _filter_items(self, text: str) -> None:
        for i in range(self.list_widget.count()):
            item: QListWidgetItem | None = self.list_widget.item(i)
            if item is not None:
                item.setHidden(text.lower() not in item.text().lower())

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self._current_selection = item.text()
        self.selectionMade.emit(self._current_selection)

    @property
    def selected(self) -> str:
        return self._current_selection


class MultiSelector(QWidget):
    """Multi-select widget backed by a config file with checkable items."""

    selectionMade = pyqtSignal(list)

    def __init__(self, config_path: Path, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_path: Path = config_path
        self.items: list[str] = self._load_items()

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
        self.list_widget.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list_widget)

        self.search_box.textChanged.connect(self._filter_items)

    def _load_items(self) -> list[str]:
        try:
            with open(self.config_path) as f:
                lines: list[str] = []
                for raw in f:
                    stripped: str = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    lines.append(stripped.split("#")[0].strip())
                return lines
        except FileNotFoundError:
            logging.error(f"Config file not found: {self.config_path}")
            return []
        except Exception as e:
            logging.error(f"Error loading config {self.config_path}: {e}")
            return []

    def _filter_items(self, text: str) -> None:
        for i in range(self.list_widget.count()):
            item: QListWidgetItem | None = self.list_widget.item(i)
            if item is not None:
                item.setHidden(text.lower() not in item.text().lower())

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self.selectionMade.emit(self.selected)

    @property
    def selected(self) -> list[str]:
        result: list[str] = []
        for i in range(self.list_widget.count()):
            item: QListWidgetItem | None = self.list_widget.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                result.append(item.text())
        return result


class ConfigComboBox(QWidget):
    """A QComboBox with auto-complete backed by a config file."""

    selectionMade = pyqtSignal(str)

    def __init__(self, config_path: Path, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_path: Path = config_path
        self.items: list[str] = self._load_items()

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

    def _load_items(self) -> list[str]:
        try:
            with open(self.config_path) as f:
                lines: list[str] = []
                for raw in f:
                    stripped: str = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    lines.append(stripped.split("#")[0].strip())
                return lines
        except FileNotFoundError:
            logging.error(f"Config file not found: {self.config_path}")
            return []
        except Exception as e:
            logging.error(f"Error loading config {self.config_path}: {e}")
            return []

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
        from PyQt6.QtCore import QTime
        self.time_edit.setMinimumTime(QTime(0, 0))
        self.time_edit.setMaximumTime(QTime(59, 59))
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
        from PyQt6.QtCore import QTime

        clamped: int = max(0, seconds)
        self.time_edit.setTime(QTime(0, clamped // 60, clamped % 60))

    @property
    def seconds(self) -> int:
        return self.time_edit.time().minute() * 60 + self.time_edit.time().second()

    @property
    def mark_button(self) -> QPushButton:
        return self.mark_btn


class ChapterPreviewDialog(QDialog):
    """Preview a ChapterSequence before confirming."""

    def __init__(self, chapter_preview: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chapter Preview")
        self.setMinimumSize(400, 300)

        layout: QVBoxLayout = QVBoxLayout(self)

        from PyQt6.QtWidgets import QTextEdit

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
        super().accept()

    @property
    def result_code(self) -> int:
        return self._result_code
