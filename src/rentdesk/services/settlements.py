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

# 상태별로 기록할 수 있는 금전 거래 (USABILITY_PLAN U5: 출고 전 선입금, 취소 후 환급·수수료).
# 임시 저장(DRAFT)은 장비를 확보하지 않았으므로 돈을 받지 않는다.
MONEY_KINDS_BY_STATUS: dict[OrderStatus, frozenset[MoneyKind]] = {
    OrderStatus.CONFIRMED: frozenset({
        MoneyKind.RENTAL_RECEIPT, MoneyKind.RENTAL_REFUND, MoneyKind.DEPOSIT_IN, MoneyKind.DEPOSIT_OUT,
    }),
    OrderStatus.IN_RENTAL: frozenset(MoneyKind),
    OrderStatus.COMPLETED: frozenset(MoneyKind),
    OrderStatus.CANCELED: frozenset({
        MoneyKind.RENTAL_RECEIPT, MoneyKind.RENTAL_REFUND, MoneyKind.DEPOSIT_OUT,
        MoneyKind.DEPOSIT_APPLIED, MoneyKind.DEPOSIT_APPLICATION_REVERSAL,
    }),
}
MONEY_ALLOWED_STATUSES = frozenset(MONEY_KINDS_BY_STATUS)
# 정산 대상: 출고 이후 + 취소(돈이 오간 경우). 출고 전 선입금은 아직 정산 대상이 아니다.
SETTLEMENT_STATUSES = frozenset({OrderStatus.IN_RENTAL, OrderStatus.COMPLETED, OrderStatus.CANCELED})
# '정산 마무리'를 쓸 수 있는 상태(더 이상 청구가 생기지 않음)
FINAL_STATUSES = frozenset({OrderStatus.COMPLETED, OrderStatus.CANCELED})

_NOT_ALLOWED_MESSAGES = {
    OrderStatus.DRAFT: "임시 저장 예약에는 돈을 기록할 수 없습니다. 예약을 확정한 뒤 기록해주세요.",
    OrderStatus.CONFIRMED: "출고 전에는 대여료 선입금·보증금 수령과 그 환급·반환만 기록할 수 있습니다.",
    OrderStatus.CANCELED: "취소된 예약에는 환급·보증금 반환·보증금에서 차감·수수료 수금만 기록할 수 있습니다.",
}


def allowed_money_kinds(status: OrderStatus) -> frozenset[MoneyKind]:
    return MONEY_KINDS_BY_STATUS.get(status, frozenset())


def check_money_allowed(order: RentalOrder, kind: MoneyKind) -> None:
    if kind not in allowed_money_kinds(order.status):
        raise InvalidStateError(
            _NOT_ALLOWED_MESSAGES.get(order.status, "이 예약에는 이 거래를 기록할 수 없습니다.")
        )


def has_ledger_activity(summary: st.SettlementSummary) -> bool:
    return summary != st.SettlementSummary()


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


