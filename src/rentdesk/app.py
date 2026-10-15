from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from rentdesk.domain.timeutil import now_utc
from rentdesk.persistence.db import DatabaseError, open_database
from rentdesk.platform.paths import AppDataUnavailableError, get_db_path, get_default_backup_dir
from rentdesk.ui.main_window import MainWindow

ORGANIZATION_NAME = "RentDeskLite"
APPLICATION_NAME = "RentDeskLite"


def bootstrap_database(db_path: Path, backup_dir: Path) -> sqlite3.Connection:
    """사용자 DB를 열고 마이그레이션한다. 기존 DB 구조 변경 전에는 보호 백업을 남긴다."""
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    return open_database(db_path, pre_migration_backup=backup_dir / f"pre_migration_{stamp}.db")


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(APPLICATION_NAME)

    try:
        conn = bootstrap_database(get_db_path(), get_default_backup_dir())
    except (AppDataUnavailableError, DatabaseError) as exc:
        QMessageBox.critical(None, "RentDesk Lite", str(exc))
        return 1
    except (sqlite3.Error, OSError):
        QMessageBox.critical(
            None,
            "RentDesk Lite",
            "데이터 파일을 열 수 없습니다. 저장 위치의 접근 권한과 남은 용량을 확인해주세요.",
        )
        return 1

    try:
        window = MainWindow()
        window.show()
        return app.exec()
    finally:
        conn.close()
