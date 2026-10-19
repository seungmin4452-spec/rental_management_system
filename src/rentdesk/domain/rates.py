"""요금 규칙(M4.6 R3): 대여일수 계산 방식, 주말·주·월 단가, 연체료 계산 방식.

장비에는 일 대여료 하나만 두고, 주말·주·월 요금은 규칙의 배수로 정한다(장비별 설정 없음).
설정 단계는 운영 설정(R2)과 같이 가게 기본값 → 카테고리 설정이며, 카테고리 규칙은 통째로 덮어쓴다.
예약 확정 시 품목마다 실제 적용 규칙을 JSON 스냅샷으로 저장한다.

이 모듈은 규칙 데이터형·검증·JSON 변환만 둔다. 금액 계산은 `pricing`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from rentdesk.domain.errors import ValidationError

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY
WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")
MAX_RULE_JSON_LENGTH = 2000


class DayCountMethod(StrEnum):
    HOURS_24 = "HOURS_24"  # 24시간 단위 올림(최소 1일)
    CALENDAR = "CALENDAR"  # 날짜 기준: 반납일 − 대여일(서울), 당일 대여 = 1일, 1박2일 = 1일


class LateFeeMethod(StrEnum):
    DAILY = "DAILY"  # 유예 후 24시간 단위 올림 × 일 대여료 × 배수
    HOURLY = "HOURLY"  # 유예 후 1시간마다 (일 대여료 ÷ N) × 배수, 24시간마다 하루치 상한


DAY_COUNT_LABELS = {
    DayCountMethod.HOURS_24: "24시간 단위",
    DayCountMethod.CALENDAR: "날짜 기준(1박2일 = 1일)",
}
LATE_METHOD_LABELS = {
    LateFeeMethod.DAILY: "하루 단위",
    LateFeeMethod.HOURLY: "시간 단위",
}


@dataclass(frozen=True)
class RateRule:
    """요금 규칙. 기본값은 R3 이전 동작(24시간 올림, 주말·주·월 단가 없음, 하루 단위 연체)."""

    day_count: DayCountMethod = DayCountMethod.HOURS_24
    return_grace_minutes: int = 0  # 24시간 방식에서 이 시간 이내 초과는 같은 날로 봄
    weekend_enabled: bool = False
    weekend_start_weekday: int = 4  # 0=월 … 6=일. 기본 금 12:00 이후 시작
    weekend_start_minute: int = 12 * 60
    weekend_end_weekday: int = 0  # 기본 월 12:00 이전 반납
    weekend_end_minute: int = 12 * 60
    weekend_multiplier: Decimal = Decimal("2")
    weekly_enabled: bool = False
    weekly_multiplier: Decimal = Decimal("5")
    monthly_enabled: bool = False
    monthly_multiplier: Decimal = Decimal("20")
    late_method: LateFeeMethod = LateFeeMethod.DAILY
    late_hourly_divisor: int = 10  # 시간당 연체료 = 일 대여료 ÷ N

    @property
    def weekend_span_minutes(self) -> int:
        start = self.weekend_start_weekday * MINUTES_PER_DAY + self.weekend_start_minute
        end = self.weekend_end_weekday * MINUTES_PER_DAY + self.weekend_end_minute
        return (end - start) % MINUTES_PER_WEEK


DEFAULT_RATE_RULE = RateRule()


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _num(value: Decimal) -> str:
    return f"{value.normalize():f}"


def weekend_range_label(rule: RateRule) -> str:
    """"금 12:00 이후 시작 ~ 월 12:00 이전 반납"."""
    return (
        f"{WEEKDAY_LABELS[rule.weekend_start_weekday]} {_hhmm(rule.weekend_start_minute)} 이후 시작 ~ "
        f"{WEEKDAY_LABELS[rule.weekend_end_weekday]} {_hhmm(rule.weekend_end_minute)} 이전 반납"
    )


def rate_rule_summary(rule: RateRule) -> str:
    """규칙 한 줄 요약(설정 화면·예약 마법사 근거 문구)."""
    parts = [DAY_COUNT_LABELS[rule.day_count]]
    if rule.day_count == DayCountMethod.HOURS_24 and rule.return_grace_minutes:
        parts[0] += f", {rule.return_grace_minutes}분 이내 초과는 같은 날"
    if rule.weekend_enabled:
        parts.append(f"주말({weekend_range_label(rule)}) 일 요금×{_num(rule.weekend_multiplier)}")
    if rule.weekly_enabled:
        parts.append(f"7일 = 일 요금×{_num(rule.weekly_multiplier)}")
    if rule.monthly_enabled:
        parts.append(f"30일 = 일 요금×{_num(rule.monthly_multiplier)}")
    if rule.late_method == LateFeeMethod.HOURLY:
        parts.append(f"연체 시간 단위(1시간 = 일 요금÷{rule.late_hourly_divisor})")
    return " · ".join(parts)


def rate_rule_short(rule: RateRule) -> str:
    """표 칸용 짧은 요약: "날짜 기준 · 주말 · 주 · 월 · 연체 시간 단위"."""
    parts = ["날짜 기준" if rule.day_count == DayCountMethod.CALENDAR else "24시간"]
    parts += [name for on, name in (
        (rule.weekend_enabled, "주말"), (rule.weekly_enabled, "주"), (rule.monthly_enabled, "월"),
    ) if on]
    if rule.late_method == LateFeeMethod.HOURLY:
        parts.append("연체 시간 단위")
    return " · ".join(parts)


# --------------------------------------------------------------------------- validation


def _int(field: str, label: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, f"{label}은(는) 정수여야 합니다.")
    if not low <= value <= high:
        raise ValidationError(field, f"{label}은(는) {low} 이상 {high} 이하여야 합니다.")
    return value


def _bool(field: str, label: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(field, f"{label} 사용 여부가 올바르지 않습니다.")
    return value


def _multiplier(field: str, label: str, value: object, high: int) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError(field, f"{label}는 숫자여야 합니다.")
    try:
        dec = Decimal(value) if isinstance(value, (str, int, Decimal)) else None
    except (InvalidOperation, ValueError):
        dec = None
    if dec is None or not dec.is_finite():
        raise ValidationError(field, f"{label}는 숫자여야 합니다.")
    if not 0 < dec <= high:
        raise ValidationError(field, f"{label}는 0보다 크고 {high} 이하여야 합니다.")
    if dec.as_tuple().exponent < -4:  # type: ignore[operator]
        raise ValidationError(field, f"{label}는 소수점 넷째 자리까지만 입력할 수 있습니다.")
    return dec


def _enum(field: str, label: str, enum_type: type[StrEnum], value: object) -> StrEnum:
    try:
        return enum_type(value)
    except ValueError:
        raise ValidationError(field, f"{label} 값이 올바르지 않습니다.") from None


def validate_rate_rule(rule: RateRule) -> RateRule:
    """서비스 계층 입력 검증. 통과하면 정규화된 새 규칙을 돌려준다."""
    if not isinstance(rule, RateRule):
        raise ValidationError("rate_rule", "요금 규칙이 올바르지 않습니다.")
    clean = RateRule(
        day_count=_enum("day_count", "대여일수 계산 방식", DayCountMethod, rule.day_count),  # type: ignore[arg-type]
        return_grace_minutes=_int(
            "return_grace_minutes", "반납 여유 시간(분)", rule.return_grace_minutes, 0, MINUTES_PER_DAY - 1
        ),
        weekend_enabled=_bool("weekend_enabled", "주말 요금", rule.weekend_enabled),
        weekend_start_weekday=_int("weekend_start_weekday", "주말 시작 요일", rule.weekend_start_weekday, 0, 6),
        weekend_start_minute=_int(
            "weekend_start_minute", "주말 시작 시각", rule.weekend_start_minute, 0, MINUTES_PER_DAY - 1
        ),
        weekend_end_weekday=_int("weekend_end_weekday", "주말 반납 요일", rule.weekend_end_weekday, 0, 6),
        weekend_end_minute=_int(
            "weekend_end_minute", "주말 반납 시각", rule.weekend_end_minute, 0, MINUTES_PER_DAY - 1
        ),
        weekend_multiplier=_multiplier("weekend_multiplier", "주말 요금 배수", rule.weekend_multiplier, 7),
        weekly_enabled=_bool("weekly_enabled", "주 단가", rule.weekly_enabled),
        weekly_multiplier=_multiplier("weekly_multiplier", "주 단가 배수", rule.weekly_multiplier, 7),
        monthly_enabled=_bool("monthly_enabled", "월 단가", rule.monthly_enabled),
        monthly_multiplier=_multiplier("monthly_multiplier", "월 단가 배수", rule.monthly_multiplier, 30),
        late_method=_enum("late_method", "연체료 계산 방식", LateFeeMethod, rule.late_method),  # type: ignore[arg-type]
        late_hourly_divisor=_int(
            "late_hourly_divisor", "시간당 연체료 나누는 수", rule.late_hourly_divisor, 1, 24
        ),
    )
    if clean.weekend_span_minutes == 0:
        raise ValidationError("weekend_end_weekday", "주말 반납 요일·시각은 시작 요일·시각과 달라야 합니다.")
    return clean


# --------------------------------------------------------------------------- JSON


def rate_rule_to_json(rule: RateRule) -> str:
    """스냅샷·설정 저장 형식. 십진수는 문자열, 키 순서 고정(같은 규칙 = 같은 문자열)."""
    data = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(rule).items()}
    data["day_count"] = rule.day_count.value
    data["late_method"] = rule.late_method.value
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rate_rule_from_data(data: object) -> RateRule:
    """JSON 객체 → 검증된 규칙. 빠진 키는 기본값(이후 버전에서 항목이 늘어도 옛 스냅샷을 읽도록), 모르는 키는 무시."""
    if not isinstance(data, dict):
        raise ValidationError("rate_rule", "요금 규칙 형식이 올바르지 않습니다.")
    values: dict[str, object] = {}
    for f in fields(RateRule):
        if f.name in data:
            value = data[f.name]
            if f.name.endswith("_multiplier") and isinstance(value, str):
                try:
                    value = Decimal(value)
                except InvalidOperation:
                    raise ValidationError(f.name, "요금 규칙의 배수가 숫자가 아닙니다.") from None
            values[f.name] = value
    return validate_rate_rule(RateRule(**values))  # type: ignore[arg-type]


def rate_rule_from_json(text: str | None) -> RateRule:
    """저장된 JSON 문자열 → 규칙. None(마이그레이션 006 이전 형식)은 기본 규칙."""
    if text is None:
        return DEFAULT_RATE_RULE
    if len(text) > MAX_RULE_JSON_LENGTH:
        raise ValidationError("rate_rule", "요금 규칙이 너무 깁니다.")
    try:
        data = json.loads(text)
    except ValueError:
        raise ValidationError("rate_rule", "요금 규칙 형식이 올바르지 않습니다.") from None
    return rate_rule_from_data(data)
