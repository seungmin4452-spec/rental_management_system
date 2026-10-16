"""대여료·연체료 계산 순수 함수 (PRD 7.2, 7.3).

금액은 항상 KRW 정수. 배수 등 소수는 `Decimal`로만 계산하고 float를 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from rentdesk.domain.errors import ValidationError

SECONDS_PER_DAY = 86_400


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def rental_days(start_at: datetime, due_at: datetime) -> int:
    """24시간 단위 올림, 최소 1일. 예: 25시간 → 2일."""
    seconds = int((due_at - start_at).total_seconds())
    return max(1, _ceil_div(seconds, SECONDS_PER_DAY))


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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


@dataclass(frozen=True)
class PricedItem:
    daily_rate_won: int
    deposit_won: int


@dataclass(frozen=True)
class OrderQuote:
    rental_days: int
    base_rental_fee_won: int
    discount_won: int
    expected_total_rental_fee_won: int
    expected_deposit_won: int


def quote_order(
    items: Sequence[PricedItem],
    start_at: datetime,
    due_at: datetime,
    discount_won: int = 0,
) -> OrderQuote:
    days = rental_days(start_at, due_at)
    base = sum(item.daily_rate_won * days for item in items)
    deposit = sum(item.deposit_won for item in items)
    validate_won("discount_won", discount_won, label="할인 금액")
    if discount_won > base:
        raise ValidationError("discount_won", "할인 금액은 기본 대여료를 초과할 수 없습니다.")
    return OrderQuote(
        rental_days=days,
        base_rental_fee_won=base,
        discount_won=discount_won,
        expected_total_rental_fee_won=base - discount_won,
        expected_deposit_won=deposit,
    )


@dataclass(frozen=True)
class LateFee:
    late_seconds: int
    late_days: int
    late_fee_won: int


def late_fee(
    daily_rate_won: int,
    due_at: datetime,
    actual_return_at: datetime,
    grace_minutes: int,
    late_multiplier: Decimal,
) -> LateFee:
    """유예시간 초과분을 24시간 단위로 올림하여 일 대여료 × 배수를 청구."""
    over = int((actual_return_at - due_at).total_seconds()) - grace_minutes * 60
    late_seconds = max(0, over)
    late_days = _ceil_div(late_seconds, SECONDS_PER_DAY) if late_seconds > 0 else 0
    fee = round_half_up(Decimal(daily_rate_won) * late_days * late_multiplier)
    return LateFee(late_seconds=late_seconds, late_days=late_days, late_fee_won=fee)


def rental_charge_won(daily_rate_won: int, start_at: datetime, due_at: datetime) -> int:
    """품목 1개의 약정 대여료(출고 시 `RENTAL` 청구액) = 스냅샷 일 대여료 × 약정 대여일수."""
    return daily_rate_won * rental_days(start_at, due_at)



@dataclass(frozen=True)
class ExtensionQuote:
    """대여 연장 요금 (USABILITY_PLAN U6)."""

    added_days: int
    base_won: int
    discount_won: int

    @property
    def amount_won(self) -> int:
        return self.base_won - self.discount_won


def quote_extension(
    daily_rates_won: Sequence[int],
    start_at: datetime,
    old_due_at: datetime,
    new_due_at: datetime,
    discount_won: object = 0,
) -> ExtensionQuote:
    """아직 반납하지 않은 장비의 (새 대여일수 − 기존 대여일수) × 확정 시 일 대여료 합계.

    대여일수 계산은 출고 때와 같은 `rental_days`(예약 시작 기준 24시간 단위 올림)를 쓴다.
    연장분 할인은 연장 기본 요금을 넘을 수 없다.
    """
    if not new_due_at > old_due_at:
        raise ValidationError("new_due_at", "새 반납 예정 일시는 지금 반납 예정보다 뒤여야 합니다.")
    added = rental_days(start_at, new_due_at) - rental_days(start_at, old_due_at)
    base = sum(rate * added for rate in daily_rates_won)
    discount = validate_won("discount_won", discount_won, label="연장 할인")
    if discount > base:
        raise ValidationError("discount_won", "연장 할인은 연장 대여료를 넘을 수 없습니다.")
    return ExtensionQuote(added_days=added, base_won=base, discount_won=discount)
