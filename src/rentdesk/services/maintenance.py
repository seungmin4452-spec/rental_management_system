"""정비/사용 불가 블록 등록·종료 (PRD P0-09, S-08).

M1 범위: 가용성 엔진(T-14)이 사용할 블록 등록/종료와 미래 확정 예약 충돌 경고.
반납 시 자동 정비 블록 생성은 M2(ReturnService)에서 이 서비스의 내부 함수를 재사용한다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rentdesk.domain import availability as av
from rentdesk.domain.errors import ConflictInfo, InvalidStateError, NotFoundError, ValidationError
from rentdesk.domain.models import MaintenanceBlock
from rentdesk.domain.timeutil import parse_utc_str, parse_utc_str_opt, to_utc
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import Clock, clean_required, current_time, default_clock


@dataclass(frozen=True)
class BlockRow:
    """정비 목록(S-08) 행."""

    block_id: int
    asset_id: int
    asset_code: str
    asset_name: str
    start_at: datetime
    end_at: datetime | None
    reason: str
    closed_at: datetime | None

    def active_at(self, at: datetime) -> bool:
        return av.block_active_at(self.start_at, self.end_at, at)

    def state_label(self, at: datetime) -> str:
        if self.active_at(at):
            return "진행 중"
        return "예정" if at < self.start_at else "종료"


def list_block_rows(
    conn: sqlite3.Connection, *, include_closed: bool = False, asset_id: int | None = None
) -> list[BlockRow]:
    return [
        BlockRow(
            block_id=r["id"], asset_id=r["asset_id"], asset_code=r["asset_code"],
            asset_name=r["asset_name"], start_at=parse_utc_str(r["start_at_utc"]),
            end_at=parse_utc_str_opt(r["end_at_utc"]), reason=r["reason"],
            closed_at=parse_utc_str_opt(r["closed_at_utc"]),
        )
        for r in repo.list_block_rows(conn, include_closed=include_closed, asset_id=asset_id)
    ]


@dataclass(frozen=True)
class BlockResult:
    block: MaintenanceBlock
    affected_reservations: tuple[ConflictInfo, ...]  # 자동 취소하지 않고 경고만


def affected_confirmed_reservations(
    conn: sqlite3.Connection, asset_id: int, start_at: datetime, end_at: datetime | None
) -> list[ConflictInfo]:
    """블록 구간과 겹치는 출고 전 확정 예약."""
    asset = repo.get_asset(conn, asset_id)
    result = []
    for row in repo.confirmed_orders_for_asset(conn, asset_id):
        o_start, o_end = av.reservation_interval(
            parse_utc_str(row["start_at_utc"]), parse_utc_str(row["due_at_utc"]),
            row["turnaround_buffer_snapshot_minutes"],
        )
        if av.overlaps(start_at, end_at, o_start, o_end):
            result.append(
                ConflictInfo(
                    asset_id=asset_id, asset_code=asset.asset_code,
                    kind=av.OccupancyKind.RESERVATION.value, start_at=o_start, end_at=o_end,
                    order_id=row["order_id"], order_no=row["order_no"],
                )
            )
    return result


def insert_block_in_txn(
    conn: sqlite3.Connection,
    asset_id: int,
    start_at: datetime,
    end_at: datetime | None,
    reason: str,
    now: datetime,
) -> BlockResult:
    """호출자의 트랜잭션 안에서 블록을 등록한다(반납 처리 등에서 재사용)."""
    if repo.get_asset(conn, asset_id) is None:
        raise NotFoundError("장비를 찾을 수 없습니다.")
    start_at = to_utc(start_at)
    end_at = to_utc(end_at) if end_at is not None else None
    if end_at is not None and not start_at < end_at:
        raise ValidationError("end_at", "정비 종료 예정 시각은 시작 시각보다 뒤여야 합니다.")
    affected = affected_confirmed_reservations(conn, asset_id, start_at, end_at)
    block_id = repo.insert_block(
        conn, asset_id=asset_id, start_at=start_at, end_at=end_at, reason=reason, now=now
    )
    repo.insert_audit(
        conn, entity_type="maintenance_block", entity_id=block_id, action="CREATE",
        description=f"asset_id={asset_id}", now=now,
    )
    return BlockResult(block=repo.get_block(conn, block_id), affected_reservations=tuple(affected))


class MaintenanceService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def create_block(
        self,
        asset_id: int,
        start_at: datetime,
        end_at: datetime | None,
        reason: str,
    ) -> BlockResult:
        """블록을 등록하고, 겹치는 기존 확정 예약은 경고 목록으로 반환한다.

        화면은 등록 전에 `preview_affected()`로 충돌을 보여주고 사용자 확인을 받는다.
        """
        clean_reason = clean_required(reason, "reason", "사유", 500)
        now = current_time(self._clock)
        with transaction(self._conn):
            return insert_block_in_txn(self._conn, asset_id, start_at, end_at, clean_reason, now)

    def preview_affected(
        self, asset_id: int, start_at: datetime, end_at: datetime | None
    ) -> list[ConflictInfo]:
        return affected_confirmed_reservations(
            self._conn, asset_id, to_utc(start_at), to_utc(end_at) if end_at else None
        )

    def list_blocks(
        self, *, include_closed: bool = False, asset_id: int | None = None
    ) -> list[BlockRow]:
        """미종료(기본) 또는 전체 정비 일정. 종료 시각이 있는 미래 블록도 '미종료'에 포함."""
        return list_block_rows(self._conn, include_closed=include_closed, asset_id=asset_id)

    def close_block(self, block_id: int, end_at: datetime | None = None) -> MaintenanceBlock:
        """정비 종료. 종료 시각 미지정 시 현재 시각. 해당 시각부터 예약 가능으로 재평가된다."""
        now = current_time(self._clock)
        end = to_utc(end_at) if end_at is not None else now
        with transaction(self._conn):
            block = repo.get_block(self._conn, block_id)
            if block is None:
                raise NotFoundError("정비 일정을 찾을 수 없습니다.")
            if block.closed_at is not None:
                raise InvalidStateError("이미 종료된 정비 일정입니다.")
            if not block.start_at < end:
                raise ValidationError("end_at", "정비 종료 시각은 시작 시각보다 뒤여야 합니다.")
            repo.close_block(self._conn, block_id, end_at=end, now=now)
            repo.insert_audit(
                self._conn, entity_type="maintenance_block", entity_id=block_id,
                action="CLOSE", description=None, now=now,
            )
        return repo.get_block(self._conn, block_id)
