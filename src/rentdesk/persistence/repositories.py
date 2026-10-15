"""SQL CRUD. 항상 파라미터 바인딩을 사용하며 트랜잭션 경계는 서비스 계층이 정한다.

이 모듈은 검증·정책 판단을 하지 않는다(서비스 책임). DB 행 ↔ 도메인 데이터형 변환만 담당.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal

from rentdesk.domain.models import (
    Asset,
    Category,
    ChargeKind,
    ChargeLine,
    Customer,
    CustomerType,
    ItemStatus,
    LifecycleStatus,
    MaintenanceBlock,
    MoneyEntry,
    MoneyKind,
    OrderStatus,
    PaymentMethod,
    RentalItem,
    RentalOrder,
    ReturnCondition,
)
from rentdesk.domain.timeutil import parse_utc_str, parse_utc_str_opt, to_utc_str


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


# --------------------------------------------------------------------------- categories


def _row_to_category(row: sqlite3.Row) -> Category:
    return Category(id=row["id"], name=row["name"], is_archived=bool(row["is_archived"]))


def get_category_by_name(conn: sqlite3.Connection, name: str) -> Category | None:
    row = conn.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()
    return _row_to_category(row) if row else None


def get_category(conn: sqlite3.Connection, category_id: int) -> Category | None:
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    return _row_to_category(row) if row else None


def insert_category(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def list_categories(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[Category]:
    sql = "SELECT * FROM categories"
    if not include_archived:
        sql += " WHERE is_archived = 0"
    sql += " ORDER BY name"
    return [_row_to_category(r) for r in conn.execute(sql)]


def set_category_archived(conn: sqlite3.Connection, category_id: int, archived: bool) -> None:
    conn.execute(
        "UPDATE categories SET is_archived = ? WHERE id = ?", (int(archived), category_id)
    )


# --------------------------------------------------------------------------- assets

_ASSET_SELECT = """
SELECT a.*, c.name AS category_name
FROM assets a JOIN categories c ON c.id = a.category_id
"""


def _row_to_asset(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        asset_code=row["asset_code"],
        name=row["name"],
        category_id=row["category_id"],
        category_name=row["category_name"],
        brand=row["brand"],
        model=row["model"],
        serial_no=row["serial_no"],
        location=row["location"],
        daily_rate_won=row["daily_rate_won"],
        deposit_won=row["deposit_won"],
        lifecycle_status=LifecycleStatus(row["lifecycle_status"]),
        note=row["note"],
        created_at=parse_utc_str(row["created_at_utc"]),
    )


def insert_asset(
    conn: sqlite3.Connection,
    *,
    asset_code: str,
    name: str,
    category_id: int,
    brand: str | None,
    model: str | None,
    serial_no: str | None,
    location: str | None,
    daily_rate_won: int,
    deposit_won: int,
    note: str | None,
    created_at: datetime,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO assets (asset_code, name, category_id, brand, model, serial_no, location,
                            daily_rate_won, deposit_won, lifecycle_status, note, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """,
        (
            asset_code, name, category_id, brand, model, serial_no, location,
            daily_rate_won, deposit_won, note, to_utc_str(created_at),
        ),
    )
    return int(cur.lastrowid)


def update_asset(
    conn: sqlite3.Connection,
    asset_id: int,
    *,
    name: str,
    category_id: int,
    brand: str | None,
    model: str | None,
    serial_no: str | None,
    location: str | None,
    daily_rate_won: int,
    deposit_won: int,
    note: str | None,
) -> None:
    conn.execute(
        """
        UPDATE assets SET name = ?, category_id = ?, brand = ?, model = ?, serial_no = ?,
               location = ?, daily_rate_won = ?, deposit_won = ?, note = ?
        WHERE id = ?
        """,
        (name, category_id, brand, model, serial_no, location,
         daily_rate_won, deposit_won, note, asset_id),
    )


def set_asset_lifecycle(conn: sqlite3.Connection, asset_id: int, status: LifecycleStatus) -> None:
    conn.execute(
        "UPDATE assets SET lifecycle_status = ? WHERE id = ?", (status.value, asset_id)
    )


