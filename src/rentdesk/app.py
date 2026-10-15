from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from rentdesk.ui.main_window import MainWindow

ORGANIZATION_NAME = "RentDeskLite"
APPLICATION_NAME = "RentDeskLite"


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(APPLICATION_NAME)

    window = MainWindow()
    window.show()

    return app.exec()
