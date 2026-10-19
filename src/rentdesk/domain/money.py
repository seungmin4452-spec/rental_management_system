"""금액 공통 함수: 정수 원 검증, 반올림(PRD 7장). `pricing`·`kits`가 함께 쓴다."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from rentdesk.domain.errors import ValidationError


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validate_won(field: str, value: object, *, label: str) -> int:
    """원 단위 0 이상 정수 검증. bool/float/문자열 거부."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, f"{label}은(는) 원 단위 정수여야 합니다.")
    if value < 0:
        raise ValidationError(field, f"{label}은(는) 0 이상이어야 합니다.")
    return value
