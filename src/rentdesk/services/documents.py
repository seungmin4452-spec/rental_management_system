"""대여확인서·반납확인서·견적서 내용 만들기 (MULTI_INDUSTRY_PLAN R6, 구 U8).

예약·정산 원장·설정에서 `domain.documents.Document`를 채운다. 저장·인쇄는 화면 계층이 한다.
읽기 전용이며 DB를 바꾸지 않는다(문서 출력 기록도 남기지 않는다).

- 견적서: 임시 저장·예약확정. 품목 스냅샷 기준 예상 금액 + 유효기간·보증금 안내.
- 대여확인서: 예약확정(출고 전, 예상 금액·빈 체크 칸) / 대여 중·반납 완료(출고 때 청구한 금액·출고 확인 결과).
- 반납확인서: 장비가 1개 이상 반납된 예약. 반납 시각·상태·연체료·추가 비용·정산 결과.

개인정보: 고객은 이름과 연락처만 넣는다(이메일·고객 메모·예약 내부 메모는 넣지 않는다).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import timedelta

from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.documents import (
    DocAmount,
    DocBusiness,
    DocChecklist,
    Document,
    DocumentKind,
    DocTable,
)
from rentdesk.domain.errors import InvalidStateError, NotFoundError
from rentdesk.domain.extra_charges import short_text
from rentdesk.domain.models import (
    ORDER_STATUS_LABELS,
    ChargeKind,
    ItemStatus,
    OrderStatus,
    RentalItem,
    RentalOrder,
    ReturnCondition,
)
from rentdesk.domain.settlement import charge_kind_label
from rentdesk.domain.timeutil import SEOUL
from rentdesk.persistence import repositories as repo
from rentdesk.services.common import Clock, current_time, default_clock
from rentdesk.services.reservations import quote_for_order
from rentdesk.services.settings import read_business_info, read_document_settings
from rentdesk.services.settlements import load_summary

CONDITION_LABELS = {ReturnCondition.GOOD: "정상", ReturnCondition.MAINTENANCE_REQUIRED: "정비 필요"}
SIGNERS = ("고객", "사업자")

_AVAILABLE_STATUSES = {
    DocumentKind.QUOTE: frozenset({OrderStatus.DRAFT, OrderStatus.CONFIRMED}),
    DocumentKind.RENTAL: frozenset({OrderStatus.CONFIRMED, OrderStatus.IN_RENTAL, OrderStatus.COMPLETED}),
    DocumentKind.RETURN: frozenset({OrderStatus.IN_RENTAL, OrderStatus.COMPLETED}),
}
_UNAVAILABLE = {
    DocumentKind.QUOTE: "견적서는 임시 저장 또는 예약확정 상태의 예약만 출력할 수 있습니다.",
    DocumentKind.RENTAL: "대여확인서는 예약확정·대여 중·반납 완료 상태의 예약만 출력할 수 있습니다.",
    DocumentKind.RETURN: "반납확인서는 장비가 1개 이상 반납된 예약만 출력할 수 있습니다.",
}


def document_available(order: RentalOrder, kind: DocumentKind) -> bool:
    if order.status not in _AVAILABLE_STATUSES[kind]:
        return False
    if kind == DocumentKind.RETURN:
        return any(i.status == ItemStatus.RETURNED for i in order.items)
    return True


def _active_items(order: RentalOrder) -> list[RentalItem]:
    return [i for i in order.items if i.status != ItemStatus.CANCELED]


def _without_code(description: str | None, code: str) -> str:
    """"CAM-1 주 단가 적용: 1주" → "주 단가 적용: 1주"."""
    text = description or ""
    return text[len(code) + 1:] if text.startswith(f"{code} ") else text


class DocumentService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def available(self, order_id: int) -> list[DocumentKind]:
        """이 예약에서 지금 출력할 수 있는 문서(견적서 → 대여확인서 → 반납확인서 순)."""
        order = self._get_order(order_id)
        return [k for k in (DocumentKind.QUOTE, DocumentKind.RENTAL, DocumentKind.RETURN)
                if document_available(order, k)]

    def build(self, order_id: int, kind: DocumentKind) -> Document:
        order = self._get_order(order_id)
        kind = DocumentKind(kind)
        if not document_available(order, kind):
            raise InvalidStateError(_UNAVAILABLE[kind])
        if kind == DocumentKind.QUOTE:
            return self._quote(order)
        if kind == DocumentKind.RENTAL:
            return self._rental(order)
        return self._return(order)

    def quote(self, order_id: int) -> Document:
        return self.build(order_id, DocumentKind.QUOTE)

    def rental_confirmation(self, order_id: int) -> Document:
        return self.build(order_id, DocumentKind.RENTAL)

    def return_confirmation(self, order_id: int) -> Document:
        return self.build(order_id, DocumentKind.RETURN)

    # ------------------------------------------------------------------ 공통

    def _get_order(self, order_id: int) -> RentalOrder:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        return order

    def _business(self) -> DocBusiness:
        b = read_business_info(self._conn)
        return DocBusiness(b.name, b.phone, b.address, b.registration_no)

    def _customer_info(self, order: RentalOrder, *, honorific: str = "") -> list[tuple[str, str]]:
        customer = repo.get_customer(self._conn, order.customer_id)
        name = customer.name if customer else ""
        return [("고객", f"{name} {honorific}".strip()), ("연락처", (customer.phone or "") if customer else "")]

    def _asset_names(self, items: list[RentalItem]) -> dict[int, str]:
        assets = repo.get_assets(self._conn, [i.asset_id for i in items])
        return {asset_id: a.name for asset_id, a in assets.items()}

    def _period_info(self, order: RentalOrder) -> list[tuple[str, str]]:
        info = [("대여 시작", format_dt(order.start_at)), ("반납 예정", format_dt(order.due_at))]
        extensions = repo.list_extensions(self._conn, order.id)
        if extensions:
            info.append(("처음 반납 예정", f"{format_dt(extensions[0].previous_due_at)} (연장 {len(extensions)}회)"))
        if order.actual_checkout_at is not None:
            info.append(("출고 일시", format_dt(order.actual_checkout_at)))
        return info

    def _new(self, kind: DocumentKind, order: RentalOrder, info: list[tuple[str, str]], **kwargs) -> Document:
        return Document(
            kind=kind, business=self._business(), order_no=order.order_no,
            issued_at=current_time(self._clock), info=tuple(info), **kwargs,
        )

    def _planned_table(self, order: RentalOrder) -> tuple[DocTable, list[DocAmount], int]:
        """출고 전: 품목 스냅샷 기준 예상 금액 표·금액 줄과 보증금 합계."""
        items = _active_items(order)
        q = quote_for_order(order, items)
        names = self._asset_names(items)
        kit_names = {k.id: k.name for k in order.kits}
        rows = tuple(
            (i.asset_code, names.get(i.asset_id, ""), format_won(i.daily_rate_snapshot_won),
             f"{format_won(fee.amount_won)}\n{fee.basis}", format_won(i.deposit_snapshot_won),
             kit_names.get(i.order_kit_id, ""))
            for i, fee in zip(items, q.item_fees, strict=True)
        )
        table = DocTable(("관리번호", "장비명", "일 대여료", "대여료", "보증금", "세트"), rows,
                         title=f"대여 장비 ({len(rows)}개)", right_cols=(2, 3, 4))
        amounts = [DocAmount("기본 대여료", format_won(q.base_rental_fee_won))]
        if q.discount_won:
            amounts.append(DocAmount("할인", format_won(-q.discount_won)))
        amounts += [DocAmount(f"세트 할인: {k.name} ({k.label})", format_won(-k.amount_won))
                    for k in q.kit_discounts if k.amount_won]
        amounts.append(DocAmount(f"대여료 합계 ({q.days_label})", format_won(q.expected_total_rental_fee_won),
                                 strong=True))
        amounts.append(DocAmount("보증금", format_won(q.expected_deposit_won), strong=True))
        return table, amounts, q.rental_days

    # ------------------------------------------------------------------ 견적서

    def _quote(self, order: RentalOrder) -> Document:
        settings = read_document_settings(self._conn)
        now = current_time(self._clock)
        valid_until = (now.astimezone(SEOUL) + timedelta(days=settings.quote_valid_days)).date()
        table, amounts, _ = self._planned_table(order)
        info = self._customer_info(order, honorific="귀하") + self._period_info(order)
        info.append(("견적 유효기간", f"{valid_until:%Y-%m-%d}까지 (견적일로부터 {settings.quote_valid_days}일)"))
        notes = [
            "보증금은 반납 후 장비 상태를 확인하고 돌려드립니다. 연체료·추가 비용이 있으면 보증금에서 뺄 수 있습니다.",
            "이 견적은 견적일 기준 금액입니다. 유효기간이 지나면 금액이 바뀔 수 있습니다.",
        ]
        if order.status == OrderStatus.DRAFT:
            notes.append("아직 예약이 확정되지 않았습니다. 예약을 확정하기 전에는 장비 일정에 따라 대여가 어려울 수 있습니다.")
        for kit in order.kits:
            if kit.checklist:
                notes.append(f"세트 '{kit.name}' 구성품: {', '.join(kit.checklist)}")
        return self._new(DocumentKind.QUOTE, order, info, tables=(table,), amounts=tuple(amounts),
                         amounts_title="견적 금액", notes=tuple(notes))

    # ------------------------------------------------------------------ 대여확인서

    def _rental(self, order: RentalOrder) -> Document:
        settings = read_document_settings(self._conn)
        summary = load_summary(self._conn, order.id)
        info = self._customer_info(order) + self._period_info(order)
        info.append(("상태", ORDER_STATUS_LABELS[order.status]))
        if order.status == OrderStatus.CONFIRMED:
            table, amounts, _ = self._planned_table(order)
            checklists = tuple(DocChecklist(k.name, k.checklist) for k in order.kits)
            checklist_title = "세트 구성품 확인 (출고 때 표시)"
        else:
            table, amounts = self._charged_table(order)
            checklists = tuple(DocChecklist(k.name, k.checklist, k.checkout_checks) for k in order.kits)
            checklist_title = "세트 구성품 출고 확인 (■ 확인 □ 미확인)"
        if summary.net_rental_receipts_won:
            amounts.append(DocAmount("받은 대여료", format_won(summary.net_rental_receipts_won)))
        if summary.deposit_in_won:
            amounts.append(DocAmount("받은 보증금", format_won(summary.deposit_in_won)))
        memo = (("출고 메모(외관·구성품)", order.checkout_note),) if order.checkout_note else ()
        return self._new(
            DocumentKind.RENTAL, order, info, tables=(table,), amounts=tuple(amounts),
            checklists=checklists, checklist_title=checklist_title, memo=memo,
            terms=settings.rental_terms, signatures=SIGNERS,
        )

    def _charged_table(self, order: RentalOrder) -> tuple[DocTable, list[DocAmount]]:
        """출고 후: 출고 때 청구한 대여료·할인과 연장 요금."""
        items = _active_items(order)
        names = self._asset_names(items)
        kit_names = {k.id: k.name for k in order.kits}
        charges = repo.list_charges(self._conn, order.id)
        rental = {c.rental_item_id: c for c in charges if c.kind == ChargeKind.RENTAL and c.rental_item_id}
        rows = []
        for i in items:
            line = rental.get(i.id)
            fee = (f"{format_won(line.amount_won)}\n{_without_code(line.description, i.asset_code)}"
                   if line else "")
            rows.append((i.asset_code, names.get(i.asset_id, ""), format_won(i.daily_rate_snapshot_won), fee,
                         format_won(i.deposit_snapshot_won), kit_names.get(i.order_kit_id, "")))
        table = DocTable(("관리번호", "장비명", "일 대여료", "대여료", "보증금", "세트"), tuple(rows),
                         title=f"대여 장비 ({len(rows)}개)", right_cols=(2, 3, 4))
        base = sum(c.amount_won for c in charges if c.kind == ChargeKind.RENTAL)
        amounts = [DocAmount("기본 대여료", format_won(base))]
        total = base
        for c in charges:
            if c.kind == ChargeKind.DISCOUNT:
                amounts.append(DocAmount(c.description or "할인", format_won(c.amount_won)))
                total += c.amount_won
        extension = sum(e.amount_won for e in repo.list_extensions(self._conn, order.id))
        if extension:
            amounts.append(DocAmount("연장 요금", format_won(extension)))
            total += extension
        amounts.append(DocAmount("대여료 합계", format_won(total), strong=True))
        amounts.append(DocAmount("보증금", format_won(sum(i.deposit_snapshot_won for i in items)), strong=True))
        return table, amounts

    # ------------------------------------------------------------------ 반납확인서

    def _return(self, order: RentalOrder) -> Document:
        items = _active_items(order)
        names = self._asset_names(items)
        charges = repo.list_charges(self._conn, order.id)
        late: dict[int, int] = defaultdict(int)
        extra: dict[int, list[str]] = defaultdict(list)
        for c in charges:
            if c.rental_item_id is None:
                continue
            if c.kind == ChargeKind.LATE:
                late[c.rental_item_id] += c.amount_won
            elif c.kind == ChargeKind.DAMAGE:
                extra[c.rental_item_id].append(f"{format_won(c.amount_won)}\n{short_text(c.description)}")
        rows = []
        for i in items:
            returned = i.status == ItemStatus.RETURNED
            rows.append((
                i.asset_code, names.get(i.asset_id, ""),
                format_dt(i.actual_return_at) if returned else "대여 중",
                CONDITION_LABELS.get(i.return_condition, "") if returned and i.return_condition else "",
                format_won(late[i.id]) if late[i.id] else "",
                "\n".join(extra[i.id]),
                (i.return_note or "") if returned else "",
            ))
        item_table = DocTable(
            ("관리번호", "장비명", "반납 일시", "반납 상태", "연체료", "추가 비용", "반납 메모"), tuple(rows),
            title="반납 장비", right_cols=(4,),
        )
        charge_table = DocTable(
            ("종류", "내용", "금액"),
            tuple((charge_kind_label(c.kind, c.description), c.description or "", format_won(c.amount_won))
                  for c in charges),
            title="청구 내역", right_cols=(2,),
        )
        s = load_summary(self._conn, order.id)
        amounts = [
            DocAmount("청구 합계", format_won(s.charge_total_won), strong=True),
            DocAmount("받은 대여료", format_won(s.net_rental_receipts_won)),
        ]
        if s.net_deposit_applied_won:
            amounts.append(DocAmount("보증금에서 차감", format_won(s.net_deposit_applied_won)))
        amounts.append(DocAmount("미수금" if s.amount_due_won >= 0 else "돌려줄 대여료",
                                 format_won(abs(s.amount_due_won)), strong=True))
        amounts += [
            DocAmount("받은 보증금", format_won(s.deposit_in_won)),
            DocAmount("돌려준 보증금", format_won(s.deposit_out_won)),
            DocAmount("돌려줄 보증금", format_won(s.deposit_held_won), strong=True),
            DocAmount("정산", "정산 완료" if s.is_settled else "미정산", strong=True),
        ]
        info = self._customer_info(order) + self._period_info(order)
        info.append(("상태", ORDER_STATUS_LABELS[order.status]))
        out = [i.asset_code for i in items if i.status == ItemStatus.OUT]
        notes = (f"아직 반납하지 않은 장비: {', '.join(out)}",) if out else ()
        returned_kits = {i.order_kit_id for i in items if i.status == ItemStatus.RETURNED}
        checklists = tuple(DocChecklist(k.name, k.checklist, k.return_checks)
                           for k in order.kits if k.id in returned_kits)
        return self._new(
            DocumentKind.RETURN, order, info, tables=(item_table, charge_table), amounts=tuple(amounts),
            amounts_title="정산", checklists=checklists, checklist_title="세트 구성품 반납 확인 (■ 확인 □ 미확인)",
            notes=notes, signatures=SIGNERS,
        )

