from PySide6.QtCore import QCoreApplication

from rentdesk.platform.paths import (
    get_app_data_dir,
    get_db_path,
    get_default_backup_dir,
    get_license_dir,
    get_logs_dir,
)


def _ensure_app_identity_set():
    if not QCoreApplication.organizationName():
        QCoreApplication.setOrganizationName("RentDeskLite")
    if not QCoreApplication.applicationName():
        QCoreApplication.setApplicationName("RentDeskLite")


def test_app_data_dir_is_outside_install_locations():
    _ensure_app_identity_set()

    app_data_dir = get_app_data_dir()

    assert app_data_dir.exists()
    assert "/Applications/" not in str(app_data_dir)
    assert "Program Files" not in str(app_data_dir)


def test_db_logs_license_backup_paths_are_under_app_data_dir():
    _ensure_app_identity_set()

    app_data_dir = get_app_data_dir()

    assert get_db_path().parent.parent == app_data_dir
    assert get_logs_dir().parent == app_data_dir
    assert get_license_dir().parent == app_data_dir
    assert get_default_backup_dir().parent == app_data_dir