@dataclass(frozen=True)
class SettlementRow:
    """정산 내역 목록 행."""

    order: RentalOrder
    customer_name: str
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
                "payment_method", "보증금 차감/차감 취소는 결제수단을 기록하지 않습니다."
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
    check_money_allowed(order, clean.kind)
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
        check_money_allowed(order, clean.kind)
        summary = load_summary(self._conn, order_id)
        amount = st.validate_money_entry(summary, clean.kind, clean.amount_won)
        return st.apply_money(summary, clean.kind, amount)

    def record(self, order_id: int, data: MoneyInput) -> MoneyEntry:
        now = current_time(self._clock)
        with transaction(self._conn):
            order = self._get_order(order_id)
            entry_id = record_in_txn(self._conn, order, data, now)
        return repo.get_money_entry(self._conn, entry_id)

    def final_plan(self, order_id: int) -> st.FinalSettlementPlan:
        """반납 완료·취소 예약의 '정산 마무리' 계획(저장하지 않음)."""
        order = self._get_order(order_id)
        if order.status not in FINAL_STATUSES:
            raise InvalidStateError("모든 장비가 반납되었거나 취소된 예약만 정산을 마무리할 수 있습니다.")
        return st.plan_final_settlement(load_summary(self._conn, order_id))

    def finalize(self, order_id: int, expected: st.FinalSettlementPlan, *,
                 payment_method: PaymentMethod | None = None,
                 collect_remaining: bool = False, note: str | None = None) -> st.SettlementSummary:
        """계획대로 거래를 한 트랜잭션으로 기록한다.

        화면에서 본 계획(`expected`)과 저장 직전 다시 계산한 계획이 다르면(다른 창에서 거래 기록 등)
        아무것도 기록하지 않고 거부한다. collect_remaining이면 보증금으로 모자란 금액을 받은 것으로 기록한다.
        결제수단은 실제 돈이 오가는 거래(환급·반환·수금)에만 붙인다.
        """
        now = current_time(self._clock)
        with transaction(self._conn):
            order = self._get_order(order_id)
            if order.status not in FINAL_STATUSES:
                raise InvalidStateError("모든 장비가 반납되었거나 취소된 예약만 정산을 마무리할 수 있습니다.")
            plan = st.plan_final_settlement(load_summary(self._conn, order_id))
            if plan != expected:
                raise InvalidStateError("정산 내용이 바뀌었습니다. 화면을 다시 확인한 뒤 진행해주세요.")
            steps = list(plan.steps)
            if collect_remaining and plan.remaining_due_won:
                steps.append(st.SettlementStep(MoneyKind.RENTAL_RECEIPT, plan.remaining_due_won))
            if not steps:
                raise InvalidStateError("정산할 금액이 없습니다.")
            for step in steps:
                method = None if step.kind in st.INTERNAL_KINDS else payment_method
                record_in_txn(self._conn, order, MoneyInput(
                    kind=step.kind, amount_won=step.amount_won, payment_method=method,
                    note=note or "정산 마무리",
                ), now)
            return load_summary(self._conn, order_id)

    def allowed_kinds(self, order_id: int) -> frozenset[MoneyKind]:
        """이 예약에 지금 기록할 수 있는 거래 종류(화면의 유형 목록용)."""
        return allowed_money_kinds(self._get_order(order_id).status)

    def unsettled_orders(self) -> list[UnsettledOrder]:
        """출고 이후·취소 예약 중 미수금 또는 보증금 잔액이 남은 예약(대시보드 '미정산')."""
        orders = repo.list_orders(self._conn, statuses=sorted(SETTLEMENT_STATUSES))
        totals = repo.ledger_totals_by_order(self._conn, [o.id for o in orders])
        result = []
        for order in orders:
            summary = st.summarize(*totals[order.id])
            if not summary.is_settled:
                result.append(UnsettledOrder(order.id, order.order_no, order.status, summary))
        return result

    def list_rows(self, *, unsettled_only: bool = True) -> list[SettlementRow]:
        """정산 현황(기본: 미정산만). 시작 시각 최신순.

        출고 전 선입금이 있는 확정 예약은 전체 보기에서만 보인다(아직 정산 대상이 아님).
        돈이 오가지 않은 취소 예약은 제외한다.
        """
        statuses = SETTLEMENT_STATUSES | {OrderStatus.CONFIRMED}
        orders = repo.list_orders(self._conn, statuses=sorted(statuses))
        totals = repo.ledger_totals_by_order(self._conn, [o.id for o in orders])
        names = repo.customer_names(self._conn, (o.customer_id for o in orders))
        rows = []
        for order in orders:
            summary = st.summarize(*totals[order.id])
            if order.status in (OrderStatus.CONFIRMED, OrderStatus.CANCELED) and not has_ledger_activity(summary):
                continue
            if unsettled_only and (summary.is_settled or order.status == OrderStatus.CONFIRMED):
                continue
            rows.append(SettlementRow(order, names.get(order.customer_id, ""), summary))
        return rows

    def _get_order(self, order_id: int) -> RentalOrder:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        return order
