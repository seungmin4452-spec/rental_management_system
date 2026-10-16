"""예약 달력(U9) 순수 함수: 날짜 칸, 막대 종류, 줄 배정, 선택 칸 → 예약 기간."""

from datetime import UTC, date, datetime

import pytest

from rentdesk.domain import calendar as cal
from rentdesk.domain.models import ItemStatus, OrderStatus
from tests.support import kst

FIRST = date(2026, 10, 19)  # 월요일


def span(start, end, days=7):
    return cal.column_span(start, end, FIRST, days)


def test_week_and_month_helpers():
    assert cal.week_start(date(2026, 10, 25)) == FIRST  # 일요일 → 그 주 월요일
    assert cal.week_start(FIRST) == FIRST
    assert cal.month_days(date(2026, 10, 19)) == (date(2026, 10, 1), 31)
    assert cal.month_days(date(2026, 2, 10)) == (date(2026, 2, 1), 28)
    assert cal.month_days(date(2026, 12, 31)) == (date(2026, 12, 1), 31)


def test_view_range_is_seoul_midnight_in_utc():
    start, end = cal.view_range(FIRST, 7)
    assert start == datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 25, 15, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        cal.view_range(FIRST, 0)


def test_span_within_one_day_and_across_midnight():
    assert span(kst(10, 20, 10), kst(10, 20, 18)) == (1, 1)
    # 하루 대여(10시~다음 날 10시)는 장비가 밖에 있는 이틀을 칠한다
    assert span(kst(10, 20, 10), kst(10, 21, 10)) == (1, 2)
    assert span(kst(10, 20, 23, 30), kst(10, 21, 0, 30)) == (1, 2)


def test_span_end_exactly_at_midnight_excludes_that_day():
    assert span(kst(10, 20, 10), kst(10, 21, 0, 0)) == (1, 1)
    assert span(kst(10, 20, 10), kst(10, 21, 0, 1)) == (1, 2)


def test_span_clipped_to_view_and_outside_view():
    assert span(kst(10, 10, 10), kst(10, 21, 10)) == (0, 2)  # 이전 주에 시작
    assert span(kst(10, 24, 10), kst(11, 5, 10)) == (5, 6)  # 다음 주까지
    assert span(kst(10, 10, 10), kst(10, 19, 0, 0)) is None  # 보기 첫날 자정에 끝남
    assert span(kst(10, 26, 0, 0), kst(10, 27, 0, 0)) is None  # 보기 다음 날 자정 시작
    assert span(kst(10, 25, 23, 59), kst(10, 26, 1)) == (6, 6)


def test_span_open_end_runs_to_last_column():
    assert span(kst(10, 21, 10), None) == (2, 6)
    assert span(kst(10, 1, 10), None) == (0, 6)
    assert span(kst(10, 30, 10), None) is None


def test_span_zero_length_and_reversed():
    assert span(kst(10, 22, 9), kst(10, 22, 9)) == (3, 3)
    with pytest.raises(ValueError):
        span(kst(10, 22, 9), kst(10, 22, 8))


@pytest.mark.parametrize(
    ("order", "item", "ret", "now_day", "kind", "open_end"),
    [
        (OrderStatus.DRAFT, ItemStatus.RESERVED, None, 19, cal.BarKind.DRAFT, False),
        (OrderStatus.CONFIRMED, ItemStatus.RESERVED, None, 19, cal.BarKind.CONFIRMED, False),
        # 출고 전 확정 예약은 반납 예정이 지나도 연체가 아니다(연체는 OUT 품목만)
        (OrderStatus.CONFIRMED, ItemStatus.RESERVED, None, 25, cal.BarKind.CONFIRMED, False),
        (OrderStatus.IN_RENTAL, ItemStatus.OUT, None, 19, cal.BarKind.IN_RENTAL, False),
        (OrderStatus.IN_RENTAL, ItemStatus.OUT, None, 25, cal.BarKind.OVERDUE, True),
        (OrderStatus.IN_RENTAL, ItemStatus.RETURNED, (10, 21, 9), 25, cal.BarKind.RETURNED, False),
        (OrderStatus.COMPLETED, ItemStatus.RETURNED, (10, 23, 9), 25, cal.BarKind.RETURNED, False),
    ],
)
def test_item_bar_kinds(order, item, ret, now_day, kind, open_end):
    start, due = kst(10, 20, 10), kst(10, 22, 10)
    actual = kst(*ret) if ret else None
    result = cal.item_bar(order, item, start, due, actual, kst(10, now_day, 12))
    assert result is not None
    assert result[0] == kind
    assert result[1] == start
    if open_end:
        assert result[2] is None
    elif ret:
        assert result[2] == actual  # 반납 완료는 실제 반납 시각까지
    else:
        assert result[2] == due


def test_item_bar_skips_canceled_and_requires_return_time():
    start, due, now = kst(10, 20, 10), kst(10, 22, 10), kst(10, 19)
    assert cal.item_bar(OrderStatus.CANCELED, ItemStatus.RESERVED, start, due, None, now) is None
    assert cal.item_bar(OrderStatus.IN_RENTAL, ItemStatus.CANCELED, start, due, None, now) is None
    with pytest.raises(ValueError):
        cal.item_bar(OrderStatus.COMPLETED, ItemStatus.RETURNED, start, due, None, now)


def test_assign_lanes_minimizes_rows_and_keeps_input_order():
    # 같은 날 오전 반납 + 오후 출고는 같은 칸을 쓰므로 다른 줄
    assert cal.assign_lanes([(0, 2), (2, 4), (5, 6)]) == [0, 1, 0]
    assert cal.assign_lanes([(3, 4), (0, 1)]) == [0, 0]
    assert cal.assign_lanes([(0, 6), (1, 1), (2, 2), (1, 3)]) == [0, 1, 1, 2]
    assert cal.assign_lanes([]) == []


def test_selection_period_future_days():
    now = kst(10, 19, 9)
    assert cal.selection_period(date(2026, 10, 20), date(2026, 10, 22), now) == (
        kst(10, 20, 10), kst(10, 22, 10)
    )
    # 하루만 고르면 1일 대여(다음 날 같은 시각 반납)
    assert cal.selection_period(date(2026, 10, 20), date(2026, 10, 20), now) == (
        kst(10, 20, 10), kst(10, 21, 10)
    )
    # 거꾸로 끌어도 같은 결과
    assert cal.selection_period(date(2026, 10, 22), date(2026, 10, 20), now) == (
        kst(10, 20, 10), kst(10, 22, 10)
    )


def test_selection_period_today_after_default_hour_uses_next_hour():
    now = kst(10, 19, 14, 25)
    assert cal.selection_period(date(2026, 10, 19), date(2026, 10, 21), now) == (
        kst(10, 19, 15), kst(10, 21, 15)
    )
    assert cal.selection_period(date(2026, 10, 19), date(2026, 10, 19), kst(10, 19, 9)) == (
        kst(10, 19, 10), kst(10, 20, 10)
    )


def test_selection_period_rejects_past_days_and_late_night_today():
    with pytest.raises(ValueError):
        cal.selection_period(date(2026, 10, 18), date(2026, 10, 20), kst(10, 19, 9))
    with pytest.raises(ValueError):  # 23시 이후 오늘 칸: 다음 정시가 내일
        cal.selection_period(date(2026, 10, 19), date(2026, 10, 19), kst(10, 19, 23, 10))
