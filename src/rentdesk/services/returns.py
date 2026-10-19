"""ReturnService: 장비 단위 부분 반납, 연체료/파손비 확정, 정비 블록 (PRD S-06, 6.2, 7.3).

- `IN_RENTAL` 주문의 `OUT` 품목만 반납할 수 있다. 이미 반납된 품목은 거부(이중 과금 금지, T-12).
- 반납 시각은 출고 시각 이후이며 미래일 수 없다.
- 연체료는 품목 스냅샷(일 대여료·유예·배수)으로 반납 순간 한 번만 `LATE` 청구한다.
- 파손비는 운영자가 입력한 금액으로 `DAMAGE` 청구한다.
- `MAINTENANCE_REQUIRED`면 같은 트랜잭션에서 정비 블록(종료 미정)을 만들고 영향 예약을 경고한다.
- 반납 시각+버퍼와 겹치는 후속 확정 예약도 자동 취소하지 않고 경고한다.
- 마지막 품목이 반납되면 주문은 `COMPLETED`(정산 완료와는 별개).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from rentdesk.domain.errors import ConflictInfo, InvalidStateError, NotFoundError, ValidationError
from rentdesk.domain.models import (
    ChargeKind,
    ItemStatus,
    MaintenanceBlock,
    OrderStatus,
    RentalItem,
    RentalOrder,
    ReturnCondition,
)
from rentdesk.domain.pricing import LateFee, late_fee, late_fee_basis, validate_won
from rentdesk.domain.timeutil import ensure_aware, format_seoul, to_utc
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import Clock, clean_optional, current_time, default_clock
from rentdesk.services.maintenance import affected_confirmed_reservations, insert_block_in_txn


@dataclass(frozen=True)
class ReturnItemInput:
    item_id: int
    returned_at: datetime | None = None  # None = 현재 시각
    condition: ReturnCondition = ReturnCondition.GOOD
    damage_fee_won: int = 0
    note: str | None = None
    maintenance_reason: str | None = None  # 정비 필요 시 블록 사유(미입력 시 기본 문구)


@dataclass(frozen=True)
class LateFeePreview:
    """반납 전 예측 연체료와 계산 근거(원장에 기록하지 않음)."""

    item_id: int
    asset_code: str
    due_at: datetime
    returned_at: datetime
    grace_minutes: int
    late: LateFee
    basis: str


@dataclass(frozen=True)
class ReturnedLine:
    item_id: int
    asset_code: str
    late_fee_won: int
    damage_fee_won: int
    basis: str


@dataclass(frozen=True)
class ReturnResult:
    order: RentalOrder
    lines: tuple[ReturnedLine, ...]
    maintenance_blocks: tuple[MaintenanceBlock, ...]
    affected_reservations: tuple[ConflictInfo, ...]  # 자동 취소하지 않고 경고만

    @property
    def completed(self) -> bool:
        return self.order.status == OrderStatus.COMPLETED


def _late_preview(order: RentalOrder, item: RentalItem, returned_at: datetime) -> LateFeePreview:
    fee = late_fee(
        item.daily_rate_snapshot_won, order.due_at, returned_at,
        item.grace_minutes_snapshot, item.late_multiplier_snapshot, item.rate_rule_snapshot,
    )
    head = (
        f"약정 반납 {format_seoul(order.due_at)}, 실제 반납 {format_seoul(returned_at)}, "
        f"연체료 없이 기다려 주는 시간 {item.grace_minutes_snapshot}분"
    )
    if not fee.is_late:
        basis = f"{head} → 연체 없음"
    else:
        basis = f"{head} → " + late_fee_basis(
            item.daily_rate_snapshot_won, item.late_multiplier_snapshot, item.rate_rule_snapshot, fee
        )
    return LateFeePreview(
        item_id=item.id, asset_code=item.asset_code, due_at=order.due_at,
        returned_at=returned_at, grace_minutes=item.grace_minutes_snapshot, late=fee,
        basis=basis,
    )


def _resolve_time(value: datetime | None, now: datetime) -> datetime:
    if value is None:
        return now
    if not isinstance(value, datetime):
        raise ValidationError("returned_at", "반납 시각을 입력해주세요.")
    try:
        ensure_aware(value)
    except ValueError:
        raise ValidationError("returned_at", "반납 시각에 시간대 정보가 없습니다.") from None
    return to_utc(value)


def _check_return_time(order: RentalOrder, returned_at: datetime, now: datetime) -> None:
    if returned_at > now:
        raise ValidationError("returned_at", "반납 시각은 현재 시각 이후일 수 없습니다.")
    if order.actual_checkout_at is not None and returned_at < order.actual_checkout_at:
        raise ValidationError(
            "returned_at",
            f"반납 시각은 출고 시각({format_seoul(order.actual_checkout_at)})보다 빠를 수 없습니다.",
        )


class ReturnService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def returnable_items(self, order_id: int) -> list[RentalItem]:
        """반납 화면에 나열할 `OUT` 품목."""
        return [i for i in self._get_order(order_id).items if i.status == ItemStatus.OUT]

    def preview_late_fee(
        self, order_id: int, item_id: int, returned_at: datetime | None = None
    ) -> LateFeePreview:
        now = current_time(self._clock)
        order = self._get_order(order_id)
        item = self._find_item(order, item_id)
        if item.status != ItemStatus.OUT:
            raise InvalidStateError(f"{item.asset_code}: 대여 중인 장비가 아닙니다.")
        at = _resolve_time(returned_at, now)
        _check_return_time(order, at, now)
        return _late_preview(order, item, at)

    def return_items(self, order_id: int, inputs: Sequence[ReturnItemInput]) -> ReturnResult:
        inputs = tuple(inputs)
        if not inputs:
            raise ValidationError("items", "반납할 장비를 1개 이상 선택해주세요.")
        ids = [i.item_id for i in inputs]
        if len(set(ids)) != len(ids):
            raise ValidationError("items", "같은 장비를 한 번에 두 번 반납할 수 없습니다.")
        now = current_time(self._clock)

        cleaned = []
        for data in inputs:
            try:
                condition = ReturnCondition(data.condition)
            except ValueError:
                raise ValidationError("condition", "반납 상태가 올바르지 않습니다.") from None
            cleaned.append((
                data,
                _resolve_time(data.returned_at, now),
                condition,
                validate_won("damage_fee_won", data.damage_fee_won, label="파손비"),
                clean_optional(data.note, "note", "반납 메모", 1000),
                clean_optional(data.maintenance_reason, "maintenance_reason", "정비 사유", 500),
            ))

        lines: list[ReturnedLine] = []
        blocks: list[MaintenanceBlock] = []
        affected: dict[tuple[int, int], ConflictInfo] = {}
        with transaction(self._conn):
            order = self._get_order(order_id)
            if order.status != OrderStatus.IN_RENTAL:
                raise InvalidStateError(self._not_returnable_message(order.status))

            for data, returned_at, condition, damage, note, reason in cleaned:
                item = self._find_item(order, data.item_id)
                if item.status == ItemStatus.RETURNED:
                    raise InvalidStateError(
                        f"{item.asset_code}: 이미 반납 처리된 장비입니다. 다시 반납할 수 없습니다."
                    )
                if item.status != ItemStatus.OUT:
                    raise InvalidStateError(f"{item.asset_code}: 대여 중인 장비가 아닙니다.")
                _check_return_time(order, returned_at, now)

                preview = _late_preview(order, item, returned_at)
                repo.mark_item_returned(
                    self._conn, item.id, returned_at=returned_at, condition=condition, note=note
                )
                if preview.late.late_fee_won > 0:
                    repo.insert_charge(
                        self._conn, order_id=order.id, rental_item_id=item.id,
                        kind=ChargeKind.LATE, amount_won=preview.late.late_fee_won,
                        description=preview.basis[:500], now=now,
                    )
                if damage > 0:
                    repo.insert_charge(
                        self._conn, order_id=order.id, rental_item_id=item.id,
                        kind=ChargeKind.DAMAGE, amount_won=damage,
                        description=f"{item.asset_code} 파손비", now=now,
                    )

                # 반납 시각 + 점검 버퍼 안에 시작하는 후속 확정 예약 경고
                buffer_end = returned_at + timedelta(minutes=item.turnaround_buffer_snapshot_minutes)
                for c in affected_confirmed_reservations(
                    self._conn, item.asset_id, returned_at, buffer_end
                ):
                    affected[(c.order_id, c.asset_id)] = c

                if condition == ReturnCondition.MAINTENANCE_REQUIRED:
                    block = insert_block_in_txn(
                        self._conn, item.asset_id, returned_at, None,
                        reason or f"반납 시 정비 필요 ({order.order_no})", now,
                    )
                    blocks.append(block.block)
                    for c in block.affected_reservations:
                        affected[(c.order_id, c.asset_id)] = c

                repo.insert_audit(
                    self._conn, entity_type="rental_item", entity_id=item.id, action="RETURN",
                    description=f"{order.order_no} {item.asset_code} {condition.value}", now=now,
                )
                lines.append(ReturnedLine(
                    item_id=item.id, asset_code=item.asset_code,
                    late_fee_won=preview.late.late_fee_won, damage_fee_won=damage,
                    basis=preview.basis,
                ))

            remaining = [
                i for i in repo.get_items(self._conn, order.id) if i.status == ItemStatus.OUT
            ]
            if not remaining:
                repo.set_order_status(self._conn, order.id, OrderStatus.COMPLETED, now=now)
                repo.insert_audit(
                    self._conn, entity_type="rental_order", entity_id=order.id,
                    action="COMPLETE", description=order.order_no, now=now,
                )

        return ReturnResult(
            order=self._get_order(order_id),
            lines=tuple(lines),
            maintenance_blocks=tuple(blocks),
            affected_reservations=tuple(
                sorted(affected.values(), key=lambda c: (c.start_at, c.order_no or ""))
            ),
        )

    def _get_order(self, order_id: int) -> RentalOrder:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        return order

    @staticmethod
    def _find_item(order: RentalOrder, item_id: int) -> RentalItem:
        for item in order.items:
            if item.id == item_id:
                return item
        raise ValidationError("items", "이 예약에 포함된 장비가 아닙니다.")

    @staticmethod
    def _not_returnable_message(status: OrderStatus) -> str:
        if status == OrderStatus.COMPLETED:
            return "모든 장비가 이미 반납된 예약입니다."
        if status == OrderStatus.CANCELED:
            return "취소된 예약은 반납할 수 없습니다."
        return "출고되지 않은 예약은 반납할 수 없습니다."
