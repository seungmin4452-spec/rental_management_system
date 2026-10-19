"""M4.6 R3: 요금 규칙 설정(가게 기본값 → 카테고리), 품목별 스냅샷, 출고·연장·반납·대시보드 계산."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import ChargeKind, ReturnCondition
from rentdesk.domain.rates import DEFAULT_RATE_RULE, DayCountMethod, LateFeeMethod, RateRule
from rentdesk.persistence import db
from rentdesk.persistence.db import apply_migrations, connect
from rentdesk.services.settings import rate_rules_for_assets
from tests.support import kst

WEEKLY = RateRule(weekly_enabled=True)
CAMPING = RateRule(day_count=DayCountMethod.CALENDAR, weekend_enabled=True)


@pytest.fixture
def camping(svc):
    category = svc.assets.create_category("캠핑")
    return svc.assets.update_category_rate_rule(category.id, CAMPING)


def _charges(svc, order_id, kind):
    return [c for c in svc.settlements.get_settlement(order_id).charges if c.kind == kind]


def test_new_database_has_default_shop_rule(svc):
    assert svc.settings.get_rate_rule() == DEFAULT_RATE_RULE


def test_shop_rule_update_validates_and_audits(svc):
    with pytest.raises(ValidationError):
        svc.settings.update_rate_rule(RateRule(weekly_multiplier=Decimal("8")))
    assert svc.settings.get_rate_rule() == DEFAULT_RATE_RULE
    saved = svc.settings.update_rate_rule(WEEKLY)
    assert saved == WEEKLY
    action = svc.conn.execute("SELECT action FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert action == "UPDATE_RATE_RULE"


def test_category_rule_overrides_whole_shop_rule(svc, camping):
    svc.settings.update_rate_rule(WEEKLY)
    tent = svc.add_asset("TENT-1", category="캠핑")
    cam = svc.add_asset("CAM-1", category="카메라")
    rules = rate_rules_for_assets(svc.conn, [tent, cam])
    assert rules[tent.id] == CAMPING  # 주 단가는 가게 규칙이지만 카테고리 규칙이 통째로 우선
    assert rules[cam.id] == WEEKLY
    cleared = svc.assets.update_category_rate_rule(camping.id, None)
    assert cleared.rate_rule is None
    assert rate_rules_for_assets(svc.conn, [tent])[tent.id] == WEEKLY


def test_category_rule_validation(svc, camping):
    with pytest.raises(ValidationError):
        svc.assets.update_category_rate_rule(camping.id, RateRule(late_hourly_divisor=0))
    with pytest.raises(ValidationError):
        svc.assets.update_category_rate_rule(camping.id, "weekly")  # type: ignore[arg-type]
    assert svc.assets.list_categories()[0].rate_rule == CAMPING


def test_update_category_saves_name_policy_and_rule_together(svc, camping):
    saved = svc.assets.update_category(
        camping.id, name="캠핑용품", turnaround_buffer_minutes=None, grace_minutes=None,
        late_multiplier=None, rate_rule=WEEKLY,
    )
    assert (saved.name, saved.rate_rule) == ("캠핑용품", WEEKLY)
    kept = svc.assets.update_category(
        camping.id, name="캠핑용품", turnaround_buffer_minutes=60, grace_minutes=None, late_multiplier=None,
    )
    assert kept.rate_rule == WEEKLY  # 생략하면 그대로
    with pytest.raises(ValidationError):
        svc.assets.update_category(
            camping.id, name="다른 이름", turnaround_buffer_minutes=None, grace_minutes=None,
            late_multiplier=None, rate_rule=RateRule(weekly_multiplier=Decimal("0")),
        )
    assert svc.assets.list_categories()[0].name == "캠핑용품"  # 검증 실패 시 이름도 그대로


def test_mixed_order_snapshots_rule_per_item_and_quotes(svc, camping):
    tent = svc.add_asset("TENT-1", category="캠핑", rate=10_000)
    cam = svc.add_asset("CAM-1", category="카메라", rate=10_000)
    # 금 18:00 ~ 일 18:00: 텐트는 날짜 기준 2일 vs 주말 2일치 = 20,000, 카메라는 48시간 = 2일
    order = svc.book(svc.add_customer().id, [tent.id, cam.id], kst(10, 23, 18), kst(10, 25, 18))
    rules = {i.asset_id: i.rate_rule_snapshot for i in order.items}
    assert rules == {tent.id: CAMPING, cam.id: DEFAULT_RATE_RULE}
    quote = svc.reservations.quote(order)
    assert quote.base_rental_fee_won == 40_000

    # 1박2일(금 14:00 → 토 12:00): 텐트 날짜 기준 1일, 카메라 22시간 = 1일
    order2 = svc.book(svc.add_customer("박").id, [tent.id, cam.id], kst(10, 30, 14), kst(10, 31, 12))
    assert svc.reservations.quote(order2).base_rental_fee_won == 20_000


def test_settings_change_does_not_touch_confirmed_order(svc, camping):
    tent = svc.add_asset("TENT-1", category="캠핑", rate=10_000)
    order = svc.book(svc.add_customer().id, [tent.id], kst(10, 20, 10), kst(10, 27, 18))
    before = svc.reservations.quote(order).base_rental_fee_won
    svc.assets.update_category_rate_rule(camping.id, WEEKLY)
    svc.settings.update_rate_rule(RateRule(monthly_enabled=True))
    after = svc.reservations.get_order(order.id)
    assert after.items[0].rate_rule_snapshot == CAMPING
    assert svc.reservations.quote(after).base_rental_fee_won == before == 70_000


def test_draft_rule_is_refreshed_on_confirm(svc, camping):
    tent = svc.add_asset("TENT-1", category="캠핑")
    draft = svc.book(svc.add_customer().id, [tent.id], kst(10, 20, 10), kst(10, 21, 10), confirm=False)
    svc.assets.update_category_rate_rule(camping.id, WEEKLY)
    confirmed = svc.reservations.confirm(draft.id)
    assert confirmed.items[0].rate_rule_snapshot == WEEKLY


def test_preview_quote_uses_current_rules_and_snapshots(svc, camping):
    tent = svc.add_asset("TENT-1", category="캠핑", rate=10_000)
    cam = svc.add_asset("CAM-1", category="카메라", rate=10_000)
    q = svc.reservations.preview_quote([cam.id, tent.id], kst(10, 20, 10), kst(10, 21, 18))
    assert [f.amount_won for f in q.item_fees] == [20_000, 10_000]  # 순서는 요청 순서
    order = svc.book(svc.add_customer().id, [tent.id], kst(10, 20, 10), kst(10, 21, 18))
    svc.assets.update_category_rate_rule(camping.id, None)
    q = svc.reservations.preview_quote([tent.id, cam.id], kst(10, 20, 10), kst(10, 21, 18), order_id=order.id)
    assert [f.amount_won for f in q.item_fees] == [10_000, 20_000]  # 기존 품목은 스냅샷 규칙


def test_price_basis_lists_only_long_rate_items(svc):
    svc.settings.update_rate_rule(WEEKLY)
    cam = svc.add_asset("CAM-1", rate=10_000)
    lens = svc.add_asset("LENS-1", rate=10_000)
    basis = svc.reservations.price_basis([cam.id, lens.id], kst(10, 20, 10), kst(10, 30, 10))
    assert basis == ["CAM-1: 주 단가 적용: 1주 + 3일", "LENS-1: 주 단가 적용: 1주 + 3일"]
    assert svc.reservations.price_basis([cam.id], kst(10, 20, 10), kst(10, 22, 10)) == []


def test_policy_basis_shows_category_rate_rule(svc, camping):
    tent = svc.add_asset("TENT-1", category="캠핑")
    lines = svc.reservations.policy_basis([tent.id])
    assert len(lines) == 1 and "요금 날짜 기준(1박2일 = 1일)" in lines[0] and "(카테고리 설정)" in lines[0]
    cam = svc.add_asset("CAM-1")
    assert svc.reservations.policy_basis([cam.id]) == []


def test_checkout_charges_period_fee_with_basis(svc):
    svc.settings.update_rate_rule(WEEKLY)
    cam = svc.add_asset("CAM-1", rate=10_000)
    order = svc.book(svc.add_customer().id, [cam.id], kst(10, 20, 10), kst(10, 30, 10))
    svc.checkout(order.id, kst(10, 20, 10))
    [rental] = _charges(svc, order.id, ChargeKind.RENTAL)
    assert rental.amount_won == 80_000
    assert rental.description == "CAM-1 주 단가 적용: 1주 + 3일"


def test_checkout_default_rule_description_unchanged_amount(svc):
    cam = svc.add_asset("CAM-1", rate=30_000)
    order = svc.book(svc.add_customer().id, [cam.id], kst(10, 20, 10), kst(10, 22, 10))
    svc.checkout(order.id, kst(10, 20, 10))
    [rental] = _charges(svc, order.id, ChargeKind.RENTAL)
    assert rental.amount_won == 60_000 and "30,000원 × 2일" in rental.description


def test_extension_into_weekly_rate_adds_no_charge(svc):
    svc.settings.update_rate_rule(WEEKLY)
    cam = svc.add_asset("CAM-1", rate=10_000)
    order = svc.book(svc.add_customer().id, [cam.id], kst(10, 20, 10), kst(10, 25, 10))  # 5일 50,000
    svc.checkout(order.id, kst(10, 20, 10))
    preview = svc.extensions.preview(order.id, kst(10, 26, 10))
    assert (preview.quote.added_days, preview.quote.amount_won, preview.quote.waived_by_long_rate) == (1, 0, True)
    svc.extensions.extend(order.id, kst(10, 26, 10))
    assert _charges(svc, order.id, ChargeKind.ADJUSTMENT) == []
    [ext] = svc.extensions.list_extensions(order.id)
    assert (ext.added_days, ext.amount_won) == (1, 0)
    # 더 늘리면 7일 넘는 부분만 일 요금
    svc.extensions.extend(order.id, kst(10, 28, 10))
    [adj] = _charges(svc, order.id, ChargeKind.ADJUSTMENT)
    assert adj.amount_won == 10_000


def test_return_hourly_late_fee_from_snapshot(svc, clock):
    svc.settings.update_rate_rule(RateRule(late_method=LateFeeMethod.HOURLY, late_hourly_divisor=10))
    cam = svc.add_asset("CAM-1", rate=10_000)
    order = svc.book(svc.add_customer().id, [cam.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.settings.update_rate_rule(DEFAULT_RATE_RULE)  # 확정 뒤 바꿔도 스냅샷 사용
    svc.checkout(order.id, kst(10, 20, 10))
    item = order.items[0]
    clock.now = kst(10, 21, 14)
    preview = svc.returns.preview_late_fee(order.id, item.id, kst(10, 21, 14))  # 유예 60분 뒤 3시간
    assert preview.late.late_fee_won == 3_000
    assert "연체 3시간" in preview.basis
    svc.return_asset(order.id, cam.id, kst(10, 21, 14), condition=ReturnCondition.GOOD)
    [late] = _charges(svc, order.id, ChargeKind.LATE)
    assert late.amount_won == 3_000


def test_dashboard_overdue_uses_item_rule(svc, clock):
    svc.settings.update_rate_rule(RateRule(late_method=LateFeeMethod.HOURLY, late_hourly_divisor=10))
    cam = svc.add_asset("CAM-1", rate=10_000)
    order = svc.book(svc.add_customer().id, [cam.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.checkout(order.id, kst(10, 20, 10))
    clock.now = kst(10, 21, 13)  # 유예 60분 뒤 2시간
    [row] = svc.dashboard.snapshot().overdue
    assert row.expected_late_fee_won == 2_000


def test_migration_fills_default_rule_for_existing_items(tmp_path, monkeypatch):
    original = db.load_migrations()
    monkeypatch.setattr(db, "load_migrations", lambda: [m for m in original if m.version <= 5])
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
            " ('R-1', 1, 'CONFIRMED', '2026-10-20T01:00:00Z', '2026-10-21T01:00:00Z', 60,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO rental_items (order_id, asset_id, status, daily_rate_snapshot_won,"
            " deposit_snapshot_won, late_multiplier_snapshot, grace_minutes_snapshot,"
            " turnaround_buffer_snapshot_minutes) VALUES (1, 1, 'RESERVED', 1000, 0, '1', 60, 60)"
        )
        monkeypatch.setattr(db, "load_migrations", lambda: original)
        apply_migrations(conn)
        from rentdesk.persistence import repositories as repo

        order = repo.get_order(conn, 1)
        assert order.items[0].rate_rule_snapshot == DEFAULT_RATE_RULE
        assert repo.get_category(conn, 1).rate_rule is None
        from rentdesk.services.settings import read_rate_rule

        assert read_rate_rule(conn) == DEFAULT_RATE_RULE
    finally:
        conn.close()


def test_rule_json_check_constraint(svc, camping):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        svc.conn.execute("UPDATE categories SET rate_rule_json = 'not json' WHERE id = ?", (camping.id,))
