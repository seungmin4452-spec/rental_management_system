"""OS별 사용자 데이터 경로 조회. QStandardPaths.AppLocalDataLocation만 신뢰하고
C:\\, /Applications, ~/Library 등 절대경로를 직접 하드코딩하지 않는다.

주의: `configure_app_identity()`가 먼저 호출된 상태에서만 writableLocation이
안정적인 값을 반환한다 (app.py에서 보장).

조직명은 비워 둔다. Qt는 `<AppLocalData>/<조직명>/<앱 이름>`을 쓰므로 조직명과 앱 이름이
같으면 `RentalDesk/RentalDesk`로 중복된다. 비우면 PRD 10.1 목표 경로가 된다.
  macOS:   ~/Library/Application Support/RentalDesk
  Windows: %LOCALAPPDATA%/RentalDesk
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QStandardPaths

APPLICATION_NAME = "RentalDesk"
ORGANIZATION_NAME = ""


def configure_app_identity() -> None:
    """앱 데이터 경로를 결정하는 Qt 앱 식별 정보. 모든 진입점에서 경로 조회 전에 호출한다."""
    QCoreApplication.setOrganizationName(ORGANIZATION_NAME)
    QCoreApplication.setApplicationName(APPLICATION_NAME)


def restrict_to_user(path: Path) -> None:
    """POSIX(macOS)에서 다른 사용자 계정이 앱 데이터(고객 정보·로그)를 읽지 못하게 한다.

    Windows의 %LOCALAPPDATA%는 이미 사용자 전용 ACL이므로 건드리지 않는다.
    """
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


class AppDataUnavailableError(RuntimeError):
    """OS가 쓰기 가능한 앱 데이터 경로를 제공하지 못할 때."""


def get_app_data_dir() -> Path:
    location = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    if not location:
        raise AppDataUnavailableError(
            "운영체제에서 앱 데이터 저장 위치를 확인할 수 없습니다. "
            "사용자 폴더 접근 권한을 확인해주세요."
        )
    path = Path(location)
    path.mkdir(parents=True, exist_ok=True)
    restrict_to_user(path)
    return path


def get_db_path() -> Path:
    db_dir = get_app_data_dir() / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "rentdesk.db"


def get_logs_dir() -> Path:
    logs_dir = get_app_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_license_dir() -> Path:
    license_dir = get_app_data_dir() / "license"
    license_dir.mkdir(parents=True, exist_ok=True)
    return license_dir


def get_default_backup_dir() -> Path:
    backup_dir = get_app_data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def get_demo_db_path() -> Path:
    """예제 데이터 모드 전용 DB. 실제 사용자 DB(`data/rentdesk.db`)와 다른 폴더에 둔다."""
    demo_dir = get_app_data_dir() / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    return demo_dir / "rentdesk_demo.db"


def get_lock_path() -> Path:
    return get_app_data_dir() / "rentdesk.lock"


def get_preferences_path() -> Path:
    """기기별 화면 설정(기본 백업 폴더 등). DB가 아니므로 백업 파일에 들어가지 않는다."""
    return get_app_data_dir() / "preferences.json"
