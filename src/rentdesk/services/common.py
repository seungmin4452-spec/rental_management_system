"""서비스 계층 공통: 시계 주입, 문자열 입력 정리."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.timeutil import now_utc, to_utc

Clock = Callable[[], datetime]


def current_time(clock: Clock) -> datetime:
    return to_utc(clock())


def default_clock() -> datetime:
    return now_utc()


def clean_required(value: object, field: str, label: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(field, f"{label}을(를) 입력해주세요.")
    text = value.strip()
    if len(text) > max_len:
        raise ValidationError(field, f"{label}은(는) 최대 {max_len}자까지 입력할 수 있습니다.")
    return text


def clean_optional(value: object, field: str, label: str, max_len: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(field, f"{label} 값이 올바르지 않습니다.")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_len:
        raise ValidationError(field, f"{label}은(는) 최대 {max_len}자까지 입력할 수 있습니다.")
    return text
