"""종료 시 자동 백업 (USABILITY_PLAN U3)."""

from __future__ import annotations

from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services import auto_backup as ab
from rentdesk.services.backup import BackupService, inspect_backup
from tests.support import kst


def test_one_file_per_seoul_day_replaced_on_same_day(conn, svc, tmp_path):
    folder = tmp_path / "auto"
    svc.add_asset("CAM-1")
    first = ab.run_auto_backup(conn, folder, kst(10, 20, 9))
    svc.add_asset("CAM-2")
    second = ab.run_auto_backup(conn, folder, kst(10, 20, 23, 50))
    assert first.path == second.path and first.path.name == "RentDeskLite_auto_2026-10-20.zip"
    assert list(folder.glob("*.zip")) == [second.path]
    validated = inspect_backup(second.path)  # 수동 백업과 같은 형식·검증 통과
    assert validated.manifest.summary.counts["assets"] == 2
    validated.cleanup()
    # 서울 자정 넘으면 새 파일
    third = ab.run_auto_backup(conn, folder, kst(10, 21, 0, 5))
    assert third.path.name == "RentDeskLite_auto_2026-10-21.zip"


def test_keeps_only_latest_files(conn, tmp_path):
    folder = tmp_path / "auto"
    for day in range(1, 6):
        ab.run_auto_backup(conn, folder, kst(10, day, 12), keep=3)
    names = sorted(p.name for p in folder.glob("*.zip"))
    assert names == [f"RentDeskLite_auto_2026-10-0{d}.zip" for d in (3, 4, 5)]


def test_prune_ignores_manual_backups(tmp_path):
    (tmp_path / "RentDeskLite_backup_x.zip").write_text("x")
    for d in range(1, 4):
        (tmp_path / f"RentDeskLite_auto_2026-10-0{d}.zip").write_text("x")
    removed = ab.prune(tmp_path, keep=1)
    assert len(removed) == 2
    assert (tmp_path / "RentDeskLite_backup_x.zip").exists()


def test_does_not_touch_manual_backup_reminder(conn, tmp_path):
    ab.run_auto_backup(conn, tmp_path / "auto", kst(10, 20, 9))
    assert BackupService(conn).last_backup_at() is None  # 7일 알림은 외부 보관 백업 기준


def test_failure_returns_notice_instead_of_raising(conn, tmp_path):
    conn.close()  # 닫힌 연결 → 백업 실패
    result = ab.run_auto_backup(conn, tmp_path / "auto", kst(10, 20, 9))
    assert result.path is None and "자동 백업을 만들지 못했습니다" in result.notice


def test_copy_to_backup_dir_and_missing_usb(conn, tmp_path):
    usb = tmp_path / "USB 백업"
    usb.mkdir()
    ok = ab.run_auto_backup(conn, tmp_path / "auto", kst(10, 20, 9), copy_dir=usb, copy_requested=True)
    assert ok.copied_to == usb / ok.path.name and ok.copied_to.exists() and ok.notice is None
    gone = ab.run_auto_backup(conn, tmp_path / "auto", kst(10, 21, 9),
                              copy_dir=tmp_path / "빠진 USB", copy_requested=True)
    assert gone.path is not None and gone.copied_to is None and "찾을 수 없어" in gone.notice


def test_settings_default_on_and_records_result(conn, tmp_path):
    prefs = LocalPreferences(tmp_path / "preferences.json")
    settings = ab.AutoBackupSettings(prefs)
    assert settings.enabled and not settings.copy_to_backup_dir
    result = settings.run(conn, tmp_path / "auto", kst(10, 20, 9))
    assert result.path.exists()
    assert settings.last_at() == kst(10, 20, 9)
    assert settings.notice is None
    settings.set_enabled(False)
    assert settings.run(conn, tmp_path / "auto", kst(10, 21, 9)) is None


def test_settings_survive_corrupt_values(tmp_path):
    prefs = LocalPreferences(tmp_path / "preferences.json")
    prefs.set(ab.LAST_KEY, "엉망")
    prefs.set(ab.ENABLED_KEY, "yes")
    settings = ab.AutoBackupSettings(prefs)
    assert settings.last_at() is None
    assert settings.enabled  # 알 수 없는 값은 기본(켬)