def get_asset(conn: sqlite3.Connection, asset_id: int) -> Asset | None:
    row = conn.execute(_ASSET_SELECT + " WHERE a.id = ?", (asset_id,)).fetchone()
    return _row_to_asset(row) if row else None


def get_asset_by_code(conn: sqlite3.Connection, asset_code: str) -> Asset | None:
    row = conn.execute(_ASSET_SELECT + " WHERE a.asset_code = ?", (asset_code,)).fetchone()
    return _row_to_asset(row) if row else None


def get_assets(conn: sqlite3.Connection, asset_ids: Sequence[int]) -> dict[int, Asset]:
    if not asset_ids:
        return {}
    rows = conn.execute(
        _ASSET_SELECT + f" WHERE a.id IN ({_placeholders(len(asset_ids))})", tuple(asset_ids)
    )
    return {r["id"]: _row_to_asset(r) for r in rows}


def search_assets(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    include_retired: bool = False,
    category_id: int | None = None,
) -> list[Asset]:
    clauses, params = [], []
    if query:
        pattern = _like_pattern(query)
        clauses.append(
            "(a.asset_code LIKE ? ESCAPE '\\' OR a.name LIKE ? ESCAPE '\\'"
            " OR a.model LIKE ? ESCAPE '\\' OR a.brand LIKE ? ESCAPE '\\'"
            " OR a.serial_no LIKE ? ESCAPE '\\')"
        )
        params += [pattern] * 5
    if not include_retired:
        clauses.append("a.lifecycle_status = 'ACTIVE'")
    if category_id is not None:
        clauses.append("a.category_id = ?")
        params.append(category_id)
    sql = _ASSET_SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY a.asset_code"
    return [_row_to_asset(r) for r in conn.execute(sql, params)]


def max_auto_asset_number(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MAX(CAST(substr(asset_code, 5) AS INTEGER)) FROM assets"
        " WHERE asset_code GLOB 'AST-[0-9][0-9][0-9][0-9][0-9][0-9]'"
    ).fetchone()
    return row[0] or 0


# --------------------------------------------------------------------------- customers


def _row_to_customer(row: sqlite3.Row) -> Customer:
    return Customer(
        id=row["id"],
        name=row["name"],
        customer_type=CustomerType(row["customer_type"]),
        phone=row["phone"],
        email=row["email"],
        note=row["note"],
        is_archived=bool(row["is_archived"]),
        created_at=parse_utc_str(row["created_at_utc"]),
    )


def insert_customer(
    conn: sqlite3.Connection,
    *,
    name: str,
    customer_type: CustomerType,
    phone: str | None,
    email: str | None,
    note: str | None,
    created_at: datetime,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO customers (name, customer_type, phone, email, note, is_archived, created_at_utc)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (name, customer_type.value, phone, email, note, to_utc_str(created_at)),
    )
    return int(cur.lastrowid)


def update_customer(
    conn: sqlite3.Connection,
    customer_id: int,
    *,
    name: str,
    customer_type: CustomerType,
    phone: str | None,
    email: str | None,
    note: str | None,
) -> None:
    conn.execute(
        "UPDATE customers SET name = ?, customer_type = ?, phone = ?, email = ?, note = ?"
        " WHERE id = ?",
        (name, customer_type.value, phone, email, note, customer_id),
    )


def set_customer_archived(conn: sqlite3.Connection, customer_id: int, archived: bool) -> None:
    conn.execute(
        "UPDATE customers SET is_archived = ? WHERE id = ?", (int(archived), customer_id)
    )


def get_customer(conn: sqlite3.Connection, customer_id: int) -> Customer | None:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return _row_to_customer(row) if row else None


def search_customers(
    conn: sqlite3.Connection, *, query: str = "", include_archived: bool = False
) -> list[Customer]:
    clauses, params = [], []
    if query:
        pattern = _like_pattern(query)
        clauses.append(
            "(name LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\')"
        )
        params += [pattern] * 3
    if not include_archived:
        clauses.append("is_archived = 0")
    sql = "SELECT * FROM customers"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY name, id"
    return [_row_to_customer(r) for r in conn.execute(sql, params)]


