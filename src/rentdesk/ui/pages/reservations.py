"""S-04 예약 목록. 상태·기간·고객(이름/예약번호 검색) 필터."""

from __future__ import annotations

from datetime import timedelta

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.display import format_dt, format_won, seoul_day_range, seoul_today
from rentdesk.domain.models import ORDER_STATUS_LABELS, OrderStatus
from rentdesk.ui import feedback, theme
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DataTable, DateEdit, empty_hint


class ReservationsPage(Page):
    title = "예약 관리"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        self.query = QLineEdit()
        self.query.setPlaceholderText("예약번호 또는 고객명")
        self.query.setClearButtonEnabled(True)
        self.query.textChanged.connect(self.refresh)
        self.status = QComboBox()
        self.status.addItem("전체 상태", None)
        self.status.addItem("진행 중(임시·확정·대여 중)", "ACTIVE")
        for s, label in ORDER_STATUS_LABELS.items():
            self.status.addItem(label, s)
        self.status.currentIndexChanged.connect(self.refresh)
        today = seoul_today(ctx.now())
        self.use_period = QCheckBox("기간")
        self.period_from = DateEdit(today - timedelta(days=30))
        self.period_to = DateEdit(today + timedelta(days=60))
        for w in (self.period_from, self.period_to):
            w.dateChanged.connect(self.refresh)
            w.setEnabled(False)
        self.use_period.toggled.connect(self._toggle_period)
        filters = QHBoxLayout()
        filters.addWidget(self.query, 1)
        filters.addWidget(self.status)
        filters.addWidget(self.use_period)
        filters.addWidget(self.period_from)
        filters.addWidget(QLabel("~"))
        filters.addWidget(self.period_to)

        self.table = DataTable(
            ["예약번호", "고객", "예약 기간", "장비 수", "상태", "예상 대여료", "출고/반납"],
            badge_columns=(4,),
        )
        self.table.activated_key.connect(lambda key: self.nav.open_order(int(key)))
        self.empty = empty_hint("아직 예약이 없습니다.", "새 예약", lambda: self.nav.new_reservation())
        new_btn = theme.set_variant(QPushButton("새 예약"), "primary")
        new_btn.clicked.connect(lambda: self.nav.new_reservation())
        detail_btn = QPushButton("상세 보기")
        detail_btn.clicked.connect(self.open_selected)
        actions = QHBoxLayout()
        actions.addWidget(new_btn)
        actions.addWidget(detail_btn)
        actions.addStretch(1)
        self.count = QLabel()
        actions.addWidget(self.count)

        lay = QVBoxLayout(self)
        lay.addLayout(filters)
        lay.addLayout(actions)
        lay.addWidget(self.empty)
        lay.addWidget(self.table, 1)
        self.refresh()

    def _toggle_period(self, on: bool) -> None:
        self.period_from.setEnabled(on)
        self.period_to.setEnabled(on)
        self.refresh()

    def apply_filters(self, **filters: object) -> None:
        if "query" in filters:
            self.query.setText(str(filters["query"]))
        if "status" in filters:
            self.status.setCurrentIndex(max(0, self.status.findData(filters["status"])))

    def refresh(self) -> None:
        data = self.status.currentData()
        if data == "ACTIVE":
            statuses = [OrderStatus.DRAFT, OrderStatus.CONFIRMED, OrderStatus.IN_RENTAL]
        elif data is None:
            statuses = None
        else:
            statuses = [OrderStatus(data)]
        start = end = None
        if self.use_period.isChecked():
            if self.period_from.value() > self.period_to.value():
                self.count.setText("기간 시작일이 종료일보다 늦습니다.")
                self.table.set_rows([])
                return
            start = seoul_day_range(self.period_from.value())[0]
            end = seoul_day_range(self.period_to.value())[1]
        rows = self.ctx.reservations.list_order_rows(
            statuses=statuses, period_start=start, period_end=end, query=self.query.text()
        )
        self.table.set_rows([
            (r.order.id, [r.order.order_no, r.customer_name,
                          f"{format_dt(r.order.start_at)} ~ {format_dt(r.order.due_at)}",
                          r.item_count, ORDER_STATUS_LABELS[r.order.status],
                          format_won(r.expected_total_rental_fee_won), r.progress_label])
            for r in rows
        ], align_right=(3, 5))
        self.count.setText(f"{len(rows)}건")
        no_filter = not self.query.text().strip() and statuses is None and not self.use_period.isChecked()
        self.empty.setVisible(not rows and no_filter)

    def open_selected(self) -> None:
        key = self.table.selected_key()
        if key is None:
            feedback.warn(self, "예약을 먼저 선택해주세요.")
            return
        self.nav.open_order(int(key))
