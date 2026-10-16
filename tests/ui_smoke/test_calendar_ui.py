"""예약 달력 화면(U9): 막대·줄·이동, 막대 더블클릭, 칸 선택 → 새 예약, 마법사 미리 채우기, 500장비 그리기."""

import time
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetSelectionRange

from rentdesk.domain.calendar import BarKind
from rentdesk.domain.models import OrderStatus
from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard
from rentdesk.ui.main_window import MainWindow
from rentdesk.ui.pages.calendar import BAR_ROLE, CalendarPage
from tests.support import bulk_load_orders, kst


class FakeNav:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def open_order(self, order_id):
        self.calls.append(("order", order_id))

    def open_asset(self, asset_id):
        self.calls.append(("asset", asset_id))

    def open_customer(self, customer_id):
        self.calls.append(("customer", customer_id))

    def new_reservation(self, customer_id=None, *, asset_ids=(), start_at=None, due_at=None):
        self.calls.append(("new", list(asset_ids), start_at, due_at))

    def goto(self, page, **filters):
        self.calls.append(("goto", page))

    def data_changed(self):
        pass


def make_page(ctx):
    nav = FakeNav()
    return CalendarPage(ctx, nav), nav


def select(page, row, first, last):
    page.table.clearSelection()
    page.table.setRangeSelected(QTableWidgetSelectionRange(row, first, row, last), True)


def test_week_view_draws_bars_with_spans_and_lanes(ctx, svc):
    a = svc.add_asset("CAM-1")
    svc.add_asset("CAM-2")
    k = svc.add_customer("홍길동")
    first = svc.book(k.id, [a.id], kst(10, 20, 9), kst(10, 21, 9))
    second = svc.book(k.id, [a.id], kst(10, 21, 15), kst(10, 22, 15))
    page, _ = make_page(ctx)  # 시계 10/19(월) → 이번 주 월~일

    assert page.period_label.text() == "2026년 10월 19일 ~ 25일"
    assert page.table.columnCount() == 7
    assert page.table.rowCount() == 3  # CAM-1은 같은 날 반납·출고로 2줄, CAM-2는 1줄
    assert page.table.horizontalHeaderItem(0).text() == "10/19\n오늘"
    assert page.table.horizontalHeaderItem(6).text() == "10/25\n일"
    assert page.table.verticalHeaderItem(0).text().startswith("CAM-1")
    assert page.table.verticalHeaderItem(1).text() == ""
    bar1 = page.bar_at(0, 1)
    assert bar1.order_id == first.id and bar1.kind == BarKind.CONFIRMED
    assert page.table.columnSpan(0, 1) == 2
    assert page.bar_at(1, 2).order_id == second.id
    assert page.table.item(0, 1).toolTip().startswith("[예약확정]")


def test_navigation_week_month_and_today(ctx, svc):
    svc.add_asset("CAM-1")
    page, _ = make_page(ctx)
    page.move(1)
    assert page.period_label.text() == "2026년 10월 26일 ~ 11월 1일"
    page.go_today()
    page.mode.setCurrentIndex(page.mode.findData("month"))
    assert page.period_label.text() == "2026년 10월"
    assert page.table.columnCount() == 31
    page.move(1)
    assert page.period_label.text() == "2026년 11월" and page.table.columnCount() == 30
    page.move(-1)
    page.move(-1)
    assert page.period_label.text() == "2026년 9월"


def test_filters_and_draft_toggle(ctx, svc):
    a = svc.add_asset("CAM-1")
    svc.add_asset("LEN-1", category="렌즈")
    k = svc.add_customer()
    svc.book(k.id, [a.id], kst(10, 21, 10), kst(10, 22, 10), confirm=False)
    page, _ = make_page(ctx)
    assert page.bar_at(0, 2).kind == BarKind.DRAFT
    page.show_drafts.setChecked(False)
    assert page.bar_at(0, 2) is None
    page.category.setCurrentIndex(page.category.findText("렌즈"))
    assert page.table.rowCount() == 1
    assert page.table.verticalHeaderItem(0).text().startswith("LEN-1")
    assert not page.empty.isVisible()


