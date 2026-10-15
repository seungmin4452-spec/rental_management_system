from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from nacl.signing import VerifyKey
from PySide6.QtWidgets import QApplication, QMessageBox

from rentdesk import __version__
from rentdesk.context import AppContext
from rentdesk.domain.timeutil import now_utc, to_seoul
from rentdesk.license_public_key import PUBLIC_KEY_HEX
from rentdesk.persistence.db import (
    DatabaseError,
    DatabaseIntegrityError,
    latest_schema_version,
    open_database,
)
from rentdesk.platform import paths as platform_paths
from rentdesk.platform.device_id import get_default_provider
from rentdesk.platform.environment import get_environment_info
from rentdesk.platform.instance_lock import InstanceLock
from rentdesk.platform.paths import AppDataUnavailableError, configure_app_identity
from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services.backup import (
    BackupError,
    BackupService,
    create_backup,
    inspect_backup,
    restore_database,
    restore_with_protection,
)
from rentdesk.services.licensing import LicenseService, LicenseStatus, verify_license
from rentdesk.services.sample_data import load_sample_data
from rentdesk.ui import feedback
from rentdesk.ui.backup_actions import choose_backup_file, confirm_restore, save_backup_dialog
from rentdesk.ui.dialogs.license_dialog import GateChoice, LicenseDialog
from rentdesk.ui.main_window import MainWindow

log = logging.getLogger("rentdesk")


@dataclass(frozen=True)
class AppPaths:
    """사용자 데이터 위치. 실행 시 QStandardPaths로 정하고 테스트는 임시 폴더를 넣는다."""
    db: Path
    backups: Path
    demo_db: Path
    license_dir: Path
    preferences: Path

    @classmethod
    def from_platform(cls) -> AppPaths:
        return cls(
            db=platform_paths.get_db_path(),
            backups=platform_paths.get_default_backup_dir(),
            demo_db=platform_paths.get_demo_db_path(),
            license_dir=platform_paths.get_license_dir(),
            preferences=platform_paths.get_preferences_path(),
        )


