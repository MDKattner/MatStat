from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from scripts.helpers import cfg_dir, csv_dir, taged_dir
from scripts.qt_app.workers import CompileStatsWorker


class CompileStatsWidget(QWidget):
    """Qt6 GUI equivalent of the 'Compile Stats' script."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: CompileStatsWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout: QVBoxLayout = QVBoxLayout(self)

        header: QLabel = QLabel("Compile Stats")
        header.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)

        desc: QLabel = QLabel(
            "Process all tagged videos and extract chapter data into wrestler-specific CSV files."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        btn_layout: QHBoxLayout = QHBoxLayout()
        self.process_btn: QPushButton = QPushButton("Process All Tagged Videos")
        self.process_btn.clicked.connect(self._start_compilation)
        btn_layout.addWidget(self.process_btn)

        self.cancel_btn: QPushButton = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.progress_bar: QProgressBar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label: QLabel = QLabel("Ready")
        layout.addWidget(self.status_label)

        results_group: QGroupBox = QGroupBox("Results")
        rl: QVBoxLayout = QVBoxLayout(results_group)

        self.results_list: QListWidget = QListWidget()
        rl.addWidget(self.results_list)

        self.error_display: QTextEdit = QTextEdit()
        self.error_display.setReadOnly(True)
        self.error_display.setPlaceholderText("Errors will appear here...")
        self.error_display.setMaximumHeight(150)
        rl.addWidget(self.error_display)

        layout.addWidget(results_group)

    def _start_compilation(self) -> None:
        try:
            with open(cfg_dir / "Wrestlers.config") as f:
                wrestler_names: list[str] = [
                    line.split("#")[0].strip()
                    for line in f
                    if line.strip() and not line.startswith("#")
                ]
        except FileNotFoundError:
            self.error_display.append("ERROR: Wrestlers.config not found.")
            return

        try:
            video_paths: list[Path] = [
                taged_dir / f for f in sorted(os.listdir(taged_dir))
            ]
        except FileNotFoundError:
            self.error_display.append("ERROR: Tagged videos directory not found.")
            return

        if not video_paths:
            self.status_label.setText("No tagged videos found.")
            self.error_display.append("No tagged videos found in vids/taged/.")
            return

        self.results_list.clear()
        self.error_display.clear()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(video_paths))
        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(f"Processing {len(video_paths)} videos...")

        self._worker = CompileStatsWorker(video_paths, wrestler_names)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
            self.status_label.setText("Cancelled.")
            self._reset_ui()

    def _on_progress(self, current: int, total: int, vid_name: str) -> None:
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Processing {current}/{total}: {vid_name}")

    def _on_finished(self, result: dict[str, str], errors: list[str]) -> None:
        self._reset_ui()

        write_errors: list[str] = []
        for name, data in result.items():
            output_path: Path = (csv_dir / name).with_suffix(".csv")
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                has_data: bool = bool(data.strip())
                if has_data:
                    with open(output_path, "w") as f:
                        f.write(data)
                    seq_count: int = len(data.strip().split("\n"))
                    item: QListWidgetItem = QListWidgetItem(
                        f"✓ {name}: {seq_count} sequences"
                    )
                else:
                    item: QListWidgetItem = QListWidgetItem(
                        f"⚠ {name}: 0 sequences (no file created)"
                    )
                self.results_list.addItem(item)
            except IOError as e:
                write_errors.append(f"Failed to write {output_path.name}: {e}")

        if errors:
            self.error_display.append("=== Processing Errors ===")
            for err in errors:
                self.error_display.append(f"  - {err}")

        if write_errors:
            self.error_display.append("\n=== Write Errors ===")
            for err in write_errors:
                self.error_display.append(f"  - {err}")

        total_msg: str = f"Complete. {len(result)} wrestler files updated."
        if errors or write_errors:
            total_msg += f" {len(errors) + len(write_errors)} issue(s) reported."
            self.status_label.setText(total_msg)
        else:
            self.status_label.setText(total_msg)

    def _reset_ui(self) -> None:
        self.progress_bar.setVisible(False)
        self.process_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
