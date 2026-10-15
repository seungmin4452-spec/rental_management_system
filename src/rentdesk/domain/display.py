"""화면·엑셀 공통 표시 형식 (PRD 5 공통 UI 원칙).

- 금액: `1,234,000원`
- 일시: 서울 시각 `YYYY-MM-DD HH:mm`
- 지연 시간: `1일 4시간`, `35분`
DB 접근이 없는 순수 함수만 둔다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from rentdesk.domain.timeutil import SEOUL, format_seoul, to_utc


def format_won(amount: int) -> str:
    return f"{amount:,}원"


def format_dt(dt: datetime | None, empty: str = "") -> str:
    return format_seoul(dt) if dt is not None else empty


def format_duration(seconds: int) -> str:
    """양의 초를 '일/시간/분' 한국어로. 1분 미만은 '1분 미만'."""
    if seconds < 60:
        return "1분 미만"
    minutes_total = seconds // 60
    days, rem = divmod(minutes_total, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}일")
    if hours:
        parts.append(f"{hours}시간")
    if mins and not days:
        parts.append(f"{mins}분")
    return " ".join(parts)


def seoul_day_range(day: date) -> tuple[datetime, datetime]:
    """서울 날짜 하루를 UTC `[start, end)` 구간으로."""
    start = datetime.combine(day, time(0, 0), tzinfo=SEOUL)
    return to_utc(start), to_utc(start + timedelta(days=1))


def seoul_today(now: datetime) -> date:
    return now.astimezone(SEOUL).date()
