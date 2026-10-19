"""메인 창: 좌측 메뉴 + 우측 업무 화면 + 상단 전역 검색 (PRD 5 공통 UI 원칙, W-01).

이 파일에는 SQL/요금 계산 로직을 두지 않는다. 모든 데이터는 `AppContext`의 서비스로 읽는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
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
from rentdesk.services.auto_backup import AutoBackupSettings
from rentdesk.services.search import RESULT_KIND_LABELS, ResultKind, SearchResult
from rentdesk.ui import feedback, theme
from rentdesk.ui.pages.assets import AssetsPage
from rentdesk.ui.pages.base import Page
from rentdesk.ui.pages.calendar import CalendarPage
from rentdesk.ui.pages.customers import CustomersPage
from rentdesk.ui.pages.dashboard import DashboardPage
from rentdesk.ui.pages.maintenance import MaintenancePage
from rentdesk.ui.pages.rentals import RentalsPage
from rentdesk.ui.pages.reports import ReportsPage
from rentdesk.ui.pages.reservations import ReservationsPage
from rentdesk.ui.pages.settings import SettingsPage
from rentdesk.ui.pages.settlements import SettlementsPage

WINDOW_TITLE = "RentalDesk"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MIN_WIDTH = 1100
MIN_HEIGHT = 700

# (page key, 메뉴 이름, 화면 클래스)
PAGES: list[tuple[str, str, type[Page]]] = [
    ("dashboard", "대시보드", DashboardPage),
    ("calendar", "예약 달력", CalendarPage),
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
        open_btn = theme.set_variant(QPushButton("열기"), "primary")
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
    restore_requested = Signal(object)  # ValidatedBackup

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        title = WINDOW_TITLE + (" [예제 데이터 모드]" if ctx.is_demo else "")
        self.setWindowTitle(title)
        self._fit_to_screen()

        central = QWidget(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        content = QWidget(central)
        content.setObjectName("Content")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(28, 20, 28, 20)
        outer.setSpacing(14)

        if ctx.is_demo:
            banner = QFrame()
            banner.setProperty("banner", "warning")
            bl = QHBoxLayout(banner)
            bl.setContentsMargins(14, 10, 14, 10)
            bl.addWidget(QLabel(
                "예제 데이터 모드 — 실제 데이터가 아닙니다. 입력 내용은 실제 데이터에 저장되지 않습니다."
            ))
            outer.addWidget(banner)

        # 백업 권장 안내(PRD 10.2): 첫 실행·7일 이상 백업하지 않은 경우
        self.backup_banner = QFrame()
        self.backup_banner.setProperty("banner", "info")
        bb = QHBoxLayout(self.backup_banner)
        bb.setContentsMargins(14, 8, 10, 8)
        self.backup_banner_label = QLabel()
        self.backup_banner_label.setWordWrap(True)
        backup_now = QPushButton("백업하러 가기")
        backup_now.clicked.connect(lambda: self.goto("settings"))
        bb.addWidget(self.backup_banner_label, 1)
        bb.addWidget(backup_now)
        outer.addWidget(self.backup_banner)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")
        top.addWidget(self.page_title)
        top.addStretch(1)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("SearchBox")
        self.search_box.setPlaceholderText("장비번호 · 고객명 · 예약번호 검색 후 Enter")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(340)
        self._search_icon = self.search_box.addAction(
            theme.icon("search", "text_muted", size=18), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search_box.returnPressed.connect(self.run_search)
        top.addWidget(self.search_box)
        outer.addLayout(top)

        self.pages = QStackedWidget(content)
        self.page_by_key: dict[str, Page] = {}
        for key, _label, cls in PAGES:
            page = cls(ctx, self)
            if page.layout() is not None:
                page.layout().setContentsMargins(0, 4, 0, 0)
            self.page_by_key[key] = page
            self.pages.addWidget(page)
        settings = self.page_by_key["settings"]
        settings.demo_toggle_requested.connect(self.demo_toggle_requested.emit)
        settings.restore_requested.connect(self.restore_requested.emit)
        settings.backup_created.connect(self.update_backup_banner)
        self.menu_list.currentRowChanged.connect(self._on_menu)
        outer.addWidget(self.pages, 1)
        root.addWidget(content, 1)
        self.setCentralWidget(central)
        self._install_shortcuts()
        self.update_backup_banner()
        self.menu_list.setCurrentRow(0)

    def _fit_to_screen(self) -> None:
        """기본·최소 크기를 화면 작업 영역 안으로 줄인다(배율 자동 조정 후에도 모자랄 때 대비)."""
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is None or avail.width() <= 0 or avail.height() <= 0:
            self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
            self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
            return
        frame_w, frame_h = 20, 45  # 창 테두리·제목 표시줄 여유
        max_w, max_h = avail.width() - frame_w, avail.height() - frame_h
        self.setMinimumSize(min(MIN_WIDTH, max_w), min(MIN_HEIGHT, max_h))
        self.resize(min(DEFAULT_WIDTH, max_w), min(DEFAULT_HEIGHT, max_h))

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(12, 20, 12, 16)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(10, 0, 0, 16)
        brand.setSpacing(10)
        self.brand_mark = QLabel()
        self.brand_mark.setObjectName("BrandMark")
        self.brand_mark.setFixedSize(32, 32)
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(WINDOW_TITLE)
        name.setObjectName("BrandName")
        brand.addWidget(self.brand_mark)
        brand.addWidget(name)
        brand.addStretch(1)
        lay.addLayout(brand)

        self.menu_list = QListWidget(side)
        self.menu_list.setObjectName("SideNav")
        self.menu_list.setIconSize(QSize(20, 20))
        self.menu_list.setSpacing(2)
        self.menu_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for _key, label, _cls in PAGES:
            self.menu_list.addItem(QListWidgetItem(label))
        lay.addWidget(self.menu_list, 1)

        self.sidebar_footer = QFrame()
        self.sidebar_footer.setObjectName("SidebarFooter")
        fl = QVBoxLayout(self.sidebar_footer)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(0)
        caption = QLabel("데이터" if self.ctx.is_demo else "사용권")
        theme.set_tone(caption, "muted")
        self.sidebar_status = QLabel()
        fl.addWidget(caption)
        fl.addWidget(self.sidebar_status)
        lay.addWidget(self.sidebar_footer)
        self._update_sidebar_status()
        self._apply_icons()
        return side

    def _update_sidebar_status(self) -> None:
        if self.ctx.is_demo:
            self.sidebar_status.setText("예제 데이터 모드")
            theme.set_tone(self.sidebar_status, "warning")
            return
        if self.ctx.license is not None and self.ctx.license.check().valid:
            self.sidebar_status.setText("정품 · 이 PC 1대")
            theme.set_tone(self.sidebar_status, "success")
            return
        self.sidebar_footer.hide()

    def _apply_icons(self) -> None:
        """테마(라이트/다크)가 바뀌면 아이콘 색도 다시 만든다."""
        self.brand_mark.setPixmap(theme.icon_pixmap("brand", size=18, stroke=theme.current().on_accent))
        for row, (key, _label, _cls) in enumerate(PAGES):
            self.menu_list.item(row).setIcon(
                theme.icon(key, "text_sub", selected_tone="accent_text")
            )
        if hasattr(self, "_search_icon"):
            self._search_icon.setIcon(theme.icon("search", "text_muted", size=18))

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_icons()
        super().changeEvent(event)

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
        about = QAction("프로그램 정보·문의", self)
        about.triggered.connect(self.show_about)
        self.starter_action = QAction("시작 도우미(장비 종류별 카테고리 추가)", self)
        self.starter_action.triggered.connect(lambda: self.open_starter())
        help_menu = self.menuBar().addMenu("도움말")
        help_menu.addAction(self.starter_action)
        help_menu.addAction(about)

    def open_starter(self, *, first_run: bool = False) -> bool:
        """시작 도우미(M4.6 R7). 카테고리를 추가했으면 True."""
        from rentdesk.ui.dialogs.starter_dialog import StarterDialog

        kinds = feedback.run(self, self.ctx.starter.kinds)
        if not kinds:
            return False
        dlg = StarterDialog(self.ctx, kinds, self, first_run=first_run)
        dlg.exec()
        if dlg.result_data is None:
            return False
        self.data_changed()
        return True

    def offer_starter(self) -> bool:
        """첫 실행(실제 데이터가 비어 있고 아직 안 물어봄)이면 시작 도우미를 띄운다. 예제 데이터에서는 띄우지 않는다."""
        if self.ctx.is_demo or not feedback.run(self, self.ctx.starter.should_offer):
            return False
        self.open_starter(first_run=True)
        return True

    def show_about(self) -> None:
        from rentdesk.ui.dialogs.about_dialog import AboutDialog

        AboutDialog(self).exec()

    # ------------------------------------------------------------------ Navigator

    def current_page(self) -> Page:
        return self.pages.currentWidget()

    def _on_menu(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        self.page_title.setText(PAGES[row][1])
        self.refresh_current()

    def refresh_current(self) -> None:
        feedback.run(self, self.current_page().refresh)

    def data_changed(self) -> None:
        self.refresh_current()

    def update_backup_banner(self) -> None:
        text = None if self.ctx.is_demo else feedback.run(self, self.ctx.backup.reminder)
        if not self.ctx.is_demo and self.ctx.preferences is not None:
            notice = AutoBackupSettings(self.ctx.preferences).notice  # 지난 종료 때 자동 백업 문제
            if notice:
                text = f"{notice}\n{text}" if text else notice
        self.backup_banner_label.setText(text or "")
        self.backup_banner.setVisible(bool(text))

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

    def new_reservation(self, customer_id: int | None = None, *, asset_ids: Sequence[int] = (),
                        start_at: datetime | None = None, due_at: datetime | None = None) -> None:
        from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard

        dlg = ReservationWizard(self.ctx, parent=self, customer_id=customer_id,
                                asset_ids=asset_ids, start_at=start_at, due_at=due_at)
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
