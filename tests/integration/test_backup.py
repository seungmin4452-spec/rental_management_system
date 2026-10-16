"""백업·복원 (PRD 10.2~10.3, T-19, T-20, CROSS_PLATFORM F)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

from rentdesk.domain.models import MoneyKind, PaymentMethod
from rentdesk.persistence.db import connect, latest_schema_version, load_migrations, open_database
from rentdesk.services import backup as backup_mod
from rentdesk.services.backup import (
    DB_MEMBER,
    MANIFEST_NAME,
    BackupError,
    BackupExistsError,
    BackupService,
    create_backup,
    data_summary,
    inspect_backup,
    restore_database,
    restore_with_protection,
)
from rentdesk.services.settlements import MoneyInput
from tests.support import kst


def _populate(svc) -> None:
    """장비·고객·예약·출고·부분 반납·금전 원장까지 만든다(한글 포함)."""
    cam1 = svc.add_asset("CAM-1", name="미러리스 카메라 한글 🎥")
    cam2 = svc.add_asset("CAM-2")
    customer = svc.add_customer("홍길동")
    order = svc.book(customer.id, [cam1.id, cam2.id], kst(10, 20, 10), kst(10, 21, 10), discount=5_000)
    svc.checkout(order.id, kst(10, 20, 10))
    svc.return_asset(order.id, cam1.id, kst(10, 21, 9))
    svc.settlements.record(order.id, MoneyInput(
        kind=MoneyKind.DEPOSIT_IN, amount_won=200_000, payment_method=PaymentMethod.CASH))
    svc.settlements.record(order.id, MoneyInput(
        kind=MoneyKind.RENTAL_RECEIPT, amount_won=30_000, payment_method=PaymentMethod.TRANSFER))
    svc.add_customer("보관 전 고객")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_zip(src: Path, dest: Path, *, manifest=None, db_bytes=None, extra=None) -> Path:
    with zipfile.ZipFile(src) as zf:
        m = json.loads(zf.read(MANIFEST_NAME))
        db = zf.read(DB_MEMBER)
    if manifest is not None:
        manifest(m)
    if db_bytes is not None:
        db = db_bytes(db)
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(m))
        zf.writestr(DB_MEMBER, db)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return dest


@pytest.fixture
def backup_file(svc, conn, tmp_path) -> Path:
    _populate(svc)
    folder = tmp_path / "백업 폴더 🙂"
    folder.mkdir()
    return create_backup(conn, folder / "백업 1.zip", now=svc.clock.now).path


# ------------------------------------------------------------------ T-19


def test_t19_backup_while_running_then_restore_preserves_counts_and_totals(svc, conn, tmp_path):
    _populate(svc)
    before = data_summary(conn)
    # 다른 연결이 쓰기 트랜잭션을 진행 중(미커밋)이어도 백업은 커밋된 상태만 담는다
    other = connect(conn.execute("PRAGMA database_list").fetchone()["file"])
    other.execute("BEGIN")
    other.execute("INSERT INTO categories (name) VALUES ('미커밋 분류')")
    try:
        result = create_backup(conn, tmp_path / "실행 중 백업", now=svc.clock.now)
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert result.path.name == "실행 중 백업.zip"
    assert result.manifest.summary == before

    target = tmp_path / "새 PC" / "data" / "rentdesk.db"
    validated = inspect_backup(result.path)
    try:
        restored = restore_database(validated, target, now=svc.clock.now, backup_dir=tmp_path / "bk")
    finally:
        validated.cleanup()
    try:
        assert data_summary(restored) == before
        assert before.counts["rental_orders"] == 1 and before.charge_total_won != 0
        assert restored.execute("SELECT name FROM assets WHERE asset_code='CAM-1'").fetchone()[0] \
            == "미러리스 카메라 한글 🎥"
        assert restored.execute("SELECT 1 FROM categories WHERE name='미커밋 분류'").fetchone() is None
        # 시간은 UTC 문자열 그대로(시간대 변환 없이) 보존
        src_times = conn.execute("SELECT start_at_utc, due_at_utc FROM rental_orders").fetchall()
        dst_times = restored.execute("SELECT start_at_utc, due_at_utc FROM rental_orders").fetchall()
        assert [tuple(r) for r in src_times] == [tuple(r) for r in dst_times]
        assert restored.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        restored.close()


def test_manifest_fields_and_no_absolute_paths_or_license(svc, conn, tmp_path):
    _populate(svc)
    conn.execute("UPDATE app_settings SET value_json = ? WHERE key = 'default_backup_dir'",
                 (json.dumps(str(tmp_path / "내 백업")),))
    result = create_backup(conn, tmp_path / "b.zip", now=svc.clock.now)
    with zipfile.ZipFile(result.path) as zf:
        assert sorted(zf.namelist()) == [MANIFEST_NAME, DB_MEMBER]
        manifest = json.loads(zf.read(MANIFEST_NAME))
        db_bytes = zf.read(DB_MEMBER)
    for key in ("backup_format_version", "schema_version", "created_at_utc", "sha256", "product"):
        assert key in manifest
    assert manifest["product"] == "RentalDesk"
    assert manifest["schema_version"] == latest_schema_version()
    assert manifest["sha256"] == hashlib.sha256(db_bytes).hexdigest()
    assert str(tmp_path) not in json.dumps(manifest, ensure_ascii=False)
    # DB 사본에도 기기 절대경로가 남지 않는다(실제 DB 설정은 그대로)
    assert str(tmp_path).encode() not in db_bytes
    assert json.loads(conn.execute(
        "SELECT value_json FROM app_settings WHERE key='default_backup_dir'").fetchone()[0])


def test_backup_overwrite_needs_confirmation(svc, conn, tmp_path):
    path = tmp_path / "b.zip"
    path.write_text("old")
    with pytest.raises(BackupExistsError):
        create_backup(conn, path, now=svc.clock.now)
    assert path.read_text() == "old"
    create_backup(conn, path, now=svc.clock.now, overwrite=True)
    assert zipfile.is_zipfile(path)


def test_backup_to_missing_folder_is_friendly_error(svc, conn, tmp_path):
    with pytest.raises(BackupError, match="폴더를 찾을 수 없습니다"):
        create_backup(conn, tmp_path / "없는 폴더" / "b.zip", now=svc.clock.now)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 권한 비트로 쓰기 거부를 재현")
def test_backup_permission_error_message(svc, conn, tmp_path):
    import os
    import stat

    folder = tmp_path / "읽기전용"
    folder.mkdir()
    os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(BackupError, match="권한"):
            create_backup(conn, folder / "b.zip", now=svc.clock.now)
    finally:
        os.chmod(folder, stat.S_IRWXU)


def test_service_records_last_backup_and_reminder(svc, conn, tmp_path):
    service = BackupService(conn, svc.clock)
    assert service.last_backup_at() is None
    assert "아직 백업한 적이 없습니다" in service.reminder()
    service.create(tmp_path / "b.zip")
    assert service.last_backup_at() == svc.clock.now
    assert service.reminder() is None
    svc.clock.advance(days=7)
    assert "7일 이상" in service.reminder()
    assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='BACKUP_CREATED'").fetchone()[0] == 1


# ------------------------------------------------------------------ T-20


CORRUPTIONS = {
    "not_zip": lambda src, dest: dest.write_bytes(b"this is not a zip") or dest,
    "truncated_zip": lambda src, dest: dest.write_bytes(src.read_bytes()[: src.stat().st_size // 2]) or dest,
    "db_byte_flipped_hash_mismatch": lambda src, dest: _rewrite_zip(
        src, dest, db_bytes=lambda b: b[:2000] + bytes([b[2000] ^ 0xFF]) + b[2001:]),
    "missing_manifest": lambda src, dest: _write_only_db(src, dest),
    "wrong_product": lambda src, dest: _rewrite_zip(src, dest, manifest=lambda m: m.update(product="Other")),
    "future_format": lambda src, dest: _rewrite_zip(
        src, dest, manifest=lambda m: m.update(backup_format_version=99)),
    "newer_schema": lambda src, dest: _rewrite_zip(
        src, dest, manifest=lambda m: m.update(schema_version=latest_schema_version() + 1)),
    "summary_tampered": lambda src, dest: _rewrite_zip(
        src, dest, manifest=lambda m: m["summary"].update(charge_total_won=1)),
    "path_traversal_member": lambda src, dest: _rewrite_zip(src, dest, extra={"../evil.txt": b"x"}),
    "manifest_not_json": lambda src, dest: _replace_manifest(src, dest, b"{not json"),
}


def _write_only_db(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(src) as zf:
        db = zf.read(DB_MEMBER)
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(DB_MEMBER, db)
    return dest


def _replace_manifest(src: Path, dest: Path, raw: bytes) -> Path:
    with zipfile.ZipFile(src) as zf:
        db = zf.read(DB_MEMBER)
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(MANIFEST_NAME, raw)
        zf.writestr(DB_MEMBER, db)
    return dest


@pytest.mark.parametrize("kind", [*sorted(CORRUPTIONS), "db_garbage_with_matching_hash"])
def test_t20_corrupt_backup_rejected_and_current_db_untouched(svc, conn, db_path, backup_file, tmp_path, kind):
    bad = tmp_path / f"bad_{kind}.zip"
    if kind == "db_garbage_with_matching_hash":
        # 해시는 맞지만 SQLite 내용이 망가진 백업 → integrity_check에서 거부
        with zipfile.ZipFile(backup_file) as zf:
            m = json.loads(zf.read(MANIFEST_NAME))
            db = zf.read(DB_MEMBER)
        broken = db[:100] + b"\x00" * 4000 + db[4100:]
        m["sha256"] = hashlib.sha256(broken).hexdigest()
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr(MANIFEST_NAME, json.dumps(m))
            zf.writestr(DB_MEMBER, broken)
    else:
        CORRUPTIONS[kind](backup_file, bad)
    svc.add_asset("CAM-AFTER-BACKUP")  # 백업 이후에 생긴 현재 데이터
    conn.close()
    before_hash = _file_hash(db_path)

    with pytest.raises(BackupError) as info:
        validated = inspect_backup(bad)
        validated.cleanup()
    assert info.value.message  # 사용자에게 보여줄 한국어 메시지
    assert not (tmp_path / "evil.txt").exists()

    assert _file_hash(db_path) == before_hash
    reopened = open_database(db_path)
    try:
        assert reopened.execute("SELECT 1 FROM assets WHERE asset_code='CAM-AFTER-BACKUP'").fetchone()
    finally:
        reopened.close()


def test_restore_keeps_original_when_reopen_fails(svc, conn, db_path, backup_file, tmp_path, monkeypatch):
    svc.add_asset("CAM-ORIGINAL-ONLY")
    conn.close()
    before_hash = _file_hash(db_path)
    validated = inspect_backup(backup_file)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(backup_mod, "open_database", boom)
    try:
        with pytest.raises(BackupError, match="기존 데이터로 되돌렸습니다"):
            restore_database(validated, db_path, now=svc.clock.now, backup_dir=tmp_path / "bk")
    finally:
        validated.cleanup()
    assert _file_hash(db_path) == before_hash
    assert [p.name for p in db_path.parent.iterdir()] == [db_path.name]  # 임시 파일 정리


def test_restore_moves_leftover_journal_with_old_file(svc, conn, db_path, backup_file, tmp_path):
    """비정상 종료 뒤 남은 저널은 옛 DB와 함께 치워져 새 DB에 적용되지 않는다."""
    conn.close()
    Path(f"{db_path}-journal").write_bytes(b"\x00" * 512)
    validated = inspect_backup(backup_file)
    try:
        restored = restore_database(validated, db_path, now=svc.clock.now, backup_dir=tmp_path / "bk")
    finally:
        validated.cleanup()
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()
    assert sorted(p.name for p in db_path.parent.iterdir()) == [db_path.name]


def test_restore_with_protection_flow(svc, conn, db_path, backup_file, tmp_path):
    svc.add_asset("CAM-NEW")  # 복원하면 사라질 현재 데이터 → 보호 백업에 남아야 함
    current_summary = data_summary(conn)
    validated = inspect_backup(backup_file)
    try:
        outcome = restore_with_protection(
            conn, validated, db_path=db_path, backup_dir=tmp_path / "보호",
            now=svc.clock.now, reopen=open_database,
        )
    finally:
        validated.cleanup()
    try:
        assert outcome.restored
        assert outcome.conn.execute("SELECT 1 FROM assets WHERE asset_code='CAM-NEW'").fetchone() is None
        assert outcome.protection.parent == tmp_path / "보호"
        protected = inspect_backup(outcome.protection)
        assert protected.manifest.summary == current_summary
        protected.cleanup()
    finally:
        outcome.conn.close()


def test_restore_with_protection_reopens_original_on_failure(svc, conn, db_path, backup_file, tmp_path,
                                                             monkeypatch):
    svc.add_asset("CAM-KEEP")
    validated = inspect_backup(backup_file)
    monkeypatch.setattr(backup_mod, "open_database",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
    try:
        outcome = restore_with_protection(
            conn, validated, db_path=db_path, backup_dir=tmp_path / "보호",
            now=svc.clock.now, reopen=open_database,
        )
    finally:
        validated.cleanup()
    try:
        assert not outcome.restored and outcome.error
        assert outcome.conn.execute("SELECT 1 FROM assets WHERE asset_code='CAM-KEEP'").fetchone()
    finally:
        outcome.conn.close()


def test_backup_from_other_os_restores(svc, conn, backup_file, tmp_path):
    """source_os는 참고 정보일 뿐 포맷 판정에 쓰지 않는다(CP-05/06의 포맷 측면)."""
    other = _rewrite_zip(backup_file, tmp_path / "from_windows.zip",
                         manifest=lambda m: m.update(source_os="Windows"))
    validated = inspect_backup(other)
    try:
        assert validated.manifest.source_os == "Windows"
        restored = restore_database(validated, tmp_path / "data" / "rentdesk.db",
                                    now=svc.clock.now, backup_dir=tmp_path / "bk")
        assert data_summary(restored) == data_summary(conn)
        restored.close()
    finally:
        validated.cleanup()


def test_backup_made_under_old_product_name_restores(svc, conn, backup_file, tmp_path):
    """이름 변경(RentDesk Lite → RentalDesk) 전에 만든 백업도 복원할 수 있다."""
    old = _rewrite_zip(backup_file, tmp_path / "old_name.zip",
                       manifest=lambda m: m.update(product="RentDeskLite"))
    validated = inspect_backup(old)
    try:
        restored = restore_database(validated, tmp_path / "data" / "rentdesk.db",
                                    now=svc.clock.now, backup_dir=tmp_path / "bk")
        assert data_summary(restored) == data_summary(conn)
        restored.close()
    finally:
        validated.cleanup()


def test_older_schema_backup_is_migrated_on_restore(tmp_path, clock):
    old_path = tmp_path / "old.db"
    old = connect(old_path)
    first = load_migrations()[0]
    old.executescript(first.sql)
    old.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)")
    old.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00Z')")
    old.execute("INSERT INTO categories (name) VALUES ('옛 분류')")
    result = create_backup(old, tmp_path / "old.zip", now=clock.now)
    old.close()
    assert result.manifest.schema_version == 1

    validated = inspect_backup(result.path)
    try:
        restored = restore_database(validated, tmp_path / "data" / "rentdesk.db", now=clock.now,
                                    backup_dir=tmp_path / "bk")
    finally:
        validated.cleanup()
    try:
        assert restored.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] \
            == latest_schema_version()
        assert restored.execute("SELECT name FROM categories WHERE is_system = 0").fetchone()[0] == "옛 분류"
    finally:
        restored.close()
