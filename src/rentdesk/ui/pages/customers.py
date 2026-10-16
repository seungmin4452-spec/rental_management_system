"""S-03 고객 관리."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.models import Customer
from rentdesk.ui import feedback, theme
from rentdesk.ui.dialogs.customer_dialog import CUSTOMER_TYPE_LABELS, CustomerDialog
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DataTable, empty_hint


class CustomersPage(Page):
    title = "고객 관리"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        self.query = QLineEdit()
        self.query.setPlaceholderText("고객명·전화번호·이메일 검색")
        self.query.setClearButtonEnabled(True)
        self.query.textChanged.connect(self.refresh)
        self.include_archived = QCheckBox("숨긴 고객 포함")
        self.include_archived.toggled.connect(self.refresh)
        filters = QHBoxLayout()
        filters.addWidget(self.query, 1)
        filters.addWidget(self.include_archived)

        self.table = DataTable(["고객명/업체명", "구분", "전화번호", "이메일", "숨김"])
        self.table.activated_key.connect(lambda key: self.open_customer(int(key)))
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.empty = empty_hint("등록된 고객이 없습니다.", "고객 등록", self.create)

        self.new_btn = theme.set_variant(QPushButton("고객 등록"), "primary")
        self.edit_btn = QPushButton("수정")
        self.archive_btn = QPushButton("거래 중지(숨김)")
        self.delete_btn = theme.set_variant(QPushButton("삭제"), "danger")
        self.reserve_btn = QPushButton("새 예약")
        self.orders_btn = QPushButton("예약 보기")
        self.new_btn.clicked.connect(self.create)
        self.edit_btn.clicked.connect(lambda: self._with_selected(lambda c: self.open_customer(c.id)))
        self.archive_btn.clicked.connect(self.toggle_archive)
        self.delete_btn.clicked.connect(self.delete)
        self.reserve_btn.clicked.connect(
            lambda: self._with_selected(lambda c: self.nav.new_reservation(c.id))
        )
        self.orders_btn.clicked.connect(
            lambda: self._with_selected(lambda c: self.nav.goto("reservations", query=c.name))
        )
        actions = QHBoxLayout()
        for b in (self.new_btn, self.edit_btn, self.archive_btn, self.delete_btn,
                  self.reserve_btn, self.orders_btn):
            actions.addWidget(b)
        actions.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(filters)
        lay.addLayout(actions)
        lay.addWidget(self.empty)
        lay.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        self.customers = {
            c.id: c for c in self.ctx.customers.search_customers(
                self.query.text(), include_archived=self.include_archived.isChecked()
            )
        }
        self.table.set_rows([
            (c.id, [c.name, CUSTOMER_TYPE_LABELS[c.customer_type], c.phone or "", c.email or "",
                    "숨김" if c.is_archived else ""])
            for c in self.customers.values()
        ])
        self.empty.setVisible(not self.customers and not self.query.text().strip())
        self._update_buttons()

    def selected_customer(self) -> Customer | None:
        key = self.table.selected_key()
        return self.customers.get(key) if key is not None else None

    def _with_selected(self, fn) -> None:
        customer = self.selected_customer()
        if customer is None:
            feedback.warn(self, "고객을 먼저 선택해주세요.")
            return
        fn(customer)

    def _update_buttons(self) -> None:
        c = self.selected_customer()
        for b in (self.edit_btn, self.archive_btn, self.delete_btn, self.reserve_btn, self.orders_btn):
            b.setEnabled(c is not None)
        self.reserve_btn.setEnabled(c is not None and not c.is_archived)
        self.archive_btn.setText("숨김 해제" if c is not None and c.is_archived else "거래 중지(숨김)")

    def open_customer(self, customer_id: int) -> None:
        customer = self.ctx.customers.get_customer(customer_id)
        if CustomerDialog(self.ctx, customer, self).exec():
            self.nav.data_changed()

    def create(self) -> None:
        dlg = CustomerDialog(self.ctx, parent=self)
        if dlg.exec():
            self.nav.data_changed()
            if dlg.result_customer:
                self.table.select_key(dlg.result_customer.id)

    def toggle_archive(self) -> None:
        def run(c: Customer) -> None:
            archived = not c.is_archived
            text = (f"{c.name} 고객을 거래 중지(숨김)합니다. 기존 예약 이력은 유지되고 신규 예약 대상에서 제외됩니다."
                    if archived else f"{c.name} 고객의 숨김을 해제합니다.")
            if feedback.confirm(self, text, "고객 숨김") and feedback.run(
                self, lambda: self.ctx.customers.set_archived(c.id, archived)
            ):
                self.nav.data_changed()
        self._with_selected(run)

    def delete(self) -> None:
        def run(c: Customer) -> None:
            if not feedback.confirm(
                self, f"{c.name} 고객을 삭제합니다. 예약 이력이 없는 고객만 삭제됩니다.", "고객 삭제"
            ):
                return
            if feedback.run(self, lambda: self.ctx.customers.delete_customer(c.id) or True):
                self.nav.data_changed()
        self._with_selected(run)
