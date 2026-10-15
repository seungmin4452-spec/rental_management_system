"""S-01 대시보드 (W-01)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.display import format_duration, format_dt, format_won, seoul_today
from rentdesk.domain.models import ORDER_STATUS_LABELS
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DataTable, DateEdit, StatCard

TAB_OVERDUE, TAB_CHECKOUT, TAB_RETURN, TAB_UPCOMING, TAB_MAINTENANCE, TAB_UNSETTLED = range(6)


class DashboardPage(Page):
    title = "대시보드"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        self.day = DateEdit(seoul_today(ctx.now()))
        self.day.dateChanged.connect(self.refresh)
        today_btn = QPushButton("오늘")
        today_btn.clicked.connect(lambda: self.day.set_value(seoul_today(self.ctx.now())))
        new_btn = QPushButton("새 예약")
        new_btn.clicked.connect(lambda: self.nav.new_reservation())
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>오늘의 운영 현황</b>"))
        top.addStretch(1)
        top.addWidget(QLabel("기준일"))
        top.addWidget(self.day)
        top.addWidget(today_btn)
        top.addWidget(new_btn)

        self.cards = {
            "rented": StatCard("대여 중 장비"),
            "checkout": StatCard("출고 예정(대기)"),
            "return": StatCard("반납 예정(대기)"),
            "overdue": StatCard("현재 연체 품목"),
            "unsettled": StatCard("반납 완료·미정산"),
        }
        cards = QHBoxLayout()
        for c in self.cards.values():
            cards.addWidget(c)

        self.risk = QLabel()
        self.risk.setWordWrap(True)
        self.risk.setStyleSheet("color: #c62828;")

        self.tabs = QTabWidget()
        self.overdue = DataTable(["장비번호", "장비명", "고객", "예약번호", "약정 반납", "지연 시간", "예상 연체료"])
        self.checkouts = DataTable(["예정 시각", "예약번호", "고객", "장비번호", "장비명", "처리"])
        self.returns = DataTable(["예정 시각", "예약번호", "고객", "장비번호", "장비명", "처리"])
        self.upcoming = DataTable(["시작", "반납 예정", "예약번호", "고객", "장비 수", "예상 대여료"])
        self.maintenance = DataTable(["장비번호", "장비명", "시작", "종료 예정", "사유"])
        self.unsettled = DataTable(["예약번호", "상태", "청구 합계", "미수금", "보증금 보유"])
        for table, label in [
            (self.overdue, "연체 주의"), (self.checkouts, "출고 일정"), (self.returns, "반납 일정"),
            (self.upcoming, "7일 내 확정 예약"), (self.maintenance, "정비 중 장비"),
            (self.unsettled, "미정산"),
        ]:
            self.tabs.addTab(table, label)
        for table in (self.overdue, self.checkouts, self.returns, self.upcoming, self.unsettled):
            table.activated_key.connect(lambda key: self.nav.open_order(int(key)))
        self.maintenance.activated_key.connect(lambda key: self.nav.open_asset(int(key)))

        self.cards["rented"].clicked.connect(lambda: self.nav.goto("rentals"))
        self.cards["checkout"].clicked.connect(lambda: self.tabs.setCurrentIndex(TAB_CHECKOUT))
        self.cards["return"].clicked.connect(lambda: self.tabs.setCurrentIndex(TAB_RETURN))
        self.cards["overdue"].clicked.connect(lambda: self.tabs.setCurrentIndex(TAB_OVERDUE))
        self.cards["unsettled"].clicked.connect(lambda: self.tabs.setCurrentIndex(TAB_UNSETTLED))

        hint = QLabel("목록 항목을 더블클릭하면 예약 상세(정비 중 장비는 장비 상세)로 이동합니다.")
        hint.setStyleSheet("color: #666;")
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(cards)
        lay.addWidget(self.risk)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(hint)
        self.refresh()

    def refresh(self) -> None:
        d = self.ctx.dashboard.snapshot(self.day.value())
        self.data = d
        is_today = d.day == seoul_today(d.now)
        self.cards["checkout"].title.setText("오늘 출고 예정(대기)" if is_today else f"{d.day} 출고(대기)")
        self.cards["return"].title.setText("오늘 반납 예정(대기)" if is_today else f"{d.day} 반납(대기)")
        self.cards["rented"].set_value(d.rented_asset_count)
        self.cards["checkout"].set_value(d.pending_checkouts)
        self.cards["return"].set_value(d.pending_returns)
        self.cards["overdue"].set_value(len(d.overdue), alert=True)
        self.cards["unsettled"].set_value(d.completed_unsettled_count, alert=True)

        if d.followup_risks:
            self.risk.setText("연체로 인한 후속 예약 위험: " + "; ".join(
                f"{r.overdue.asset_code} 미반납 → 예약 {r.order_no}({format_dt(r.start_at)} 시작)"
                for r in d.followup_risks
            ))
        else:
            self.risk.setText("")

        self.overdue.set_rows([
            (r.order_id, [r.asset_code, r.asset_name, r.customer_name, r.order_no,
                          format_dt(r.due_at), format_duration(r.overdue_seconds),
                          format_won(r.expected_late_fee_won)])
            for r in d.overdue
        ], highlight=lambda _: True, align_right=(6,))
        self.checkouts.set_rows([
            (r.order_id, [format_dt(r.scheduled_at), r.order_no, r.customer_name, r.asset_code,
                          r.asset_name, r.state_label])
            for r in d.checkouts_today
        ])
        self.returns.set_rows([
            (r.order_id, [format_dt(r.scheduled_at), r.order_no, r.customer_name, r.asset_code,
                          r.asset_name, r.state_label])
            for r in d.returns_today
        ])
        self.upcoming.set_rows([
            (r.order.id, [format_dt(r.order.start_at), format_dt(r.order.due_at), r.order.order_no,
                          r.customer_name, r.item_count, format_won(r.expected_total_rental_fee_won)])
            for r in d.upcoming
        ], align_right=(4, 5))
        self.maintenance.set_rows([
            (b.asset_id, [b.asset_code, b.asset_name, format_dt(b.start_at),
                          format_dt(b.end_at, "미정"), b.reason])
            for b in d.maintenance
        ])
        self.unsettled.set_rows([
            (u.order_id, [u.order_no, ORDER_STATUS_LABELS[u.status],
                          format_won(u.summary.charge_total_won), format_won(u.summary.amount_due_won),
                          format_won(u.summary.deposit_held_won)])
            for u in d.unsettled
        ], align_right=(2, 3, 4))
        counts = [len(d.overdue), len(d.checkouts_today), len(d.returns_today), len(d.upcoming),
                  len(d.maintenance), len(d.unsettled)]
        base = ["연체 주의", "출고 일정", "반납 일정", "7일 내 확정 예약", "정비 중 장비", "미정산"]
        for i, (name, n) in enumerate(zip(base, counts, strict=True)):
            self.tabs.setTabText(i, f"{name} ({n})")