# --------------------------------------------------------------------------- orders / items


def _row_to_item(row: sqlite3.Row) -> RentalItem:
    return RentalItem(
        id=row["id"],
        order_id=row["order_id"],
        asset_id=row["asset_id"],
        asset_code=row["asset_code"],
        status=ItemStatus(row["status"]),
        daily_rate_snapshot_won=row["daily_rate_snapshot_won"],
        deposit_snapshot_won=row["deposit_snapshot_won"],
        late_multiplier_snapshot=Decimal(row["late_multiplier_snapshot"]),
        grace_minutes_snapshot=row["grace_minutes_snapshot"],
        actual_return_at=parse_utc_str_opt(row["actual_return_at_utc"]),
        return_condition=(
            ReturnCondition(row["return_condition"]) if row["return_condition"] else None
        ),
        return_note=row["return_note"],
    )


def _row_to_order(row: sqlite3.Row, items: Iterable[RentalItem]) -> RentalOrder:
    return RentalOrder(
        id=row["id"],
        order_no=row["order_no"],
        customer_id=row["customer_id"],
        status=OrderStatus(row["status"]),
        start_at=parse_utc_str(row["start_at_utc"]),
        due_at=parse_utc_str(row["due_at_utc"]),
        actual_checkout_at=parse_utc_str_opt(row["actual_checkout_at_utc"]),
        turnaround_buffer_snapshot_minutes=row["turnaround_buffer_snapshot_minutes"],
        discount_won=row["discount_won"],
        notes=row["notes"],
        created_at=parse_utc_str(row["created_at_utc"]),
        updated_at=parse_utc_str(row["updated_at_utc"]),
        items=tuple(items),
        checkout_note=row["checkout_note"],
    )


def max_order_seq_for_date(conn: sqlite3.Connection, yyyymmdd: str) -> int:
    row = conn.execute(
        "SELECT MAX(CAST(substr(order_no, 12) AS INTEGER)) FROM rental_orders"
        " WHERE order_no GLOB ?",
        (f"R-{yyyymmdd}-[0-9]*",),
    ).fetchone()
    return row[0] or 0


def insert_order(
    conn: sqlite3.Connection,
    *,
    order_no: str,
    customer_id: int,
    status: OrderStatus,
    start_at: datetime,
    due_at: datetime,
    buffer_minutes: int,
    discount_won: int,
    notes: str | None,
    now: datetime,
) -> int:
    ts = to_utc_str(now)
    cur = conn.execute(
        """
        INSERT INTO rental_orders (order_no, customer_id, status, start_at_utc, due_at_utc,
                                   turnaround_buffer_snapshot_minutes, discount_won, notes,
                                   created_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (order_no, customer_id, status.value, to_utc_str(start_at), to_utc_str(due_at),
         buffer_minutes, discount_won, notes, ts, ts),
    )
    return int(cur.lastrowid)


def update_order_header(
    conn: sqlite3.Connection,
    order_id: int,
    *,
    customer_id: int,
    start_at: datetime,
    due_at: datetime,
    discount_won: int,
    notes: str | None,
    now: datetime,
) -> None:
    conn.execute(
        """
        UPDATE rental_orders SET customer_id = ?, start_at_utc = ?, due_at_utc = ?,
               discount_won = ?, notes = ?, updated_at_utc = ?
        WHERE id = ?
        """,
        (customer_id, to_utc_str(start_at), to_utc_str(due_at), discount_won, notes,
         to_utc_str(now), order_id),
    )


def set_order_status(
    conn: sqlite3.Connection,
    order_id: int,
    status: OrderStatus,
    *,
    now: datetime,
    buffer_minutes: int | None = None,
) -> None:
    if buffer_minutes is None:
        conn.execute(
            "UPDATE rental_orders SET status = ?, updated_at_utc = ? WHERE id = ?",
            (status.value, to_utc_str(now), order_id),
        )
    else:
        conn.execute(
            "UPDATE rental_orders SET status = ?, turnaround_buffer_snapshot_minutes = ?,"
            " updated_at_utc = ? WHERE id = ?",
            (status.value, buffer_minutes, to_utc_str(now), order_id),
        )


def insert_item(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    asset_id: int,
    daily_rate_won: int,
    deposit_won: int,
    late_multiplier: Decimal,
    grace_minutes: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO rental_items (order_id, asset_id, status, daily_rate_snapshot_won,
                                  deposit_snapshot_won, late_multiplier_snapshot,
                                  grace_minutes_snapshot)
        VALUES (?, ?, 'RESERVED', ?, ?, ?, ?)
        """,
        (order_id, asset_id, daily_rate_won, deposit_won, str(late_multiplier), grace_minutes),
    )
    return int(cur.lastrowid)


