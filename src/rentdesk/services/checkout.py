"""CheckoutService: 원자적 출고 및 최초 대여료 청구 생성 (PRD S-05, 6.1, 7.2).

- `CONFIRMED` 주문의 모든 `RESERVED` 품목을 한 번에 `OUT`으로 바꾼다(부분 출고는 P1).
- 출고 시각은 예약 시작 시각 이후, 약정 반납 시각 전, 현재 시각 이전이어야 한다.
- 품목별 `RENTAL`(스냅샷 일 대여료 × 약정 대여일수)과 주문 할인 `DISCOUNT`(음수)를
  한 번만 청구한다. 재출고 시도는 상태 검사 + DB unique index로 이중 차단된다(T-07).
- 선택 입력한 대여료 수금/보증금 수령은 같은 트랜잭션에서 금전 원장에 기록한다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rentdesk.domain.errors import InvalidStateError, NotFoundError, ValidationError
from rentdesk.domain.models import (
    ChargeKind,
    ItemStatus,
    MoneyKind,
    OrderStatus,
    PaymentMethod,
    RentalOrder,
)
from rentdesk.domain.pricing import OrderQuote, period_fee, validate_won
from rentdesk.domain.timeutil import ensure_aware, format_seoul, to_utc
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import Clock, clean_optional, current_time, default_clock
from rentdesk.services.reservations import quote_items
from rentdesk.services.settlements import MoneyInput, record_in_txn


@dataclass(frozen=True)
class CheckoutInput:
    checkout_at: datetime | None = None  # None = 현재 시각
    note: str | None = None  # 장비 외관/구성품 메모
    rental_receipt_won: int = 0  # 선택: 받은 대여료
    deposit_in_won: int = 0  # 선택: 받은 보증금
    payment_method: PaymentMethod | None = None


@dataclass(frozen=True)
class CheckoutPreview:
    """출고 화면 표시용: 받을 대여료·보증금과 출고 불가 사유."""

    quote: OrderQuote
    blockers: tuple[str, ...]

    @property
    def can_checkout(self) -> bool:
        return not self.blockers


def _checkout_blockers(
    conn: sqlite3.Connection, order: RentalOrder, checkout_at: datetime, now: datetime
) -> list[str]:
    if order.status == OrderStatus.IN_RENTAL or order.status == OrderStatus.COMPLETED:
        return ["이미 출고 처리된 예약입니다."]
    if order.status == OrderStatus.DRAFT:
        return ["임시저장 예약은 먼저 확정해야 출고할 수 있습니다."]
    if order.status != OrderStatus.CONFIRMED:
        return ["취소된 예약은 출고할 수 없습니다."]

    blockers = []
    if checkout_at > now:
        blockers.append("출고 시각은 현재 시각 이후일 수 없습니다.")
    if checkout_at < order.start_at:
        blockers.append(
            f"예약 시작 시각({format_seoul(order.start_at)}) 전에는 출고할 수 없습니다. "
            "조기 출고가 필요하면 예약 기간을 먼저 수정해주세요."
        )
    if checkout_at >= order.due_at:
        blockers.append(
            f"약정 반납 시각({format_seoul(order.due_at)})이 지나 출고할 수 없습니다. "
            "예약 기간을 먼저 수정해주세요."
        )

    items = [i for i in order.items if i.status == ItemStatus.RESERVED]
    if not items:
        blockers.append("출고할 장비가 없습니다.")
    asset_ids = [i.asset_id for i in items]
    for row in repo.out_items_in_other_orders(conn, asset_ids, order.id):
        blockers.append(
            f"{row['asset_code']}: 예약 {row['order_no']}에서 아직 반납되지 않았습니다."
        )
    codes = {i.asset_id: i.asset_code for i in items}
    for block in repo.blocks_for_assets(conn, asset_ids):
        if block.start_at <= checkout_at and (block.end_at is None or checkout_at < block.end_at):
            blockers.append(f"{codes[block.asset_id]}: 정비/사용 불가 상태입니다.")
    return blockers


class CheckoutService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def preview(self, order_id: int, checkout_at: datetime | None = None) -> CheckoutPreview:
        now = current_time(self._clock)
        order = self._get_order(order_id)
        at = self._resolve_time(checkout_at, now)
        items = [i for i in order.items if i.status != ItemStatus.CANCELED]
        quote = quote_items(items, order.start_at, order.due_at, order.discount_won)
        return CheckoutPreview(
            quote=quote, blockers=tuple(_checkout_blockers(self._conn, order, at, now))
        )

    def checkout(self, order_id: int, data: CheckoutInput | None = None) -> RentalOrder:
        data = data or CheckoutInput()
        now = current_time(self._clock)
        checkout_at = self._resolve_time(data.checkout_at, now)
        note = clean_optional(data.note, "note", "출고 메모", 1000)
        receipt = validate_won("rental_receipt_won", data.rental_receipt_won, label="받은 대여료")
        deposit = validate_won("deposit_in_won", data.deposit_in_won, label="받은 보증금")

        with transaction(self._conn):
            order = self._get_order(order_id)
            blockers = _checkout_blockers(self._conn, order, checkout_at, now)
            if blockers:
                raise InvalidStateError("\n".join(blockers))

            items = [i for i in order.items if i.status == ItemStatus.RESERVED]
            # 할인 한도 재검증(할인 ≤ 기본 대여료)
            quote_items(items, order.start_at, order.due_at, order.discount_won)
            for item in items:
                fee = period_fee(
                    item.daily_rate_snapshot_won, order.start_at, order.due_at, item.rate_rule_snapshot
                )
                repo.insert_charge(
                    self._conn, order_id=order.id, rental_item_id=item.id,
                    kind=ChargeKind.RENTAL, amount_won=fee.amount_won,
                    description=f"{item.asset_code} {fee.basis}"[:500],
                    now=now,
                )
            if order.discount_won > 0:
                repo.insert_charge(
                    self._conn, order_id=order.id, rental_item_id=None,
                    kind=ChargeKind.DISCOUNT, amount_won=-order.discount_won,
                    description="예약 할인", now=now,
                )
            repo.set_items_status_for_order(
                self._conn, order.id, ItemStatus.RESERVED, ItemStatus.OUT
            )
            repo.mark_order_checked_out(
                self._conn, order.id, checkout_at=checkout_at, checkout_note=note, now=now
            )
            repo.insert_audit(
                self._conn, entity_type="rental_order", entity_id=order.id, action="CHECKOUT",
                description=order.order_no, now=now,
            )

            checked_out = repo.get_order(self._conn, order.id)
            if receipt > 0:
                record_in_txn(
                    self._conn, checked_out,
                    MoneyInput(MoneyKind.RENTAL_RECEIPT, receipt, data.payment_method, "출고 시 수금"),
                    now,
                )
            if deposit > 0:
                record_in_txn(
                    self._conn, checked_out,
                    MoneyInput(MoneyKind.DEPOSIT_IN, deposit, data.payment_method, "출고 시 보증금 수령"),
                    now,
                )
        return self._get_order(order_id)

    @staticmethod
    def _resolve_time(value: datetime | None, now: datetime) -> datetime:
        if value is None:
            return now
        if not isinstance(value, datetime):
            raise ValidationError("checkout_at", "출고 시각을 입력해주세요.")
        try:
            ensure_aware(value)
        except ValueError:
            raise ValidationError("checkout_at", "출고 시각에 시간대 정보가 없습니다.") from None
        return to_utc(value)

    def _get_order(self, order_id: int) -> RentalOrder:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        return order
