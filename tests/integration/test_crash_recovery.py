"""비정상 종료 후 재실행 (PRD 10.3, T-25) 및 손상 DB 복구."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from rentdesk.app import bootstrap_database
from rentdesk.persistence.db import DatabaseIntegrityError, open_database
from rentdesk.services.backup import create_backup, data_summary, inspect_backup, restore_database
from tests.support import Services, kst

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def _crash_mid_transaction(db_path: Path) -> subprocess.CompletedProcess:
    """다른 프로세스가 쓰기 트랜잭션 도중 강제 종료(os._exit)된 상황을 만든다."""
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(REPO_SRC)!r})
        from rentdesk.persistence.db import connect
        conn = connect({str(db_path)!r})
        conn.execute("BEGIN IMMEDIATE")
        for i in range(500):
            conn.execute("INSERT INTO categories (name) VALUES (?)", (f"미완료-{{i}}",))
        conn.execute("UPDATE app_settings SET value_json = '999' WHERE key = 'grace_minutes'")
        sys.stdout.write("crashing\\n"); sys.stdout.flush()
        os._exit(1)
    """)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_t25_restart_after_crash_keeps_committed_data_only(tmp_path, clock):
    db_path = tmp_path / "데이터" / "rentdesk.db"
    db_path.parent.mkdir()
    conn = bootstrap_database(db_path, tmp_path / "backups")
    svc = Services(conn, clock)
    asset = svc.add_asset("CAM-1")
    order = svc.book(svc.add_customer("홍길동").id, [asset.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.checkout(order.id, kst(10, 20, 10))
    committed = data_summary(conn)
    conn.close()

    result = _crash_mid_transaction(db_path)
    assert result.returncode == 1 and "crashing" in result.stdout

    reopened = bootstrap_database(db_path, tmp_path / "backups")  # 무결성 검사 포함
    try:
        assert reopened.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert data_summary(reopened) == committed
        assert reopened.execute("SELECT COUNT(*) FROM categories WHERE name LIKE '미완료-%'").fetchone()[0] == 0
        assert reopened.execute(
            "SELECT value_json FROM app_settings WHERE key='grace_minutes'").fetchone()[0] == "60"
        assert reopened.execute("SELECT status FROM rental_orders").fetchone()[0] == "IN_RENTAL"
        # 잠금이 남지 않아 곧바로 다시 쓸 수 있다(남은 저널은 SQLite가 무효화한 cold 저널)
        Services(reopened, clock).add_asset("CAM-AFTER-CRASH")
    finally:
        reopened.close()


def _corrupt(db_path: Path) -> None:
    data = bytearray(db_path.read_bytes())
    # 1페이지(헤더·스키마)는 두고 두 번째 페이지부터 덮어써 B-tree를 깨뜨린다
    page = 4096
    data[page: page * 3] = b"\xa5" * (page * 2)
    db_path.write_bytes(bytes(data))


def test_corrupt_db_detected_at_startup_and_file_untouched(tmp_path, clock):
    db_path = tmp_path / "rentdesk.db"
    conn = open_database(db_path)
    svc = Services(conn, clock)
    for i in range(30):
        svc.add_asset(f"CAM-{i}")
    conn.close()
    _corrupt(db_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    with pytest.raises(DatabaseIntegrityError) as info:
        bootstrap_database(db_path, tmp_path / "backups")
    assert "손상" in str(info.value) and "삭제하지 않았습니다" in str(info.value)
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    assert not (tmp_path / "backups").exists()  # 손상 파일에 마이그레이션·백업을 시도하지 않음


def test_not_a_database_file_is_reported_as_corrupt(tmp_path):
    db_path = tmp_path / "rentdesk.db"
    db_path.write_bytes(b"definitely not sqlite" * 100)
    with pytest.raises(DatabaseIntegrityError):
        open_database(db_path)


def test_recover_corrupt_db_from_backup_keeps_corrupt_copy(tmp_path, clock):
    db_path = tmp_path / "data" / "rentdesk.db"
    db_path.parent.mkdir()
    conn = open_database(db_path)
    svc = Services(conn, clock)
    for i in range(30):
        svc.add_asset(f"CAM-{i}")
    backup = create_backup(conn, tmp_path / "백업.zip", now=clock.now)
    expected = data_summary(conn)
    conn.close()
    _corrupt(db_path)
    Path(f"{db_path}-journal").write_bytes(b"stale journal")  # 손상과 함께 남은 저널
    corrupt_bytes = db_path.read_bytes()

    validated = inspect_backup(backup.path)
    keep = tmp_path / "backups" / "corrupt_x.db"
    try:
        restored = restore_database(validated, db_path, now=clock.now, backup_dir=tmp_path / "backups",
                                    keep_current_as=keep)
    finally:
        validated.cleanup()
    try:
        assert data_summary(restored) == expected
    finally:
        restored.close()
    assert keep.read_bytes() == corrupt_bytes
    assert Path(f"{keep}-journal").exists()
    assert not Path(f"{db_path}-journal").exists()
