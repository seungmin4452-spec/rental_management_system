"""대여 연장 (USABILITY_PLAN U6): 새 반납 예정 일시 → 추가 요금·충돌 미리보기 → 확정."""

from __future__ import annotations

from datetime import timedelta

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.errors import ConflictError, DomainError
from rentdesk.domain.models import RentalOrder
from rentdesk.services.availability import describe_conflict
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import FormErrors, SeoulDateTimeEdit, WonSpinBox, button_row


class ExtensionDialog(QDialog):
    def __init__(self, ctx: AppContext, order_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.order_id = order_id
        self.result_order: RentalOrder | None = None
        order = ctx.reservations.get_order(order_id)
        self.setWindowTitle(f"대여 연장 {order.order_no}")
        self.setMinimumWidth(560)

        info = QLabel(
            f"<b>{order.order_no}</b> · 고객 {ctx.reservations.customer_name(order)}<br>"
            f"지금 반납 예정: <b>{format_dt(order.due_at)}</b>"
        )
        form = QFormLayout()
        self.errors = FormErrors(form)
        # 기본값: 반납 예정 시각은 그대로 두고 하루씩 늘려 지금 이후가 되는 첫 날(연체 중이어도 바로 쓸 수 있게)
        default_due = order.due_at + timedelta(days=1)
        while default_due <= ctx.now():
            default_due += timedelta(days=1)
        self.new_due = SeoulDateTimeEdit(default_due)
        self.new_due.dateTimeChanged.connect(self.refresh_preview)
        self.discount = WonSpinBox()
        self.discount.setMaximumWidth(220)
        self.discount.valueChanged.connect(self.refresh_preview)
        self.note = QLineEdit()
        self.note.setMaxLength(500)
        self.note.setPlaceholderText("예: 고객 전화로 하루 연장 요청")
        self.errors.add_row("새 반납 예정", self.new_due, "new_due_at")
        self.errors.add_row("연장 할인(선택)", self.discount, "discount_won")
        self.errors.add_row("메모", self.note, "note")

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        theme.set_tone(self.problem, "danger")
        notice = QLabel("연장은 되돌릴 수 없습니다. 잘못 연장했으면 정산 화면에서 조정으로 바로잡습니다.")
        notice.setWordWrap(True)
        theme.set_tone(notice, "muted")

        self.submit_btn = theme.set_variant(QPushButton("연장 확정"), "primary")
        self.submit_btn.clicked.connect(self.submit)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addWidget(self.preview)
        lay.addWidget(self.problem)
        lay.addWidget(notice)
        lay.addLayout(button_row(cancel, self.submit_btn))
        self.refresh_preview()

    def refresh_preview(self) -> None:
        self.errors.clear()
        self.problem.setText("")
        try:
            p = self.ctx.extensions.preview(self.order_id, self.new_due.value(), self.discount.value())
        except DomainError as exc:
            self.preview.setText("")
            if not self.errors.show(exc):
                self.problem.setText(exc.message)
            self.submit_btn.setEnabled(False)
            return
        q = p.quote
        text = (f"연장 장비: {', '.join(p.asset_codes)}<br>"
                f"추가 대여일수 <b>{q.added_days}일</b> · 추가 대여료 {format_won(q.base_won)}")
        if q.discount_won:
            text += f" − 할인 {format_won(q.discount_won)}"
        text += f" = <b>{format_won(q.amount_won)}</b>"
        if q.added_days == 0:
            text += "<br>같은 요금 일수 안이라 추가 요금 없이 반납 예정만 바뀝니다."
        if p.overdue_now:
            text += "<br>지금 연체 중입니다. 연장하면 원래 반납 예정 이후 시간은 연체료 대신 연장 요금으로 계산됩니다."
        self.preview.setText(text)
        if p.conflicts:
            self.problem.setText("늘린 기간에 다른 일정이 있어 연장할 수 없습니다.\n"
                                 + "\n".join(f"- {describe_conflict(c)}" for c in p.conflicts))
        self.submit_btn.setEnabled(p.can_extend)

    def submit(self) -> bool:
        new_due = self.new_due.value()
        text = f"반납 예정을 {format_dt(new_due)}(으)로 연장합니다."
        p = feedback.run(self, lambda: self.ctx.extensions.preview(
            self.order_id, new_due, self.discount.value()))
        if p is None:
            return False
        if p.quote.amount_won:
            text += f"\n추가 대여료 {format_won(p.quote.amount_won)}이(가) 청구됩니다."
        if not feedback.confirm(self, text, "연장 확인"):
            return False

        def on_error(exc: DomainError) -> bool:
            if isinstance(exc, ConflictError):
                self.refresh_preview()
                return False  # 메시지 상자로도 알린다
            return self.errors.show(exc)

        result = feedback.run(
            self, lambda: self.ctx.extensions.extend(
                self.order_id, new_due, discount_won=self.discount.value(), note=self.note.text()),
            on_domain_error=on_error,
        )
        if result is None:
            return False
        self.result_order = result
        feedback.info(self, f"{result.order_no} 반납 예정을 {format_dt(result.due_at)}(으)로 연장했습니다.")
        self.accept()
        return True
