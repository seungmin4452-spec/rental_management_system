"""메인 창: 좌측 메뉴 + 우측 업무 화면 + 상단 전역 검색 (PRD 5 공통 UI 원칙, W-01).

이 파일에는 SQL/요금 계산 로직을 두지 않는다. 모든 데이터는 `AppContext`의 서비스로 읽는다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.services.search import RESULT_KIND_LABELS, ResultKind, SearchResult
from rentdesk.ui import feedback
from rentdesk.ui.pages.assets import AssetsPage
from rentdesk.ui.pages.base import Page
from rentdesk.ui.pages.customers import CustomersPage
from rentdesk.ui.pages.dashboard import DashboardPage
from rentdesk.ui.pages.maintenance import MaintenancePage
from rentdesk.ui.pages.rentals import RentalsPage
from rentdesk.ui.pages.reports import ReportsPage
from rentdesk.ui.pages.reservations import ReservationsPage
from rentdesk.ui.pages.settings import SettingsPage
from rentdesk.ui.pages.settlements import SettlementsPage

WINDOW_TITLE = "RentDesk Lite"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MIN_WIDTH = 1100
MIN_HEIGHT = 700

# (page key, 메뉴 이름, 화면 클래스)
PAGES: list[tuple[str, str, type[Page]]] = [
    ("dashboard", "대시보드", DashboardPage),
    ("assets", "장비 관리", AssetsPage),
    ("customers", "고객 관리", CustomersPage),
    ("reservations", "예약 관리", ReservationsPage),
    ("rentals", "출고/반납", RentalsPage),
    ("settlements", "정산 내역", SettlementsPage),
    ("maintenance", "정비 관리", MaintenancePage),
    ("reports", "보고서", ReportsPage),
    ("settings", "설정", SettingsPage),
]
MENU_ITEMS = [label for _, label, _ in PAGES]


class SearchResultsDialog(QDialog):
    def __init__(self, results: list[SearchResult], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("검색 결과")
        self.resize(520, 420)
        self.chosen: SearchResult | None = None
        self.list = QListWidget()
        for r in results:
            item = QListWidgetItem(f"[{RESULT_KIND_LABELS[r.kind]}] {r.title}   {r.detail}")
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.list.addItem(item)
        self.list.itemActivated.connect(self._choose)
        open_btn = QPushButton("열기")
        open_btn.clicked.connect(lambda: self._choose(self.list.currentItem()))
        lay = QVBoxLayout(self)
        lay.addWidget(self.list)
        lay.addWidget(open_btn)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _choose(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self.chosen = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class MainWindow(QMainWindow):
    demo_toggle_requested = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        title = WINDOW_TITLE + (" [예제 데이터 모드]" if ctx.is_demo else "")
        self.setWindowTitle(title)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        central = QWidget(self)
        outer = QVBoxLayout(central)

        if ctx.is_demo:
            banner = QLabel("예제 데이터 모드 — 실제 데이터가 아닙니다. 입력 내용은 실제 데이터에 저장되지 않습니다.")
            banner.setStyleSheet("background: #fff3cd; color: #664d03; padding: 6px;")
            outer.addWidget(banner)

        top = QHBoxLayout()
        top.addWidget(QLabel(f"<b>{WINDOW_TITLE}</b>"))
        top.addStretch(1)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("장비번호·고객명·예약번호 검색 후 Enter")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(320)
        self.search_box.returnPressed.connect(self.run_search)
        top.addWidget(self.search_box)
        outer.addLayout(top)

        body = QHBoxLayout()
        self.menu_list = QListWidget(central)
        self.menu_list.addItems(MENU_ITEMS)
        self.menu_list.setMaximumWidth(200)
        self.menu_list.setSpacing(3)
        self.pages = QStackedWidget(central)
        self.page_by_key: dict[str, Page] = {}
        for key, _label, cls in PAGES:
            page = cls(ctx, self)
            self.page_by_key[key] = page
            self.pages.addWidget(page)
        self.page_by_key["settings"].demo_toggle_requested.connect(self.demo_toggle_requested.emit)
        self.menu_list.currentRowChanged.connect(self._on_menu)
        body.addWidget(self.menu_list)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)
        self._install_shortcuts()
        self.menu_list.setCurrentRow(0)

    def _install_shortcuts(self) -> None:
        find = QAction("검색", self)
        find.setShortcut(QKeySequence(QKeySequence.StandardKey.Find))
        find.triggered.connect(lambda: (self.search_box.setFocus(), self.search_box.selectAll()))
        refresh = QAction("새로 고침", self)
        refresh.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Refresh))
        refresh.triggered.connect(self.refresh_current)
        new = QAction("새 예약", self)
        new.setShortcut(QKeySequence(QKeySequence.StandardKey.New))
        new.triggered.connect(lambda: self.new_reservation())
        quit_action = QAction("종료", self)
        quit_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
        quit_action.triggered.connect(self.close)
        menu = self.menuBar().addMenu("파일")
        for a in (new, find, refresh):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(quit_action)

    # ------------------------------------------------------------------ Navigator

    def current_page(self) -> Page:
        return self.pages.currentWidget()

    def _on_menu(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        self.refresh_current()

    def refresh_current(self) -> None:
        feedback.run(self, self.current_page().refresh)

    def data_changed(self) -> None:
        self.refresh_current()

    def goto(self, page: str, **filters: object) -> None:
        keys = [k for k, _, _ in PAGES]
        target = self.page_by_key[page]
        if filters:
            target.apply_filters(**filters)
        self.menu_list.setCurrentRow(keys.index(page))

    def open_order(self, order_id: int) -> None:
        from rentdesk.ui.dialogs.order_detail import OrderDetailDialog

        dlg = OrderDetailDialog(self.ctx, order_id, self)
        dlg.exec()
        if dlg.changed:
            self.data_changed()

    def open_asset(self, asset_id: int) -> None:
        from rentdesk.ui.dialogs.asset_dialog import AssetDetailDialog

        dlg = AssetDetailDialog(self.ctx, asset_id, self)
        dlg.exec()
        self.data_changed()

    def open_customer(self, customer_id: int) -> None:
        self.goto("customers")
        self.page_by_key["customers"].open_customer(customer_id)

    def new_reservation(self, customer_id: int | None = None) -> None:
        from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard

        dlg = ReservationWizard(self.ctx, parent=self, customer_id=customer_id)
        if dlg.exec() and dlg.result_order:
            self.data_changed()

    # ------------------------------------------------------------------ search

    def run_search(self) -> list[SearchResult] | None:
        query = self.search_box.text()
        results = feedback.run(self, lambda: self.ctx.search.search(query))
        if results is None:
            return None
        if not results:
            feedback.info(self, f"'{query.strip()}'에 해당하는 장비·고객·예약이 없습니다.", "검색 결과")
            return results
        dlg = SearchResultsDialog(results, self)
        if dlg.exec() and dlg.chosen:
            self.open_result(dlg.chosen)
        return results

    def open_result(self, result: SearchResult) -> None:
        if result.kind == ResultKind.ASSET:
            self.open_asset(result.entity_id)
        elif result.kind == ResultKind.CUSTOMER:
            self.open_customer(result.entity_id)
        else:
            self.open_order(result.entity_id)
