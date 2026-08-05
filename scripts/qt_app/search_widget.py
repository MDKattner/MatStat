from __future__ import annotations

import csv
import logging
from pathlib import Path

import pandas as pd

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from scripts.helpers import (
    LoadAllWrestlerData,
    MakeFormattedDataFrame,
    cfg_dir,
    csv_dir,
    taged_dir,
)
from scripts.qt_app.widgets import ConfigComboBox, VideoPreviewPanel


class SearchWidget(QWidget):
    """Search across all wrestler tagged data with filterable queries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_data: dict[str, pd.DataFrame] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout: QVBoxLayout = QVBoxLayout(self)

        header: QLabel = QLabel("Search All Tagged Data")
        header.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        main_layout.addWidget(header)

        splitter: QSplitter = QSplitter(Qt.Orientation.Horizontal)

        left_scroll: QScrollArea = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(300)
        left_panel: QWidget = QWidget()
        left_layout: QVBoxLayout = QVBoxLayout(left_panel)
        self._build_filters(left_layout)
        left_scroll.setWidget(left_panel)

        right_panel: QWidget = QWidget()
        right_layout: QVBoxLayout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._build_results(right_layout)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 650])

        main_layout.addWidget(splitter, stretch=1)

    def _build_filters(self, layout: QVBoxLayout) -> None:
        load_group: QGroupBox = QGroupBox("1. Load Data")
        lg: QVBoxLayout = QVBoxLayout(load_group)
        self.load_btn: QPushButton = QPushButton("Load All Wrestler Data")
        self.load_btn.clicked.connect(self._load_data)
        lg.addWidget(self.load_btn)
        self.load_status: QLabel = QLabel("Not loaded")
        lg.addWidget(self.load_status)
        layout.addWidget(load_group)

        filter_group: QGroupBox = QGroupBox("2. Filters")
        fg: QVBoxLayout = QVBoxLayout(filter_group)

        fg.addWidget(QLabel("Wrestler:"))
        self.wrestler_filter: QComboBox = QComboBox()
        self.wrestler_filter.setEditable(True)
        self.wrestler_filter.addItem("All")
        fg.addWidget(self.wrestler_filter)

        fg.addWidget(QLabel("Attack/Defense:"))
        self.attack_filter: QComboBox = QComboBox()
        self.attack_filter.addItems(["All", "Attacking", "Defending"])
        fg.addWidget(self.attack_filter)

        fg.addWidget(QLabel("Tie Up:"))
        self.tie_filter: QComboBox = QComboBox()
        self.tie_filter.setEditable(True)
        self.tie_filter.addItem("")
        ties_path: Path = cfg_dir / "Ties.config"
        if ties_path.exists():
            with open(ties_path) as f:
                for line in f:
                    stripped: str = line.strip()
                    if stripped and not stripped.startswith("#"):
                        self.tie_filter.addItem(stripped.split("#")[0].strip())
        fg.addWidget(self.tie_filter)

        fg.addWidget(QLabel("Team Move (contains):"))
        self.team_move_filter: QLineEdit = QLineEdit()
        self.team_move_filter.setPlaceholderText("e.g. high crotch")
        fg.addWidget(self.team_move_filter)

        fg.addWidget(QLabel("Opponent Move (contains):"))
        self.opp_move_filter: QLineEdit = QLineEdit()
        self.opp_move_filter.setPlaceholderText("e.g. sprawl")
        fg.addWidget(self.opp_move_filter)

        fg.addWidget(QLabel("Min Net Points:"))
        self.min_pts: QSpinBox = QSpinBox()
        self.min_pts.setRange(-20, 20)
        self.min_pts.setValue(-20)
        fg.addWidget(self.min_pts)

        fg.addWidget(QLabel("Max Net Points:"))
        self.max_pts: QSpinBox = QSpinBox()
        self.max_pts.setRange(-20, 20)
        self.max_pts.setValue(20)
        fg.addWidget(self.max_pts)

        layout.addWidget(filter_group)

        search_btn: QPushButton = QPushButton("Search")
        search_btn.clicked.connect(self._execute_search)
        layout.addWidget(search_btn)

    def _build_results(self, layout: QVBoxLayout) -> None:
        self.result_count: QLabel = QLabel("Results: 0")
        self.result_count.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(self.result_count)

        preview_label: QLabel = QLabel("Preview")
        preview_label.setStyleSheet("font-size: 14px; font-weight: bold; margin-top: 8px;")
        layout.addWidget(preview_label)

        self._video_panel: VideoPreviewPanel = VideoPreviewPanel()
        layout.addWidget(self._video_panel, stretch=1)

        seq_label: QLabel = QLabel("Matched Sequences")
        seq_label.setStyleSheet("font-size: 14px; font-weight: bold; margin-top: 8px;")
        layout.addWidget(seq_label)

        self.results_list: QListWidget = QListWidget()
        self.results_list.setAlternatingRowColors(True)
        self.results_list.itemClicked.connect(self._on_result_selected)
        self.results_list.setFixedHeight(180)
        layout.addWidget(self.results_list)

        btn_layout: QHBoxLayout = QHBoxLayout()
        self.export_btn: QPushButton = QPushButton("Export Results as CSV")
        self.export_btn.clicked.connect(self._export_csv)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._results_df: pd.DataFrame | None = None

    def _load_data(self) -> None:
        self.load_btn.setEnabled(False)
        self.load_status.setText("Loading...")
        QApplication.processEvents()

        self._all_data = LoadAllWrestlerData(csv_dir)

        self.wrestler_filter.clear()
        self.wrestler_filter.addItem("All")
        for name in sorted(self._all_data.keys()):
            self.wrestler_filter.addItem(name)
            self.load_status.setText(f"Loaded {len(self._all_data)} wrestler(s)")

        self.load_btn.setText("Reload Data")

    def _execute_search(self) -> None:
        if not self._all_data:
            QMessageBox.information(self, "No Data", "Click 'Load All Wrestler Data' first.")
            return

        selected_wrestler: str = self.wrestler_filter.currentText()
        attack_mode: str = self.attack_filter.currentText()
        tie_text: str = self.tie_filter.currentText().strip()
        team_move_text: str = self.team_move_filter.text().strip().lower()
        opp_move_text: str = self.opp_move_filter.text().strip().lower()
        min_pts: int = self.min_pts.value()
        max_pts: int = self.max_pts.value()

        rows: list[dict] = []

        for wrestler_name, df in self._all_data.items():
            if selected_wrestler != "All" and wrestler_name != selected_wrestler:
                continue

            subset: pd.DataFrame = df.copy()
            subset["_wrestler"] = wrestler_name

            if attack_mode == "Attacking":
                subset = subset[subset["Attacking"] == True]  # type: ignore[assignment]
            elif attack_mode == "Defending":
                subset = subset[subset["Attacking"] == False]  # type: ignore[assignment]

            if tie_text:
                subset = subset[subset["Tie Up"] == tie_text]  # type: ignore[assignment]

            if team_move_text:
                subset = subset[subset["Team Moves"].apply(  # type: ignore[assignment]
                    lambda m: any(team_move_text in mv.lower() for mv in m) if isinstance(m, list) else False
                )]

            if opp_move_text:
                subset = subset[subset["Opponent Moves"].apply(  # type: ignore[assignment]
                    lambda m: any(opp_move_text in mv.lower() for mv in m) if isinstance(m, list) else False
                )]

            subset = subset[(subset["Net Points"] >= min_pts) & (subset["Net Points"] <= max_pts)]  # type: ignore[assignment]

            for index, row in subset.iterrows():
                rows.append({
                    "Wrestler": wrestler_name,
                    "Origin": index,
                    "Start Time": row["Start Time"],
                    "End Time": row["End Time"],
                    "Attacking": "A" if bool(row["Attacking"]) else "D",
                    "Tie Up": row["Tie Up"],
                    "Team Moves": ", ".join(row["Team Moves"]) if isinstance(row["Team Moves"], list) else "",
                    "Opponent Moves": ", ".join(row["Opponent Moves"]) if isinstance(row["Opponent Moves"], list) else "",
                    "Net Points": row["Net Points"],
                })

        self._results_df = pd.DataFrame(rows)
        self.result_count.setText(f"Results: {len(rows)}")
        self.results_list.clear()

        for r in rows:
            text: str = (
                f"{r['Wrestler']} | {r['Origin'].split(':')[0]} "
                f"[{r['Start Time'] // 60}:{r['Start Time'] % 60:02}"
                f"-{r['End Time'] // 60}:{r['End Time'] % 60:02}] "
                f"{r['Attacking']} | {r['Tie Up']} | "
                f"{r['Team Moves'][:40]}"
            )
            item: QListWidgetItem = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, list(rows).index(r))
            self.results_list.addItem(item)

    def _on_result_selected(self, item: QListWidgetItem) -> None:
        if self._results_df is None:
            return

        idx: int = item.data(Qt.ItemDataRole.UserRole)
        row = self._results_df.iloc[idx]
        video_name: str = row["Origin"].split(":")[0]
        start_time: int = int(row["Start Time"])
        video_path: Path = taged_dir / video_name

        if video_path.exists():
            self._video_panel.load_video(str(video_path))
            self._video_panel._player.setPosition(start_time * 1000)
        else:
            logging.warning(f"Source video not found for preview: {video_path}")

    def _export_csv(self) -> None:
        if self._results_df is None or self._results_df.empty:
            QMessageBox.information(self, "No Results", "Run a search first.")
            return

        export_path: Path = csv_dir / "search_results.csv"
        try:
            self._results_df.to_csv(export_path, index=False)
            QMessageBox.information(
                self,
                "Exported",
                f"Saved {len(self._results_df)} results to {export_path}",
            )
        except OSError as e:
            QMessageBox.critical(self, "Export Error", f"Could not write file: {e}")
