"""예약 상세 (S-04 상세 액션): 수정·확정·취소·출고·장비별 반납·정산 내역."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.models import ORDER_STATUS_LABELS, OrderStatus
from rentdesk.ui import feedback
from rentdesk.ui.widgets import DataTable, button_row

ITEM_LABELS = {"RESERVED": "예약", "OUT": "대여 중", "RETURNED": "반납", "CANCELED": "취소"}
CONDITION_LABELS = {"GOOD": "정상", "MAINTENANCE_REQUIRED": "정비 필요"}


class OrderDetailDialog(QDialog):
    def __init__(self, ctx: AppContext, order_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.order_id = order_id
        self.changed = False
        self.setMinimumSize(820, 520)

        self.header = QLabel()
        self.header.setWordWrap(True)
        self.items = DataTable(
            ["장비번호", "상태", "일 대여료(스냅샷)", "보증금(스냅샷)", "실제 반납", "반납 상태", "반납 메모"]
        )
        self.money = QLabel()
        self.money.setWordWrap(True)

        self.edit_btn = QPushButton("수정")
        self.confirm_btn = QPushButton("예약 확정")
        self.cancel_btn = QPushButton("예약 취소")
        self.checkout_btn = QPushButton("출고 처리")
        self.return_btn = QPushButton("반납 처리")
        self.settle_btn = QPushButton("정산 내역")
        close = QPushButton("닫기")
        self.edit_btn.clicked.connect(self.edit)
        self.confirm_btn.clicked.connect(self.confirm)
        self.cancel_btn.clicked.connect(self.cancel_order)
        self.checkout_btn.clicked.connect(self.open_checkout)
        self.return_btn.clicked.connect(self.open_return)
        self.settle_btn.clicked.connect(self.open_settlement)
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.items, 1)
        lay.addWidget(self.money)
        lay.addLayout(button_row(self.edit_btn, self.confirm_btn, self.cancel_btn,
                                 self.checkout_btn, self.return_btn, self.settle_btn, close))
        self.refresh()

    def refresh(self) -> None:
        order = self.ctx.reservations.get_order(self.order_id)
        self.order = order
        self.setWindowTitle(f"예약 상세 {order.order_no}")
        lines = [
            f"<b>{order.order_no}</b> · 고객 <b>{self.ctx.reservations.customer_name(order)}</b>"
            f" · 상태 <b>{ORDER_STATUS_LABELS[order.status]}</b>",
            f"예약 기간 {format_dt(order.start_at)} ~ {format_dt(order.due_at)}"
            f" · 출고 {format_dt(order.actual_checkout_at, '-')}"
            f" · 점검 버퍼 {order.turnaround_buffer_snapshot_minutes}분",
        ]
        if order.notes:
            lines.append(f"메모: {order.notes}")
        if order.checkout_note:
            lines.append(f"출고 메모: {order.checkout_note}")
        self.header.setText("<br>".join(lines))
        self.items.set_rows([
            (i.id, [i.asset_code, ITEM_LABELS[i.status.value],
                    format_won(i.daily_rate_snapshot_won), format_won(i.deposit_snapshot_won),
                    format_dt(i.actual_return_at),
                    CONDITION_LABELS.get(i.return_condition.value, "") if i.return_condition else "",
                    i.return_note or ""])
            for i in order.items
        ], align_right=(2, 3))

        q = self.ctx.reservations.quote(order) if order.status != OrderStatus.CANCELED else None
        if order.status in (OrderStatus.IN_RENTAL, OrderStatus.COMPLETED):
            s = self.ctx.settlements.summary(self.order_id)
            state = "정산 완료" if s.is_settled else "미정산"
            self.money.setText(
                f"청구 합계 {format_won(s.charge_total_won)} · 대여료 순수금 "
                f"{format_won(s.net_rental_receipts_won)} · 미수금 <b>{format_won(s.amount_due_won)}</b>"
                f" · 보증금 보유 <b>{format_won(s.deposit_held_won)}</b> · <b>{state}</b>"
            )
        elif q is not None:
            self.money.setText(
                f"예상 대여료 {format_won(q.expected_total_rental_fee_won)} ({q.rental_days}일,"
                f" 할인 {format_won(q.discount_won)}) · 예상 보증금 {format_won(q.expected_deposit_won)}"
            )
        else:
            self.money.setText("취소된 예약입니다. 청구 원장이 생성되지 않았습니다.")

        st = order.status
        self.edit_btn.setEnabled(st in (OrderStatus.DRAFT, OrderStatus.CONFIRMED))
        self.confirm_btn.setEnabled(st == OrderStatus.DRAFT)
        self.cancel_btn.setEnabled(st in (OrderStatus.DRAFT, OrderStatus.CONFIRMED))
        self.checkout_btn.setEnabled(st == OrderStatus.CONFIRMED)
        self.return_btn.setEnabled(st == OrderStatus.IN_RENTAL)
        self.settle_btn.setEnabled(st in (OrderStatus.IN_RENTAL, OrderStatus.COMPLETED))

    def _after(self, ok: bool) -> None:
        if ok:
            self.changed = True
        self.refresh()

    def edit(self) -> None:
        from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard

        self._after(bool(ReservationWizard(self.ctx, self.order, self).exec()))

    def confirm(self) -> bool:
        result = feedback.run(self, lambda: self.ctx.reservations.confirm(self.order_id))
        if result is not None:
            feedback.info(self, f"예약 {result.order_no}이(가) 확정되었습니다.")
        self._after(result is not None)
        return result is not None

    def cancel_order(self) -> bool:
        if not feedback.confirm(
            self, f"예약 {self.order.order_no}을(를) 취소합니다. 취소는 되돌릴 수 없습니다.",
            "예약 취소 확인",
        ):
            return False
        result = feedback.run(self, lambda: self.ctx.reservations.cancel(self.order_id))
        self._after(result is not None)
        return result is not None

    def open_checkout(self) -> None:
        from rentdesk.ui.dialogs.checkout_dialog import CheckoutDialog

        self._after(bool(CheckoutDialog(self.ctx, self.order_id, self).exec()))

    def open_return(self) -> None:
        from rentdesk.ui.dialogs.return_dialog import ReturnDialog

        self._after(bool(ReturnDialog(self.ctx, self.order_id, self).exec()))

    def open_settlement(self) -> None:
        from rentdesk.ui.dialogs.settlement_dialog import SettlementDialog

        dlg = SettlementDialog(self.ctx, self.order_id, self)
        dlg.exec()
        self._after(dlg.changed)
