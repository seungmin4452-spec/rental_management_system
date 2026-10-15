from datetime import UTC, date, datetime, timedelta

import pytest

from rentdesk.domain.availability import AssetState, block_active_at, current_asset_state
from rentdesk.domain.display import (
    format_duration,
    format_dt,
    format_won,
    seoul_day_range,
    seoul_today,
)
from tests.support import kst


@pytest.mark.parametrize(("amount", "text"), [
    (0, "0원"), (1_234_000, "1,234,000원"), (-10_000, "-10,000원"),
])
def test_format_won(amount, text):
    assert format_won(amount) == text


@pytest.mark.parametrize(("seconds", "text"), [
    (30, "1분 미만"), (60, "1분"), (3_600, "1시간"), (3_660, "1시간 1분"),
    (86_400 + 4 * 3_600 + 59, "1일 4시간"), (2 * 86_400 + 120, "2일"),
])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_format_dt_uses_seoul_and_empty():
    assert format_dt(datetime(2026, 10, 15, 1, 5, tzinfo=UTC)) == "2026-10-15 10:05"
    assert format_dt(None, "-") == "-"


def test_seoul_day_range_is_utc_half_open():
    start, end = seoul_day_range(date(2026, 10, 15))
    assert start == datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
    assert end - start == timedelta(days=1)
    assert seoul_today(datetime(2026, 10, 14, 15, 0, tzinfo=UTC)) == date(2026, 10, 15)
    assert seoul_today(datetime(2026, 10, 14, 14, 59, tzinfo=UTC)) == date(2026, 10, 14)


class TestCurrentAssetState:
    now = kst(10, 20, 12)

    def test_available(self):
        assert current_asset_state(
            retired=False, out_due_at=None, in_maintenance=False, now=self.now
        ) == AssetState.AVAILABLE

    def test_rented_until_due_then_overdue(self):
        assert current_asset_state(
            retired=False, out_due_at=self.now, in_maintenance=False, now=self.now
        ) == AssetState.RENTED
        assert current_asset_state(
            retired=False, out_due_at=self.now - timedelta(seconds=1), in_maintenance=False,
            now=self.now,
        ) == AssetState.OVERDUE

    def test_maintenance_and_retired(self):
        assert current_asset_state(
            retired=False, out_due_at=None, in_maintenance=True, now=self.now
        ) == AssetState.MAINTENANCE
        assert current_asset_state(
            retired=True, out_due_at=None, in_maintenance=False, now=self.now
        ) == AssetState.RETIRED


def test_block_active_at_half_open():
    s, e = kst(10, 20, 10), kst(10, 20, 12)
    assert block_active_at(s, e, s)
    assert not block_active_at(s, e, e)
    assert not block_active_at(s, e, s - timedelta(seconds=1))
    assert block_active_at(s, None, kst(12, 31))
