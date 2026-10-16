"""대여 연장 (USABILITY_PLAN U6)."""

import pytest

from rentdesk.domain.errors import ConflictError, InvalidStateError, ValidationError
from rentdesk.domain.models import ChargeKind, OrderStatus
from tests.support import kst

START, DUE = kst(10, 20, 10), kst(10, 21, 10)  # 1일


@pytest.fixture
def cam(svc):
    return svc.add_asset("CAM-1", rate=30_000, deposit=100_000)


@pytest.fixture
def out_order(svc, cam):
    order = svc.book(svc.add_customer().id, [cam.id], START, DUE)
    return svc.checkout(order.id, START)


def _adjustments(svc, order_id):
    return [c for c in svc.settlements.get_settlement(order_id).charges if c.kind == ChargeKind.ADJUSTMENT]


def test_extend_charges_added_days_and_moves_due(svc, cam, out_order):
    p = svc.extensions.preview(out_order.id, kst(10, 23, 10))
    assert (p.quote.added_days, p.quote.amount_won, p.can_extend) == (2, 60_000, True)
    extended = svc.extensions.extend(out_order.id, kst(10, 23, 10), note="고객 요청")
    assert extended.due_at == kst(10, 23, 10)
    (adj,) = _adjustments(svc, out_order.id)
    assert adj.amount_won == 60_000 and "연장 +2일" in adj.description and "CAM-1" in adj.description
    (ext,) = svc.extensions.list_extensions(out_order.id)
    assert (ext.previous_due_at, ext.new_due_at, ext.added_days, ext.note) == (DUE, kst(10, 23, 10), 2, "고객 요청")
    result = svc.return_asset(out_order.id, cam.id, kst(10, 23, 9))  # 새 반납 예정 전 → 연체 없음
    assert all(line.late_fee_won == 0 for line in result.lines)
    assert svc.settlements.summary(out_order.id).charge_total_won == 90_000


def test_extend_rejected_when_new_period_conflicts(svc, cam, out_order):
    other = svc.book(svc.add_customer("다음 고객").id, [cam.id], kst(10, 22, 12), kst(10, 23, 12))
    p = svc.extensions.preview(out_order.id, kst(10, 23, 10))
    assert not p.can_extend and p.conflicts[0].order_no == other.order_no
    with pytest.raises(ConflictError, match=other.order_no):
        svc.extensions.extend(out_order.id, kst(10, 23, 10))
    assert svc.reservations.get_order(out_order.id).due_at == DUE
    assert _adjustments(svc, out_order.id) == [] and svc.extensions.list_extensions(out_order.id) == []
    assert svc.extensions.preview(out_order.id, kst(10, 22, 10)).can_extend  # 버퍼 60분 전까지는 가능


def test_extend_while_overdue_replaces_late_fee(svc, cam, out_order):
    svc.clock.now = kst(10, 21, 15)  # 5시간 연체 중
    p = svc.extensions.preview(out_order.id, kst(10, 22, 15))
    assert p.overdue_now and p.quote.added_days == 2  # 1일 → 3일(53시간 올림)
    svc.extensions.extend(out_order.id, kst(10, 22, 15))
    result = svc.return_asset(out_order.id, cam.id, kst(10, 22, 14))
    assert all(line.late_fee_won == 0 for line in result.lines)


def test_extend_after_partial_return_charges_only_out_items(svc, cam):
    lens = svc.add_asset("LEN-1", rate=20_000, deposit=50_000, category="렌즈", name="렌즈")
    order = svc.book(svc.add_customer().id, [cam.id, lens.id], START, DUE)
    svc.checkout(order.id, START)
    svc.return_asset(order.id, cam.id, kst(10, 21, 9))
    p = svc.extensions.preview(order.id, kst(10, 22, 10))
    assert p.asset_codes == ("LEN-1",) and p.quote.amount_won == 20_000


def test_extend_with_discount_and_validation(svc, out_order):
    p = svc.extensions.preview(out_order.id, kst(10, 22, 10), discount_won=5_000)
    assert p.quote.amount_won == 25_000
    with pytest.raises(ValidationError, match="연장 할인"):
        svc.extensions.preview(out_order.id, kst(10, 22, 10), discount_won=40_000)
    with pytest.raises(ValidationError, match="지금 반납 예정보다 뒤"):
        svc.extensions.preview(out_order.id, kst(10, 21, 9))
    svc.clock.now = kst(10, 22, 0)
    with pytest.raises(ValidationError, match="지금보다 뒤"):
        svc.extensions.preview(out_order.id, kst(10, 21, 23))


def test_extend_within_same_charged_day_adds_no_charge(svc, cam):
    # 22시간 예약(1일 요금) → 24시간으로 연장해도 여전히 1일: 추가 요금 없이 반납 예정만 바뀐다
    order = svc.book(svc.add_customer().id, [cam.id], START, kst(10, 21, 8))
    svc.checkout(order.id, START)
    svc.extensions.extend(order.id, kst(10, 21, 10))
    assert _adjustments(svc, order.id) == []
    (ext,) = svc.extensions.list_extensions(order.id)
    assert (ext.added_days, ext.amount_won) == (0, 0)


def test_extend_only_in_rental(svc, cam):
    order = svc.book(svc.add_customer().id, [cam.id], START, DUE)
    with pytest.raises(InvalidStateError, match="대여 중인 예약만"):
        svc.extensions.preview(order.id, kst(10, 22, 10))
    svc.checkout(order.id, START)
    svc.return_asset(order.id, cam.id, kst(10, 21, 9))
    assert svc.reservations.get_order(order.id).status == OrderStatus.COMPLETED
    with pytest.raises(InvalidStateError):
        svc.extensions.extend(order.id, kst(10, 22, 10))


def test_extension_history_is_append_only(svc, out_order, conn):
    import sqlite3

    svc.extensions.extend(out_order.id, kst(10, 22, 10))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM order_extensions")
