"""정산 내역 목록 (S-07 진입점)."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.models import ORDER_STATUS_LABELS
from rentdesk.ui import feedback, theme
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DataTable


class SettlementsPage(Page):
    title = "정산 내역"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        self.unsettled_only = QCheckBox("미정산만 보기")
        self.unsettled_only.setChecked(True)
        self.unsettled_only.toggled.connect(self.refresh)
        open_btn = theme.set_variant(QPushButton("정산 열기"), "primary")
        open_btn.clicked.connect(self.open_selected)
        self.count = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.unsettled_only)
        top.addWidget(open_btn)
        top.addStretch(1)
        top.addWidget(self.count)
        self.table = DataTable(
            ["예약번호", "고객", "상태", "출고", "청구 합계", "대여료 순수금", "미수금", "보증금 보유", "정산"],
            badge_columns=(2, 8),
        )
        self.table.activated_key.connect(lambda k: self.open_settlement(int(k)))
        note = QLabel("반납 완료와 정산 완료는 별개입니다. 미수금·보증금 보유액이 모두 0원이어야 정산 완료입니다.")
        theme.set_tone(note, "muted")
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(note)
        self.refresh()

    def refresh(self) -> None:
        rows = self.ctx.settlements.list_rows(unsettled_only=self.unsettled_only.isChecked())
        unsettled = {r.order.id for r in rows if not r.summary.is_settled}
        self.table.set_rows([
            (r.order.id, [r.order.order_no, r.customer_name, ORDER_STATUS_LABELS[r.order.status],
                          format_dt(r.order.actual_checkout_at), format_won(r.summary.charge_total_won),
                          format_won(r.summary.net_rental_receipts_won),
                          format_won(r.summary.amount_due_won), format_won(r.summary.deposit_held_won),
                          "완료" if r.summary.is_settled else "미정산"])
            for r in rows
        ], highlight=lambda k: k in unsettled, align_right=(4, 5, 6, 7))
        self.count.setText(f"{len(rows)}건")

    def open_selected(self) -> None:
        key = self.table.selected_key()
        if key is None:
            feedback.warn(self, "정산할 예약을 먼저 선택해주세요.")
            return
        self.open_settlement(int(key))

    def open_settlement(self, order_id: int) -> None:
        from rentdesk.ui.dialogs.settlement_dialog import SettlementDialog

        dlg = SettlementDialog(self.ctx, order_id, self)
        dlg.exec()
        if dlg.changed:
            self.nav.data_changed()
