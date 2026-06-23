from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from scripts.helpers import cfg_dir


_CONFIG_FILES: dict[str, str] = {
    "Wrestlers.config": "Wrestler names (one per line)",
    "Moves.config": "Wrestling moves organized by phase",
    "Ties.config": "Ties and positions by phase",
    "Outcomes.config": "Scoring outcome codes",
}


def _parse_config_entries(file_path: Path) -> tuple[list[str], list[str]]:
    header_lines: list[str] = []
    entry_lines: list[str] = []

    try:
        with open(file_path) as f:
            for raw in f:
                stripped: str = raw.strip()
                if not stripped or stripped.startswith("#"):
                    header_lines.append(raw)
                else:
                    entry_text: str = stripped.split("#")[0].strip()
                    entry_lines.append(entry_text)
                    header_lines.append(raw)
    except FileNotFoundError:
        logging.error(f"Config file not found: {file_path}")

    return header_lines, entry_lines


def _write_config_file(file_path: Path, header_lines: list[str], new_entries: list[str]) -> None:
    entry_idx: int = 0
    out_lines: list[str] = []

    for raw in header_lines:
        stripped: str = raw.strip()
        if stripped and not stripped.startswith("#"):
            if entry_idx < len(new_entries):
                indent: str = raw[: len(raw) - len(raw.lstrip())]
                out_lines.append(f"{indent}{new_entries[entry_idx]}\n")
                entry_idx += 1
            else:
                out_lines.append(raw)
        else:
            out_lines.append(raw)

    file_path.write_text("".join(out_lines))


class ConfigEditorDialog(QDialog):
    def __init__(self, parent: ... = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Config Editor")
        self.setMinimumSize(500, 400)

        self._current_file: str = ""
        self._header_lines: list[str] = []
        self._entries: list[str] = []

        self._setup_ui()
        self._load_config_file(list(_CONFIG_FILES.keys())[0])

    def _setup_ui(self) -> None:
        layout: QVBoxLayout = QVBoxLayout(self)

        selector_layout: QHBoxLayout = QHBoxLayout()
        selector_layout.addWidget(QLabel("Config file:"))
        self.file_combo: QComboBox = QComboBox()
        self.file_combo.addItems(_CONFIG_FILES.keys())
        self.file_combo.currentTextChanged.connect(self._on_file_changed)
        selector_layout.addWidget(self.file_combo, stretch=1)
        layout.addLayout(selector_layout)

        self._desc_label: QLabel = QLabel("")
        self._desc_label.setStyleSheet("color: gray; font-size: 11px; margin-bottom: 6px;")
        layout.addWidget(self._desc_label)

        self.list_widget: QListWidget = QListWidget()
        layout.addWidget(self.list_widget, stretch=1)

        btn_layout: QHBoxLayout = QHBoxLayout()
        self.add_btn: QPushButton = QPushButton("Add")
        self.add_btn.clicked.connect(self._add_entry)
        btn_layout.addWidget(self.add_btn)

        self.edit_btn: QPushButton = QPushButton("Edit")
        self.edit_btn.clicked.connect(self._edit_entry)
        btn_layout.addWidget(self.edit_btn)

        self.delete_btn: QPushButton = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete_entry)
        btn_layout.addWidget(self.delete_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._save_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_file_changed(self, file_name: str) -> None:
        self._load_config_file(file_name)

    def _load_config_file(self, file_name: str) -> None:
        file_path: Path = cfg_dir / file_name
        self._current_file = file_name
        desc: str = _CONFIG_FILES.get(file_name, "")
        self._desc_label.setText(desc)
        self._header_lines, self._entries = _parse_config_entries(file_path)
        self._refresh_list()

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for entry in self._entries:
            item: QListWidgetItem = QListWidgetItem(entry)
            self.list_widget.addItem(item)

    def _add_entry(self) -> None:
        text: str | None
        text, ok = QInputDialog.getText(self, "Add Entry", "New entry:")
        if ok and text:
            text = text.strip()
            if text:
                self._entries.append(text)
                self._refresh_list()

    def _edit_entry(self) -> None:
        current: QListWidgetItem | None = self.list_widget.currentItem()
        if current is None:
            QMessageBox.information(self, "No Selection", "Select an entry to edit.")
            return

        old_text: str = current.text()
        text: str | None
        text, ok = QInputDialog.getText(self, "Edit Entry", "Edit entry:", text=old_text)
        if ok and text:
            text = text.strip()
            if text:
                idx: int = self.list_widget.row(current)
                self._entries[idx] = text
                self._refresh_list()

    def _delete_entry(self) -> None:
        current: QListWidgetItem | None = self.list_widget.currentItem()
        if current is None:
            QMessageBox.information(self, "No Selection", "Select an entry to delete.")
            return

        reply: QMessageBox.StandardButton = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete '{current.text()}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            idx: int = self.list_widget.row(current)
            del self._entries[idx]
            self._refresh_list()

    def _save_and_accept(self) -> None:
        file_path: Path = cfg_dir / self._current_file

        backup_path: Path = file_path.with_suffix(file_path.suffix + ".bak")
        try:
            shutil.copy2(file_path, backup_path)
        except OSError as e:
            QMessageBox.warning(self, "Backup Warning", f"Could not create backup: {e}")

        try:
            _write_config_file(file_path, self._header_lines, self._entries)
            logging.info(f"Saved config '{self._current_file}' with {len(self._entries)} entries.")
            self.accept()
        except OSError as e:
            QMessageBox.critical(self, "Save Error", f"Could not write file: {e}")
