"""OS별 사용자 데이터 경로 조회. QStandardPaths.AppLocalDataLocation만 신뢰하고
C:\\, /Applications, ~/Library 등 절대경로를 직접 하드코딩하지 않는다.

주의: QCoreApplication.setOrganizationName/setApplicationName이 먼저 호출된
상태에서만 writableLocation이 안정적인 값을 반환한다 (app.py에서 보장).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths


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