def update_item_snapshot(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    daily_rate_won: int,
    deposit_won: int,
    late_multiplier: Decimal,
    grace_minutes: int,
) -> None:
    conn.execute(
        """
        UPDATE rental_items SET daily_rate_snapshot_won = ?, deposit_snapshot_won = ?,
               late_multiplier_snapshot = ?, grace_minutes_snapshot = ?
        WHERE id = ?
        """,
        (daily_rate_won, deposit_won, str(late_multiplier), grace_minutes, item_id),
    )


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM rental_items WHERE id = ?", (item_id,))


def set_items_status_for_order(
    conn: sqlite3.Connection, order_id: int, from_status: ItemStatus, to_status: ItemStatus
) -> None:
    conn.execute(
        "UPDATE rental_items SET status = ? WHERE order_id = ? AND status = ?",
        (to_status.value, order_id, from_status.value),
    )


_ITEM_SELECT = """
SELECT i.*, a.asset_code
FROM rental_items i JOIN assets a ON a.id = i.asset_id
"""


def get_items(conn: sqlite3.Connection, order_id: int) -> list[RentalItem]:
    rows = conn.execute(_ITEM_SELECT + " WHERE i.order_id = ? ORDER BY i.id", (order_id,))
    return [_row_to_item(r) for r in rows]


