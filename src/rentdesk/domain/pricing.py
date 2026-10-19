"""대여료·연체료 계산 순수 함수 (PRD 7.2, 7.3, MULTI_INDUSTRY_PLAN R3).

금액은 항상 KRW 정수. 배수 등 소수는 `Decimal`로만 계산하고 float를 쓰지 않는다.
품목마다 요금 규칙(`RateRule`)이 다를 수 있으므로(카테고리별 설정) 대여료는 품목 단위로 계산한다.
규칙을 넘기지 않으면 기본 규칙(R3 이전 동작: 24시간 올림·일 요금만·하루 단위 연체)을 쓴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.rates import (
    DEFAULT_RATE_RULE,
    WEEKDAY_LABELS,
    DayCountMethod,
    LateFeeMethod,
    RateRule,
)
from rentdesk.domain.timeutil import SEOUL, to_seoul

SECONDS_PER_DAY = 86_400
SECONDS_PER_HOUR = 3_600
WEEK_DAYS = 7
MONTH_DAYS = 30


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rental_days(start_at: datetime, due_at: datetime, rule: RateRule = DEFAULT_RATE_RULE) -> int:
    """대여일수(최소 1일).

    - 24시간 방식: 24시간 단위 올림. 반납 여유 시간 이내 초과는 같은 날로 본다. 예: 25시간 → 2일.
    - 날짜 기준: 서울 날짜로 반납일 − 대여일. 당일 대여 = 1일, 1박2일 = 1일.
    """
    if rule.day_count == DayCountMethod.CALENDAR:
        return max(1, (to_seoul(due_at).date() - to_seoul(start_at).date()).days)
    seconds = int((due_at - start_at).total_seconds()) - rule.return_grace_minutes * 60
    return max(1, _ceil_div(max(0, seconds), SECONDS_PER_DAY))


def parse_multiplier(value: str | Decimal) -> Decimal:
    """연체 배수 문자열 검증. 0 이상, 유한 십진수."""
    try:
        dec = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("late_multiplier", "연체 1일당 일 대여료의 배수는 숫자여야 합니다.") from None
    if not dec.is_finite() or dec < 0:
        raise ValidationError("late_multiplier", "연체 1일당 일 대여료의 배수는 0 이상의 숫자여야 합니다.")
    return dec


def validate_won(field: str, value: object, *, label: str) -> int:
    """원 단위 0 이상 정수 검증. bool/float/문자열 거부."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, f"{label}은(는) 원 단위 정수여야 합니다.")
    if value < 0:
        raise ValidationError(field, f"{label}은(는) 0 이상이어야 합니다.")
    return value


# --------------------------------------------------------------------------- 기간 요금


@dataclass(frozen=True)
class UnitPrices:
    """품목 1개의 단위 요금(정수 원). 주·월·주말 요금 = 일 대여료 × 배수, 반올림."""

    daily_won: int
    weekly_won: int | None = None
    monthly_won: int | None = None
    weekend_won: int | None = None


def unit_prices(daily_rate_won: int, rule: RateRule = DEFAULT_RATE_RULE) -> UnitPrices:
    def times(multiplier: Decimal) -> int:
        return round_half_up(Decimal(daily_rate_won) * multiplier)

    return UnitPrices(
        daily_won=daily_rate_won,
        weekly_won=times(rule.weekly_multiplier) if rule.weekly_enabled else None,
        monthly_won=times(rule.monthly_multiplier) if rule.monthly_enabled else None,
        weekend_won=times(rule.weekend_multiplier) if rule.weekend_enabled else None,
    )


@dataclass(frozen=True)
class PeriodFee:
    """품목 1개의 기간 요금과 계산 근거.

    months·weeks·rest_days는 실제로 청구한 단위 수다. 남은 날의 일 요금 합이 한 단위 요금보다
    비싸면 그 단위 하나로 청구하므로(예: 6일 > 1주 요금 → 1주) days와 다를 수 있다.
    """

    amount_won: int
    days: int
    daily_won: int
    months: int = 0
    weeks: int = 0
    rest_days: int = 0
    weekend: bool = False

    @property
    def uses_long_rate(self) -> bool:
        return self.weekend or self.months > 0 or self.weeks > 0

    @property
    def basis(self) -> str:
        """"일 대여료 10,000원 × 3일" / "주 단가 적용: 1주 + 3일" / "주말 요금 적용"."""
        if self.weekend:
            return f"주말 요금 적용({self.days}일 기간)"
        if not (self.months or self.weeks):
            return f"일 대여료 {self.daily_won:,}원 × {self.days}일"
        parts = []
        if self.months:
            parts.append(f"{self.months}개월")
        if self.weeks:
            parts.append(f"{self.weeks}주")
        if self.rest_days:
            parts.append(f"{self.rest_days}일")
        head = "월 단가 적용" if self.months else "주 단가 적용"
        return f"{head}: {' + '.join(parts)}"