def bootstrap_database(db_path: Path, backup_dir: Path) -> sqlite3.Connection:
    """사용자 DB를 열고(무결성 검사 포함) 마이그레이션한다. 기존 DB 구조 변경 전에는 보호 백업을 남긴다."""
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
    """라이선스 확인, 실제 데이터 ↔ 예제 데이터 모드 전환, 복원 후 화면 재시작.

    두 모드의 DB 연결을 섞지 않는다. 라이선스가 없으면 실제 데이터 화면을 열지 않지만
    실제 DB를 삭제·변경하지 않으며 백업으로 꺼내는 것은 허용한다(PRD 11.1).
    """
    def __init__(self, real_conn: sqlite3.Connection, paths: AppPaths, license_service,
                 preferences=None) -> None:
        self.real_conn = real_conn
        self.paths = paths
        self.license = license_service
        self.preferences = preferences
        self.demo_conn: sqlite3.Connection | None = None
        self.window = None
        # 테스트에서 대화상자 대신 선택값을 돌려주도록 바꿀 수 있다.
        self.run_gate: Callable[..., str] = self._run_gate_dialog

    # ------------------------------------------------------------------ windows

    def _context(self, conn: sqlite3.Connection, *, demo: bool):

        return AppContext(conn, is_demo=demo, license=self.license, preferences=self.preferences)

    def show(self, ctx) -> None:

        old = self.window
        self.window = MainWindow(ctx)
        self.window.demo_toggle_requested.connect(self.toggle_demo)
        self.window.restore_requested.connect(self.restore)
        self.window.show()
        if old is not None:
            old.close()
            old.deleteLater()

    @property
    def in_demo(self) -> bool:
        return self.window is not None and self.window.ctx.is_demo

    def start(self) -> bool:
        """첫 화면을 띄운다. 사용자가 종료를 고르면 False."""
        check = self.license.check()
        if check.valid:
            self.show(self._context(self.real_conn, demo=False))
            return True
        log.warning("license check failed: %s", check.status.value)
        return self._gate(from_demo=False)

    def _run_gate_dialog(self, *, from_demo: bool) -> str:

        dlg = LicenseDialog(self.license, self.window, backup_real=self.backup_real,
                            quit_label="예제 데이터 모드 계속" if from_demo else "종료")
        dlg.exec()
        return dlg.choice

    def _gate(self, *, from_demo: bool) -> bool:

        choice = self.run_gate(from_demo=from_demo)
        if choice == GateChoice.ACTIVATED and self.license.check().valid:
            self._leave_demo_to_real()
            return True
        if from_demo:
            return True  # 예제 모드 유지
        if choice == GateChoice.DEMO:
            return self.start_demo(ask=False)
        return False

    def backup_real(self, parent) -> None:

        save_backup_dialog(parent, BackupService(self.real_conn), self.preferences)

    def _leave_demo_to_real(self) -> None:
        self.show(self._context(self.real_conn, demo=False))
        if self.demo_conn is not None:
            self.demo_conn.close()
            self.demo_conn = None

    def start_demo(self, *, ask: bool = True) -> bool:

        if ask and not feedback.confirm(
            self.window,
            "예제 데이터 모드를 시작합니다. 실제 데이터와 분리된 별도 파일을 새로 만들며 "
            "실제 데이터는 변경되지 않습니다.",
            "예제 데이터 모드",
        ):
            return False
        try:
            conn = create_demo_database(self.paths.demo_db)
        except (OSError, sqlite3.Error, DatabaseError):
            log.exception("demo database creation failed")
            feedback.warn(self.window, "예제 데이터를 준비하지 못했습니다. 저장 공간과 권한을 확인해주세요.")
            return False
        if self.demo_conn is not None:
            self.demo_conn.close()
        self.demo_conn = conn
        self.show(self._context(conn, demo=True))
        return True

    def toggle_demo(self) -> None:
        if self.in_demo:
            if self.license.check().valid:
                self._leave_demo_to_real()
            else:
                self._gate(from_demo=True)
            return
        self.start_demo(ask=True)

    # ------------------------------------------------------------------ restore

    def restore(self, validated) -> bool:
        """설정 화면에서 확인을 마친 백업으로 실제 DB를 교체하고 화면을 다시 연다."""

        parent = self.window
        try:
            outcome = restore_with_protection(
                self.real_conn, validated, db_path=self.paths.db, backup_dir=self.paths.backups,
                now=now_utc(), reopen=lambda p: bootstrap_database(p, self.paths.backups),
            )
        except BackupError as exc:
            feedback.warn(parent, f"복원하지 않았습니다. 현재 데이터는 그대로입니다.\n{exc.message}")
            return False
        finally:
            validated.cleanup()
        self.real_conn = outcome.conn
        self.show(self._context(self.real_conn, demo=False))
        if not outcome.restored:
            feedback.warn(self.window, f"{outcome.error}\n현재 데이터는 그대로입니다.", "복원 실패")
            return False
        feedback.info(
            self.window,
            f"백업 데이터로 복원했습니다.\n복원 전 데이터는 보호 백업으로 저장했습니다:\n{outcome.protection}",
            "복원 완료",
        )
        return True

    def close(self) -> None:
        if self.demo_conn is not None:
            self.demo_conn.close()
            self.demo_conn = None


