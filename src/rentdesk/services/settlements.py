"""SettlementService: 청구 합계·미수·보증금 수령/반환/충당, 금전 원장 (PRD P0, 7.4, S-07).

- 금전 원장은 추가만 한다. 잘못된 입력은 역거래(`related_entry_id`)로 상쇄한다.
- 검증은 `BEGIN IMMEDIATE` 트랜잭션 안에서 최신 원장 기준으로 다시 수행한다.
- 결제수단은 기록값일 뿐 결제를 수행하지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rentdesk.domain import settlement as st
from rentdesk.domain.errors import InvalidStateError, NotFoundError, ValidationError
from rentdesk.domain.models import (
    ChargeLine,
    MoneyEntry,
    MoneyKind,
    OrderStatus,
    PaymentMethod,
    RentalOrder,
)
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import Clock, clean_optional, current_time, default_clock

# 출고 전 수금/예약금은 P0 범위 밖(PRD 6.1). 출고 이후 주문만 금전 거래를 받는다.
MONEY_ALLOWED_STATUSES = frozenset({OrderStatus.IN_RENTAL, OrderStatus.COMPLETED})


@dataclass(frozen=True)
class MoneyInput:
    kind: MoneyKind
    amount_won: int
    payment_method: PaymentMethod | None = None
    note: str | None = None
    related_entry_id: int | None = None


@dataclass(frozen=True)
class OrderSettlement:
    order: RentalOrder
    summary: st.SettlementSummary
    charges: tuple[ChargeLine, ...]
    entries: tuple[MoneyEntry, ...]


@dataclass(frozen=True)
class UnsettledOrder:
    order_id: int
    order_no: str
    status: OrderStatus
    summary: st.SettlementSummary


def load_summary(conn: sqlite3.Connection, order_id: int) -> st.SettlementSummary:
    totals = repo.ledger_totals_by_order(conn, [order_id])[order_id]
    return st.summarize(*totals)


def _clean_money_input(data: MoneyInput) -> MoneyInput:
    try:
        kind = MoneyKind(data.kind)
    except ValueError:
        raise ValidationError("kind", "거래 종류가 올바르지 않습니다.") from None
    method = data.payment_method
    if method is not None:
        try:
            method = PaymentMethod(method)
        except ValueError:
            raise ValidationError("payment_method", "결제수단이 올바르지 않습니다.") from None
        if kind in st.INTERNAL_KINDS:
            raise ValidationError(
                "payment_method", "보증금 충당/충당 취소는 결제수단을 기록하지 않습니다."
            )
    related = data.related_entry_id
    if related is not None and (isinstance(related, bool) or not isinstance(related, int)):
        raise ValidationError("related_entry_id", "정정 대상 거래가 올바르지 않습니다.")
    return MoneyInput(
        kind=kind,
        amount_won=data.amount_won,
        payment_method=method,
        note=clean_optional(data.note, "note", "메모", 500),
        related_entry_id=related,
    )


def record_in_txn(
    conn: sqlite3.Connection, order: RentalOrder, data: MoneyInput, now: datetime
) -> int:
    """호출자의 트랜잭션 안에서 금전 거래 1건을 검증·기록(출고 처리에서도 재사용)."""
    clean = _clean_money_input(data)
    if order.status not in MONEY_ALLOWED_STATUSES:
        raise InvalidStateError("출고 이후의 예약에만 수금·보증금 거래를 기록할 수 있습니다.")
    related = None
    if clean.related_entry_id is not None:
        original = repo.get_money_entry(conn, clean.related_entry_id)
        if original is None or original.order_id != order.id:
            raise ValidationError("related_entry_id", "같은 예약의 거래만 정정할 수 있습니다.")
        related = st.RelatedEntry(
            kind=original.kind,
            amount_won=original.amount_won,
            already_reversed_won=repo.reversed_total_for_entry(conn, original.id),
        )
    amount = st.validate_money_entry(
        load_summary(conn, order.id), clean.kind, clean.amount_won, related=related
    )
    entry_id = repo.insert_money_entry(
        conn, order_id=order.id, kind=clean.kind, amount_won=amount,
        payment_method=clean.payment_method, note=clean.note,
        related_entry_id=clean.related_entry_id, now=now,
    )
    repo.insert_audit(
        conn, entity_type="money_entry", entity_id=entry_id, action=clean.kind.value,
        description=f"{order.order_no} {amount}", now=now,
    )
    return entry_id


class SettlementService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def get_settlement(self, order_id: int) -> OrderSettlement:
        order = self._get_order(order_id)
        return OrderSettlement(
            order=order,
            summary=load_summary(self._conn, order_id),
            charges=tuple(repo.list_charges(self._conn, order_id)),
            entries=tuple(repo.list_money_entries(self._conn, order_id)),
        )

    def summary(self, order_id: int) -> st.SettlementSummary:
        self._get_order(order_id)
        return load_summary(self._conn, order_id)

    def preview(self, order_id: int, data: MoneyInput) -> st.SettlementSummary:
        """저장 전 미리보기: 검증 후 반영 결과를 돌려준다(원장 변경 없음)."""
        order = self._get_order(order_id)
        clean = _clean_money_input(data)
        if order.status not in MONEY_ALLOWED_STATUSES:
            raise InvalidStateError("출고 이후의 예약에만 수금·보증금 거래를 기록할 수 있습니다.")
        summary = load_summary(self._conn, order_id)
        amount = st.validate_money_entry(summary, clean.kind, clean.amount_won)
        return st.apply_money(summary, clean.kind, amount)

    def record(self, order_id: int, data: MoneyInput) -> MoneyEntry:
        now = current_time(self._clock)
        with transaction(self._conn):
            order = self._get_order(order_id)
            entry_id = record_in_txn(self._conn, order, data, now)
        return repo.get_money_entry(self._conn, entry_id)

    def unsettled_orders(self) -> list[UnsettledOrder]:
        """출고 이후 주문 중 미수금 또는 보증금 잔액이 남은 주문(대시보드 '미정산')."""
        orders = repo.list_orders(self._conn, statuses=sorted(MONEY_ALLOWED_STATUSES))
        totals = repo.ledger_totals_by_order(self._conn, [o.id for o in orders])
        result = []
        for order in orders:
            summary = st.summarize(*totals[order.id])
            if not summary.is_settled:
                result.append(UnsettledOrder(order.id, order.order_no, order.status, summary))
        return result

    def _get_order(self, order_id: int) -> RentalOrder:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        return order