def get_order(conn: sqlite3.Connection, order_id: int) -> RentalOrder | None:
    row = conn.execute("SELECT * FROM rental_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        return None
    return _row_to_order(row, get_items(conn, order_id))


def list_orders(
    conn: sqlite3.Connection,
    *,
    statuses: Sequence[OrderStatus] | None = None,
    customer_id: int | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    query: str = "",
) -> list[RentalOrder]:
    """기간 필터는 예약 기간 `[start, due)`가 `[period_start, period_end)`와 겹치는 주문.

    `query`는 예약번호 또는 고객명 부분 일치.
    """
    clauses, params = [], []
    if query:
        pattern = _like_pattern(query)
        clauses.append(
            "(order_no LIKE ? ESCAPE '\\' OR customer_id IN"
            " (SELECT id FROM customers WHERE name LIKE ? ESCAPE '\\'))"
        )
        params += [pattern, pattern]
    if statuses:
        clauses.append(f"status IN ({_placeholders(len(statuses))})")
        params += [s.value for s in statuses]
    if customer_id is not None:
        clauses.append("customer_id = ?")
        params.append(customer_id)
    if period_end is not None:
        clauses.append("start_at_utc < ?")
        params.append(to_utc_str(period_end))
    if period_start is not None:
        clauses.append("due_at_utc > ?")
        params.append(to_utc_str(period_start))
    sql = "SELECT * FROM rental_orders"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY start_at_utc DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    order_ids = [r["id"] for r in rows]
    items_by_order: dict[int, list[RentalItem]] = {oid: [] for oid in order_ids}
    item_rows = conn.execute(
        _ITEM_SELECT + f" WHERE i.order_id IN ({_placeholders(len(order_ids))}) ORDER BY i.id",
        order_ids,
    )
    for ir in item_rows:
        items_by_order[ir["order_id"]].append(_row_to_item(ir))
    return [_row_to_order(r, items_by_order[r["id"]]) for r in rows]


def occupying_item_rows(
    conn: sqlite3.Connection, asset_ids: Sequence[int]
) -> list[sqlite3.Row]:
    """가용성 판단 대상 품목(확정 이후 주문의 미취소 품목). 정책 판단은 도메인 함수가 한다."""
    if not asset_ids:
        return []
    return conn.execute(
        f"""
        SELECT i.asset_id, i.status AS item_status, i.actual_return_at_utc,
               o.id AS order_id, o.order_no, o.status AS order_status,
               o.start_at_utc, o.due_at_utc, o.turnaround_buffer_snapshot_minutes
        FROM rental_items i JOIN rental_orders o ON o.id = i.order_id
        WHERE i.asset_id IN ({_placeholders(len(asset_ids))})
          AND o.status IN ('CONFIRMED', 'IN_RENTAL', 'COMPLETED')
          AND i.status <> 'CANCELED'
        """,
        tuple(asset_ids),
    ).fetchall()


def out_item_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """현재 OUT 상태인 모든 품목(연체 판단용)."""
    return conn.execute(
        """
        SELECT i.id AS item_id, i.asset_id, a.asset_code, o.id AS order_id, o.order_no,
               o.due_at_utc
        FROM rental_items i
        JOIN rental_orders o ON o.id = i.order_id
        JOIN assets a ON a.id = i.asset_id
        WHERE i.status = 'OUT'
        """
    ).fetchall()


def confirmed_orders_for_asset(
    conn: sqlite3.Connection, asset_id: int
) -> list[sqlite3.Row]:
    """해당 장비를 포함한 출고 전 확정 예약."""
    return conn.execute(
        """
        SELECT o.id AS order_id, o.order_no, o.start_at_utc, o.due_at_utc,
               o.turnaround_buffer_snapshot_minutes
        FROM rental_items i JOIN rental_orders o ON o.id = i.order_id
        WHERE i.asset_id = ? AND o.status = 'CONFIRMED' AND i.status = 'RESERVED'
        ORDER BY o.start_at_utc
        """,
        (asset_id,),
    ).fetchall()


def asset_has_out_items(conn: sqlite3.Connection, asset_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM rental_items WHERE asset_id = ? AND status = 'OUT' LIMIT 1", (asset_id,)
    ).fetchone()
    return row is not None


def customer_has_orders(conn: sqlite3.Connection, customer_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM rental_orders WHERE customer_id = ? LIMIT 1", (customer_id,)
    ).fetchone()
    return row is not None


def mark_order_checked_out(
    conn: sqlite3.Connection,
    order_id: int,
    *,
    checkout_at: datetime,
    checkout_note: str | None,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE rental_orders SET status = 'IN_RENTAL', actual_checkout_at_utc = ?,"
        " checkout_note = ?, updated_at_utc = ? WHERE id = ?",
        (to_utc_str(checkout_at), checkout_note, to_utc_str(now), order_id),
    )


def mark_item_returned(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    returned_at: datetime,
    condition: ReturnCondition,
    note: str | None,
) -> None:
    conn.execute(
        "UPDATE rental_items SET status = 'RETURNED', actual_return_at_utc = ?,"
        " return_condition = ?, return_note = ? WHERE id = ? AND status = 'OUT'",
        (to_utc_str(returned_at), condition.value, note, item_id),
    )


def out_items_in_other_orders(
    conn: sqlite3.Connection, asset_ids: Sequence[int], order_id: int
) -> list[sqlite3.Row]:
    """다른 주문에서 아직 반납되지 않은(OUT) 같은 장비."""
    if not asset_ids:
        return []
    return conn.execute(
        f"""
        SELECT i.asset_id, a.asset_code, o.order_no
        FROM rental_items i
        JOIN rental_orders o ON o.id = i.order_id
        JOIN assets a ON a.id = i.asset_id
        WHERE i.asset_id IN ({_placeholders(len(asset_ids))})
          AND i.status = 'OUT' AND i.order_id <> ?
        ORDER BY a.asset_code
        """,
        (*asset_ids, order_id),
    ).fetchall()


# --------------------------------------------------------------------------- ledgers


def _row_to_charge(row: sqlite3.Row) -> ChargeLine:
    return ChargeLine(
        id=row["id"],
        order_id=row["order_id"],
        rental_item_id=row["rental_item_id"],
        kind=ChargeKind(row["kind"]),
        amount_won=row["amount_won"],
        description=row["description"],
        created_at=parse_utc_str(row["created_at_utc"]),
    )


def _row_to_money(row: sqlite3.Row) -> MoneyEntry:
    return MoneyEntry(
        id=row["id"],
        order_id=row["order_id"],
        kind=MoneyKind(row["kind"]),
        amount_won=row["amount_won"],
        payment_method=PaymentMethod(row["payment_method"]) if row["payment_method"] else None,
        note=row["note"],
        created_at=parse_utc_str(row["created_at_utc"]),
        related_entry_id=row["related_entry_id"],
    )


def insert_charge(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    rental_item_id: int | None,
    kind: ChargeKind,
    amount_won: int,
    description: str | None,
    now: datetime,
) -> int:
    cur = conn.execute(
        "INSERT INTO charge_lines (order_id, rental_item_id, kind, amount_won, description,"
        " created_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, rental_item_id, kind.value, amount_won, description, to_utc_str(now)),
    )
    return int(cur.lastrowid)


def list_charges(conn: sqlite3.Connection, order_id: int) -> list[ChargeLine]:
    rows = conn.execute(
        "SELECT * FROM charge_lines WHERE order_id = ? ORDER BY id", (order_id,)
    )
    return [_row_to_charge(r) for r in rows]


def insert_money_entry(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    kind: MoneyKind,
    amount_won: int,
    payment_method: PaymentMethod | None,
    note: str | None,
    related_entry_id: int | None,
    now: datetime,
) -> int:
    cur = conn.execute(
        "INSERT INTO money_entries (order_id, kind, amount_won, payment_method, note,"
        " created_at_utc, related_entry_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (order_id, kind.value, amount_won, payment_method.value if payment_method else None,
         note, to_utc_str(now), related_entry_id),
    )
    return int(cur.lastrowid)


