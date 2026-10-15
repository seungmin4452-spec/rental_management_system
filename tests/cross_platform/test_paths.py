"""OS별 사용자 데이터 경로·권한 (PRD 10.1, CROSS_PLATFORM E, TASKS M4 경로·로그 보호)."""

import logging
import sys

import pytest
from PySide6.QtCore import QStandardPaths

from rentdesk.platform.paths import (
    APPLICATION_NAME,
    configure_app_identity,
    get_app_data_dir,
    get_db_path,
    get_default_backup_dir,
    get_demo_db_path,
    get_license_dir,
    get_lock_path,
    get_logs_dir,
    get_preferences_path,
)
from rentdesk.platform.preferences import LocalPreferences


@pytest.fixture(autouse=True)
def qt_test_paths():
    """실제 사용자 폴더 대신 Qt 테스트 전용 위치(~/.qttest 등)를 쓴다."""
    configure_app_identity()
    QStandardPaths.setTestModeEnabled(True)
    yield
    QStandardPaths.setTestModeEnabled(False)


def test_app_data_dir_is_outside_install_locations():
    app_data_dir = get_app_data_dir()
    assert app_data_dir.exists()
    assert "/Applications/" not in str(app_data_dir)
    assert ".app/" not in str(app_data_dir)
    assert "Program Files" not in str(app_data_dir)


def test_app_data_dir_name_is_not_duplicated():
    """조직명을 비워 `RentDeskLite/RentDeskLite` 중복을 없앤다(M3 이월 이슈)."""
    app_data_dir = get_app_data_dir()
    assert app_data_dir.name == APPLICATION_NAME
    assert app_data_dir.parent.name != APPLICATION_NAME


def test_all_user_files_are_under_app_data_dir():
    app_data_dir = get_app_data_dir()
    assert get_db_path() == app_data_dir / "data" / "rentdesk.db"
    assert get_logs_dir() == app_data_dir / "logs"
    assert get_license_dir() == app_data_dir / "license"
    assert get_default_backup_dir() == app_data_dir / "backups"
    assert get_demo_db_path().parent == app_data_dir / "demo"
    assert get_demo_db_path().parent != get_db_path().parent
    assert get_lock_path().parent == app_data_dir
    assert get_preferences_path().parent == app_data_dir


@pytest.mark.skipif(sys.platform == "win32", reason="Windows는 %LOCALAPPDATA% 사용자 ACL로 보호")
def test_app_data_dir_is_private_to_user_on_posix():
    assert get_app_data_dir().stat().st_mode & 0o077 == 0


def test_log_file_rotates_and_skips_info(tmp_path):
    from rentdesk.app import setup_logging

    root = logging.getLogger("rentdesk")
    before_handlers = list(root.handlers)
    previous_hook = sys.excepthook
    setup_logging(tmp_path)
    try:
        logging.getLogger("rentdesk.test").info("info message must not be written")
        logging.getLogger("rentdesk.test").warning("warning written")
        for h in root.handlers:
            h.flush()
        text = (tmp_path / "rentdesk.log").read_text(encoding="utf-8")
        assert "warning written" in text and "info message" not in text
    finally:
        for h in root.handlers[len(before_handlers):]:
            root.removeHandler(h)
            h.close()
        sys.excepthook = previous_hook


def test_local_preferences_backup_dir(tmp_path):
    prefs = LocalPreferences(tmp_path / "앱 데이터" / "preferences.json")
    assert prefs.backup_dir() is None
    folder = tmp_path / "USB 백업 🙂"
    folder.mkdir()
    prefs.set_backup_dir(folder)
    assert LocalPreferences(tmp_path / "앱 데이터" / "preferences.json").backup_dir() == folder
    folder.rmdir()  # USB 분리 등으로 사라지면 기본값으로
    assert prefs.backup_dir() is None
    (tmp_path / "앱 데이터" / "preferences.json").write_text("{broken", encoding="utf-8")
    assert prefs.backup_dir() is None
