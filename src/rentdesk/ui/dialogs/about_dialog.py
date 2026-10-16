"""도움말 > 프로그램 정보: 버전, 판매자 문의 안내, 데이터·로그 폴더 열기 (USABILITY_PLAN U2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from rentdesk import __version__
from rentdesk.platform import paths as platform_paths
from rentdesk.platform.environment import get_environment_info
from rentdesk.platform.resources import SupportInfo, get_support_info
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import button_row


def open_folder(parent: QWidget | None, get_path: Callable[[], Path]) -> bool:
    """파일 탐색기(Finder)로 폴더를 연다. 실패하면 경로를 알려준다."""
    path = feedback.run(parent, get_path)
    if path is None:
        return False
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        feedback.warn(parent, f"폴더를 열 수 없습니다. 직접 찾아가 주세요.\n{path}")
        return False
    return True


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, support: SupportInfo | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("프로그램 정보")
        self.setMinimumWidth(480)
        self.support = support or get_support_info()
        env = get_environment_info()

        title = QLabel("RentDesk Lite")
        title.setObjectName("SectionTitle")
        form = QFormLayout()
        form.addRow("버전", QLabel(__version__))
        form.addRow("운영체제", QLabel(f"{env.os_version} ({env.machine})"))
        contact = QLabel(self.support.contact)
        contact.setWordWrap(True)
        form.addRow("문의", contact)
        notice = QLabel(
            "문의할 때 이 창의 버전과 운영체제를 함께 알려주시면 빠르게 확인할 수 있습니다. "
            "고객 정보가 들어 있는 데이터 파일은 보내지 마세요."
        )
        notice.setWordWrap(True)
        theme.set_tone(notice, "muted")

        data_btn = QPushButton("데이터 폴더 열기")
        data_btn.clicked.connect(lambda: open_folder(self, platform_paths.get_app_data_dir))
        logs_btn = QPushButton("오류 기록 폴더 열기")
        logs_btn.clicked.connect(lambda: open_folder(self, platform_paths.get_logs_dir))
        buttons = [data_btn, logs_btn]
        if self.support.manual_url:
            manual_btn = QPushButton("사용 안내 열기")
            manual_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.support.manual_url)))
            buttons.append(manual_btn)
        close = theme.set_variant(QPushButton("닫기"), "primary")
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addLayout(form)
        lay.addWidget(notice)
        lay.addLayout(button_row(*buttons, close))
