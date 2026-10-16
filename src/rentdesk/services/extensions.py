"""ExtensionService: 대여 연장 (USABILITY_PLAN U6).

- 대상: 대여 중(`IN_RENTAL`) 예약의 아직 반납하지 않은 장비 전체. 일부 장비만 연장은 범위 밖
  (먼저 일부 반납한 뒤 연장).
- 하나의 트랜잭션에서 ① 늘어난 기간의 충돌 재검사 ② 반납 예정 변경 ③ 연장 대여료 청구
  ④ 연장 이력 기록을 함께 처리한다.
- 연장 대여료는 장비별 RENTAL 1건 제약 때문에 주문 단위 '조정'(ADJUSTMENT) 청구로 남긴다.
- 이미 연체 중이면 원래 반납 예정 이후 시간은 연체료 대신 연장 요금으로 대체된다
  (새 반납 예정이 지금보다 뒤여야 하므로 반납 시 연체료는 새 반납 예정 기준으로 계산).
- 연장 취소는 지원하지 않는다. 잘못 연장했으면 조정 청구로 바로잡는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rentdesk.domain.display import format_dt
from rentdesk.domain.errors import (
    ConflictError,
    ConflictInfo,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from rentdesk.domain.models import (
    ChargeKind,
    ItemStatus,
    OrderExtension,
    OrderStatus,
    RentalOrder,
)
from rentdesk.domain.pricing import ExtensionQuote, quote_extension
from rentdesk.domain.timeutil import ensure_aware, to_utc
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.availability import describe_conflict, find_conflicts
from rentdesk.services.common import Clock, clean_optional, current_time, default_clock


@dataclass(frozen=True)
class ExtensionPreview:
    order: RentalOrder
    new_due_at: datetime
    asset_codes: tuple[str, ...]
    quote: ExtensionQuote
    conflicts: tuple[ConflictInfo, ...]
    overdue_now: bool  # 지금 연체 중인지(연장 요금으로 대체됨을 안내)

    @property
    def can_extend(self) -> bool:
        return not self.conflicts


class ExtensionService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def preview(self, order_id: int, new_due_at: datetime, discount_won: object = 0) -> ExtensionPreview:
        """저장 없이 연장 요금과 충돌을 계산한다. 입력 오류는 ValidationError/InvalidStateError."""
        now = current_time(self._clock)
        order = self._get_order(order_id)
        return self._preview(order, new_due_at, discount_won, now)

    def extend(self, order_id: int, new_due_at: datetime, *, discount_won: object = 0,
               note: str | None = None) -> RentalOrder:
        clean_note = clean_optional(note, "note", "연장 메모", 500)
        now = current_time(self._clock)
        with transaction(self._conn):
            order = self._get_order(order_id)
            p = self._preview(order, new_due_at, discount_won, now)
            if p.conflicts:
                lines = "\n".join(f"- {describe_conflict(c)}" for c in p.conflicts)
                raise ConflictError(f"늘린 기간에 다른 일정이 있어 연장할 수 없습니다.\n{lines}", p.conflicts)
            new_due = to_utc(p.new_due_at)
            codes = ", ".join(p.asset_codes)
            if p.quote.amount_won:
                desc = (f"연장 +{p.quote.added_days}일 ({codes}) "
                        f"{format_dt(order.due_at)} → {format_dt(new_due)}")
                if p.quote.discount_won:
                    desc += f", 연장 할인 {p.quote.discount_won:,}원"
                repo.insert_charge(
                    self._conn, order_id=order.id, rental_item_id=None, kind=ChargeKind.ADJUSTMENT,
                    amount_won=p.quote.amount_won, description=desc[:500], now=now,
                )
            repo.insert_extension(
                self._conn, order_id=order.id, previous_due_at=order.due_at, new_due_at=new_due,
                added_days=p.quote.added_days, amount_won=p.quote.amount_won, note=clean_note, now=now,
            )
            repo.set_order_due(self._conn, order.id, new_due, now=now)
            repo.insert_audit(
                self._conn, entity_type="rental_order", entity_id=order.id, action="EXTEND",
                description=f"{order.order_no} +{p.quote.added_days}일 {p.quote.amount_won}", now=now,
            )
        return self._get_order(order_id)

    def list_extensions(self, order_id: int) -> list[OrderExtension]:
        self._get_order(order_id)
        return repo.list_extensions(self._conn, order_id)

    # ------------------------------------------------------------------ internals

    def _preview(self, order: RentalOrder, new_due_at: datetime, discount_won: object,
                 now: datetime) -> ExtensionPreview:
        if order.status != OrderStatus.IN_RENTAL:
            raise InvalidStateError("대여 중인 예약만 연장할 수 있습니다.")
        if not isinstance(new_due_at, datetime):
            raise ValidationError("new_due_at", "새 반납 예정 일시를 입력해주세요.")
        new_due = ensure_aware(new_due_at)
        if not new_due > now:
            raise ValidationError("new_due_at", "새 반납 예정 일시는 지금보다 뒤여야 합니다.")
        out_items = [i for i in order.items if i.status == ItemStatus.OUT]
        if not out_items:
            raise InvalidStateError("연장할 대여 중 장비가 없습니다.")
        quote = quote_extension(
            [i.daily_rate_snapshot_won for i in out_items], order.start_at, order.due_at, new_due,
            discount_won,
        )
        conflicts = find_conflicts(
            self._conn, [i.asset_id for i in out_items], order.start_at, new_due,
            {i.asset_id: i.turnaround_buffer_snapshot_minutes for i in out_items}, now,
            exclude_order_id=order.id,
        )
        return ExtensionPreview(
            order=order, new_due_at=new_due, asset_codes=tuple(i.asset_code for i in out_items),
            quote=quote, conflicts=tuple(conflicts), overdue_now=now > order.due_at,
        )

    def _get_order(self, order_id: int) -> RentalOrder:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        return order
