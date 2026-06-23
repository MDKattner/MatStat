from __future__ import annotations

import logging

from PyQt6.QtCore import QCoreApplication, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from scripts.qt_app.combine_clips_widget import CombineClipsWidget
from scripts.qt_app.compile_stats_widget import CompileStatsWidget
from scripts.qt_app.config_editor_dialog import ConfigEditorDialog
from scripts.qt_app.search_widget import SearchWidget
from scripts.qt_app.tag_film_widget import TagFilmWidget
from scripts.qt_app.team_evaluation_widget import TeamEvaluationWidget


class LogBridge(QObject):
    """QObject bridge to emit log messages as Qt signals."""

    messageLogged = pyqtSignal(str)


class QtLogHandler(logging.Handler):
    """A logging handler that forwards messages to a LogBridge."""

    def __init__(self, bridge: LogBridge) -> None:
        super().__init__()
        self._bridge: LogBridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s:%(levelname)s:%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        msg: str = self.format(record)
        self._bridge.messageLogged.emit(msg)


class LogDockWidget(QDockWidget):
    """A dockable log panel that displays logging output."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Log", parent)
        self.setObjectName("LogDock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
        )

        container: QWidget = QWidget()
        layout: QVBoxLayout = QVBoxLayout(container)

        self.log_view: QPlainTextEdit = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_view)

        clear_btn: QPushButton = QPushButton("Clear")
        clear_btn.clicked.connect(self.log_view.clear)
        layout.addWidget(clear_btn)

        self.setWidget(container)

    def append_message(self, message: str) -> None:
        self.log_view.appendPlainText(message)


class MainWindow(QMainWindow):
    """Main application window for MatStat."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MatStat")
        self.setMinimumSize(1200, 800)

        self._setup_logging()
        self._setup_ui()
        self._setup_menu()

    def _setup_logging(self) -> None:
        self._log_bridge: LogBridge = LogBridge()
        self._log_handler: QtLogHandler = QtLogHandler(self._log_bridge)
        self._log_handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(self._log_handler)

    def _setup_ui(self) -> None:
        self.tabs: QTabWidget = QTabWidget()

        self.tag_film_tab: TagFilmWidget = TagFilmWidget()
        self.compile_stats_tab: CompileStatsWidget = CompileStatsWidget()
        self.team_eval_tab: TeamEvaluationWidget = TeamEvaluationWidget()
        self.combine_clips_tab: CombineClipsWidget = CombineClipsWidget()
        self.search_tab: SearchWidget = SearchWidget()

        self.tabs.addTab(self.tag_film_tab, "Tag Film")
        self.tabs.addTab(self.compile_stats_tab, "Compile Stats")
        self.tabs.addTab(self.team_eval_tab, "Team Evaluation")
        self.tabs.addTab(self.combine_clips_tab, "Combine Clips")
        self.tabs.addTab(self.search_tab, "Search")

        self.setCentralWidget(self.tabs)

        self.status_bar: QStatusBar = self.statusBar()
        self.status_bar.showMessage("Ready")

        self.log_dock: LogDockWidget = LogDockWidget(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self._log_bridge.messageLogged.connect(self.log_dock.append_message)

    def _setup_menu(self) -> None:
        menu_bar: QMenuBar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")

        settings_action: QAction = file_menu.addAction("&Settings...")
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._show_settings)

        file_menu.addSeparator()

        quit_action: QAction = file_menu.addAction("&Quit")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(QCoreApplication.instance().quit)

        help_menu = menu_bar.addMenu("&Help")
        about_action: QAction = help_menu.addAction("&About")
        about_action.triggered.connect(self._show_about)

    def _show_settings(self) -> None:
        dialog: ConfigEditorDialog = ConfigEditorDialog(self)
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About MatStat",
            "MatStat v2.0\n\n"
            "A tool for statistical analysis and move-and-position-specific "
            "film generation of folkstyle wrestling film.\n\n"
            "Uses ffmpeg for video processing, PyQt6 for the GUI.\n\n"
            "GPL-3.0 License",
        )
