"""M4.6 R1: 항상 있는 "기타" 카테고리 — 마이그레이션, 이름 변경·숨김 거부, 정렬."""

from __future__ import annotations

import sqlite3

import pytest

from rentdesk.domain.errors import DuplicateError, InvalidStateError
from rentdesk.domain.models import Category, category_sort_key
from rentdesk.persistence import db
from rentdesk.persistence.db import apply_migrations, connect, current_schema_version
from rentdesk.persistence.db import transaction


def _db_at_version(path, version: int, monkeypatch) -> sqlite3.Connection:
    original = db.load_migrations()
    monkeypatch.setattr(db, "load_migrations", lambda: [m for m in original if m.version <= version])
    conn = connect(path)
    apply_migrations(conn)
    monkeypatch.setattr(db, "load_migrations", lambda: original)
    assert current_schema_version(conn) == version
    return conn


def _add_asset(conn, code: str, category_id: int) -> None:
    conn.execute(
        "INSERT INTO assets (asset_code, name, category_id, daily_rate_won, deposit_won, created_at_utc)"
        " VALUES (?, '장비', ?, 1000, 0, '2026-01-01T00:00:00Z')",
        (code, category_id),
    )


def _system_rows(conn):
    return conn.execute("SELECT id, name, is_archived FROM categories WHERE is_system = 1").fetchall()


def test_new_database_has_system_category(svc):
    system = svc.assets.system_category()
    assert (system.name, system.is_system, system.is_archived) == ("기타", True, False)
    assert [c.name for c in svc.assets.list_categories()] == ["기타"]


def test_migration_creates_system_category_on_existing_db(tmp_path, monkeypatch):
    conn = _db_at_version(tmp_path / "old.db", 3, monkeypatch)
    try:
        conn.execute("INSERT INTO categories (name) VALUES ('카메라')")
        _add_asset(conn, "CAM-1", 1)
        apply_migrations(conn)
        rows = _system_rows(conn)
        assert len(rows) == 1 and rows[0]["name"] == "기타" and rows[0]["is_archived"] == 0
        assert conn.execute("SELECT category_id FROM assets").fetchone()[0] == 1  # 기존 장비 그대로
    finally:
        conn.close()


def test_migration_reuses_existing_archived_category_named_etc(tmp_path, monkeypatch):
    conn = _db_at_version(tmp_path / "old.db", 3, monkeypatch)
    try:
        conn.execute("INSERT INTO categories (name, is_archived) VALUES ('기타', 1)")
        _add_asset(conn, "X-1", 1)
        apply_migrations(conn)
        rows = _system_rows(conn)
        assert [tuple(r) for r in rows] == [(1, "기타", 0)]  # 같은 행을 시스템으로, 숨김 해제
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 1
    finally:
        conn.close()


def test_system_category_cannot_be_renamed_or_archived(svc):
    system = svc.assets.system_category()
    with pytest.raises(InvalidStateError, match="이름을 바꿀 수 없습니다"):
        svc.assets.rename_category(system.id, "미분류")
    with pytest.raises(InvalidStateError, match="숨길 수 없습니다"):
        svc.assets.set_category_archived(system.id, True)
    assert svc.assets.set_category_archived(system.id, False).is_system  # 보이게 하는 것은 문제없음
    assert svc.assets.system_category().name == "기타"


def test_database_guards_system_category(conn, svc):
    system_id = svc.assets.system_category().id
    for sql in (
        "UPDATE categories SET name = '미분류' WHERE id = ?",
        "UPDATE categories SET is_archived = 1 WHERE id = ?",
        "UPDATE categories SET is_system = 0 WHERE id = ?",
        "DELETE FROM categories WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(conn):
                conn.execute(sql, (system_id,))
    other = svc.assets.create_category("카메라")
    with pytest.raises(sqlite3.IntegrityError):  # 시스템 카테고리는 하나뿐
        with transaction(conn):
            conn.execute("UPDATE categories SET is_system = 1 WHERE id = ?", (other.id,))


def test_rename_regular_category(svc):
    camera = svc.assets.create_category("카메라")
    svc.assets.create_category("렌즈")
    asset = svc.add_asset("CAM-1", category="카메라")
    assert svc.assets.rename_category(camera.id, "  카메라 바디 ").name == "카메라 바디"
    assert svc.assets.get_asset(asset.id).category_name == "카메라 바디"
    with pytest.raises(DuplicateError):
        svc.assets.rename_category(camera.id, "렌즈")
    with pytest.raises(DuplicateError):
        svc.assets.rename_category(camera.id, "기타")
    assert svc.assets.rename_category(camera.id, "카메라 바디").name == "카메라 바디"  # 같은 이름 그대로


def test_system_category_always_listed_last(svc):
    for name in ("텐트", "가방", "Zoom", "aux"):
        svc.assets.create_category(name)
    archived = svc.assets.create_category("하하")
    svc.assets.set_category_archived(archived.id, True)
    assert [c.name for c in svc.assets.list_categories()][-1] == "기타"
    assert [c.name for c in svc.assets.list_categories(include_archived=True)][-1] == "기타"


def test_category_sort_key_puts_system_last():
    cats = [Category(1, "기타", False, True), Category(2, "텐트", False), Category(3, "가방", False)]
    assert [c.name for c in sorted(cats, key=category_sort_key)] == ["가방", "텐트", "기타"]


def test_asset_can_be_registered_in_system_category(svc):
    asset = svc.add_asset("X-1", category="기타")
    assert asset.category_id == svc.assets.system_category().id
    assert [c.name for c in svc.assets.list_categories()] == ["기타"]  # 새 카테고리를 만들지 않음
