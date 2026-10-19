"""라이선스 사용 조건 계산 (KMONG_LAUNCH_PLAN K3·K4·K5) — DB·화면과 무관한 순수 함수.

- 버전: 앱 버전 `주.부.수`(예: 1.0.3) 중 `주.부`가 라이선스의 `updates_through`(예: "1.0") 이하이면
  사용 가능하다. 수(패치) 번호만 오른 오류 수정 버전은 항상 허용되고, 1.1 이상 기능 업데이트는
  업데이트를 구매해 새 라이선스를 받아야 한다.
- 1개월 라이선스 만료일: 발급일(서울 날짜) + N개월의 같은 날짜. 그 달에 같은 날이 없으면 말일.
  라이선스는 만료일(서울) 23:59:59까지 유효하다.
- 남은 일수: 서울 날짜 기준. 만료일 당일은 0일.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, time

from rentdesk.domain.timeutil import SEOUL

# 버전 필드가 없는 라이선스(K5 이전 발급분)는 1.0.x까지 허용한다.
DEFAULT_UPDATES_THROUGH = "1.0"
# 남은 사용 기간을 미리 알려주기 시작하는 날 수(만료 7일 전부터).
EXPIRY_NOTICE_DAYS = 7

_VERSION = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")
_SERIES = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")


def parse_app_version(value: str) -> tuple[int, int, int]:
    """`1.0.3` → (1, 0, 3). 형식이 다르면 ValueError."""
    m = _VERSION.match(value.strip()) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"앱 버전 형식이 올바르지 않습니다: {value!r}")
    return int(m[1]), int(m[2]), int(m[3])


def parse_series(value: str) -> tuple[int, int]:
    """라이선스 `updates_through` 값 `1.0` → (1, 0). 형식이 다르면 ValueError."""
    m = _SERIES.match(value.strip()) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"업데이트 허용 버전 형식이 올바르지 않습니다(예: 1.0): {value!r}")
    return int(m[1]), int(m[2])


def series_of(app_version: str) -> str:
    """앱 버전이 속한 업데이트 단위. `1.0.3` → `1.0`."""
    major, minor, _patch = parse_app_version(app_version)
    return f"{major}.{minor}"


def is_version_covered(app_version: str, updates_through: str) -> bool:
    """이 라이선스로 해당 앱 버전을 쓸 수 있는지. 둘 중 하나라도 형식이 틀리면 ValueError."""
    major, minor, _patch = parse_app_version(app_version)
    return (major, minor) <= parse_series(updates_through)


def add_months(day: date, months: int) -> date:
    """같은 날짜로 N개월 뒤. 1월 31일 + 1개월 → 2월 말일."""
    if months < 1:
        raise ValueError("개월 수는 1 이상이어야 합니다.")
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def expiry_end_utc(expiry_day: date) -> datetime:
    """만료일(서울 날짜)이 끝나는 시각(23:59:59)을 UTC로."""
    return datetime.combine(expiry_day, time(23, 59, 59), tzinfo=SEOUL).astimezone(UTC)


def expiry_for_months(issued_at: datetime, months: int) -> datetime:
    """발급 시각 기준 N개월 라이선스의 만료 시각(UTC)."""
    return expiry_end_utc(add_months(issued_at.astimezone(SEOUL).date(), months))


def days_left(expires_at: datetime, now: datetime) -> int:
    """만료일까지 남은 날 수(서울 날짜). 당일 0, 이미 지난 날짜는 음수."""
    return (expires_at.astimezone(SEOUL).date() - now.astimezone(SEOUL).date()).days


def expiry_notice(expires_at: datetime | None, now: datetime) -> str | None:
    """만료 임박(7일 이내) 안내 문구. 기간 제한이 없거나 아직 멀었거나 이미 끝났으면 None."""
    if expires_at is None or now >= expires_at:
        return None
    left = days_left(expires_at, now)
    if left > EXPIRY_NOTICE_DAYS:
        return None
    if left <= 0:
        return "사용 기간이 오늘 끝납니다"
    return f"사용 기간이 {left}일 남았습니다"
