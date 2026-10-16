"""출고/반납 작업 목록 (S-05, S-06 진입점): 출고 대기(확정) / 반납 대기(대여 중)."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from rentdesk.domain.display import format_dt
from rentdesk.domain.models import OrderStatus
from rentdesk.ui import feedback, theme
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DataTable


class RentalsPage(Page):
    title = "출고/반납"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        self.to_checkout = DataTable(["시작", "반납 예정", "예약번호", "고객", "장비 수"])
        self.to_return = DataTable(["약정 반납", "예약번호", "고객", "대여 중", "반납 완료", "연체"],
                                   badge_columns=(5,))
        self.to_checkout.activated_key.connect(lambda k: self.open_checkout(int(k)))
        self.to_return.activated_key.connect(lambda k: self.open_return(int(k)))
        checkout_btn = theme.set_variant(QPushButton("출고 처리"), "primary")
        checkout_btn.clicked.connect(lambda: self._selected(self.to_checkout, self.open_checkout))
        return_btn = QPushButton("반납 처리")
        return_btn.clicked.connect(lambda: self._selected(self.to_return, self.open_return))
        extend_btn = QPushButton("연장")
        extend_btn.clicked.connect(lambda: self._selected(self.to_return, self.open_extension))
        detail_btn = QPushButton("예약 상세")
        detail_btn.clicked.connect(self._detail)

        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>출고 대기 (확정 예약)</b>"))
        head.addStretch(1)
        head.addWidget(checkout_btn)
        lay.addLayout(head)
        lay.addWidget(self.to_checkout, 1)
        head2 = QHBoxLayout()
        head2.addWidget(QLabel("<b>반납 대기 (대여 중)</b> — 일부 장비만 먼저 반납할 수 있습니다."))
        head2.addStretch(1)
        head2.addWidget(return_btn)
        head2.addWidget(extend_btn)
        head2.addWidget(detail_btn)
        lay.addLayout(head2)
        lay.addWidget(self.to_return, 1)
        self.refresh()

    def refresh(self) -> None:
        now = self.ctx.now()
        confirmed = sorted(
            self.ctx.reservations.list_order_rows(statuses=[OrderStatus.CONFIRMED]),
            key=lambda r: r.order.start_at,
        )
        self.to_checkout.set_rows([
            (r.order.id, [format_dt(r.order.start_at), format_dt(r.order.due_at), r.order.order_no,
                          r.customer_name, r.item_count])
            for r in confirmed
        ], align_right=(4,))
        renting = sorted(
            self.ctx.reservations.list_order_rows(statuses=[OrderStatus.IN_RENTAL]),
            key=lambda r: r.order.due_at,
        )
        overdue = {r.order.id for r in renting if now > r.order.due_at}
        self.to_return.set_rows([
            (r.order.id, [format_dt(r.order.due_at), r.order.order_no, r.customer_name, r.out_count,
                          r.returned_count, "연체" if now > r.order.due_at else ""])
            for r in renting
        ], highlight=lambda k: k in overdue)

    def _selected(self, table: DataTable, fn) -> None:
        key = table.selected_key()
        if key is None:
            feedback.warn(self, "목록에서 예약을 먼저 선택해주세요.")
            return
        fn(int(key))

    def _detail(self) -> None:
        key = self.to_return.selected_key() or self.to_checkout.selected_key()
        if key is None:
            feedback.warn(self, "목록에서 예약을 먼저 선택해주세요.")
            return
        self.nav.open_order(int(key))

    def open_checkout(self, order_id: int) -> None:
        from rentdesk.ui.dialogs.checkout_dialog import CheckoutDialog

        if CheckoutDialog(self.ctx, order_id, self).exec():
            self.nav.data_changed()

    def open_return(self, order_id: int) -> None:
        from rentdesk.ui.dialogs.return_dialog import ReturnDialog

        if ReturnDialog(self.ctx, order_id, self).exec():
            self.nav.data_changed()

    def open_extension(self, order_id: int) -> None:
        from rentdesk.ui.dialogs.extension_dialog import ExtensionDialog

        if ExtensionDialog(self.ctx, order_id, self).exec():
            self.nav.data_changed()