def get_money_entry(conn: sqlite3.Connection, entry_id: int) -> MoneyEntry | None:
    row = conn.execute("SELECT * FROM money_entries WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_money(row) if row else None


def list_money_entries(conn: sqlite3.Connection, order_id: int) -> list[MoneyEntry]:
    rows = conn.execute(
        "SELECT * FROM money_entries WHERE order_id = ? ORDER BY id", (order_id,)
    )
    return [_row_to_money(r) for r in rows]


def reversed_total_for_entry(conn: sqlite3.Connection, entry_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_won), 0) FROM money_entries WHERE related_entry_id = ?",
        (entry_id,),
    ).fetchone()
    return row[0]


def ledger_totals_by_order(
    conn: sqlite3.Connection, order_ids: Sequence[int]
) -> dict[int, tuple[list[tuple[ChargeKind, int]], list[tuple[MoneyKind, int]]]]:
    """주문별 (청구 종류별 합계, 금전 종류별 합계). 정산 판단은 도메인 함수가 한다."""
    result: dict[int, tuple[list, list]] = {oid: ([], []) for oid in order_ids}
    if not order_ids:
        return result
    marks = _placeholders(len(order_ids))
    for r in conn.execute(
        f"SELECT order_id, kind, SUM(amount_won) AS total FROM charge_lines"
        f" WHERE order_id IN ({marks}) GROUP BY order_id, kind",
        tuple(order_ids),
    ):
        result[r["order_id"]][0].append((ChargeKind(r["kind"]), r["total"]))
    for r in conn.execute(
        f"SELECT order_id, kind, SUM(amount_won) AS total FROM money_entries"
        f" WHERE order_id IN ({marks}) GROUP BY order_id, kind",
        tuple(order_ids),
    ):
        result[r["order_id"]][1].append((MoneyKind(r["kind"]), r["total"]))
    return result


