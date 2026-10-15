"""상태 전이 표와 시간 유틸 (PRD 6, 9.2)."""

from datetime import UTC, datetime

import pytest

from rentdesk.domain.models import (
    ItemStatus,
    OrderStatus,
    can_transition_item,
    can_transition_order,
)
from rentdesk.domain.timeutil import format_seoul, parse_utc_str, to_utc_str
from tests.support import kst


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (OrderStatus.DRAFT, OrderStatus.CONFIRMED, True),
        (OrderStatus.DRAFT, OrderStatus.CANCELED, True),
        (OrderStatus.DRAFT, OrderStatus.IN_RENTAL, False),
        (OrderStatus.CONFIRMED, OrderStatus.IN_RENTAL, True),
        (OrderStatus.CONFIRMED, OrderStatus.CANCELED, True),
        (OrderStatus.IN_RENTAL, OrderStatus.COMPLETED, True),
        (OrderStatus.IN_RENTAL, OrderStatus.CANCELED, False),
        (OrderStatus.COMPLETED, OrderStatus.IN_RENTAL, False),
        (OrderStatus.CANCELED, OrderStatus.CONFIRMED, False),
    ],
)
def test_order_transitions(current, target, allowed):
    assert can_transition_order(current, target) is allowed


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (ItemStatus.RESERVED, ItemStatus.OUT, True),
        (ItemStatus.RESERVED, ItemStatus.CANCELED, True),
        (ItemStatus.OUT, ItemStatus.RETURNED, True),
        (ItemStatus.OUT, ItemStatus.CANCELED, False),
        (ItemStatus.RETURNED, ItemStatus.OUT, False),
    ],
)
def test_item_transitions(current, target, allowed):
    assert can_transition_item(current, target) is allowed


def test_utc_string_roundtrip_and_format():
    dt = kst(10, 20, 10, 30)
    text = to_utc_str(dt)
    assert text == "2026-10-20T01:30:00Z"
    assert parse_utc_str(text) == dt
    assert format_seoul(parse_utc_str(text)) == "2026-10-20 10:30"


def test_utc_string_sorts_chronologically():
    times = [kst(10, 20, 9), kst(10, 19, 23, 59), kst(10, 20, 0, 0, 1)]
    assert sorted(to_utc_str(t) for t in times) == [to_utc_str(t) for t in sorted(times)]


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        to_utc_str(datetime(2026, 10, 20, 10, 0))


def test_microseconds_truncated():
    assert to_utc_str(datetime(2026, 1, 1, 0, 0, 0, 999_999, tzinfo=UTC)) == "2026-01-01T00:00:00Z"
