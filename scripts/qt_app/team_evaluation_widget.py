from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pandas as pd

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

from scripts.helpers import (
    GenerateDefenseDF,
    GenerateInitiationDF,
    GenerateOffenseDF,
    MakeFormattedDataFrame,
    csv_dir,
    eval_dir,
)
from scripts.qt_app.workers import LoadDataWorker


class TeamEvaluationWidget(QWidget):
    """Qt6 GUI equivalent of the 'Team Evaluation' script."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._evaluation_errors: list[str] = []
        self._csv_files: list[Path] = []
        self._current_worker: LoadDataWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout: QVBoxLayout = QVBoxLayout(self)

        header: QLabel = QLabel("Team Evaluation")
        header.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)

        desc: QLabel = QLabel(
            "Generate a multi-sheet Excel report with offensive, defensive, "
            "and initiation statistics from compiled wrestler CSV data."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        btn_layout: QHBoxLayout = QHBoxLayout()
        self.generate_btn: QPushButton = QPushButton("Generate Report")
        self.generate_btn.clicked.connect(self._generate_report)
        btn_layout.addWidget(self.generate_btn)

        self.open_btn: QPushButton = QPushButton("Open Report")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_report)
        btn_layout.addWidget(self.open_btn)

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

    def _generate_report(self) -> None:
        self._csv_files = [
            p for p in csv_dir.glob("*.csv") if p.name != "UNKNOWN.csv"
        ]

        if not self._csv_files:
            self.status_label.setText("No wrestler data files found.")
            self.error_display.append(
                "No CSV files found in stats/wrestler_data/. Run Compile Stats first."
            )
            return

        self.results_list.clear()
        self.error_display.clear()
        self._evaluation_errors = []
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(self._csv_files))
        self.progress_bar.setValue(0)
        self.generate_btn.setEnabled(False)
        self.status_label.setText(
            f"Processing {len(self._csv_files)} wrestler files..."
        )

        self._report_data: list[tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]] = []

        self._process_next_file(0)

    def _process_next_file(self, index: int) -> None:
        if index >= len(self._csv_files):
            self._write_excel()
            return

        csv_file: Path = self._csv_files[index]
        sheet_name: str = csv_file.stem
        self.status_label.setText(f"Loading data for '{sheet_name}'...")

        self._current_worker = LoadDataWorker(csv_file)
        self._current_worker.resultReady.connect(lambda df, i=index, name=sheet_name: self._on_data_loaded(df, i, name))
        self._current_worker.error.connect(lambda err, name=sheet_name: self._on_data_error(err, name))
        self._current_worker.finished.connect(lambda i=index: self._on_worker_finished(i))
        self._current_worker.start()

    def _on_worker_finished(self, index: int) -> None:
        self._current_worker = None
        self._process_next_file(index + 1)

    def _on_data_loaded(self, df: pd.DataFrame, index: int, sheet_name: str) -> None:
        try:
            init_df: pd.DataFrame = GenerateInitiationDF(df)
            def_df: pd.DataFrame = GenerateDefenseDF(df)
            off_df: pd.DataFrame = GenerateOffenseDF(df)

            self._report_data.append((sheet_name, init_df, def_df, off_df, df))
            self.progress_bar.setValue(index + 1)
            self.results_list.addItem(
                QListWidgetItem(f"✓ {sheet_name}: data loaded")
            )
        except Exception as e:
            self._on_data_error(str(e), sheet_name)

    def _on_data_error(self, error: str, sheet_name: str) -> None:
        self._evaluation_errors.append(f"Failed to process '{sheet_name}': {error}")
        self.error_display.append(f"ERROR: {sheet_name} - {error}")
        self.results_list.addItem(QListWidgetItem(f"✗ {sheet_name}: {error}"))

    def _write_excel(self) -> None:
        excel_file: Path = eval_dir / "Team_Stats.xlsx"
        self.status_label.setText("Writing Excel report...")

        try:
            excel_file.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(excel_file, engine="openpyxl", mode="w") as writer:
                for sheet_name, init_df, def_df, off_df, raw_df in self._report_data:
                    init_df.to_excel(writer, sheet_name=sheet_name)
                    def_df.to_excel(writer, sheet_name=sheet_name, startrow=4)
                    off_df.to_excel(writer, sheet_name=sheet_name, startrow=4, startcol=8)
                    raw_df.to_excel(writer, sheet_name=sheet_name, startrow=4, startcol=16)

            self.open_btn.setEnabled(True)
            msg: str = f"Report generated: {excel_file}"
            self.status_label.setText(msg)
            self.results_list.addItem(QListWidgetItem(f"\n✓ Report saved to {excel_file}"))

            if self._evaluation_errors:
                self.error_display.append("\n=== Errors ===")
                for err in self._evaluation_errors:
                    self.error_display.append(f"  - {err}")
        except Exception as e:
            self.status_label.setText("Failed to generate report.")
            self.error_display.append(f"Excel write error: {e}")

        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)

    def _open_report(self) -> None:
        excel_file: Path = eval_dir / "Team_Stats.xlsx"
        if excel_file.exists():
            try:
                subprocess.run(["xdg-open", str(excel_file)], check=False)
            except FileNotFoundError:
                self.status_label.setText("Could not open file (xdg-open not found).")
        else:
            self.status_label.setText("Report file not found. Generate it first.")