def _week_plan(days: int, daily: int, weekly: int) -> tuple[int, int, int]:
    """(금액, 주 수, 남은 일수). 남은 날의 일 요금 합이 주 요금보다 비싸면 주 하나로."""
    weeks, rest = divmod(days, WEEK_DAYS)
    if rest * daily > weekly:
        return (weeks + 1) * weekly, weeks + 1, 0
    return weeks * weekly + rest * daily, weeks, rest


def is_weekend_rental(start_at: datetime, due_at: datetime, rule: RateRule) -> bool:
    """예약 기간이 주말 범위 한 번 안에 완전히 들어가는지(서울 시각, 경계 포함)."""
    if not rule.weekend_enabled:
        return False
    start = to_seoul(start_at).replace(tzinfo=None)
    due = to_seoul(due_at).replace(tzinfo=None)
    monday = datetime.combine(start.date() - timedelta(days=start.weekday()), datetime.min.time())
    window_start = monday + timedelta(days=rule.weekend_start_weekday, minutes=rule.weekend_start_minute)
    if window_start > start:
        window_start -= timedelta(days=WEEK_DAYS)
    window_end = window_start + timedelta(minutes=rule.weekend_span_minutes)
    return due <= window_end


def period_fee(
    daily_rate_won: int, start_at: datetime, due_at: datetime, rule: RateRule = DEFAULT_RATE_RULE
) -> PeriodFee:
    """품목 1개의 기간 요금 = 사용 중인 방식(일·주·월·주말) 중 고객에게 가장 싼 값.

    같은 금액이면 일 요금 → 주 → 월 → 주말 순으로 앞의 것을 근거로 쓴다.
    """
    days = rental_days(start_at, due_at, rule)
    prices = unit_prices(daily_rate_won, rule)
    daily = prices.daily_won
    candidates = [PeriodFee(days * daily, days, daily, rest_days=days)]
    if prices.weekly_won is not None:
        amount, weeks, rest = _week_plan(days, daily, prices.weekly_won)
        candidates.append(PeriodFee(amount, days, daily, weeks=weeks, rest_days=rest))
    if prices.monthly_won is not None:
        months, remainder = divmod(days, MONTH_DAYS)
        if prices.weekly_won is not None:
            rest_amount, weeks, rest = _week_plan(remainder, daily, prices.weekly_won)
        else:
            rest_amount, weeks, rest = remainder * daily, 0, remainder
        if rest_amount > prices.monthly_won:
            months, rest_amount, weeks, rest = months + 1, 0, 0, 0
        candidates.append(PeriodFee(
            months * prices.monthly_won + rest_amount, days, daily, months=months, weeks=weeks, rest_days=rest,
        ))
    if prices.weekend_won is not None and is_weekend_rental(start_at, due_at, rule):
        candidates.append(PeriodFee(prices.weekend_won, days, daily, weekend=True))
    return min(candidates, key=lambda c: c.amount_won)


def rental_charge_won(
    daily_rate_won: int, start_at: datetime, due_at: datetime, rule: RateRule = DEFAULT_RATE_RULE
) -> int:
    """품목 1개의 약정 대여료(출고 시 `RENTAL` 청구액)."""
    return period_fee(daily_rate_won, start_at, due_at, rule).amount_won


# --------------------------------------------------------------------------- 예약 견적


@dataclass(frozen=True)
class PricedItem:
    daily_rate_won: int
    deposit_won: int
    rule: RateRule = DEFAULT_RATE_RULE


