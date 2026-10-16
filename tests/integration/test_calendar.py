"""예약 달력 서비스(U9): 보기 기간 조회, 막대 종류·칸·줄, 필터, 새 예약 기본 기간, 500장비 성능."""

import time
from datetime import date

import pytest

from rentdesk.domain.calendar import BarKind
from rentdesk.domain.errors import ValidationError
from tests.support import bulk_load_orders, kst

WEEK = date(2026, 10, 19)  # 월요일, 시계는 10/19 09:00


def bars_of(view, asset_id):
    row = next(r for r in view.rows if r.asset.id == asset_id)
    return row.bars


def test_week_view_days_and_empty_rows(svc):
    a = svc.add_asset("CAM-1")
    b = svc.add_asset("CAM-2")
    view = svc.calendar.view(WEEK, 7)
    assert view.days[0] == WEEK and view.days[-1] == date(2026, 10, 25)
    assert view.today == date(2026, 10, 19)
    assert [r.asset.id for r in view.rows] == [a.id, b.id]
    assert all(r.bars == () and r.lane_count == 1 for r in view.rows)


def test_bars_for_each_status(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer("홍길동")
    draft = svc.book(k.id, [a.id], kst(10, 24, 10), kst(10, 25, 10), confirm=False)
    confirmed = svc.book(k.id, [a.id], kst(10, 22, 10), kst(10, 23, 10))
    rented = svc.book(k.id, [a.id], kst(10, 19, 10), kst(10, 20, 10))
    svc.checkout(rented.id, kst(10, 19, 10))
    view = svc.calendar.view(WEEK, 7)
    by_order = {b.order_id: b for b in bars_of(view, a.id)}
    assert by_order[rented.id].kind == BarKind.IN_RENTAL
    assert (by_order[rented.id].first_col, by_order[rented.id].last_col) == (0, 1)
    assert by_order[confirmed.id].kind == BarKind.CONFIRMED
    assert (by_order[confirmed.id].first_col, by_order[confirmed.id].last_col) == (3, 4)
    assert by_order[draft.id].kind == BarKind.DRAFT
    assert by_order[draft.id].customer_name == "홍길동"
    assert by_order[draft.id].label == f"{draft.order_no} 홍길동 (임시)"
    assert "점유하지 않습니다" in by_order[draft.id].tooltip
    # 10/23 10시 반납 예정과 10/24 10시 시작은 10/23·10/24 칸을 각각 쓰므로 줄이 겹치지 않는다
    assert {b.lane for b in by_order.values()} == {0}

    hidden = svc.calendar.view(WEEK, 7, include_drafts=False)
    assert draft.id not in {b.order_id for b in bars_of(hidden, a.id)}


def test_overdue_bar_runs_to_view_end_and_returned_bar_ends_at_actual_return(svc, clock):
    a = svc.add_asset("CAM-1")
    b = svc.add_asset("CAM-2")
    k = svc.add_customer()
    late = svc.book(k.id, [a.id], kst(10, 19, 10), kst(10, 20, 10))
    svc.checkout(late.id, kst(10, 19, 10))
    done = svc.book(k.id, [b.id], kst(10, 19, 10), kst(10, 22, 10))
    svc.checkout(done.id, kst(10, 19, 10))
    svc.return_asset(done.id, b.id, kst(10, 21, 9))
    clock.now = kst(10, 21, 12)

    view = svc.calendar.view(WEEK, 7)
    (overdue,) = bars_of(view, a.id)
    assert overdue.kind == BarKind.OVERDUE and overdue.end_at is None
    assert (overdue.first_col, overdue.last_col) == (0, 6)
    assert overdue.label.endswith("(연체)")
    assert "반납 전까지" in overdue.tooltip
    (returned,) = bars_of(view, b.id)
    assert returned.kind == BarKind.RETURNED
    assert (returned.first_col, returned.last_col) == (0, 2)  # 반납 예정(10/22)이 아니라 실제 반납(10/21)까지

    assert bars_of(svc.calendar.view(WEEK, 7, include_returned=False), b.id) == ()


def test_canceled_orders_hidden_and_extension_moves_bar(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer()
    canceled = svc.book(k.id, [a.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.reservations.cancel(canceled.id)
    rented = svc.book(k.id, [a.id], kst(10, 19, 10), kst(10, 20, 10))
    svc.checkout(rented.id, kst(10, 19, 10))
    svc.extensions.extend(rented.id, kst(10, 23, 10))
    (bar,) = bars_of(svc.calendar.view(WEEK, 7), a.id)
    assert bar.order_id == rented.id and bar.last_col == 4  # 연장된 반납 예정(10/23)까지


def test_same_day_turnover_uses_second_lane(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer()
    first = svc.book(k.id, [a.id], kst(10, 20, 9), kst(10, 21, 9))
    second = svc.book(k.id, [a.id], kst(10, 21, 15), kst(10, 22, 15))
    (row,) = svc.calendar.view(WEEK, 7).rows
    lanes = {b.order_id: b.lane for b in row.bars}
    assert lanes == {first.id: 0, second.id: 1}
    assert row.lane_count == 2


def test_maintenance_blocks_and_period_filter(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer()
    svc.book(k.id, [a.id], kst(11, 10, 10), kst(11, 11, 10))  # 보기 밖
    svc.maintenance.create_block(a.id, kst(10, 21, 9), None, "센서 청소")
    svc.maintenance.create_block(a.id, kst(10, 1, 9), kst(10, 3, 9), "지난 정비")
    (block,) = bars_of(svc.calendar.view(WEEK, 7), a.id)
    assert block.kind == BarKind.MAINTENANCE and block.order_id is None
    assert (block.first_col, block.last_col) == (2, 6)
    assert block.label == "정비: 센서 청소"
    assert "종료일 미정" in block.tooltip


def test_bar_started_before_view_is_marked(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer()
    svc.clock.now = kst(10, 10, 9)
    order = svc.book(k.id, [a.id], kst(10, 15, 10), kst(10, 21, 10))
    svc.clock.now = kst(10, 19, 9)
    (bar,) = bars_of(svc.calendar.view(WEEK, 7), a.id)
    assert bar.order_id == order.id and bar.starts_before_view
    assert (bar.first_col, bar.last_col) == (0, 2)


def test_filters_by_category_query_and_hides_retired(svc):
    cam = svc.add_asset("CAM-1", category="카메라")
    svc.add_asset("LEN-1", category="렌즈", name="광각 렌즈")
    retired = svc.add_asset("CAM-9")
    svc.assets.retire_asset(retired.id)
    lens_cat = next(c.id for c in svc.assets.list_categories() if c.name == "렌즈")
    assert [r.asset.asset_code for r in svc.calendar.view(WEEK, 7).rows] == ["CAM-1", "LEN-1"]
    assert [r.asset.asset_code for r in svc.calendar.view(WEEK, 7, category_id=lens_cat).rows] == ["LEN-1"]
    assert [r.asset.id for r in svc.calendar.view(WEEK, 7, query="cam").rows] == [cam.id]


def test_view_days_validated(svc):
    for days in (0, 63):
        with pytest.raises(ValidationError):
            svc.calendar.view(WEEK, days)


def test_new_reservation_period(svc):
    assert svc.calendar.new_reservation_period(date(2026, 10, 20), date(2026, 10, 22)) == (
        kst(10, 20, 10), kst(10, 22, 10)
    )
    with pytest.raises(ValidationError):
        svc.calendar.new_reservation_period(date(2026, 10, 18), date(2026, 10, 19))


def test_month_view_500_assets_10000_orders_under_one_second(svc, conn):
    bulk_load_orders(conn, 500, 10_000)
    started = time.perf_counter()
    view = svc.calendar.view(date(2026, 10, 1), 31)
    elapsed = time.perf_counter() - started
    assert len(view.rows) == 500
    assert sum(len(r.bars) for r in view.rows) > 500
    assert elapsed < 1.0, f"{elapsed:.2f}s"
