"""예약 상세 (S-04 상세 액션): 수정·확정·취소·출고·장비별 반납·정산 내역."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.models import ORDER_STATUS_LABELS, OrderStatus
from rentdesk.domain.policy import buffer_summary
from rentdesk.services.settlements import has_ledger_activity
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import DataTable, WonSpinBox, button_row

ITEM_LABELS = {"RESERVED": "예약", "OUT": "대여 중", "RETURNED": "반납", "CANCELED": "취소"}
CONDITION_LABELS = {"GOOD": "정상", "MAINTENANCE_REQUIRED": "정비 필요"}


class CancelWithMoneyDialog(QDialog):
    """돈을 받은 예약 취소: 받은 금액을 보여주고 취소 수수료를 입력받는다."""

    def __init__(self, order, summary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"예약 취소 {order.order_no}")
        self.setMinimumWidth(460)
        info = QLabel(
            f"이 예약에서 받은 돈: 대여료 <b>{format_won(summary.net_rental_receipts_won)}</b>, "
            f"보증금 <b>{format_won(summary.deposit_held_won)}</b><br><br>"
            "취소 수수료를 받는다면 금액을 입력하세요. 취소한 뒤 정산 창이 열리면 "
            "수수료를 뺀 나머지를 '정산 마무리'로 돌려줄 수 있습니다.<br>취소는 되돌릴 수 없습니다."
        )
        info.setWordWrap(True)
        self.fee = WonSpinBox()
        self.fee.setMaximumWidth(220)
        form = QFormLayout()
        form.addRow("취소 수수료", self.fee)
        ok = theme.set_variant(QPushButton("예약 취소"), "danger")
        ok.clicked.connect(self.accept)
        back = QPushButton("돌아가기")
        back.clicked.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addLayout(button_row(back, ok))


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
        self.confirm_btn = theme.set_variant(QPushButton("예약 확정"), "primary")
        self.cancel_btn = theme.set_variant(QPushButton("예약 취소"), "danger")
        self.checkout_btn = QPushButton("출고 처리")
        self.return_btn = QPushButton("반납 처리")
        self.extend_btn = QPushButton("연장")
        self.settle_btn = QPushButton("정산 내역")
        close = QPushButton("닫기")
        self.edit_btn.clicked.connect(self.edit)
        self.confirm_btn.clicked.connect(self.confirm)
        self.cancel_btn.clicked.connect(self.cancel_order)
        self.checkout_btn.clicked.connect(self.open_checkout)
        self.return_btn.clicked.connect(self.open_return)
        self.extend_btn.clicked.connect(self.open_extension)
        self.settle_btn.clicked.connect(self.open_settlement)
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.items, 1)
        lay.addWidget(self.money)
        lay.addLayout(button_row(self.edit_btn, self.confirm_btn, self.cancel_btn,
                                 self.checkout_btn, self.return_btn, self.extend_btn,
                                 self.settle_btn, close))
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
            f" · 반납 후 준비 시간 {buffer_summary([i.turnaround_buffer_snapshot_minutes for i in order.items])}",
        ]
        if order.notes:
            lines.append(f"메모: {order.notes}")
        if order.checkout_note:
            lines.append(f"출고 메모: {order.checkout_note}")
        for ext in self.ctx.extensions.list_extensions(self.order_id):
            lines.append(
                f"연장: {format_dt(ext.previous_due_at)} → {format_dt(ext.new_due_at)}"
                f" (+{ext.added_days}일, {format_won(ext.amount_won)})" + (f" · {ext.note}" if ext.note else "")
            )
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
                f"청구 합계 {format_won(s.charge_total_won)} · 받은 대여료 "
                f"{format_won(s.net_rental_receipts_won)} · 미수금 <b>{format_won(s.amount_due_won)}</b>"
                f" · 보증금 보유 <b>{format_won(s.deposit_held_won)}</b> · <b>{state}</b>"
            )
        elif q is not None:
            text = (f"예상 대여료 {format_won(q.expected_total_rental_fee_won)} ({q.rental_days}일,"
                    f" 할인 {format_won(q.discount_won)}) · 예상 보증금 {format_won(q.expected_deposit_won)}")
            if order.status == OrderStatus.CONFIRMED:
                s = self.ctx.settlements.summary(self.order_id)
                if has_ledger_activity(s):
                    text += (f"<br>선입금 대여료 <b>{format_won(s.net_rental_receipts_won)}</b> · "
                             f"받은 보증금 <b>{format_won(s.deposit_held_won)}</b>")
            self.money.setText(text)
        else:
            s = self.ctx.settlements.summary(self.order_id)
            if has_ledger_activity(s):
                state = "정산 완료" if s.is_settled else "미정산 — 정산 내역에서 돌려줄 돈을 처리해주세요"
                self.money.setText(
                    f"취소된 예약 · 청구 {format_won(s.charge_total_won)} · 미수금 "
                    f"<b>{format_won(s.amount_due_won)}</b> · 보증금 보유 <b>{format_won(s.deposit_held_won)}</b>"
                    f" · <b>{state}</b>"
                )
            else:
                self.money.setText("취소된 예약입니다. 오간 돈이 없습니다.")

        st = order.status
        self.edit_btn.setEnabled(st in (OrderStatus.DRAFT, OrderStatus.CONFIRMED))
        self.confirm_btn.setEnabled(st == OrderStatus.DRAFT)
        self.cancel_btn.setEnabled(st in (OrderStatus.DRAFT, OrderStatus.CONFIRMED))
        self.checkout_btn.setEnabled(st == OrderStatus.CONFIRMED)
        self.return_btn.setEnabled(st == OrderStatus.IN_RENTAL)
        self.extend_btn.setEnabled(st == OrderStatus.IN_RENTAL)
        self.settle_btn.setEnabled(
            st in (OrderStatus.CONFIRMED, OrderStatus.IN_RENTAL, OrderStatus.COMPLETED)
            or (st == OrderStatus.CANCELED and has_ledger_activity(self.ctx.settlements.summary(self.order_id)))
        )

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

    def ask_cancel_fee(self, summary) -> int | None:
        """취소 수수료 입력 창. 취소하면 None(테스트에서 바꿔 끼울 수 있다)."""
        dlg = CancelWithMoneyDialog(self.order, summary, self)
        return dlg.fee.value() if dlg.exec() else None

    def cancel_order(self) -> bool:
        summary = self.ctx.settlements.summary(self.order_id)
        if has_ledger_activity(summary):
            # 선입금·보증금을 받은 예약: 취소 수수료를 정하고, 취소 뒤 정산 창에서 돌려줄 돈을 처리(U5)
            fee = self.ask_cancel_fee(summary)
            if fee is None:
                return False
        else:
            if not feedback.confirm(
                self, f"예약 {self.order.order_no}을(를) 취소합니다. 취소는 되돌릴 수 없습니다.",
                "예약 취소 확인",
            ):
                return False
            fee = 0
        result = feedback.run(self, lambda: self.ctx.reservations.cancel(self.order_id, cancel_fee_won=fee))
        self._after(result is not None)
        if result is not None and has_ledger_activity(self.ctx.settlements.summary(self.order_id)):
            self.open_settlement()
        return result is not None

    def open_checkout(self) -> None:
        from rentdesk.ui.dialogs.checkout_dialog import CheckoutDialog

        self._after(bool(CheckoutDialog(self.ctx, self.order_id, self).exec()))

    def open_return(self) -> None:
        from rentdesk.ui.dialogs.return_dialog import ReturnDialog

        self._after(bool(ReturnDialog(self.ctx, self.order_id, self).exec()))

    def open_extension(self) -> None:
        from rentdesk.ui.dialogs.extension_dialog import ExtensionDialog

        self._after(bool(ExtensionDialog(self.ctx, self.order_id, self).exec()))

    def open_settlement(self) -> None:
        from rentdesk.ui.dialogs.settlement_dialog import SettlementDialog

        dlg = SettlementDialog(self.ctx, self.order_id, self)
        dlg.exec()
        self._after(dlg.changed)