def test_double_click_bar_opens_order_or_maintenance(ctx, svc):
    a = svc.add_asset("CAM-1")
    b = svc.add_asset("CAM-2")
    order = svc.book(svc.add_customer().id, [a.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.maintenance.create_block(b.id, kst(10, 22, 9), kst(10, 23, 9), "점검")
    page, nav = make_page(ctx)
    page._on_double_click(0, 1)
    page._on_double_click(1, 3)
    assert nav.calls == [("order", order.id), ("goto", "maintenance")]


def test_selection_opens_wizard_with_asset_and_period(ctx, svc, ui):
    svc.add_asset("CAM-1")
    b = svc.add_asset("CAM-2")
    page, nav = make_page(ctx)
    assert not page.new_btn.isEnabled()
    select(page, 1, 2, 4)
    assert page.selected_cells() == (b.id, 2, 4)
    assert page.new_btn.isEnabled()
    assert page.selection_label.text() == "CAM-2 · 10/21 ~ 10/23"
    page.new_from_selection()
    assert nav.calls == [("new", [b.id], kst(10, 21, 10), kst(10, 23, 10))]

    # 빈 칸 더블클릭 = 하루 예약
    page._on_double_click(1, 5)
    assert nav.calls[-1] == ("new", [b.id], kst(10, 24, 10), kst(10, 25, 10))


def test_selection_rules_busy_warning_and_past_days(ctx, svc, ui):
    a = svc.add_asset("CAM-1")
    svc.add_asset("CAM-2")
    svc.book(svc.add_customer().id, [a.id], kst(10, 22, 10), kst(10, 23, 10))
    page, nav = make_page(ctx)
    page.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 1), True)  # 두 장비에 걸침
    assert page.selected_cells() is None and not page.new_btn.isEnabled()
    select(page, 0, 4, 5)  # 10/23 칸: 예약 막대가 걸친 날
    assert "겹치는 예약" in page.selection_label.text()
    assert page.new_btn.isEnabled()  # 시각이 안 겹치면 예약 가능 — 마법사가 최종 확인

    page.move(-1)  # 지난주
    select(page, 1, 0, 1)
    page.new_from_selection()
    assert ui.texts("warn") and "지난 날짜" in ui.texts("warn")[-1]
    assert not [c for c in nav.calls if c[0] == "new"]


def test_wizard_prefilled_from_calendar_confirms(ctx, svc, ui):
    a = svc.add_asset("CAM-1")
    svc.add_asset("CAM-2")
    k = svc.add_customer("홍길동")
    wz = ReservationWizard(ctx, customer_id=k.id, asset_ids=[a.id],
                           start_at=kst(10, 21, 10), due_at=kst(10, 23, 10))
    assert wz.start.value() == kst(10, 21, 10)
    assert wz.due.value() == kst(10, 23, 10)
    assert wz.go_next()
    checked = [
        wz.asset_table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(wz.asset_table.rowCount())
        if wz.asset_table.item(r, 0).checkState() == Qt.CheckState.Checked
    ]
    assert checked == [a.id]
    assert wz.go_next()
    assert wz.save(confirm=True)
    assert wz.result_order.status == OrderStatus.CONFIRMED
    assert [i.asset_id for i in wz.result_order.items] == [a.id]


def test_main_window_calendar_menu_and_goto_day(ctx, svc):
    svc.add_asset("CAM-1")
    window = MainWindow(ctx)
    window.goto("calendar", day=date(2026, 11, 3))
    page = window.page_by_key["calendar"]
    assert window.page_title.text() == "예약 달력"
    assert page.period_label.text() == "2026년 11월 2일 ~ 8일"
    window.close()


def test_month_view_500_assets_renders_quickly(ctx, conn, svc):
    bulk_load_orders(conn, 500, 10_000)
    page, _ = make_page(ctx)
    page.mode.setCurrentIndex(page.mode.findData("month"))
    started = time.perf_counter()
    page.refresh()
    elapsed = time.perf_counter() - started
    assert page.table.rowCount() >= 500
    assert any(page.table.item(r, c) is not None and page.table.item(r, c).data(BAR_ROLE)
               for r in range(5) for c in range(31))
    assert elapsed < 1.5, f"{elapsed:.2f}s"
