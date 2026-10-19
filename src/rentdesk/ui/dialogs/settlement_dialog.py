"""정산·입출금 기록 (S-07, W-04).

청구금액/대여료 수금/보증금 수령/환급/대금충당/미수금/보증금 보유잔액을 따로 표시한다.
결제수단은 기록값일 뿐 결제 기능이 아니다. 원장행은 수정·삭제하지 않고 역거래로 정정한다.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
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
from rentdesk.domain.models import ORDER_STATUS_LABELS, MoneyKind, OrderStatus
from rentdesk.domain.settlement import (  # noqa: F401 — CHARGE_KIND_LABELS는 예전 이름 호환
    CHARGE_KIND_LABELS,
    INTERNAL_KINDS,
    MONEY_KIND_LABELS,
    REVERSAL_OF,
    charge_kind_label,
)
from rentdesk.services.settlements import FINAL_STATUSES, MoneyInput
from rentdesk.ui import feedback, theme
from rentdesk.ui.dialogs.checkout_dialog import PAYMENT_LABELS, payment_combo
from rentdesk.ui.widgets import DataTable, FormErrors, WonSpinBox, button_row

SETTLE_NOTICE = "반납 완료 여부와 별도로 미수금/보증금 보유액을 정산해야 합니다."


def order_status_needs_details(ctx: AppContext, order_id: int) -> bool:
    """정산 마무리를 쓸 수 없는(출고 전·대여 중) 예약은 처음부터 상세 기록 영역을 펼친다."""
    return ctx.reservations.get_order(order_id).status not in FINAL_STATUSES


class SettlementDialog(QDialog):
    def __init__(self, ctx: AppContext, order_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.order_id = order_id
        self.changed = False
        self.setMinimumSize(860, 420)

        self.header = QLabel()
        self.figures: dict[str, QLabel] = {}
        fig_box = QGroupBox("정산 현황")
        grid = QGridLayout(fig_box)
        labels = [
            ("charge_total", "청구 합계"), ("net_receipts", "받은 대여료(환급 제외)"),
            ("amount_due", "미수금"), ("deposit_in", "보증금 수령"),
            ("deposit_out", "보증금 반환"), ("deposit_applied", "보증금에서 차감"),
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
        self.record_btn = theme.set_variant(QPushButton("거래 기록"), "primary")
        self.record_btn.clicked.connect(self.submit)
        form.addRow(button_row(self.record_btn))

        # 정산 마무리(U4): 보증금에서 미수 차감 → 남은 보증금 반환 → 초과 수금 환급을 한 번에
        self.final_box = QGroupBox("정산 마무리")
        fl = QVBoxLayout(self.final_box)
        self.final_text = QLabel()
        self.final_text.setWordWrap(True)
        self.final_method = payment_combo()
        self.final_collect = QCheckBox()
        self.final_collect.toggled.connect(self._update_final_button)
        self.final_btn = theme.set_variant(QPushButton("정산 마무리"), "primary")
        self.final_btn.clicked.connect(self.finalize)
        self.final_method.setMaximumWidth(220)
        self.final_form = QFormLayout()
        self.final_form.addRow("돌려주거나 받은 방법(기록용)", self.final_method)
        fl.addWidget(self.final_text)
        fl.addLayout(self.final_form)
        fl.addWidget(self.final_collect)
        fl.addLayout(button_row(self.final_btn))
        self._plan = None

        self.details = QWidget()
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(QLabel("<b>청구 원장</b>"))
        dl.addWidget(self.charges, 1)
        dl.addWidget(QLabel("<b>입출금 원장</b> (수정·삭제 불가 — 잘못 기록했으면 취소 기록을 추가해 바로잡습니다)"))
        dl.addWidget(self.entries, 1)
        dl.addWidget(form_box)
        self.details_btn = QPushButton()
        self.details_btn.clicked.connect(lambda: self._show_details(self.details.isHidden()))

        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(fig_box)
        lay.addWidget(self.state)
        lay.addWidget(self.final_box)
        lay.addWidget(self.details, 1)
        lay.addLayout(button_row(self.details_btn, close))
        self.refresh()
        # 반납 완료 예약은 정산 마무리만 보이고, 원장·직접 기록은 '자세히'에서 연다
        self._show_details(order_status_needs_details(self.ctx, self.order_id))

    def _show_details(self, show: bool) -> None:
        self.details.setVisible(show)
        self.details_btn.setText("자세히 접기" if show else "자세히 보기(원장·직접 기록)")
        if show:
            self.resize(self.width(), max(self.height(), 720))
        else:
            self.adjustSize()

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
        if order.status == OrderStatus.CONFIRMED:
            # 출고 전: 청구 원장이 아직 없으므로 '초과 수금'이 아니라 선입금으로 보여준다(U5)
            q = self.ctx.reservations.quote(order)
            rest = max(0, q.expected_total_rental_fee_won - s.net_rental_receipts_won)
            deposit_rest = max(0, q.expected_deposit_won - s.deposit_held_won)
            self.state.setText(
                f"출고 전 · 예정 대여료 {format_won(q.expected_total_rental_fee_won)} 중 선입금 "
                f"{format_won(s.net_rental_receipts_won)} → 출고 때 받을 대여료 <b>{format_won(rest)}</b> · "
                f"예정 보증금 {format_won(q.expected_deposit_won)} 중 받은 보증금 "
                f"{format_won(s.deposit_held_won)} → 출고 때 받을 보증금 <b>{format_won(deposit_rest)}</b>"
            )
            theme.set_tone(self.state, None)
        elif s.is_settled:
            self.state.setText("정산 완료: 미수금과 보증금 보유잔액이 모두 0원입니다.")
            theme.set_tone(self.state, "success")
        else:
            parts = []
            if s.amount_due_won > 0:
                parts.append(f"미수 {format_won(s.amount_due_won)}")
            elif s.amount_due_won < 0:
                parts.append(f"초과수금(환급 필요) {format_won(-s.amount_due_won)}")
            if s.deposit_held_won:
                parts.append(f"보증금 보유 {format_won(s.deposit_held_won)}")
            self.state.setText(f"[확인 필요] {' · '.join(parts)}. {SETTLE_NOTICE}")
            theme.set_tone(self.state, "danger")
        self.charges.set_rows([
            (c.id, [format_dt(c.created_at), charge_kind_label(c.kind, c.description),
                    format_won(c.amount_won), c.description or ""])
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
        allowed = self.ctx.settlements.allowed_kinds(self.order_id)
        current = self.kind.currentData()
        self.kind.blockSignals(True)
        self.kind.clear()
        for kind, label in MONEY_KIND_LABELS.items():
            if kind in allowed:
                self.kind.addItem(label, kind)
        self.kind.setCurrentIndex(max(0, self.kind.findData(current)))
        self.kind.blockSignals(False)
        self.record_btn.setEnabled(bool(allowed))
        if not allowed:
            self.preview.setText("임시 저장 예약에는 돈을 기록할 수 없습니다. 예약을 확정한 뒤 기록해주세요.")
        self._on_kind_changed()
        self._refresh_final(order)

    def _refresh_final(self, order) -> None:
        if order.status not in FINAL_STATUSES:
            self.final_box.hide()
            self._plan = None
            return
        self.final_box.show()
        self._plan = feedback.run(self, lambda: self.ctx.settlements.final_plan(self.order_id))
        plan = self._plan
        if plan is None or plan.is_empty:
            self.final_text.setText("정산할 금액이 없습니다.")
            for w in (self.final_method, self.final_collect, self.final_btn):
                w.setEnabled(False)
            self.final_collect.hide()
            self.final_form.setRowVisible(self.final_method, False)
            return
        lines = []
        if plan.deposit_applied_won:
            lines.append(f"• 보증금에서 {format_won(plan.deposit_applied_won)}을(를) 못 받은 돈으로 차감")
        if plan.rental_refund_won:
            lines.append(f"• 더 받은 대여료 <b>{format_won(plan.rental_refund_won)}</b> 돌려주기")
        if plan.deposit_return_won:
            lines.append(f"• 남은 보증금 <b>{format_won(plan.deposit_return_won)}</b> 돌려주기")
        if plan.remaining_due_won:
            lines.append(f"• 보증금으로도 모자란 <b>{format_won(plan.remaining_due_won)}</b>은(는) 고객에게 받을 돈")
        self.final_text.setText("<br>".join(lines))
        self.final_collect.setVisible(bool(plan.remaining_due_won))
        # 보증금 차감만 있으면 실제로 돈이 오가지 않으므로 결제수단을 묻지 않는다
        money_moves = bool(plan.remaining_due_won) or any(
            step.kind not in INTERNAL_KINDS for step in plan.steps
        )
        self.final_form.setRowVisible(self.final_method, money_moves)
        self.final_collect.setText(f"모자란 {format_won(plan.remaining_due_won)}을(를) 지금 받았습니다")
        self.final_collect.setChecked(False)
        self.final_method.setEnabled(True)
        self.final_collect.setEnabled(True)
        self._update_final_button()

    def _update_final_button(self) -> None:
        plan = self._plan
        if plan is None:
            self.final_btn.setEnabled(False)
            return
        has_steps = bool(plan.steps) or (self.final_collect.isChecked() and plan.remaining_due_won > 0)
        self.final_btn.setEnabled(has_steps)

    def finalize(self) -> bool:
        plan = self._plan
        if plan is None:
            return False
        collect = self.final_collect.isChecked() and plan.remaining_due_won > 0
        todo = []
        if plan.deposit_applied_won:
            todo.append(f"보증금에서 차감 {format_won(plan.deposit_applied_won)}")
        if plan.rental_refund_won:
            todo.append(f"대여료 환급 {format_won(plan.rental_refund_won)}")
        if plan.deposit_return_won:
            todo.append(f"보증금 반환 {format_won(plan.deposit_return_won)}")
        if collect:
            todo.append(f"대여료 수금 {format_won(plan.remaining_due_won)}")
        text = ("아래 거래를 한 번에 기록합니다. 기록한 거래는 수정·삭제할 수 없습니다.\n\n"
                + "\n".join(f"- {t}" for t in todo))
        if plan.remaining_due_won and not collect:
            text += f"\n\n받지 않은 {format_won(plan.remaining_due_won)}은(는) 미수금으로 남습니다."
        if not feedback.confirm(self, text, "정산 마무리 확인"):
            return False
        result = feedback.run(self, lambda: self.ctx.settlements.finalize(
            self.order_id, plan, payment_method=self.final_method.currentData(),
            collect_remaining=collect,
        ))
        self.refresh()
        if result is None:
            return False
        self.changed = True
        feedback.info(self, "정산을 기록했습니다." + (" 정산이 완료되었습니다." if result.is_settled else ""))
        return True

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
                "기록한 거래는 수정·삭제할 수 없으며 잘못된 경우 취소 기록을 추가해 바로잡아야 합니다.")
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
