"""M4.6 R3: 요금 규칙(대여일수 계산 방식, 주말·주·월 단가, 시간 단위 연체료) 순수 함수 경계값."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.pricing import (
    PricedItem,
    is_weekend_rental,
    late_fee,
    late_fee_basis,
    period_fee,
    quote_extension,
    quote_order,
    rate_examples,
    rental_days,
)
from rentdesk.domain.rates import (
    DEFAULT_RATE_RULE,
    DayCountMethod,
    LateFeeMethod,
    RateRule,
    rate_rule_from_json,
    rate_rule_summary,
    rate_rule_to_json,
    validate_rate_rule,
)
from tests.support import kst

D = 10_000
# 2026-10-19(월) ~ 10-25(일), 10-23 = 금, 10-26 = 월
START = kst(10, 20, 10)  # 화 10:00
CAL = RateRule(day_count=DayCountMethod.CALENDAR)
WEEKLY = RateRule(weekly_enabled=True)
MONTHLY = RateRule(monthly_enabled=True)
BOTH = RateRule(weekly_enabled=True, monthly_enabled=True)
WEEKEND = RateRule(weekend_enabled=True)


# --------------------------------------------------------------------------- 대여일수


@pytest.mark.parametrize(
    ("duration", "grace", "days"),
    [
        (timedelta(hours=24), 0, 1),
        (timedelta(hours=24, minutes=1), 0, 2),
        (timedelta(hours=24, minutes=30), 30, 1),  # 여유 시간 안
        (timedelta(hours=24, minutes=31), 30, 2),  # 여유 시간 밖
        (timedelta(minutes=10), 60, 1),  # 최소 1일
        (timedelta(hours=72, minutes=59), 60, 3),
    ],
)
def test_hours_24_with_return_grace(duration, grace, days):
    assert rental_days(START, START + duration, RateRule(return_grace_minutes=grace)) == days


@pytest.mark.parametrize(
    ("start", "due", "days"),
    [
        (kst(10, 20, 9), kst(10, 20, 18), 1),  # 당일 대여 = 1일
        (kst(10, 20, 14), kst(10, 21, 12), 1),  # 1박2일 = 1일
        (kst(10, 20, 10), kst(10, 21, 18), 1),  # 32시간이어도 날짜 기준 1일
        (kst(10, 20, 23, 30), kst(10, 21, 0, 30), 1),  # 서울 자정 걸침
        (kst(10, 20, 10), kst(10, 23, 10), 3),
    ],
)
def test_calendar_day_count(start, due, days):
    assert rental_days(start, due, CAL) == days


def test_calendar_uses_seoul_date_not_utc():
    # 서울 08:00 = UTC 전날 23:00. UTC 날짜로 세면 2일이 되지만 서울 기준 1일
    assert rental_days(kst(10, 20, 8), kst(10, 21, 10), CAL) == 1
    assert rental_days(kst(10, 20, 8), kst(10, 21, 10)) == 2  # 24시간 방식은 26시간 → 2일


def test_default_rule_matches_previous_behaviour():
    for hours in (1, 24, 25, 48, 49, 24 * 40 + 1):
        due = START + timedelta(hours=hours)
        fee = period_fee(D, START, due)
        assert fee.amount_won == D * rental_days(START, due)
        assert not fee.uses_long_rate


# --------------------------------------------------------------------------- 주·월 단가


@pytest.mark.parametrize(
    ("days", "amount", "weeks", "rest"),
    [
        (5, 50_000, 0, 5),  # 5일 = 일 요금 5일(주 요금 50,000과 같으면 일 요금)
        (6, 50_000, 1, 0),  # 6일 일 요금 60,000 > 1주 50,000 → 1주
        (7, 50_000, 1, 0),
        (8, 60_000, 1, 1),
        (10, 80_000, 1, 3),
        (13, 100_000, 2, 0),
        (14, 100_000, 2, 0),
    ],
)
def test_weekly_rate(days, amount, weeks, rest):
    fee = period_fee(D, START, START + timedelta(days=days), WEEKLY)
    assert (fee.amount_won, fee.weeks, fee.rest_days) == (amount, weeks, rest)


@pytest.mark.parametrize(
    ("days", "rule", "amount", "months", "weeks", "rest"),
    [
        (20, MONTHLY, 200_000, 0, 0, 20),
        (21, MONTHLY, 200_000, 1, 0, 0),  # 21일 > 20일치 → 1개월
        (29, MONTHLY, 200_000, 1, 0, 0),
        (30, MONTHLY, 200_000, 1, 0, 0),
        (31, MONTHLY, 210_000, 1, 0, 1),
        (37, BOTH, 250_000, 1, 1, 0),  # 월 + 주(7일)
        (39, BOTH, 270_000, 1, 1, 2),  # 월 + 주 + 일
        (29, BOTH, 200_000, 1, 0, 0),  # 4주+1일 = 210,000 > 월 200,000
        (35, BOTH, 250_000, 0, 5, 0),  # 같은 금액이면 주 단가(앞 방식) 근거
    ],
)
def test_monthly_rate(days, rule, amount, months, weeks, rest):
    fee = period_fee(D, START, START + timedelta(days=days), rule)
    assert fee.amount_won == amount
    assert (fee.months, fee.weeks, fee.rest_days) == (months, weeks, rest)


def test_long_rate_basis_text():
    fee = period_fee(D, START, START + timedelta(days=10), WEEKLY)
    assert fee.basis == "주 단가 적용: 1주 + 3일"
    fee = period_fee(D, START, START + timedelta(days=39), BOTH)
    assert fee.basis == "월 단가 적용: 1개월 + 1주 + 2일"
    assert period_fee(D, START, START + timedelta(days=3), WEEKLY).basis == "일 대여료 10,000원 × 3일"


def test_multiplier_rounding_is_half_up_integer_won():
    rule = RateRule(weekly_enabled=True, weekly_multiplier=Decimal("4.5"))
    fee = period_fee(10_001, START, START + timedelta(days=7), rule)
    assert fee.amount_won == 45_005  # 45,004.5 → 45,005


def test_period_fee_never_exceeds_daily_total():
    rules = [WEEKLY, MONTHLY, BOTH, WEEKEND, RateRule(weekly_enabled=True, weekly_multiplier=Decimal("7"))]
    for rule in rules:
        for days in range(1, 70):
            due = START + timedelta(days=days)
            assert period_fee(D, START, due, rule).amount_won <= D * days


def test_zero_daily_rate():
    assert period_fee(0, START, START + timedelta(days=40), BOTH).amount_won == 0


# --------------------------------------------------------------------------- 주말


@pytest.mark.parametrize(
    ("start", "due", "weekend"),
    [
        (kst(10, 23, 12), kst(10, 26, 12), True),  # 금 12:00 ~ 월 12:00 경계 포함
        (kst(10, 23, 18), kst(10, 25, 18), True),
        (kst(10, 24, 9), kst(10, 25, 20), True),  # 토 시작도 주말 범위 안
        (kst(10, 23, 11, 59), kst(10, 25, 18), False),  # 금 11:59 시작
        (kst(10, 23, 18), kst(10, 26, 12, 1), False),  # 월 12:01 반납
        (kst(10, 22, 18), kst(10, 24, 18), False),  # 목 시작
        (kst(10, 26, 10), kst(10, 26, 11), True),  # 월 10:00 시작은 지난 주말 범위의 끝 안
        (kst(10, 26, 12), kst(10, 26, 13), False),
    ],
)
def test_weekend_range_boundaries(start, due, weekend):
    assert is_weekend_rental(start, due, WEEKEND) is weekend


def test_weekend_fee_applies_only_when_cheaper():
    fee = period_fee(D, kst(10, 23, 18), kst(10, 26, 10), WEEKEND)  # 2일 15시간 → 3일
    assert (fee.amount_won, fee.weekend, fee.basis) == (20_000, True, "주말 요금 적용(3일 기간)")
    short = period_fee(D, kst(10, 24, 10), kst(10, 25, 9), WEEKEND)  # 1일 < 주말 2일치
    assert (short.amount_won, short.weekend) == (10_000, False)


def test_weekend_custom_range_sat_to_sun():
    rule = RateRule(weekend_enabled=True, weekend_start_weekday=5, weekend_start_minute=0,
                    weekend_end_weekday=6, weekend_end_minute=23 * 60 + 59)
    assert is_weekend_rental(kst(10, 24, 9), kst(10, 25, 20), rule)
    assert not is_weekend_rental(kst(10, 23, 18), kst(10, 25, 20), rule)


def test_weekend_disabled_never_applies():
    assert not is_weekend_rental(kst(10, 23, 12), kst(10, 26, 12), DEFAULT_RATE_RULE)


# --------------------------------------------------------------------------- 견적·연장


def test_quote_order_mixes_item_rules():
    items = [PricedItem(D, 50_000, WEEKLY), PricedItem(D, 0, CAL), PricedItem(D, 0)]
    quote = quote_order(items, kst(10, 20, 10), kst(10, 27, 18), discount_won=1_000)
    # 7일 8시간: 24시간 8일 주 단가 1주 + 1일 → 60,000 / 날짜 기준 7일 → 70,000 / 24시간 8일 → 80,000
    assert [f.amount_won for f in quote.item_fees] == [60_000, 70_000, 80_000]
    assert quote.base_rental_fee_won == 210_000
    assert quote.expected_total_rental_fee_won == 209_000
    assert quote.rental_days == 8 and not quote.uniform_days and quote.uses_long_rate


def test_extension_crossing_into_weekly_rate_is_free_without_refund():
    old_due = START + timedelta(days=5)
    q = quote_extension([D], START, old_due, START + timedelta(days=6), rules=[WEEKLY])
    assert (q.added_days, q.base_won, q.waived_by_long_rate) == (1, 0, True)
    q = quote_extension([D], START, old_due, START + timedelta(days=8), rules=[WEEKLY])
    assert (q.added_days, q.base_won, q.waived_by_long_rate) == (3, 10_000, False)


def test_extension_total_drop_is_zero_not_negative():
    # 6일(1주 단가 50,000) → 그대로; 5일 60,000 > 1주 — 늘려도 음수가 되지 않음
    rule = RateRule(weekly_enabled=True, weekly_multiplier=Decimal("4"))
    q = quote_extension([D], START, START + timedelta(days=4), START + timedelta(days=5), rules=[rule])
    assert q.base_won == 0 and q.amount_won == 0


def test_extension_default_rule_unchanged():
    q = quote_extension([D, 20_000], START, START + timedelta(days=2), START + timedelta(days=3))
    assert (q.added_days, q.base_won) == (1, 30_000)


def test_extension_rules_length_mismatch():
    with pytest.raises(ValueError):
        quote_extension([D], START, START + timedelta(days=1), START + timedelta(days=2), rules=[])


# --------------------------------------------------------------------------- 연체료


HOURLY = RateRule(late_method=LateFeeMethod.HOURLY, late_hourly_divisor=10)
DUE = kst(10, 21, 18)


@pytest.mark.parametrize(
    ("late", "days", "hours", "fee"),
    [
        (timedelta(minutes=60), 0, 0, 0),  # 유예 60분 안
        (timedelta(minutes=60, seconds=1), 0, 1, 1_000),  # 1초 초과 → 1시간
        (timedelta(hours=4), 0, 3, 3_000),
        (timedelta(hours=11), 0, 10, 10_000),  # 10시간 = 하루치
        (timedelta(hours=16), 0, 15, 10_000),  # 하루치 상한
        (timedelta(hours=25), 1, 0, 10_000),  # 정확히 24시간 초과
        (timedelta(hours=27), 1, 2, 12_000),
    ],
)
def test_hourly_late_fee(late, days, hours, fee):
    result = late_fee(D, DUE, DUE + late, 60, Decimal("1"), HOURLY)
    assert (result.late_days, result.late_hours, result.late_fee_won) == (days, hours, fee)


def test_hourly_late_fee_multiplier_and_rounding():
    result = late_fee(10_001, DUE, DUE + timedelta(hours=2), 0, Decimal("1.5"), HOURLY)
    # (10,001 × 2 / 10) × 1.5 = 3,000.3 → 3,000
    assert result.late_fee_won == 3_000
    assert "연체 2시간" in late_fee_basis(10_001, Decimal("1.5"), HOURLY, result)


def test_daily_late_fee_unchanged_by_rule_default():
    result = late_fee(D, DUE, DUE + timedelta(hours=26), 60, Decimal("1"))
    assert (result.late_days, result.late_fee_won, result.duration_label) == (2, 20_000, "2일")


# --------------------------------------------------------------------------- 검증·JSON


def test_rule_json_round_trip_and_default_literal():
    rule = RateRule(day_count=DayCountMethod.CALENDAR, weekend_enabled=True, weekly_enabled=True,
                    weekly_multiplier=Decimal("4.5"), late_method=LateFeeMethod.HOURLY)
    assert rate_rule_from_json(rate_rule_to_json(rule)) == rule
    assert rate_rule_from_json(None) == DEFAULT_RATE_RULE
    assert rate_rule_from_json('{"weekly_enabled": true}') == WEEKLY  # 빠진 키는 기본값


def test_migration_default_json_matches_domain_default():
    from importlib import resources

    sql = resources.files("rentdesk.persistence.migrations").joinpath("006_rate_rules.sql").read_text("utf-8")
    assert sql.count(rate_rule_to_json(DEFAULT_RATE_RULE)) == 2


@pytest.mark.parametrize(
    "rule",
    [
        RateRule(weekly_multiplier=Decimal("7.01")),
        RateRule(weekly_multiplier=Decimal("0")),
        RateRule(monthly_multiplier=Decimal("31")),
        RateRule(weekend_multiplier=Decimal("-1")),
        RateRule(weekend_multiplier=Decimal("1.00001")),
        RateRule(weekend_start_weekday=7),
        RateRule(weekend_start_minute=1440),
        RateRule(weekend_end_weekday=4, weekend_end_minute=720),  # 시작과 같음
        RateRule(late_hourly_divisor=0),
        RateRule(late_hourly_divisor=25),
        RateRule(return_grace_minutes=1440),
        RateRule(return_grace_minutes=True),  # type: ignore[arg-type]
        RateRule(weekly_enabled=1),  # type: ignore[arg-type]
        RateRule(day_count="WEEKLY"),  # type: ignore[arg-type]
    ],
)
def test_invalid_rules_rejected(rule):
    with pytest.raises(ValidationError):
        validate_rate_rule(rule)


@pytest.mark.parametrize("text", ["not json", "[]", '{"weekly_multiplier": "abc"}', '{"weekend_multiplier": 1.5}'])
def test_invalid_rule_json_rejected(text):
    with pytest.raises(ValidationError):
        rate_rule_from_json(text)


def test_summary_and_examples():
    rule = RateRule(day_count=DayCountMethod.CALENDAR, weekend_enabled=True, weekly_enabled=True,
                    monthly_enabled=True, late_method=LateFeeMethod.HOURLY)
    summary = rate_rule_summary(rule)
    for part in ("날짜 기준", "금 12:00 이후 시작 ~ 월 12:00 이전 반납", "7일 = 일 요금×5", "30일 = 일 요금×20",
                 "연체 시간 단위"):
        assert part in summary
    examples = {e.label.split(" ")[0]: e.fee for e in rate_examples(D, rule)}
    assert examples["1일"].amount_won == 10_000
    assert examples["1박2일"].amount_won == 10_000
    assert examples["주말"].weekend and examples["주말"].amount_won == 20_000
    assert examples["10일"].basis == "주 단가 적용: 1주 + 3일"
    assert examples["35일"].amount_won == 250_000
    plain = {e.label.split(" ")[0]: e.fee.amount_won for e in rate_examples(D)}
    assert plain == {"1일": 10_000, "1박2일": 20_000, "주말": 30_000, "10일": 100_000, "35일": 350_000}
