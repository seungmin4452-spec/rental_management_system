"""CalendarService: 예약 달력(U9) 조회. 보기 기간에 걸치는 예약·정비만 읽어 장비별 막대로 돌려준다.

읽기 전용이다. 예약 가능 여부의 최종 판단은 예약 확정 시 `ReservationService`가 트랜잭션 안에서 한다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rentdesk.domain import calendar as cal
from rentdesk.domain.display import format_dt, seoul_today
from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import Asset, ItemStatus, OrderStatus
from rentdesk.domain.timeutil import parse_utc_str, parse_utc_str_opt
from rentdesk.persistence import repositories as repo
from rentdesk.services.common import Clock, current_time, default_clock

MAX_VIEW_DAYS = 62


@dataclass(frozen=True)
class CalendarBar:
    """장비 한 대의 막대 1개. `first_col`~`last_col`(포함)은 보기의 날짜 칸 번호, `lane`은 장비 안의 줄."""

    asset_id: int
    kind: cal.BarKind
    start_at: datetime
    end_at: datetime | None
    first_col: int
    last_col: int
    lane: int
    order_id: int | None = None
    order_no: str | None = None
    customer_name: str | None = None
    block_id: int | None = None
    reason: str | None = None
    starts_before_view: bool = False  # 보기 첫날 이전에 시작(막대 왼쪽이 잘림)

    @property
    def label(self) -> str:
        if self.kind == cal.BarKind.MAINTENANCE:
            return f"정비: {self.reason}"
        text = f"{self.order_no} {self.customer_name}"
        if self.kind == cal.BarKind.DRAFT:
            text += " (임시)"
        elif self.kind == cal.BarKind.OVERDUE:
            text += " (연체)"
        return text

    @property
    def tooltip(self) -> str:
        kind = cal.BAR_KIND_LABELS[self.kind]
        if self.end_at is not None:
            period = f"{format_dt(self.start_at)} ~ {format_dt(self.end_at)}"
        elif self.kind == cal.BarKind.OVERDUE:
            period = f"{format_dt(self.start_at)} ~ 반납 전까지"
        else:
            period = f"{format_dt(self.start_at)} ~ 종료일 미정"
        if self.kind == cal.BarKind.MAINTENANCE:
            return f"[{kind}] {self.reason}\n{period}"
        lines = [f"[{kind}] 예약 {self.order_no} · {self.customer_name}", period]
        if self.kind == cal.BarKind.DRAFT:
            lines.append("임시 저장 예약은 장비를 점유하지 않습니다.")
        elif self.kind == cal.BarKind.OVERDUE:
            lines.append("반납 예정이 지났습니다. 반납 전까지 이 장비는 예약할 수 없습니다.")
        lines.append("더블클릭하면 " + ("정비 관리로 이동" if self.block_id else "예약 상세 보기"))
        return "\n".join(lines)


@dataclass(frozen=True)
class CalendarRow:
    asset: Asset
    bars: tuple[CalendarBar, ...]

    @property
    def lane_count(self) -> int:
        return max((b.lane + 1 for b in self.bars), default=1)


@dataclass(frozen=True)
class CalendarView:
    first_day: date
    days: tuple[date, ...]
    today: date
    rows: tuple[CalendarRow, ...]


class CalendarService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def view(
        self,
        first_day: date,
        days: int,
        *,
        query: str = "",
        category_id: int | None = None,
        include_drafts: bool = True,
        include_returned: bool = True,
    ) -> CalendarView:
        """`first_day`(서울 날짜)부터 `days`일 동안 운영 중 장비별 막대."""
        if not 1 <= days <= MAX_VIEW_DAYS:
            raise ValidationError("days", f"달력 보기 기간은 1~{MAX_VIEW_DAYS}일입니다.")
        now = current_time(self._clock)
        start, end = cal.view_range(first_day, days)
        assets = repo.search_assets(
            self._conn, query=query.strip(), include_retired=False, category_id=category_id
        )
        wanted = {a.id for a in assets}
        raw: dict[int, list[dict]] = {}

        def add(asset_id: int, kind: cal.BarKind, s: datetime, e: datetime | None, **extra) -> None:
            span = cal.column_span(s, e, first_day, days)
            if span is None:
                return
            raw.setdefault(asset_id, []).append(
                dict(asset_id=asset_id, kind=kind, start_at=s, end_at=e,
                     first_col=span[0], last_col=span[1], starts_before_view=s < start, **extra)
            )

        for row in repo.calendar_item_rows(self._conn, start, end):
            if row["asset_id"] not in wanted:
                continue
            bar = cal.item_bar(
                OrderStatus(row["order_status"]), ItemStatus(row["item_status"]),
                parse_utc_str(row["start_at_utc"]), parse_utc_str(row["due_at_utc"]),
                parse_utc_str_opt(row["actual_return_at_utc"]), now,
            )
            if bar is None:
                continue
            kind, s, e = bar
            if kind == cal.BarKind.DRAFT and not include_drafts:
                continue
            if kind == cal.BarKind.RETURNED and not include_returned:
                continue
            add(row["asset_id"], kind, s, e, order_id=row["order_id"],
                order_no=row["order_no"], customer_name=row["customer_name"])
        for block in repo.blocks_in_period(self._conn, start, end):
            if block.asset_id in wanted:
                add(block.asset_id, cal.BarKind.MAINTENANCE, block.start_at, block.end_at,
                    block_id=block.id, reason=block.reason)

        rows = []
        for asset in assets:
            items = raw.get(asset.id, [])
            lanes = cal.assign_lanes([(b["first_col"], b["last_col"]) for b in items])
            bars = tuple(
                sorted((CalendarBar(lane=lane, **b) for b, lane in zip(items, lanes, strict=True)),
                       key=lambda b: (b.lane, b.first_col))
            )
            rows.append(CalendarRow(asset, bars))
        return CalendarView(
            first_day=first_day,
            days=tuple(first_day + timedelta(days=i) for i in range(days)),
            today=seoul_today(now),
            rows=tuple(rows),
        )

    def new_reservation_period(self, first_day: date, last_day: date) -> tuple[datetime, datetime]:
        """달력에서 고른 날짜 칸 → 새 예약 기본 시작·반납 예정. 지난 날짜면 ValidationError."""
        try:
            return cal.selection_period(first_day, last_day, current_time(self._clock))
        except ValueError as exc:
            raise ValidationError("start_at", str(exc)) from exc
