"""메인 창 골격. 좌측 메뉴 + 우측 업무 화면 레이아웃만 M0에서 구성한다.
실제 화면(S-01~S-09)은 M3에서 서비스 계층과 연결한다. 이 파일에는 SQL/요금 계산
로직을 두지 않는다.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

WINDOW_TITLE = "RentDesk Lite"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MIN_WIDTH = 1100
MIN_HEIGHT = 700

MENU_ITEMS = [
    "대시보드",
    "장비 관리",
    "고객 관리",
    "예약",
    "정비/사용 불가",
    "보고서/설정",
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        central = QWidget(self)
        layout = QHBoxLayout(central)

        self.menu_list = QListWidget(central)
        self.menu_list.addItems(MENU_ITEMS)
        self.menu_list.setMaximumWidth(220)

        self.pages = QStackedWidget(central)
        for item in MENU_ITEMS:
            placeholder = QLabel(f"{item} 화면은 M3에서 구현됩니다.")
            placeholder.setContentsMargins(24, 24, 24, 24)
            self.pages.addWidget(placeholder)

        self.menu_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.menu_list.setCurrentRow(0)

        layout.addWidget(self.menu_list)
        layout.addWidget(self.pages)

        self.setCentralWidget(central)
