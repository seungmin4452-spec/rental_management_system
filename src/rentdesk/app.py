from __future__ import annotations

import logging
import sqlite3
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from rentdesk.context import AppContext
from rentdesk.domain.timeutil import now_utc
from rentdesk.persistence.db import DatabaseError, open_database
from rentdesk.platform.instance_lock import InstanceLock
from rentdesk.platform.paths import (
    AppDataUnavailableError,
    get_db_path,
    get_default_backup_dir,
    get_demo_db_path,
    get_lock_path,
    get_logs_dir,
)
from rentdesk.services.sample_data import load_sample_data
from rentdesk.ui import feedback
from rentdesk.ui.main_window import MainWindow

ORGANIZATION_NAME = "RentDeskLite"
APPLICATION_NAME = "RentDeskLite"
log = logging.getLogger("rentdesk")


def bootstrap_database(db_path: Path, backup_dir: Path) -> sqlite3.Connection:
    """사용자 DB를 열고 마이그레이션한다. 기존 DB 구조 변경 전에는 보호 백업을 남긴다."""
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    return open_database(db_path, pre_migration_backup=backup_dir / f"pre_migration_{stamp}.db")


def create_demo_database(db_path: Path) -> sqlite3.Connection:
    """예제 데이터 모드 DB를 새로 만든다. 실제 사용자 DB 경로는 절대 받지 않는다."""
    if db_path.name == "rentdesk.db" and db_path.parent.name == "data":
        raise ValueError("예제 데이터를 실제 사용자 데이터 파일에 적재할 수 없습니다.")
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    conn = open_database(db_path)
    try:
        load_sample_data(conn, now_utc())
    except BaseException:
        conn.close()
        raise
    return conn


def setup_logging(logs_dir: Path) -> None:
    """오류 로그만 파일로 남긴다. 고객 연락처·원장 상세는 기록하지 않는다(PRD 10.1)."""
    handler = RotatingFileHandler(
        logs_dir / "rentdesk.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("rentdesk")
    root.setLevel(logging.WARNING)
    root.addHandler(handler)

    def excepthook(exc_type, exc, tb) -> None:
        log.error("unhandled exception", exc_info=(exc_type, exc, tb))
        feedback.warn(None, feedback.UNEXPECTED_MESSAGE, "오류")

    sys.excepthook = excepthook


class AppController:
    """실제 데이터 ↔ 예제 데이터 모드 전환. 두 모드의 DB 연결을 섞지 않는다."""

    def __init__(self, real_conn: sqlite3.Connection) -> None:
        self.real_conn = real_conn
        self.demo_conn: sqlite3.Connection | None = None
        self.window: MainWindow | None = None
        self.show(AppContext(real_conn))

    def show(self, ctx: AppContext) -> None:
        old = self.window
        self.window = MainWindow(ctx)
        self.window.demo_toggle_requested.connect(self.toggle_demo)
        self.window.show()
        if old is not None:
            old.close()
            old.deleteLater()

    def toggle_demo(self) -> None:
        if self.window is not None and self.window.ctx.is_demo:
            self.show(AppContext(self.real_conn))
            if self.demo_conn is not None:
                self.demo_conn.close()
                self.demo_conn = None
            return
        if not feedback.confirm(
            self.window,
            "예제 데이터 모드를 시작합니다. 실제 데이터와 분리된 별도 파일을 새로 만들며 "
            "실제 데이터는 변경되지 않습니다.",
            "예제 데이터 모드",
        ):
            return
        try:
            self.demo_conn = create_demo_database(get_demo_db_path())
        except (OSError, sqlite3.Error, DatabaseError, AppDataUnavailableError):
            log.exception("demo database creation failed")
            feedback.warn(self.window, "예제 데이터를 준비하지 못했습니다. 저장 공간과 권한을 확인해주세요.")
            return
        self.show(AppContext(self.demo_conn, is_demo=True))

    def close(self) -> None:
        if self.demo_conn is not None:
            self.demo_conn.close()


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(APPLICATION_NAME)

    try:
        setup_logging(get_logs_dir())
        lock = InstanceLock(get_lock_path())
        if not lock.acquire():
            QMessageBox.information(
                None, "RentDesk Lite",
                "RentDesk Lite가 이미 실행 중입니다. 열려 있는 창을 사용해주세요.",
            )
            return 0
    except AppDataUnavailableError as exc:
        QMessageBox.critical(None, "RentDesk Lite", str(exc))
        return 1

    try:
        conn = bootstrap_database(get_db_path(), get_default_backup_dir())
    except (AppDataUnavailableError, DatabaseError) as exc:
        lock.release()
        QMessageBox.critical(None, "RentDesk Lite", str(exc))
        return 1
    except (sqlite3.Error, OSError):
        lock.release()
        QMessageBox.critical(
            None,
            "RentDesk Lite",
            "데이터 파일을 열 수 없습니다. 저장 위치의 접근 권한과 남은 용량을 확인해주세요.",
        )
        return 1

    controller = AppController(conn)
    try:
        return app.exec()
    finally:
        controller.close()
        conn.close()
        lock.release()
