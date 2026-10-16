"""M4.6 R2: 카테고리별 운영 설정(준비 시간·유예·연체 배수)과 품목별 스냅샷."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentdesk.domain.errors import ConflictError, ValidationError
from rentdesk.domain.models import ReturnCondition
from rentdesk.persistence import db
from rentdesk.persistence.db import apply_migrations, connect
from rentdesk.services.settings import policies_for_assets
from tests.support import kst


@pytest.fixture
def tent(svc):
    category = svc.assets.create_category("텐트")
    return svc.assets.update_category_policy(
        category.id, turnaround_buffer_minutes=12 * 60, grace_minutes=None, late_multiplier=None
    )


def test_category_without_settings_inherits_shop_defaults(svc):
    camera = svc.add_asset("CAM-1", category="카메라")
    policy = policies_for_assets(svc.conn, [camera])[camera.id]
    assert policy == svc.settings.get_policy()


def test_category_value_overrides_only_that_field(svc, tent):
    asset = svc.add_asset("TENT-1", category="텐트")
    policy = policies_for_assets(svc.conn, [asset])[asset.id]
    shop = svc.settings.get_policy()
    assert policy.turnaround_buffer_minutes == 720
    assert (policy.grace_minutes, policy.late_multiplier) == (shop.grace_minutes, shop.late_multiplier)
    assert tent.policy.overridden == {"turnaround_buffer_minutes"}


def test_update_category_policy_validates_like_shop_settings(svc):
    category = svc.assets.create_category("공구")
    for kwargs in (
        dict(turnaround_buffer_minutes=-1, grace_minutes=None, late_multiplier=None),
        dict(turnaround_buffer_minutes=None, grace_minutes=7 * 24 * 60 + 1, late_multiplier=None),
        dict(turnaround_buffer_minutes=None, grace_minutes=None, late_multiplier="101"),
        dict(turnaround_buffer_minutes=None, grace_minutes=None, late_multiplier="abc"),
        dict(turnaround_buffer_minutes=1.5, grace_minutes=None, late_multiplier=None),
    ):
        with pytest.raises(ValidationError):
            svc.assets.update_category_policy(category.id, **kwargs)
    saved = svc.assets.update_category_policy(
        category.id, turnaround_buffer_minutes=120, grace_minutes=0, late_multiplier="1.5"
    )
    assert saved.policy.late_multiplier == Decimal("1.5") and saved.policy.grace_minutes == 0
    cleared = svc.assets.update_category_policy(
        category.id, turnaround_buffer_minutes=None, grace_minutes=None, late_multiplier=None
    )
    assert cleared.policy.overridden == frozenset()


def test_system_category_can_have_settings(svc):
    system = svc.assets.system_category()
    saved = svc.assets.update_category_policy(
        system.id, turnaround_buffer_minutes=30, grace_minutes=None, late_multiplier=None
    )
    assert saved.policy.turnaround_buffer_minutes == 30 and saved.is_system


def test_mixed_order_snapshots_per_item(svc, tent):
    tools = svc.assets.create_category("공구")
    svc.assets.update_category_policy(
        tools.id, turnaround_buffer_minutes=None, grace_minutes=24 * 60, late_multiplier="1.5"
    )
    t = svc.add_asset("TENT-1", category="텐트")
    d = svc.add_asset("DRILL-1", category="공구")
    c = svc.add_asset("CAM-1", category="카메라")
    order = svc.book(svc.add_customer().id, [t.id, d.id, c.id], kst(10, 20, 10), kst(10, 21, 10))
    items = {i.asset_id: i for i in order.items}
    assert items[t.id].turnaround_buffer_snapshot_minutes == 720
    assert items[d.id].turnaround_buffer_snapshot_minutes == 60
    assert (items[d.id].grace_minutes_snapshot, items[d.id].late_multiplier_snapshot) == (1440, Decimal("1.5"))
    assert (items[c.id].grace_minutes_snapshot, items[c.id].late_multiplier_snapshot) == (60, Decimal("1"))


def test_draft_snapshots_are_refreshed_on_confirm(svc, tent):
    t = svc.add_asset("TENT-1", category="텐트")
    draft = svc.book(svc.add_customer().id, [t.id], kst(10, 20, 10), kst(10, 21, 10), confirm=False)
    svc.assets.update_category_policy(
        tent.id, turnaround_buffer_minutes=24 * 60, grace_minutes=None, late_multiplier=None
    )
    confirmed = svc.reservations.confirm(draft.id)
    assert confirmed.items[0].turnaround_buffer_snapshot_minutes == 1440


def test_settings_change_does_not_touch_confirmed_order(svc, tent):
    t = svc.add_asset("TENT-1", category="텐트")
    c = svc.add_asset("CAM-1", category="카메라")
    order = svc.book(svc.add_customer().id, [t.id, c.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.assets.update_category_policy(
        tent.id, turnaround_buffer_minutes=0, grace_minutes=5, late_multiplier="3"
    )
    svc.set_buffer(0)
    after = {i.asset_id: i for i in svc.reservations.get_order(order.id).items}
    assert after[t.id].turnaround_buffer_snapshot_minutes == 720
    assert after[t.id].grace_minutes_snapshot == 60
    assert after[c.id].turnaround_buffer_snapshot_minutes == 60


def test_availability_boundary_uses_item_buffer(svc, tent):
    t = svc.add_asset("TENT-1", category="텐트")
    c = svc.add_asset("CAM-1", category="카메라")
    customer = svc.add_customer()
    svc.book(customer.id, [t.id, c.id], kst(10, 20, 10), kst(10, 21, 10))
    # 텐트는 반납 예정 + 12시간 = 22:00까지 준비, 카메라는 + 1시간 = 11:00까지
    with pytest.raises(ConflictError, match="TENT-1"):
        svc.book(customer.id, [t.id], kst(10, 21, 21, 59), kst(10, 22, 10))
    svc.book(customer.id, [t.id], kst(10, 21, 22), kst(10, 22, 10))
    with pytest.raises(ConflictError):
        svc.book(customer.id, [c.id], kst(10, 21, 10, 59), kst(10, 22, 10))
    svc.book(customer.id, [c.id], kst(10, 21, 11), kst(10, 22, 10))


def test_new_reservation_before_existing_uses_its_own_category_buffer(svc, tent):
    t = svc.add_asset("TENT-1", category="텐트")
    customer = svc.add_customer()
    svc.book(customer.id, [t.id], kst(10, 22, 10), kst(10, 23, 10))
    # 새 예약(텐트)이 21일 22:00 이후에 끝나면 12시간 준비가 22일 10:00을 넘는다
    with pytest.raises(ConflictError):
        svc.book(customer.id, [t.id], kst(10, 21, 10), kst(10, 21, 22, 1))
    svc.book(customer.id, [t.id], kst(10, 21, 10), kst(10, 21, 22))


def test_screen_precheck_matches_confirm(svc, tent):
    t = svc.add_asset("TENT-1", category="텐트")
    customer = svc.add_customer()
    svc.book(customer.id, [t.id], kst(10, 20, 10), kst(10, 21, 10))
    options = svc.availability.asset_options(kst(10, 21, 21), kst(10, 22, 10))
    assert not next(o for o in options if o.asset.id == t.id).available
    options = svc.availability.asset_options(kst(10, 21, 22), kst(10, 22, 10))
    assert next(o for o in options if o.asset.id == t.id).available


def test_confirmed_edit_keeps_item_snapshot_buffer(svc, tent):
    t = svc.add_asset("TENT-1", category="텐트")
    customer = svc.add_customer()
    order = svc.book(customer.id, [t.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.assets.update_category_policy(
        tent.id, turnaround_buffer_minutes=0, grace_minutes=None, late_multiplier=None
    )
    other = svc.add_asset("TENT-2", category="텐트")
    later = svc.book(customer.id, [t.id], kst(10, 22, 10), kst(10, 23, 10))
    from rentdesk.services.reservations import ReservationInput

    edited = svc.reservations.update_order(order.id, ReservationInput(
        customer_id=customer.id, start_at=kst(10, 20, 10), due_at=kst(10, 21, 10),
        asset_ids=[t.id, other.id],
    ))
    buffers = {i.asset_id: i.turnaround_buffer_snapshot_minutes for i in edited.items}
    assert buffers == {t.id: 720, other.id: 0}  # 기존 품목은 스냅샷, 추가 품목은 현재 설정
    # 기존 품목의 스냅샷(12시간)으로 재검사: 다음 예약(22일 10:00 시작)과 겹치게 늘이면 거부
    with pytest.raises(ConflictError):
        svc.reservations.update_order(order.id, ReservationInput(
            customer_id=customer.id, start_at=kst(10, 20, 10), due_at=kst(10, 21, 22, 1),
            asset_ids=[t.id, other.id],
        ))
    assert later.status.value == "CONFIRMED"


def test_return_warning_uses_item_buffer(svc, tent):
    t = svc.add_asset("TENT-1", category="텐트")
    customer = svc.add_customer()
    first = svc.book(customer.id, [t.id], kst(10, 20, 10), kst(10, 21, 10))
    second = svc.book(customer.id, [t.id], kst(10, 21, 22), kst(10, 22, 10))
    svc.checkout(first.id, kst(10, 20, 10))
    result = svc.return_asset(first.id, t.id, kst(10, 21, 11), condition=ReturnCondition.GOOD)
    # 11:00 반납 + 12시간 = 23:00 > 다음 예약 22:00 → 경고
    assert [c.order_no for c in result.affected_reservations] == [second.order_no]


def test_migration_fills_item_buffer_from_order(tmp_path, monkeypatch):
    original = db.load_migrations()
    monkeypatch.setattr(db, "load_migrations", lambda: [m for m in original if m.version <= 4])
    conn = connect(tmp_path / "old.db")
    try:
        apply_migrations(conn)
        conn.execute("INSERT INTO customers (name, created_at_utc) VALUES ('고객', '2026-01-01T00:00:00Z')")
        conn.execute(
            "INSERT INTO assets (asset_code, name, category_id, daily_rate_won, deposit_won, created_at_utc)"
            " VALUES ('A-1', '장비', 1, 1000, 0, '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO rental_orders (order_no, customer_id, status, start_at_utc, due_at_utc,"
            " turnaround_buffer_snapshot_minutes, created_at_utc, updated_at_utc) VALUES"
            " ('R-1', 1, 'CONFIRMED', '2026-10-20T01:00:00Z', '2026-10-21T01:00:00Z', 90,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO rental_items (order_id, asset_id, status, daily_rate_snapshot_won,"
            " deposit_snapshot_won, late_multiplier_snapshot, grace_minutes_snapshot)"
            " VALUES (1, 1, 'RESERVED', 1000, 0, '1', 60)"
        )
        monkeypatch.setattr(db, "load_migrations", lambda: original)
        apply_migrations(conn)
        row = conn.execute("SELECT turnaround_buffer_snapshot_minutes FROM rental_items").fetchone()
        assert row[0] == 90
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(categories)")}
        assert {"turnaround_buffer_minutes", "grace_minutes", "late_multiplier"} <= cols
    finally:
        conn.close()