@dataclass(frozen=True)
class OrderQuote:
    rental_days: int  # 품목마다 대여일수가 다르면(카테고리별 계산 방식) 가장 긴 값
    base_rental_fee_won: int
    discount_won: int
    expected_total_rental_fee_won: int
    expected_deposit_won: int
    item_fees: tuple[PeriodFee, ...] = field(default=())

    @property
    def uniform_days(self) -> bool:
        return len({f.days for f in self.item_fees}) <= 1

    @property
    def uses_long_rate(self) -> bool:
        return any(f.uses_long_rate for f in self.item_fees)

    @property
    def days_label(self) -> str:
        """"3일" 또는 장비마다 계산 방식이 달라 일수가 다르면 "장비별 최대 3일"."""
        return f"{self.rental_days}일" if self.uniform_days else f"장비별 최대 {self.rental_days}일"


def quote_order(
    items: Sequence[PricedItem],
    start_at: datetime,
    due_at: datetime,
    discount_won: int = 0,
) -> OrderQuote:
    fees = tuple(period_fee(i.daily_rate_won, start_at, due_at, i.rule) for i in items)
    base = sum(f.amount_won for f in fees)
    deposit = sum(item.deposit_won for item in items)
    validate_won("discount_won", discount_won, label="할인 금액")
    if discount_won > base:
        raise ValidationError("discount_won", "할인 금액은 기본 대여료를 초과할 수 없습니다.")
    days = max((f.days for f in fees), default=rental_days(start_at, due_at))
    return OrderQuote(
        rental_days=days,
        base_rental_fee_won=base,
        discount_won=discount_won,
        expected_total_rental_fee_won=base - discount_won,
        expected_deposit_won=deposit,
        item_fees=fees,
    )


# --------------------------------------------------------------------------- 연체료


@dataclass(frozen=True)
class LateFee:
    late_seconds: int
    late_days: int  # 하루 단위: 올림한 연체 일수. 시간 단위: 꽉 찬 24시간 수
    late_fee_won: int
    late_hours: int = 0  # 시간 단위 방식에서 하루 미만 나머지를 올림한 시간 수
    method: LateFeeMethod = LateFeeMethod.DAILY

    @property
    def is_late(self) -> bool:
        return self.late_seconds > 0

    @property
    def duration_label(self) -> str:
        if self.method == LateFeeMethod.DAILY:
            return f"{self.late_days}일"
        if self.late_days and self.late_hours:
            return f"{self.late_days}일 {self.late_hours}시간"
        if self.late_days:
            return f"{self.late_days}일"
        return f"{self.late_hours}시간"


def late_fee(
    daily_rate_won: int,
    due_at: datetime,
    actual_return_at: datetime,
    grace_minutes: int,
    late_multiplier: Decimal,
    rule: RateRule = DEFAULT_RATE_RULE,
) -> LateFee:
    """유예시간 초과분의 연체료.

    - 하루 단위: 24시간 단위 올림 × 일 대여료 × 배수.
    - 시간 단위: 꽉 찬 24시간마다 일 대여료, 나머지는 1시간 단위 올림 × (일 대여료 ÷ N)
      (하루치 일 대여료가 상한), 합계 × 배수.
    """
    over = int((actual_return_at - due_at).total_seconds()) - grace_minutes * 60
    late_seconds = max(0, over)
    if rule.late_method == LateFeeMethod.HOURLY:
        full_days, rest = divmod(late_seconds, SECONDS_PER_DAY)
        hours = _ceil_div(rest, SECONDS_PER_HOUR)
        partial = min(Decimal(daily_rate_won) * hours / rule.late_hourly_divisor, Decimal(daily_rate_won))
        fee = round_half_up((Decimal(daily_rate_won) * full_days + partial) * late_multiplier)
        return LateFee(late_seconds, full_days, fee, late_hours=hours, method=LateFeeMethod.HOURLY)
    late_days = _ceil_div(late_seconds, SECONDS_PER_DAY) if late_seconds > 0 else 0
    fee = round_half_up(Decimal(daily_rate_won) * late_days * late_multiplier)
    return LateFee(late_seconds=late_seconds, late_days=late_days, late_fee_won=fee)


def late_fee_basis(daily_rate_won: int, multiplier: Decimal, rule: RateRule, fee: LateFee) -> str:
    """"연체 2일 × 일 대여료 30,000원 × 1배 = 60,000원" 같은 계산 근거(연체가 있을 때)."""
    mult = f"{multiplier.normalize():f}"
    if fee.method == LateFeeMethod.HOURLY:
        return (
            f"연체 {fee.duration_label}(시간당 일 대여료 {daily_rate_won:,}원 ÷ {rule.late_hourly_divisor},"
            f" 하루 상한 일 대여료) × {mult}배 = {fee.late_fee_won:,}원"
        )
    return f"연체 {fee.late_days}일 × 일 대여료 {daily_rate_won:,}원 × {mult}배 = {fee.late_fee_won:,}원"


