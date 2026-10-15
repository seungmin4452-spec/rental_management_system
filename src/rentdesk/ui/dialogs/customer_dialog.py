"""고객 등록/수정 (S-03). 민감정보를 수집하지 않으며 메모에도 적지 않도록 안내한다."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.models import Customer, CustomerType
from rentdesk.services.customers import CustomerInput
from rentdesk.ui import feedback
from rentdesk.ui.widgets import FormErrors

CUSTOMER_TYPE_LABELS = {CustomerType.INDIVIDUAL: "개인", CustomerType.BUSINESS: "사업자"}
PRIVACY_NOTICE = (
    "주민등록번호·신용카드번호·신분증 사본 등 민감한 정보는 입력하지 마세요(메모 포함)."
)


class CustomerDialog(QDialog):
    def __init__(self, ctx: AppContext, customer: Customer | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.customer = customer
        self.result_customer: Customer | None = None
        self.setWindowTitle("고객 수정" if customer else "고객 등록")
        self.setMinimumWidth(440)

        form = QFormLayout()
        self.errors = FormErrors(form)
        self.name = QLineEdit()
        self.name.setMaxLength(100)
        self.kind = QComboBox()
        for kind, label in CUSTOMER_TYPE_LABELS.items():
            self.kind.addItem(label, kind)
        self.phone = QLineEdit()
        self.phone.setMaxLength(30)
        self.phone.setPlaceholderText("선택 입력")
        self.email = QLineEdit()
        self.email.setMaxLength(100)
        self.email.setPlaceholderText("선택 입력")
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(60)

        self.errors.add_row("고객명/업체명 *", self.name, "name")
        self.errors.add_row("고객 구분", self.kind, "customer_type")
        self.errors.add_row("전화번호", self.phone, "phone")
        self.errors.add_row("이메일", self.email, "email")
        self.errors.add_row("메모", self.note, "note")
        notice = QLabel(PRIVACY_NOTICE)
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #8a6d00;")

        if customer:
            self.name.setText(customer.name)
            self.kind.setCurrentIndex(self.kind.findData(customer.customer_type))
            self.phone.setText(customer.phone or "")
            self.email.setText(customer.email or "")
            self.note.setPlainText(customer.note or "")

        buttons = QDialogButtonBox()
        buttons.addButton("저장", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(notice)
        lay.addWidget(buttons)

    def submit(self) -> bool:
        self.errors.clear()
        data = CustomerInput(
            name=self.name.text(), customer_type=self.kind.currentData(),
            phone=self.phone.text(), email=self.email.text(), note=self.note.toPlainText(),
        )
        if self.customer:
            action = lambda: self.ctx.customers.update_customer(self.customer.id, data)  # noqa: E731
        else:
            action = lambda: self.ctx.customers.create_customer(data)  # noqa: E731
        result = feedback.run(self, action, on_domain_error=self.errors.show)
        if result is None:
            return False
        self.result_customer = result
        self.accept()
        return True
