"""persistence.db: FK·마이그레이션·트랜잭션·스키마 제약 (PRD 8.1, T-21)."""

import sqlite3

import pytest

from rentdesk.persistence import db
from rentdesk.persistence.db import (
    DatabaseError,
    Migration,
    apply_migrations,
    connect,
    current_schema_version,
    latest_schema_version,
    open_database,
    transaction,
)

EXPECTED_TABLES = {
    "categories", "assets", "customers", "rental_orders", "rental_items",
    "maintenance_blocks", "charge_lines", "money_entries", "app_settings",
    "audit_events", "schema_migrations",
}


def test_t21_every_connection_has_foreign_keys_on(db_path, conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    second = connect(db_path)
    try:
        assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        second.close()


def test_initial_schema_created(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables
    assert current_schema_version(conn) == latest_schema_version() == 5
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migrations_are_idempotent(db_path, conn):
    conn.close()
    again = open_database(db_path)
    try:
        assert apply_migrations(again) == []
        assert again.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == latest_schema_version()
    finally:
        again.close()


def test_database_from_newer_version_is_refused(db_path, conn):
    conn.execute("INSERT INTO schema_migrations VALUES (999, '2030-01-01T00:00:00Z')")
    conn.close()
    with pytest.raises(DatabaseError):
        open_database(db_path)


def test_failed_migration_rolls_back_and_backs_up_first(tmp_path, monkeypatch, conn):
    original = db.load_migrations()
    conn.execute("INSERT INTO categories (name) VALUES ('기존')")
    version = latest_schema_version()
    broken = Migration(version + 1, f"{version + 1:03d}_broken.sql", "CREATE TABLE t_new (id INTEGER);\nTHIS IS NOT SQL;")
    monkeypatch.setattr(db, "load_migrations", lambda: [*original, broken])
    backup = tmp_path / "pre_migration.db"

    with pytest.raises(DatabaseError):
        apply_migrations(conn, pre_migration_backup=backup)

    assert current_schema_version(conn) == version
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='t_new'").fetchone() is None
    assert not conn.in_transaction
    copy = sqlite3.connect(backup)
    try:
        assert copy.execute("SELECT name FROM categories WHERE is_system = 0").fetchall() == [("기존",)]
    finally:
        copy.close()


def test_transaction_rolls_back_on_exception(conn):
    with pytest.raises(RuntimeError):
        with transaction(conn):
            conn.execute("INSERT INTO categories (name) VALUES ('롤백대상')")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM categories WHERE name = '롤백대상'").fetchone()[0] == 0
    assert not conn.in_transaction


def test_nested_transaction_is_refused(conn):
    with transaction(conn):
        with pytest.raises(RuntimeError):
            with transaction(conn):
                pass


def _seed_order(conn):
    # 카테고리 1번은 마이그레이션이 만든 "기타"(M4.6 R1)
    conn.execute(
        "INSERT INTO assets (id, asset_code, name, category_id, daily_rate_won, deposit_won,"
        " created_at_utc) VALUES (1, 'CAM-1', '카메라', 1, 30000, 100000, '2026-10-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO customers (id, name, created_at_utc) VALUES (1, '고객', '2026-10-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO rental_orders (id, order_no, customer_id, status, start_at_utc, due_at_utc,"
        " actual_checkout_at_utc, turnaround_buffer_snapshot_minutes, created_at_utc, updated_at_utc)"
        " VALUES (1, 'R-20261020-0001', 1, 'IN_RENTAL', '2026-10-20T01:00:00Z',"
        " '2026-10-21T01:00:00Z', '2026-10-20T01:00:00Z', 60, '2026-10-01T00:00:00Z',"
        " '2026-10-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO rental_items (id, order_id, asset_id, status, daily_rate_snapshot_won,"
        " deposit_snapshot_won, late_multiplier_snapshot, grace_minutes_snapshot)"
        " VALUES (1, 1, 1, 'OUT', 30000, 100000, '1', 60)"
    )


class TestConstraints:
    def test_foreign_key_enforced(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO assets (asset_code, name, category_id, daily_rate_won, deposit_won,"
                " created_at_utc) VALUES ('X', 'x', 999, 0, 0, '2026-10-01T00:00:00Z')"
            )

    @pytest.mark.parametrize("amount", ["-1", "1.5", "'1,000원'"])
    def test_money_must_be_non_negative_integer(self, conn, amount):
        # 카테고리 1번 = 마이그레이션이 만든 "기타"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO assets (asset_code, name, category_id, daily_rate_won, deposit_won,"
                f" created_at_utc) VALUES ('X', 'x', 1, {amount}, 0, '2026-10-01T00:00:00Z')"
            )

    def test_utc_timestamp_format_enforced(self, conn):
        # 카테고리 1번 = 마이그레이션이 만든 "기타"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO assets (asset_code, name, category_id, daily_rate_won, deposit_won,"
                " created_at_utc) VALUES ('X', 'x', 1, 0, 0, '2026-10-01 09:00')"
            )

    def test_asset_code_unique_case_insensitive(self, conn):
        _seed_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO assets (asset_code, name, category_id, daily_rate_won, deposit_won,"
                " created_at_utc) VALUES ('cam-1', 'x', 1, 0, 0, '2026-10-01T00:00:00Z')"
            )

    def test_asset_code_immutable(self, conn):
        _seed_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE assets SET asset_code = 'CAM-9' WHERE id = 1")

    def test_order_start_before_due(self, conn):
        _seed_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE rental_orders SET due_at_utc = start_at_utc WHERE id = 1")

    def test_history_rows_cannot_be_deleted(self, conn):
        _seed_order(conn)
        for sql in (
            "DELETE FROM rental_orders WHERE id = 1",
            "DELETE FROM rental_items WHERE id = 1",
            "DELETE FROM assets WHERE id = 1",
            "DELETE FROM customers WHERE id = 1",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)

    def test_ledgers_are_append_only(self, conn):
        _seed_order(conn)
        conn.execute(
            "INSERT INTO charge_lines (order_id, rental_item_id, kind, amount_won, created_at_utc)"
            " VALUES (1, 1, 'RENTAL', 30000, '2026-10-20T01:00:00Z')"
        )
        conn.execute(
            "INSERT INTO money_entries (order_id, kind, amount_won, created_at_utc)"
            " VALUES (1, 'DEPOSIT_IN', 100000, '2026-10-20T01:00:00Z')"
        )
        for sql in (
            "UPDATE charge_lines SET amount_won = 1",
            "DELETE FROM charge_lines",
            "UPDATE money_entries SET amount_won = 1",
            "DELETE FROM money_entries",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)

    def test_duplicate_rental_charge_per_item_rejected(self, conn):
        _seed_order(conn)
        sql = (
            "INSERT INTO charge_lines (order_id, rental_item_id, kind, amount_won, created_at_utc)"
            " VALUES (1, 1, 'RENTAL', 30000, '2026-10-20T01:00:00Z')"
        )
        conn.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)

    @pytest.mark.parametrize(
        ("kind", "item", "amount"),
        [("RENTAL", "1", -1), ("DISCOUNT", "NULL", 1000), ("LATE", "1", 0), ("DISCOUNT", "1", -1000)],
    )
    def test_charge_sign_rules(self, conn, kind, item, amount):
        _seed_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO charge_lines (order_id, rental_item_id, kind, amount_won, created_at_utc)"
                f" VALUES (1, {item}, '{kind}', {amount}, '2026-10-20T01:00:00Z')"
            )

    def test_money_amount_must_be_positive(self, conn):
        _seed_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO money_entries (order_id, kind, amount_won, created_at_utc)"
                " VALUES (1, 'DEPOSIT_IN', 0, '2026-10-20T01:00:00Z')"
            )

    def test_returned_item_requires_return_time_and_condition(self, conn):
        _seed_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE rental_items SET status = 'RETURNED' WHERE id = 1")

    def test_default_settings_present(self, conn):
        rows = dict(conn.execute("SELECT key, value_json FROM app_settings").fetchall())
        assert rows["grace_minutes"] == "60"
        assert rows["late_multiplier"] == '"1"'
        assert rows["turnaround_buffer_minutes"] == "60"
