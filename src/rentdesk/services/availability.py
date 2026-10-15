"""AvailabilityService: 예약 충돌·정비 블록·연체 미반납 확인 (PRD P0-03, 7.1).

DB에서 점유 후보를 읽어 도메인 순수 함수(`domain.availability`)로 판정한다.
예약 확정 경로에서는 반드시 호출자의 `BEGIN IMMEDIATE` 트랜잭션 안에서 호출해야 한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from rentdesk.domain import availability as av
from rentdesk.domain.errors import ConflictInfo
from rentdesk.domain.models import Asset, ItemStatus, LifecycleStatus, OrderStatus
from rentdesk.domain.timeutil import format_seoul, parse_utc_str, parse_utc_str_opt
from rentdesk.persistence import repositories as repo
from rentdesk.services.common import Clock, current_time, default_clock
from rentdesk.services.settings import read_policy


def load_occupancies(
    conn: sqlite3.Connection, asset_ids: Sequence[int], now: datetime
) -> list[av.Occupancy]:
    occupancies: list[av.Occupancy] = []
    for row in repo.occupying_item_rows(conn, asset_ids):
        interval = av.item_occupied_interval(
            av.ItemOccupancyInput(
                order_status=OrderStatus(row["order_status"]),
                item_status=ItemStatus(row["item_status"]),
                start_at=parse_utc_str(row["start_at_utc"]),
                due_at=parse_utc_str(row["due_at_utc"]),
                actual_return_at=parse_utc_str_opt(row["actual_return_at_utc"]),
                buffer_minutes=row["turnaround_buffer_snapshot_minutes"],
            ),
            now,
        )
        if interval is None:
            continue
        kind, start, end = interval
        occupancies.append(
            av.Occupancy(
                asset_id=row["asset_id"], kind=kind, start_at=start, end_at=end,
                order_id=row["order_id"], order_no=row["order_no"],
            )
        )
    for block in repo.blocks_for_assets(conn, asset_ids):
        occupancies.append(
            av.Occupancy(
                asset_id=block.asset_id, kind=av.OccupancyKind.MAINTENANCE,
                start_at=block.start_at, end_at=block.end_at, block_id=block.id,
            )
        )
    return occupancies


def find_conflicts(
    conn: sqlite3.Connection,
    asset_ids: Sequence[int],
    start_at: datetime,
    due_at: datetime,
    buffer_minutes: int,
    now: datetime,
    *,
    exclude_order_id: int | None = None,
) -> list[ConflictInfo]:
    """요청 구간 `[start, due + buffer)`과 겹치는 점유 목록."""
    req_start, req_end = av.reservation_interval(start_at, due_at, buffer_minutes)
    conflicts = av.find_conflicts(
        req_start, req_end, load_occupancies(conn, asset_ids, now),
        exclude_order_id=exclude_order_id,
    )
    codes = {a.id: a.asset_code for a in repo.get_assets(conn, list(asset_ids)).values()}
    return [
        ConflictInfo(
            asset_id=c.asset_id, asset_code=codes.get(c.asset_id, str(c.asset_id)),
            kind=c.kind.value, start_at=c.start_at, end_at=c.end_at,
            order_id=c.order_id, order_no=c.order_no, block_id=c.block_id,
        )
        for c in conflicts
    ]


def describe_conflict(c: ConflictInfo) -> str:
    """사용자 표시용 한 줄 설명(서울 시각)."""
    start = format_seoul(c.start_at)
    end = format_seoul(c.end_at) if c.end_at else "미정"
    kind = av.OccupancyKind(c.kind)
    if kind == av.OccupancyKind.MAINTENANCE:
        return f"{c.asset_code}: 정비/사용 불가 ({start} ~ {end})"
    if kind == av.OccupancyKind.OVERDUE:
        return f"{c.asset_code}: 예약 {c.order_no} 연체 미반납 (반납 전까지 예약 불가)"
    return f"{c.asset_code}: 예약 {c.order_no} ({start} ~ {end}, 점검 버퍼 포함)"


@dataclass(frozen=True)
class OverdueItem:
    item_id: int
    asset_id: int
    asset_code: str
    order_id: int
    order_no: str
    due_at: datetime


@dataclass(frozen=True)
class FollowupRisk:
    """연체 미반납 장비에 잡혀 있는 후속 확정 예약 (자동 취소하지 않고 경고만)."""

    overdue: OverdueItem
    order_id: int
    order_no: str
    start_at: datetime


class AvailabilityService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def find_conflicts(
        self,
        asset_ids: Sequence[int],
        start_at: datetime,
        due_at: datetime,
        *,
        buffer_minutes: int | None = None,
        exclude_order_id: int | None = None,
    ) -> list[ConflictInfo]:
        """화면 사전 확인용. 실제 확정 시에는 ReservationService가 트랜잭션 안에서 재검사한다."""
        if buffer_minutes is None:
            buffer_minutes = read_policy(self._conn).turnaround_buffer_minutes
        return find_conflicts(
            self._conn, asset_ids, start_at, due_at, buffer_minutes,
            current_time(self._clock), exclude_order_id=exclude_order_id,
        )

    def available_assets(
        self,
        start_at: datetime,
        due_at: datetime,
        *,
        query: str = "",
        exclude_order_id: int | None = None,
    ) -> list[Asset]:
        """기간 내 예약 가능한 ACTIVE 장비(겹치는 예약·정비·연체 미반납 제외)."""
        assets = repo.search_assets(self._conn, query=query.strip(), include_retired=False)
        if not assets:
            return []
        conflicts = self.find_conflicts(
            [a.id for a in assets], start_at, due_at, exclude_order_id=exclude_order_id
        )
        blocked = {c.asset_id for c in conflicts}
        return [
            a for a in assets
            if a.id not in blocked and a.lifecycle_status == LifecycleStatus.ACTIVE
        ]

    def overdue_items(self) -> list[OverdueItem]:
        now = current_time(self._clock)
        result = []
        for row in repo.out_item_rows(self._conn):
            due = parse_utc_str(row["due_at_utc"])
            if av.is_overdue(ItemStatus.OUT, due, now):
                result.append(
                    OverdueItem(
                        item_id=row["item_id"], asset_id=row["asset_id"],
                        asset_code=row["asset_code"], order_id=row["order_id"],
                        order_no=row["order_no"], due_at=due,
                    )
                )
        result.sort(key=lambda o: o.due_at)
        return result

    def overdue_followup_risks(self) -> list[FollowupRisk]:
        """'연체로 인한 후속 예약 위험' 경고 목록 (PRD 7.1, T-13)."""
        risks = []
        for overdue in self.overdue_items():
            for row in repo.confirmed_orders_for_asset(self._conn, overdue.asset_id):
                risks.append(
                    FollowupRisk(
                        overdue=overdue, order_id=row["order_id"], order_no=row["order_no"],
                        start_at=parse_utc_str(row["start_at_utc"]),
                    )
                )
        return risks