# --------------------------------------------------------------------------- 연장


@dataclass(frozen=True)
class ExtensionQuote:
    """대여 연장 요금 (USABILITY_PLAN U6, MULTI_INDUSTRY_PLAN R3)."""

    added_days: int
    base_won: int
    discount_won: int
    waived_by_long_rate: bool = False  # 주·월·주말 단가로 총액이 늘지 않아 추가 요금 0원

    @property
    def amount_won(self) -> int:
        return self.base_won - self.discount_won


def quote_extension(
    daily_rates_won: Sequence[int],
    start_at: datetime,
    old_due_at: datetime,
    new_due_at: datetime,
    discount_won: object = 0,
    rules: Sequence[RateRule] | None = None,
) -> ExtensionQuote:
    """아직 반납하지 않은 장비의 기간 요금(새 반납 예정) − 기간 요금(기존 반납 예정) 합계.

    대여일수·기간 요금은 출고 때와 같은 품목 규칙으로 예약 시작부터 다시 계산한다.
    긴 단가로 넘어가 총액이 줄면 추가 요금 0원이며 환급하지 않는다(MULTI_INDUSTRY_PLAN 7장 결정 3).
    연장분 할인은 연장 기본 요금을 넘을 수 없다.
    """
    if not new_due_at > old_due_at:
        raise ValidationError("new_due_at", "새 반납 예정 일시는 지금 반납 예정보다 뒤여야 합니다.")
    item_rules = list(rules) if rules is not None else [DEFAULT_RATE_RULE] * len(daily_rates_won)
    if len(item_rules) != len(daily_rates_won):
        raise ValueError("rules must match daily_rates_won")
    added_days = 0
    difference = 0
    for rate, rule in zip(daily_rates_won, item_rules, strict=True):
        old = period_fee(rate, start_at, old_due_at, rule)
        new = period_fee(rate, start_at, new_due_at, rule)
        added_days = max(added_days, new.days - old.days)
        difference += new.amount_won - old.amount_won
    base = max(0, difference)
    discount = validate_won("discount_won", discount_won, label="연장 할인")
    if discount > base:
        raise ValidationError("discount_won", "연장 할인은 연장 대여료를 넘을 수 없습니다.")
    waived = added_days > 0 and base == 0 and any(daily_rates_won)
    return ExtensionQuote(added_days=added_days, base_won=base, discount_won=discount, waived_by_long_rate=waived)


# --------------------------------------------------------------------------- 설정 화면 예시


# 예시 계산 기준 주: 2026-01-05(월). 서울 시각 고정(결정적).
_EXAMPLE_MONDAY = date(2026, 1, 5)


@dataclass(frozen=True)
class RateExample:
    label: str
    fee: PeriodFee


def rate_examples(daily_rate_won: int, rule: RateRule = DEFAULT_RATE_RULE) -> list[RateExample]:
    """요금 규칙 미리보기: 1일·1박2일·주말·10일·35일 예시 요금(설정 화면·카테고리 창)."""

    def at(day_offset: int, minute: int) -> datetime:
        d = _EXAMPLE_MONDAY + timedelta(days=day_offset)
        return datetime(d.year, d.month, d.day, minute // 60, minute % 60, tzinfo=SEOUL)

    def label(name: str, start: datetime, due: datetime) -> str:
        return (
            f"{name} ({WEEKDAY_LABELS[start.weekday()]} {start:%H:%M} → "
            f"{WEEKDAY_LABELS[due.weekday()]} {due:%H:%M})"
        )

    cases = [
        ("1일", at(2, 600), at(3, 600)),
        ("1박2일", at(2, 600), at(3, 1080)),
    ]
    if rule.weekend_enabled:
        start = at(rule.weekend_start_weekday, rule.weekend_start_minute)
        cases.append(("주말", start, start + timedelta(minutes=rule.weekend_span_minutes)))
    else:
        cases.append(("주말", at(4, 1080), at(7, 600)))
    cases += [
        ("10일", at(0, 600), at(10, 600)),
        ("35일", at(0, 600), at(35, 600)),
    ]
    return [
        RateExample(
            label(name, start, due) if name in ("1박2일", "주말") else name,
            period_fee(daily_rate_won, start, due, rule),
        )
        for name, start, due in cases
    ]
