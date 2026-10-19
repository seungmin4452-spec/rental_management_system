"""출고 처리 (S-05). 확정 예약의 모든 장비를 한 번에 출고한다(부분 출고는 P1)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.models import PaymentMethod, RentalOrder
from rentdesk.services.checkout import CheckoutInput
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import (
    DataTable,
    FormErrors,
    SeoulDateTimeEdit,
    WonSpinBox,
    button_row,
    now_seoul_minute,
)

PAYMENT_LABELS = {
    PaymentMethod.CASH: "현금",
    PaymentMethod.TRANSFER: "이체",
    PaymentMethod.CARD: "카드",
    PaymentMethod.OTHER: "기타",
}


def payment_combo(allow_none: bool = True) -> QComboBox:
    combo = QComboBox()
    if allow_none:
        combo.addItem("선택 안 함", None)
    for method, label in PAYMENT_LABELS.items():
        combo.addItem(label, method)
    return combo


class CheckoutDialog(QDialog):
    def __init__(self, ctx: AppContext, order_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.order_id = order_id
        self.result_order: RentalOrder | None = None
        order = ctx.reservations.get_order(order_id)
        self.setWindowTitle(f"출고 처리 {order.order_no}")
        self.setMinimumSize(640, 560)

        info = QLabel(
            f"<b>{order.order_no}</b> · 고객 {ctx.reservations.customer_name(order)}<br>"
            f"약정 {format_dt(order.start_at)} ~ {format_dt(order.due_at)}"
        )
        items = DataTable(["장비번호", "일 대여료(예약 시)", "보증금(예약 시)"])
        items.set_rows([
            (i.id, [i.asset_code, format_won(i.daily_rate_snapshot_won),
                    format_won(i.deposit_snapshot_won)])
            for i in order.items if i.status.value == "RESERVED"
        ], align_right=(1, 2))

        self.amounts = QLabel()
        self.amounts.setWordWrap(True)
        self.blockers = QLabel()
        theme.set_tone(self.blockers, "danger")
        self.blockers.setWordWrap(True)

        form = QFormLayout()
        self.errors = FormErrors(form)
        self.checkout_at = SeoulDateTimeEdit(now_seoul_minute(ctx.now()))
        self.checkout_at.dateTimeChanged.connect(self.refresh_preview)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("장비 외관/구성품 상태 메모")
        self.note.setFixedHeight(56)
        self.receipt = WonSpinBox()
        self.deposit = WonSpinBox()
        self.method = payment_combo()
        self.errors.add_row("실제 출고 시각", self.checkout_at, "checkout_at")
        self.errors.add_row("외관/구성품 메모", self.note, "note")
        self.errors.add_row("받은 대여료(선택)", self.receipt, "rental_receipt_won")
        self.errors.add_row("받은 보증금(선택)", self.deposit, "deposit_in_won")
        self.fill_btn = QPushButton("남은 금액 채우기")
        self.fill_btn.setToolTip("선입금을 뺀 남은 대여료·보증금을 입력칸에 채웁니다. 실제로 받은 금액만 기록하세요.")
        self.fill_btn.clicked.connect(self.fill_remaining)
        form.addRow("", self.fill_btn)
        self._remaining = (0, 0)
        self.errors.add_row("결제수단(기록용)", self.method, "payment_method")

        self.submit_btn = theme.set_variant(QPushButton("출고 확정"), "primary")
        self.submit_btn.clicked.connect(self.submit)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(items, 1)
        lay.addWidget(self.amounts)
        lay.addLayout(form)
        lay.addWidget(self.blockers)
        lay.addLayout(button_row(cancel, self.submit_btn))
        self.refresh_preview()

    def refresh_preview(self) -> None:
        preview = feedback.run(
            self, lambda: self.ctx.checkout.preview(self.order_id, self.checkout_at.value())
        )
        if preview is None:
            return
        summary = self.ctx.settlements.summary(self.order_id)
        q = preview.quote
        self._remaining = (
            max(0, q.expected_total_rental_fee_won - summary.net_rental_receipts_won),
            max(0, q.expected_deposit_won - summary.deposit_held_won),
        )
        self.fill_btn.setEnabled(any(self._remaining))
        self.amounts.setText(
            f"받을 대여료: <b>{format_won(q.expected_total_rental_fee_won)}</b>"
            f" ({q.days_label}, 할인 {format_won(q.discount_won)} 반영) · "
            f"받을 보증금: <b>{format_won(q.expected_deposit_won)}</b> · "
            f"선입금: 대여료 {format_won(summary.net_rental_receipts_won)}, "
            f"보증금 {format_won(summary.deposit_held_won)} · "
            f"남은 금액: 대여료 <b>{format_won(self._remaining[0])}</b>, "
            f"보증금 <b>{format_won(self._remaining[1])}</b>"
        )
        self.blockers.setText("\n".join(preview.blockers))
        self.submit_btn.setEnabled(preview.can_checkout)

    def fill_remaining(self) -> None:
        self.receipt.setValue(self._remaining[0])
        self.deposit.setValue(self._remaining[1])

    def submit(self) -> bool:
        self.errors.clear()
        data = CheckoutInput(
            checkout_at=self.checkout_at.value(), note=self.note.toPlainText(),
            rental_receipt_won=self.receipt.value(), deposit_in_won=self.deposit.value(),
            payment_method=self.method.currentData(),
        )
        money = []
        if data.rental_receipt_won:
            money.append(f"대여료 수금 {format_won(data.rental_receipt_won)}")
        if data.deposit_in_won:
            money.append(f"보증금 수령 {format_won(data.deposit_in_won)}")
        text = (
            f"출고 시각 {format_dt(data.checkout_at)}에 모든 장비를 출고 처리합니다.\n"
            "출고 후에는 예약 기간 수정·취소를 할 수 없습니다."
        )
        if money:
            text += "\n\n함께 기록할 금전 거래: " + ", ".join(money)
        if not feedback.confirm(self, text, "출고 확인"):
            return False
        self.submit_btn.setEnabled(False)  # 반복 클릭 방지(서비스도 이중 출고를 거부)
        result = feedback.run(
            self, lambda: self.ctx.checkout.checkout(self.order_id, data),
            on_domain_error=self.errors.show,
        )
        if result is None:
            self.refresh_preview()
            return False
        self.result_order = result
        feedback.info(self, f"{result.order_no} 출고가 완료되었습니다.")
        self.accept()
        return True