# --------------------------------------------------------------------------- maintenance


def _row_to_block(row: sqlite3.Row) -> MaintenanceBlock:
    return MaintenanceBlock(
        id=row["id"],
        asset_id=row["asset_id"],
        start_at=parse_utc_str(row["start_at_utc"]),
        end_at=parse_utc_str_opt(row["end_at_utc"]),
        reason=row["reason"],
        created_at=parse_utc_str(row["created_at_utc"]),
        closed_at=parse_utc_str_opt(row["closed_at_utc"]),
    )


def insert_block(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    start_at: datetime,
    end_at: datetime | None,
    reason: str,
    now: datetime,
) -> int:
    cur = conn.execute(
        "INSERT INTO maintenance_blocks (asset_id, start_at_utc, end_at_utc, reason, created_at_utc)"
        " VALUES (?, ?, ?, ?, ?)",
        (asset_id, to_utc_str(start_at), to_utc_str(end_at) if end_at else None, reason,
         to_utc_str(now)),
    )
    return int(cur.lastrowid)


def close_block(
    conn: sqlite3.Connection, block_id: int, *, end_at: datetime, now: datetime
) -> None:
    conn.execute(
        "UPDATE maintenance_blocks SET end_at_utc = ?, closed_at_utc = ? WHERE id = ?",
        (to_utc_str(end_at), to_utc_str(now), block_id),
    )


def get_block(conn: sqlite3.Connection, block_id: int) -> MaintenanceBlock | None:
    row = conn.execute("SELECT * FROM maintenance_blocks WHERE id = ?", (block_id,)).fetchone()
    return _row_to_block(row) if row else None


def blocks_for_assets(
    conn: sqlite3.Connection, asset_ids: Sequence[int]
) -> list[MaintenanceBlock]:
    if not asset_ids:
        return []
    rows = conn.execute(
        f"SELECT * FROM maintenance_blocks WHERE asset_id IN ({_placeholders(len(asset_ids))})"
        " ORDER BY start_at_utc",
        tuple(asset_ids),
    )
    return [_row_to_block(r) for r in rows]


# --------------------------------------------------------------------------- settings / audit


def get_setting(conn: sqlite3.Connection, key: str) -> object:
    row = conn.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    return json.loads(row["value_json"])


def set_setting(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value_json) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def insert_audit(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int | None,
    action: str,
    description: str | None,
    now: datetime,
) -> None:
    """개인 연락처·이메일을 description에 넣지 않는다(PRD 8.1)."""
    conn.execute(
        "INSERT INTO audit_events (occurred_at_utc, entity_type, entity_id, action, description)"
        " VALUES (?, ?, ?, ?, ?)",
        (to_utc_str(now), entity_type, entity_id, action, description),
    )


# --------------------------------------------------------------------------- history-guarded deletes


def asset_has_history(conn: sqlite3.Connection, asset_id: int) -> bool:
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM rental_items WHERE asset_id = ?)"
        " OR EXISTS (SELECT 1 FROM maintenance_blocks WHERE asset_id = ?)",
        (asset_id, asset_id),
    ).fetchone()
    return bool(row[0])


def delete_asset(conn: sqlite3.Connection, asset_id: int) -> None:
    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))


def delete_customer(conn: sqlite3.Connection, customer_id: int) -> None:
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))


# --------------------------------------------------------------------------- read models (M3 화면/보고서)


def customer_names(conn: sqlite3.Connection, customer_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted(set(customer_ids))
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT id, name FROM customers WHERE id IN ({_placeholders(len(ids))})", ids
    )
    return {r["id"]: r["name"] for r in rows}


