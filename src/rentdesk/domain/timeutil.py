"""시간 처리 규칙 (PRD 0.8, 9.2).

- 내부 계산은 timezone-aware `datetime`만 사용한다.
- DB 저장 형식은 `YYYY-MM-DDTHH:MM:SSZ`(UTC, 초 단위)로 통일하여 텍스트 정렬과 시간 순서를 일치시킨다.
- 화면 표시는 Asia/Seoul로 변환한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DISPLAY_FORMAT = "%Y-%m-%d %H:%M"


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timezone 정보가 없는 datetime은 사용할 수 없습니다.")
    return dt


def to_utc(dt: datetime) -> datetime:
    """aware datetime을 UTC로 바꾸고 초 미만을 버린다(저장 형식과 일치)."""
    return ensure_aware(dt).astimezone(UTC).replace(microsecond=0)


def to_utc_str(dt: datetime) -> str:
    return to_utc(dt).strftime(_UTC_FORMAT)


def parse_utc_str(value: str) -> datetime:
    return datetime.strptime(value, _UTC_FORMAT).replace(tzinfo=UTC)


def parse_utc_str_opt(value: str | None) -> datetime | None:
    return None if value is None else parse_utc_str(value)


def to_seoul(dt: datetime) -> datetime:
    return ensure_aware(dt).astimezone(SEOUL)


def format_seoul(dt: datetime) -> str:
    return to_seoul(dt).strftime(DISPLAY_FORMAT)


def floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def minutes(n: int) -> timedelta:
    return timedelta(minutes=n)