def recover_corrupt_database(paths: AppPaths, message: str) -> sqlite3.Connection | None:
    """시작 시 DB가 손상된 경우(T-25): 이유를 알리고 백업 복원을 제안한다. 손상 파일은 보관한다."""


    while True:
        box = QMessageBox(QMessageBox.Icon.Critical, "RentDesk Lite", message)
        restore_btn = box.addButton("백업 파일에서 복원…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("종료", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not restore_btn:
            return None
        path = choose_backup_file(None, None)
        if path is None:
            continue
        validated = confirm_restore(None, path, protect_current=False)
        if validated is None:
            continue
        now = now_utc()
        try:
            conn = restore_database(
                validated, paths.db, now=now, backup_dir=paths.backups,
                keep_current_as=paths.backups / f"corrupt_{to_seoul(now):%Y%m%d_%H%M%S}.db",
            )
        except BackupError as exc:
            feedback.warn(None, exc.message, "복원 실패")
            continue
        finally:
            validated.cleanup()
        feedback.info(None, "백업 데이터로 복원했습니다. 손상된 파일은 백업 폴더에 보관했습니다.", "복원 완료")
        return conn


def run_self_check(output: Path) -> int:
    """패키징 결과 스모크 검사(`--self-check <결과.json>`).

    사용자 데이터 폴더·잠금·라이선스 파일을 건드리지 않고 임시 폴더에서만 동작한다.
    창 없는 플랫폼(offscreen)으로 Qt 위젯·SQLite·마이그레이션·예제 데이터·백업/검증·Ed25519를 확인한다.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    checks: dict[str, object] = {}
    ok = False
    try:
        app = QApplication.instance() or QApplication([])
        env = get_environment_info()
        checks.update(os=env.os_name, machine=env.machine, os_version=env.os_version,
                      python=env.python_version, sqlite=sqlite3.sqlite_version, version=__version__)
        with tempfile.TemporaryDirectory(prefix="rentdesk-selfcheck-") as work:
            conn = create_demo_database(Path(work) / "selfcheck.db")
            try:
                checks["schema_version"] = latest_schema_version()
                window = MainWindow(AppContext(conn, is_demo=True))
                window.show()
                app.processEvents()
                checks["window_title"] = window.windowTitle()
                window.close()
                result = create_backup(conn, Path(work) / "백업 테스트.zip", now=now_utc())
                validated = inspect_backup(result.path)
                checks["backup_counts"] = validated.manifest.summary.counts
                validated.cleanup()
            finally:
                conn.close()
        # 앱에 들어 있는 공개키가 올바른 Ed25519 키이고, 형식이 틀린 파일을 거부하는지
        bogus = verify_license("{}", public_key=VerifyKey(bytes.fromhex(PUBLIC_KEY_HEX)),
                               machine_code="A" * 20, now=now_utc())
        checks["license_rejects_malformed"] = bogus.status == LicenseStatus.MALFORMED
        ok = bool(checks["license_rejects_malformed"])
    except Exception as exc:  # noqa: BLE001 - 결과 파일에 실패 이유를 남긴다
        checks["error"] = f"{exc.__class__.__name__}: {exc}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if "--self-check" in args:
        idx = args.index("--self-check")
        out = Path(args[idx + 1]) if idx + 1 < len(args) else Path.cwd() / "rentdesk_selfcheck.json"
        return run_self_check(out)



    app = QApplication(args)
    configure_app_identity()

    try:
        setup_logging(platform_paths.get_logs_dir())
        lock = InstanceLock(platform_paths.get_lock_path())
        if not lock.acquire():
            QMessageBox.information(
                None, "RentDesk Lite",
                "RentDesk Lite가 이미 실행 중입니다. 열려 있는 창을 사용해주세요.",
            )
            return 0
        paths = AppPaths.from_platform()
    except AppDataUnavailableError as exc:
        QMessageBox.critical(None, "RentDesk Lite", str(exc))
        return 1

    try:
        try:
            conn = bootstrap_database(paths.db, paths.backups)
        except DatabaseIntegrityError as exc:
            log.error("database integrity check failed at startup")
            conn = recover_corrupt_database(paths, str(exc))
            if conn is None:
                return 1
        except (AppDataUnavailableError, DatabaseError) as exc:
            QMessageBox.critical(None, "RentDesk Lite", str(exc))
            return 1
        except (sqlite3.Error, OSError):
            log.exception("database open failed")
            QMessageBox.critical(
                None,
                "RentDesk Lite",
                "데이터 파일을 열 수 없습니다. 저장 위치의 접근 권한과 남은 용량을 확인해주세요.",
            )
            return 1

        license_service = LicenseService(paths.license_dir, get_default_provider())
        controller = AppController(conn, paths, license_service, LocalPreferences(paths.preferences))
        try:
            if not controller.start():
                return 0
            return app.exec()
        finally:
            controller.close()
            controller.real_conn.close()
    finally:
        lock.release()
