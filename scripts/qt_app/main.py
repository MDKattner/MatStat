#! /usr/bin/env python
"""MatStat Qt6 GUI application entry point."""

import sys
import logging

from pathlib import Path

# Ensure project root is on sys.path so 'scripts' package is importable
_project_root: Path = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from PyQt6.QtWidgets import QApplication

from scripts.helpers import log_file
from scripts.qt_app.main_window import MainWindow


def main() -> None:
    # Ensure the log directory/file exists
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Configure file logging (same format as original scripts)
    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG,
        format="%(asctime)s:%(levelname)s:%(message)s",
        force=True,
    )

    logging.info("MatStat GUI starting.")

    # Show the on-screen log dock only when the debug flag is passed
    visible_logging: bool = "--visible-logging" in sys.argv
    app_args: list[str] = [arg for arg in sys.argv if arg != "--visible-logging"]

    app: QApplication = QApplication(app_args)
    app.setApplicationName("MatStat")
    app.setApplicationDisplayName("MatStat")
    app.setOrganizationName("MatStat")

    window: MainWindow = MainWindow(show_logging=visible_logging)
    window.show()

    logging.info("MatStat GUI started.")

    exit_code: int = app.exec()
    logging.info(f"MatStat GUI exiting with code {exit_code}.")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