def out_item_details(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """현재 OUT 품목 + 주문·고객·장비 정보(대시보드 연체/오늘 반납, 연체 보고서)."""
    return conn.execute(
        """
        SELECT i.id AS item_id, i.asset_id, a.asset_code, a.name AS asset_name,
               i.daily_rate_snapshot_won, i.late_multiplier_snapshot, i.grace_minutes_snapshot,
               o.id AS order_id, o.order_no, o.due_at_utc, o.start_at_utc,
               c.id AS customer_id, c.name AS customer_name
        FROM rental_items i
        JOIN rental_orders o ON o.id = i.order_id
        JOIN assets a ON a.id = i.asset_id
        JOIN customers c ON c.id = o.customer_id
        WHERE i.status = 'OUT'
        ORDER BY o.due_at_utc, a.asset_code
        """
    ).fetchall()


def schedule_item_rows(
    conn: sqlite3.Connection, start: datetime, end: datetime
) -> list[sqlite3.Row]:
    """`[start, end)` 안에 출고 예정(시작) 또는 반납 예정(약정 반납)인 확정 이후 주문의 품목."""
    s, e = to_utc_str(start), to_utc_str(end)
    return conn.execute(
        """
        SELECT i.id AS item_id, i.status AS item_status, i.actual_return_at_utc,
               a.asset_code, a.name AS asset_name,
               o.id AS order_id, o.order_no, o.status AS order_status,
               o.start_at_utc, o.due_at_utc, o.actual_checkout_at_utc,
               c.name AS customer_name
        FROM rental_items i
        JOIN rental_orders o ON o.id = i.order_id
        JOIN assets a ON a.id = i.asset_id
        JOIN customers c ON c.id = o.customer_id
        WHERE o.status IN ('CONFIRMED', 'IN_RENTAL', 'COMPLETED')
          AND i.status <> 'CANCELED'
          AND ((o.start_at_utc >= ? AND o.start_at_utc < ?)
               OR (o.due_at_utc >= ? AND o.due_at_utc < ?))
        ORDER BY o.start_at_utc, o.order_no, a.asset_code
        """,
        (s, e, s, e),
    ).fetchall()


def list_block_rows(
    conn: sqlite3.Connection, *, include_closed: bool = False, asset_id: int | None = None
) -> list[sqlite3.Row]:
    clauses, params = [], []
    if not include_closed:
        clauses.append("b.closed_at_utc IS NULL")
    if asset_id is not None:
        clauses.append("b.asset_id = ?")
        params.append(asset_id)
    sql = (
        "SELECT b.*, a.asset_code, a.name AS asset_name FROM maintenance_blocks b"
        " JOIN assets a ON a.id = b.asset_id"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY b.start_at_utc DESC, b.id DESC"
    return conn.execute(sql, params).fetchall()


def all_blocks(conn: sqlite3.Connection) -> list[MaintenanceBlock]:
    return [_row_to_block(r) for r in conn.execute("SELECT * FROM maintenance_blocks")]


def asset_order_rows(conn: sqlite3.Connection, asset_id: int) -> list[sqlite3.Row]:
    """장비 상세: 이 장비가 포함된 모든 예약(최근 시작 순)."""
    return conn.execute(
        """
        SELECT o.id AS order_id, o.order_no, o.status AS order_status,
               o.start_at_utc, o.due_at_utc, o.actual_checkout_at_utc,
               i.status AS item_status, i.actual_return_at_utc, i.return_condition,
               c.name AS customer_name
        FROM rental_items i
        JOIN rental_orders o ON o.id = i.order_id
        JOIN customers c ON c.id = o.customer_id
        WHERE i.asset_id = ?
        ORDER BY o.start_at_utc DESC, o.id DESC
        """,
        (asset_id,),
    ).fetchall()


def search_orders_by_no(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT o.id AS order_id, o.order_no, o.status, o.start_at_utc, o.due_at_utc,
               c.name AS customer_name
        FROM rental_orders o JOIN customers c ON c.id = o.customer_id
        WHERE o.order_no LIKE ? ESCAPE '\\'
        ORDER BY o.start_at_utc DESC LIMIT ?
        """,
        (_like_pattern(query), limit),
    ).fetchall()
