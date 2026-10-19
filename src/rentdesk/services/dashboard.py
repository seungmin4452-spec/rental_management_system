"""DashboardService: 오늘 운영 현황 (PRD P0-07, S-01).

현재 대여 중 장비 수, 오늘 출고/반납 예정, 현재 연체, 완료 후 미정산, 7일 내 예약,
정비 중 장비를 한 번에 모은다. 화면은 이 결과만 표시하며 SQL·계산을 하지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from rentdesk.domain import availability as av
from rentdesk.domain.display import seoul_day_range, seoul_today
from rentdesk.domain.models import ItemStatus, OrderStatus
from rentdesk.domain.pricing import late_fee
from rentdesk.domain.rates import rate_rule_from_json
from rentdesk.domain.timeutil import parse_utc_str
from rentdesk.persistence import repositories as repo
from rentdesk.services.availability import AvailabilityService, FollowupRisk
from rentdesk.services.common import Clock, current_time, default_clock
from rentdesk.services.maintenance import BlockRow, list_block_rows
from rentdesk.services.reservations import OrderRow, ReservationService
from rentdesk.services.settlements import SettlementService, UnsettledOrder

UPCOMING_DAYS = 7


@dataclass(frozen=True)
class OverdueRow:
    item_id: int
    asset_id: int
    asset_code: str
    asset_name: str
    order_id: int
    order_no: str
    customer_name: str
    due_at: datetime
    overdue_seconds: int
    expected_late_fee_won: int  # 기준 시각에 반납한다고 가정한 예측(원장 미기록)


@dataclass(frozen=True)
class ScheduleRow:
    """오늘 출고 또는 반납 예정 품목 1건."""

    item_id: int
    asset_code: str
    asset_name: str
    order_id: int
    order_no: str
    customer_name: str
    scheduled_at: datetime
    done: bool  # 출고 완료 / 반납 완료 여부

    @property
    def state_label(self) -> str:
        return "처리 완료" if self.done else "대기"


@dataclass(frozen=True)
class DashboardData:
    day: date
    now: datetime
    rented_asset_count: int
    checkouts_today: tuple[ScheduleRow, ...]
    returns_today: tuple[ScheduleRow, ...]
    overdue: tuple[OverdueRow, ...]
    followup_risks: tuple[FollowupRisk, ...]
    unsettled: tuple[UnsettledOrder, ...]
    upcoming: tuple[OrderRow, ...]
    maintenance: tuple[BlockRow, ...]

    @property
    def pending_checkouts(self) -> int:
        return sum(1 for r in self.checkouts_today if not r.done)

    @property
    def pending_returns(self) -> int:
        return sum(1 for r in self.returns_today if not r.done)

    @property
    def completed_unsettled_count(self) -> int:
        """반납 완료(COMPLETED)했지만 미정산인 예약 수."""
        return sum(1 for u in self.unsettled if u.status == OrderStatus.COMPLETED)


def overdue_rows(conn: sqlite3.Connection, at: datetime) -> list[OverdueRow]:
    """`at` 기준 연체 OUT 품목과 그 시각에 반납 시 예측 연체료."""
    result = []
    for r in repo.out_item_details(conn):
        due = parse_utc_str(r["due_at_utc"])
        if not av.is_overdue(ItemStatus.OUT, due, at):
            continue
        fee = late_fee(
            r["daily_rate_snapshot_won"], due, at, r["grace_minutes_snapshot"],
            Decimal(r["late_multiplier_snapshot"]), rate_rule_from_json(r["rate_rule_snapshot_json"]),
        )
        result.append(OverdueRow(
            item_id=r["item_id"], asset_id=r["asset_id"], asset_code=r["asset_code"],
            asset_name=r["asset_name"], order_id=r["order_id"], order_no=r["order_no"],
            customer_name=r["customer_name"], due_at=due,
            overdue_seconds=int((at - due).total_seconds()),
            expected_late_fee_won=fee.late_fee_won,
        ))
    return result


def schedule_rows(
    conn: sqlite3.Connection, day: date
) -> tuple[list[ScheduleRow], list[ScheduleRow]]:
    """서울 날짜 `day`의 (출고 예정, 반납 예정) 품목."""
    start, end = seoul_day_range(day)
    checkouts, returns = [], []
    for r in repo.schedule_item_rows(conn, start, end):
        start_at = parse_utc_str(r["start_at_utc"])
        due_at = parse_utc_str(r["due_at_utc"])
        common = dict(
            item_id=r["item_id"], asset_code=r["asset_code"], asset_name=r["asset_name"],
            order_id=r["order_id"], order_no=r["order_no"], customer_name=r["customer_name"],
        )
        if start <= start_at < end:
            checkouts.append(ScheduleRow(
                **common, scheduled_at=start_at,
                done=r["order_status"] != OrderStatus.CONFIRMED.value,
            ))
        if start <= due_at < end:
            returns.append(ScheduleRow(
                **common, scheduled_at=due_at,
                done=r["item_status"] == ItemStatus.RETURNED.value,
            ))
    returns.sort(key=lambda x: (x.scheduled_at, x.order_no, x.asset_code))
    return checkouts, returns


def active_maintenance_rows(conn: sqlite3.Connection, at: datetime) -> list[BlockRow]:
    rows = [b for b in list_block_rows(conn, include_closed=True) if b.active_at(at)]
    rows.sort(key=lambda b: b.asset_code)
    return rows


class DashboardService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def snapshot(self, day: date | None = None) -> DashboardData:
        """`day`(서울 날짜, 기본 오늘)의 일정과 현재 시각 기준 연체·미정산·정비 현황."""
        now = current_time(self._clock)
        day = day or seoul_today(now)
        checkouts, returns = schedule_rows(self._conn, day)
        out_assets = {r["asset_id"] for r in repo.out_item_details(self._conn)}
        upcoming_start, _ = seoul_day_range(seoul_today(now))
        upcoming_end = upcoming_start + timedelta(days=UPCOMING_DAYS)
        upcoming = [
            row for row in ReservationService(self._conn, self._clock).list_order_rows(
                statuses=[OrderStatus.CONFIRMED], period_start=upcoming_start,
                period_end=upcoming_end,
            )
            if row.order.start_at < upcoming_end
        ]
        upcoming.sort(key=lambda r: r.order.start_at)
        availability = AvailabilityService(self._conn, self._clock)
        return DashboardData(
            day=day,
            now=now,
            rented_asset_count=len(out_assets),
            checkouts_today=tuple(checkouts),
            returns_today=tuple(returns),
            overdue=tuple(overdue_rows(self._conn, now)),
            followup_risks=tuple(availability.overdue_followup_risks()),
            unsettled=tuple(SettlementService(self._conn, self._clock).unsettled_orders()),
            upcoming=tuple(upcoming),
            maintenance=tuple(active_maintenance_rows(self._conn, now)),
        )
