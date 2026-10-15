"""정산·입출금 기록 (S-07, W-04).

청구금액/대여료 수금/보증금 수령/환급/대금충당/미수금/보증금 보유잔액을 따로 표시한다.
결제수단은 기록값일 뿐 결제 기능이 아니다. 원장행은 수정·삭제하지 않고 역거래로 정정한다.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.errors import DomainError
from rentdesk.domain.models import ORDER_STATUS_LABELS, ChargeKind, MoneyKind
from rentdesk.domain.settlement import INTERNAL_KINDS, MONEY_KIND_LABELS, REVERSAL_OF
from rentdesk.services.settlements import MoneyInput
from rentdesk.ui import feedback
from rentdesk.ui.dialogs.checkout_dialog import PAYMENT_LABELS, payment_combo
from rentdesk.ui.widgets import DataTable, FormErrors, WonSpinBox, button_row

CHARGE_KIND_LABELS = {
    ChargeKind.RENTAL: "대여료",
    ChargeKind.DISCOUNT: "할인",
    ChargeKind.LATE: "연체료",
    ChargeKind.DAMAGE: "파손비",
    ChargeKind.ADJUSTMENT: "조정",
}
SETTLE_NOTICE = "반납 완료 여부와 별도로 미수금/보증금 보유액을 정산해야 합니다."


class SettlementDialog(QDialog):
    def __init__(self, ctx: AppContext, order_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.order_id = order_id
        self.changed = False
        self.setMinimumSize(860, 640)

        self.header = QLabel()
        self.figures: dict[str, QLabel] = {}
        fig_box = QGroupBox("정산 현황")
        grid = QGridLayout(fig_box)
        labels = [
            ("charge_total", "청구 합계"), ("net_receipts", "대여료 순수금"),
            ("amount_due", "미수금"), ("deposit_in", "보증금 수령"),
            ("deposit_out", "보증금 반환"), ("deposit_applied", "보증금 대금충당"),
            ("deposit_held", "보증금 보유잔액"),
        ]
        for n, (key, text) in enumerate(labels):
            value = QLabel()
            self.figures[key] = value
            grid.addWidget(QLabel(text), n // 4 * 2, n % 4)
            grid.addWidget(value, n // 4 * 2 + 1, n % 4)
        self.state = QLabel()
        self.state.setWordWrap(True)

        self.charges = DataTable(["일시", "종류", "금액", "내용"])
        self.entries = DataTable(["번호", "일시", "유형", "금액", "수단", "정정 대상", "비고"])

        form_box = QGroupBox("거래 기록")
        form = QFormLayout(form_box)
        self.errors = FormErrors(form)
        self.kind = QComboBox()
        for kind, label in MONEY_KIND_LABELS.items():
            self.kind.addItem(label, kind)
        self.kind.currentIndexChanged.connect(self._on_kind_changed)
        self.amount = WonSpinBox()
        self.amount.valueChanged.connect(self._update_preview)
        self.method = payment_combo()
        self.related = QComboBox()
        self.note = QLineEdit()
        self.note.setMaxLength(500)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.errors.add_row("유형", self.kind, "kind")
        self.errors.add_row("금액", self.amount, "amount_won")
        self.errors.add_row("결제수단(기록용)", self.method, "payment_method")
        self.errors.add_row("정정 대상(선택)", self.related, "related_entry_id")
        self.errors.add_row("비고", self.note, "note")
        form.addRow("저장 후", self.preview)
        self.record_btn = QPushButton("거래 기록")
        self.record_btn.clicked.connect(self.submit)
        form.addRow(button_row(self.record_btn))

        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(fig_box)
        lay.addWidget(self.state)
        lay.addWidget(QLabel("<b>청구 원장</b>"))
        lay.addWidget(self.charges, 1)
        lay.addWidget(QLabel("<b>입출금 원장</b> (수정·삭제 불가, 정정은 역거래로 기록)"))
        lay.addWidget(self.entries, 1)
        lay.addWidget(form_box)
        lay.addLayout(button_row(close))
        self.refresh()

    def refresh(self) -> None:
        st = self.ctx.settlements.get_settlement(self.order_id)
        order, s = st.order, st.summary
        self.setWindowTitle(f"정산 {order.order_no}")
        self.header.setText(
            f"<b>{order.order_no}</b> · 고객 {self.ctx.reservations.customer_name(order)} · "
            f"상태 {ORDER_STATUS_LABELS[order.status]}"
        )
        values = {
            "charge_total": s.charge_total_won, "net_receipts": s.net_rental_receipts_won,
            "amount_due": s.amount_due_won, "deposit_in": s.deposit_in_won,
            "deposit_out": s.deposit_out_won, "deposit_applied": s.net_deposit_applied_won,
            "deposit_held": s.deposit_held_won,
        }
        for key, v in values.items():
            self.figures[key].setText(f"<b>{format_won(v)}</b>")
        if s.is_settled:
            self.state.setText("정산 완료: 미수금과 보증금 보유잔액이 모두 0원입니다.")
            self.state.setStyleSheet("color: #2e7d32;")
        else:
            parts = []
            if s.amount_due_won > 0:
                parts.append(f"미수 {format_won(s.amount_due_won)}")
            elif s.amount_due_won < 0:
                parts.append(f"초과수금(환급 필요) {format_won(-s.amount_due_won)}")
            if s.deposit_held_won:
                parts.append(f"보증금 보유 {format_won(s.deposit_held_won)}")
            self.state.setText(f"[확인 필요] {' · '.join(parts)}. {SETTLE_NOTICE}")
            self.state.setStyleSheet("color: #c62828;")
        self.charges.set_rows([
            (c.id, [format_dt(c.created_at), CHARGE_KIND_LABELS[c.kind], format_won(c.amount_won),
                    c.description or ""])
            for c in st.charges
        ], align_right=(2,))
        self._entries = st.entries
        self.entries.set_rows([
            (e.id, [e.id, format_dt(e.created_at), MONEY_KIND_LABELS[e.kind],
                    format_won(e.amount_won),
                    PAYMENT_LABELS.get(e.payment_method, "") if e.payment_method else "",
                    e.related_entry_id or "", e.note or ""])
            for e in st.entries
        ], align_right=(3,))
        allowed = order.status.value in ("IN_RENTAL", "COMPLETED")
        self.record_btn.setEnabled(allowed)
        if not allowed:
            self.preview.setText("출고 이후의 예약에만 수금·보증금 거래를 기록할 수 있습니다.")
        self._on_kind_changed()

    def _on_kind_changed(self) -> None:
        kind = self.kind.currentData()
        internal = kind in INTERNAL_KINDS
        self.method.setEnabled(not internal)
        if internal:
            self.method.setCurrentIndex(0)
        self.related.clear()
        self.related.addItem("지정 안 함", None)
        original = REVERSAL_OF.get(kind)
        for e in getattr(self, "_entries", ()):
            if original is not None and e.kind == original:
                self.related.addItem(
                    f"#{e.id} {format_dt(e.created_at)} {format_won(e.amount_won)}", e.id
                )
        self.related.setEnabled(original is not None)
        self._update_preview()

    def _input(self) -> MoneyInput:
        return MoneyInput(
            kind=self.kind.currentData(), amount_won=self.amount.value(),
            payment_method=self.method.currentData(), note=self.note.text(),
            related_entry_id=self.related.currentData(),
        )

    def _update_preview(self) -> None:
        if not self.record_btn.isEnabled():
            return
        self.errors.clear()
        if self.amount.value() == 0:
            self.preview.setText("금액을 입력하면 저장 후 잔액을 미리 보여줍니다.")
            return
        try:
            after = self.ctx.settlements.preview(self.order_id, self._input())
        except DomainError as exc:
            if not self.errors.show(exc):
                self.preview.setText(exc.message)
            return
        self.preview.setText(
            f"미수금 {format_won(after.amount_due_won)} · "
            f"보증금 보유잔액 {format_won(after.deposit_held_won)}"
        )

    def submit(self) -> bool:
        data = self._input()
        label = MONEY_KIND_LABELS[MoneyKind(data.kind)]
        text = (f"{label} {format_won(data.amount_won)}을(를) 기록합니다.\n"
                "기록한 거래는 수정·삭제할 수 없으며 잘못된 경우 역거래로 정정해야 합니다.")
        if not feedback.confirm(self, text, "금전 거래 확인"):
            return False
        entry = feedback.run(
            self, lambda: self.ctx.settlements.record(self.order_id, data),
            on_domain_error=self.errors.show,
        )
        if entry is None:
            return False
        self.changed = True
        self.amount.setValue(0)
        self.note.clear()
        self.refresh()
        return True
