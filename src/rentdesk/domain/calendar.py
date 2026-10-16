"""예약 달력(U9) 순수 함수: 날짜 칸 계산, 막대 종류·칸 범위, 겹침 줄 배정, 선택 칸 → 예약 기간.

- 날짜 칸은 서울 날짜 하루 `[00:00, 다음 날 00:00)`. 막대 구간도 `[start, end)`이고 `end=None`은 끝 없음.
- 막대는 **대여 기간 자체**(반납 후 준비 시간 제외)를 보여준다. 예약 가능 여부 판정은
  `domain.availability`가 준비 시간을 포함해 따로 한다.
- DB·현재 시각 전역에 의존하지 않는다(`now`는 호출자가 주입).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from rentdesk.domain.models import ItemStatus, OrderStatus
from rentdesk.domain.timeutil import SEOUL, to_seoul, to_utc

WEEK_DAYS = 7


class BarKind(StrEnum):
    DRAFT = "DRAFT"  # 임시 저장(장비 점유 없음)
    CONFIRMED = "CONFIRMED"  # 예약확정(출고 전)
    IN_RENTAL = "IN_RENTAL"  # 대여 중
    OVERDUE = "OVERDUE"  # 반납 예정이 지난 미반납(반납 전까지 끝 없음)
    RETURNED = "RETURNED"  # 반납 완료(실제 반납 시각까지)
    MAINTENANCE = "MAINTENANCE"  # 정비·사용 불가


BAR_KIND_LABELS = {
    BarKind.DRAFT: "임시 저장",
    BarKind.CONFIRMED: "예약확정",
    BarKind.IN_RENTAL: "대여 중",
    BarKind.OVERDUE: "연체",
    BarKind.RETURNED: "반납 완료",
    BarKind.MAINTENANCE: "정비",
}


def week_start(day: date) -> date:
    """그 주의 월요일."""
    return day - timedelta(days=day.weekday())


def month_days(day: date) -> tuple[date, int]:
    """그 달의 1일과 날짜 수."""
    first = day.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first, (next_month - first).days


def view_range(first_day: date, days: int) -> tuple[datetime, datetime]:
    """보기 기간(서울 날짜 `first_day`부터 `days`일)을 UTC `[start, end)`로."""
    if days < 1:
        raise ValueError("보기 기간은 1일 이상이어야 합니다.")
    start = datetime.combine(first_day, time(0, 0), tzinfo=SEOUL)
    return to_utc(start), to_utc(start + timedelta(days=days))


def item_bar(
    order_status: OrderStatus,
    item_status: ItemStatus,
    start_at: datetime,
    due_at: datetime,
    actual_return_at: datetime | None,
    now: datetime,
) -> tuple[BarKind, datetime, datetime | None] | None:
    """예약 품목 1건의 막대 종류와 표시 구간. 취소된 주문·품목은 None."""
    if order_status == OrderStatus.CANCELED or item_status == ItemStatus.CANCELED:
        return None
    if item_status == ItemStatus.RETURNED:
        if actual_return_at is None:
            raise ValueError("반납 완료 품목에는 실제 반납 시각이 필요합니다.")
        return BarKind.RETURNED, start_at, actual_return_at
    if item_status == ItemStatus.OUT:
        if now > due_at:
            return BarKind.OVERDUE, start_at, None
        return BarKind.IN_RENTAL, start_at, due_at
    if order_status == OrderStatus.DRAFT:
        return BarKind.DRAFT, start_at, due_at
    return BarKind.CONFIRMED, start_at, due_at


def column_span(
    start_at: datetime, end_at: datetime | None, first_day: date, days: int
) -> tuple[int, int] | None:
    """구간 `[start, end)`이 걸치는 날짜 칸 번호(처음, 마지막 — 둘 다 포함). 보기 밖이면 None.

    끝이 정확히 자정이면 그날 칸은 포함하지 않는다(`[start, end)`).
    길이 0인 구간(시작 = 끝)은 시작 칸 하나로 본다.
    """
    view_start, view_end = view_range(first_day, days)
    if end_at is not None and end_at < start_at:
        raise ValueError("구간의 끝이 시작보다 앞설 수 없습니다.")
    if start_at >= view_end:
        return None
    last_instant = None  # 구간에 포함되는 마지막 순간(끝 제외). 길이 0이면 시작 순간.
    if end_at is not None:
        last_instant = end_at - timedelta(microseconds=1) if end_at > start_at else start_at
        if last_instant < view_start:
            return None
    first_col = max(0, (to_seoul(start_at).date() - first_day).days)
    if last_instant is None:
        last_col = days - 1
    else:
        last_col = min(days - 1, (to_seoul(last_instant).date() - first_day).days)
    return first_col, last_col


def assign_lanes(spans: Sequence[tuple[int, int]]) -> list[int]:
    """같은 장비의 막대들이 같은 칸을 나눠 쓰지 않도록 줄 번호를 배정(입력 순서대로 결과).

    시작 칸이 빠른 순서로 가장 낮은 빈 줄에 넣는다(구간 분할 탐욕법 — 줄 수 최소).
    """
    order = sorted(range(len(spans)), key=lambda i: (spans[i][0], spans[i][1]))
    lane_last: list[int] = []  # 줄별 마지막으로 차지한 칸
    result = [0] * len(spans)
    for i in order:
        first, last = spans[i]
        for lane, used_until in enumerate(lane_last):
            if used_until < first:
                lane_last[lane] = last
                result[i] = lane
                break
        else:
            lane_last.append(last)
            result[i] = len(lane_last) - 1
    return result


def selection_period(
    first_day: date, last_day: date, now: datetime, *, default_hour: int = 10
) -> tuple[datetime, datetime]:
    """달력에서 고른 날짜 칸 → 새 예약의 시작·반납 예정(서울 시각, 정시).

    - 시작: 첫 날 `default_hour`시. 첫 날이 오늘이고 그 시각이 지났으면 지금 이후 첫 정시.
    - 반납 예정: 마지막 날 같은 시각. 하루만 골랐으면 다음 날 같은 시각(1일 대여).
    막대가 "장비가 밖에 있는 날"을 칠하므로, 고른 칸 수와 대여 후 칠해지는 칸 수가 같다.
    예약 마법사에서 시각을 바꿀 수 있다.
    """
    if last_day < first_day:
        first_day, last_day = last_day, first_day
    start = datetime.combine(first_day, time(default_hour, 0), tzinfo=SEOUL)
    local_now = to_seoul(now)
    if start < local_now:
        next_hour = local_now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if next_hour.date() != first_day:
            raise ValueError("지난 날짜에는 새 예약을 만들 수 없습니다.")
        start = next_hour
    if last_day == first_day:
        due = start + timedelta(days=1)
    else:
        due = datetime.combine(last_day, start.timetz())
    return start, due
