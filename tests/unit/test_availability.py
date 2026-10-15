"""domain.availability 순수 함수 (PRD 7.1)."""

from datetime import timedelta

import pytest

from rentdesk.domain.availability import (
    ItemOccupancyInput,
    Occupancy,
    OccupancyKind,
    find_conflicts,
    is_overdue,
    item_occupied_interval,
    overlaps,
    reservation_interval,
    validate_reservation_window,
)
from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import ItemStatus, OrderStatus
from tests.support import kst

H = timedelta(hours=1)


class TestOverlaps:
    def test_half_open_end_is_exclusive(self):
        assert not overlaps(kst(10, 20, 10), kst(10, 20, 12), kst(10, 20, 12), kst(10, 20, 14))
        assert not overlaps(kst(10, 20, 12), kst(10, 20, 14), kst(10, 20, 10), kst(10, 20, 12))

    def test_partial_overlap(self):
        assert overlaps(kst(10, 20, 10), kst(10, 20, 12), kst(10, 20, 11), kst(10, 20, 13))

    def test_containment(self):
        assert overlaps(kst(10, 20, 10), kst(10, 20, 18), kst(10, 20, 11), kst(10, 20, 12))

    def test_one_second_overlap(self):
        assert overlaps(
            kst(10, 20, 10), kst(10, 20, 12), kst(10, 20, 11, 59, 59), kst(10, 20, 14)
        )

    def test_open_ended_interval_is_infinite(self):
        assert overlaps(kst(10, 20, 10), None, kst(12, 31, 0), kst(12, 31, 1))
        assert not overlaps(kst(10, 20, 10), None, kst(10, 19, 0), kst(10, 20, 10))
        assert overlaps(kst(10, 20, 10), None, kst(10, 25, 0), None)


def _item(order_status, item_status, *, returned=None, buffer=60):
    return ItemOccupancyInput(
        order_status=order_status,
        item_status=item_status,
        start_at=kst(10, 20, 10),
        due_at=kst(10, 20, 12),
        actual_return_at=returned,
        buffer_minutes=buffer,
    )


class TestItemOccupiedInterval:
    now = kst(10, 19, 9)

    @pytest.mark.parametrize("order_status", [OrderStatus.DRAFT, OrderStatus.CANCELED])
    def test_draft_and_canceled_orders_do_not_occupy(self, order_status):
        assert item_occupied_interval(_item(order_status, ItemStatus.RESERVED), self.now) is None

    def test_canceled_item_does_not_occupy(self):
        item = _item(OrderStatus.IN_RENTAL, ItemStatus.CANCELED)
        assert item_occupied_interval(item, self.now) is None

    def test_confirmed_reserved_occupies_until_due_plus_buffer(self):
        result = item_occupied_interval(_item(OrderStatus.CONFIRMED, ItemStatus.RESERVED), self.now)
        assert result == (OccupancyKind.RESERVATION, kst(10, 20, 10), kst(10, 20, 13))

    def test_out_before_due_occupies_until_due_plus_buffer(self):
        item = _item(OrderStatus.IN_RENTAL, ItemStatus.OUT)
        result = item_occupied_interval(item, kst(10, 20, 11))
        assert result == (OccupancyKind.RESERVATION, kst(10, 20, 10), kst(10, 20, 13))

    def test_out_exactly_at_due_is_not_overdue(self):
        item = _item(OrderStatus.IN_RENTAL, ItemStatus.OUT)
        kind, _, _ = item_occupied_interval(item, kst(10, 20, 12))
        assert kind == OccupancyKind.RESERVATION

    def test_out_after_due_blocks_indefinitely(self):
        item = _item(OrderStatus.IN_RENTAL, ItemStatus.OUT)
        result = item_occupied_interval(item, kst(10, 20, 12, 0, 1))
        assert result == (OccupancyKind.OVERDUE, kst(10, 20, 10), None)

    @pytest.mark.parametrize("order_status", [OrderStatus.IN_RENTAL, OrderStatus.COMPLETED])
    def test_returned_item_occupies_until_actual_return_plus_buffer(self, order_status):
        item = _item(order_status, ItemStatus.RETURNED, returned=kst(10, 20, 11))
        result = item_occupied_interval(item, kst(10, 25, 0))
        assert result == (OccupancyKind.RETURNED, kst(10, 20, 10), kst(10, 20, 12))

    def test_returned_without_time_is_programming_error(self):
        with pytest.raises(ValueError):
            item_occupied_interval(_item(OrderStatus.COMPLETED, ItemStatus.RETURNED), self.now)


def test_is_overdue_only_for_out_items():
    due = kst(10, 20, 12)
    after = due + timedelta(seconds=1)
    assert is_overdue(ItemStatus.OUT, due, after)
    assert not is_overdue(ItemStatus.OUT, due, due)
    assert not is_overdue(ItemStatus.RESERVED, due, after)
    assert not is_overdue(ItemStatus.RETURNED, due, after)


def test_reservation_interval_adds_buffer():
    assert reservation_interval(kst(10, 20, 10), kst(10, 20, 12), 60) == (
        kst(10, 20, 10), kst(10, 20, 13)
    )


class TestFindConflicts:
    occs = [
        Occupancy(asset_id=1, kind=OccupancyKind.RESERVATION, start_at=kst(10, 20, 10),
                  end_at=kst(10, 20, 13), order_id=7, order_no="R-1"),
        Occupancy(asset_id=1, kind=OccupancyKind.MAINTENANCE, start_at=kst(10, 22, 0),
                  end_at=None, block_id=3),
    ]

    def test_t04_buffer_extends_existing_reservation(self):
        # T-04 (순수 함수): 기존 10~12 + 버퍼 60 → 13:00까지 점유, 신규 12:30 시작은 충돌
        found = find_conflicts(kst(10, 20, 12, 30), kst(10, 20, 15), self.occs)
        assert [c.order_no for c in found] == ["R-1"]

    def test_excludes_own_order(self):
        assert find_conflicts(
            kst(10, 20, 10), kst(10, 20, 15), self.occs, exclude_order_id=7
        ) == []

    def test_open_maintenance_block_blocks_future(self):
        found = find_conflicts(kst(11, 1, 0), kst(11, 2, 0), self.occs)
        assert [c.kind for c in found] == [OccupancyKind.MAINTENANCE]


class TestValidateWindow:
    now = kst(10, 19, 9, 0, 30)

    def test_start_must_be_before_due(self):
        with pytest.raises(ValidationError) as exc:
            validate_reservation_window(kst(10, 20, 12), kst(10, 20, 12), self.now,
                                        require_future_start=False)
        assert exc.value.field == "due_at"

    def test_past_start_rejected_for_confirmation(self):
        with pytest.raises(ValidationError) as exc:
            validate_reservation_window(kst(10, 19, 8, 59), kst(10, 20, 12), self.now,
                                        require_future_start=True)
        assert exc.value.field == "start_at"

    def test_same_minute_as_now_is_allowed(self):
        validate_reservation_window(kst(10, 19, 9, 0), kst(10, 20, 12), self.now,
                                    require_future_start=True)

    def test_past_start_allowed_for_draft(self):
        validate_reservation_window(kst(10, 1, 9), kst(10, 2, 12), self.now,
                                    require_future_start=False)

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError):
            validate_reservation_window(kst(10, 20, 10).replace(tzinfo=None), kst(10, 20, 12),
                                        self.now, require_future_start=False)
