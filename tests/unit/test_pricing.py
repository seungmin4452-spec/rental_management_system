"""domain.pricing 순수 함수 (PRD 7.2, 7.3)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.pricing import (
    PricedItem,
    late_fee,
    parse_multiplier,
    quote_order,
    rental_days,
    round_half_up,
    validate_won,
)
from tests.support import kst

START = kst(10, 20, 10)


@pytest.mark.parametrize(
    ("duration", "days"),
    [
        (timedelta(minutes=1), 1),
        (timedelta(hours=24), 1),
        (timedelta(hours=24, seconds=1), 2),
        (timedelta(hours=25), 2),
        (timedelta(hours=48), 2),
        (timedelta(days=7, hours=1), 8),
    ],
)
def test_rental_days_ceil_24h_min_1(duration, days):
    assert rental_days(START, START + duration) == days


def test_t23_25_hours_at_30000_is_60000():
    quote = quote_order([PricedItem(30_000, 100_000)], START, START + timedelta(hours=25))
    assert quote.rental_days == 2
    assert quote.base_rental_fee_won == 60_000
    assert quote.expected_total_rental_fee_won == 60_000
    assert quote.expected_deposit_won == 100_000


def test_quote_multiple_items_and_discount():
    items = [PricedItem(30_000, 100_000), PricedItem(15_000, 50_000), PricedItem(0, 0)]
    quote = quote_order(items, START, START + timedelta(days=3), discount_won=5_000)
    assert quote.base_rental_fee_won == 135_000
    assert quote.expected_total_rental_fee_won == 130_000
    assert quote.expected_deposit_won == 150_000


def test_discount_equal_to_base_allowed_but_not_more():
    items = [PricedItem(10_000, 0)]
    assert quote_order(items, START, START + timedelta(hours=1), 10_000).expected_total_rental_fee_won == 0
    with pytest.raises(ValidationError) as exc:
        quote_order(items, START, START + timedelta(hours=1), 10_001)
    assert exc.value.field == "discount_won"


@pytest.mark.parametrize("bad", [-1, 1.5, "1000", True, None])
def test_validate_won_rejects_non_integer_or_negative(bad):
    with pytest.raises(ValidationError):
        validate_won("x", bad, label="금액")


def test_round_half_up():
    assert round_half_up(Decimal("0.5")) == 1
    assert round_half_up(Decimal("1.5")) == 2
    assert round_half_up(Decimal("2.5")) == 3  # 은행가 반올림이 아님
    assert round_half_up(Decimal("2.4999")) == 2


class TestLateFee:
    due = kst(10, 21, 18)

    def test_t10_exactly_grace_is_free(self):
        fee = late_fee(30_000, self.due, self.due + timedelta(minutes=60), 60, Decimal("1"))
        assert fee.late_fee_won == 0
        assert fee.late_days == 0

    def test_t11_one_second_over_grace_is_one_day(self):
        fee = late_fee(30_000, self.due, self.due + timedelta(minutes=60, seconds=1), 60, Decimal("1"))
        assert fee.late_days == 1
        assert fee.late_fee_won == 30_000

    def test_us05_1901_is_one_day(self):
        fee = late_fee(30_000, self.due, kst(10, 21, 19, 1), 60, Decimal("1"))
        assert fee.late_fee_won == 30_000

    def test_early_return_is_free(self):
        assert late_fee(30_000, self.due, self.due - timedelta(hours=3), 60, Decimal("1")).late_fee_won == 0

    def test_multiplier_rounds_half_up(self):
        # 10,001 × 1 × 1.5 = 15,001.5 → 15,002
        fee = late_fee(10_001, self.due, self.due + timedelta(hours=2), 60, Decimal("1.5"))
        assert fee.late_fee_won == 15_002

    def test_multi_day_late(self):
        fee = late_fee(30_000, self.due, self.due + timedelta(days=2, hours=1, seconds=1), 60, Decimal("1"))
        assert fee.late_days == 3
        assert fee.late_fee_won == 90_000


@pytest.mark.parametrize("bad", ["abc", "-1", "NaN", "Infinity"])
def test_parse_multiplier_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        parse_multiplier(bad)


def test_parse_multiplier_accepts_decimal_string():
    assert parse_multiplier("1.25") == Decimal("1.25")
