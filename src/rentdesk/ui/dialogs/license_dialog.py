"""라이선스 확인 화면 (PRD 11.1).

라이선스가 없거나 유효하지 않으면 이유와 기기 코드를 보여주고, 라이선스 파일 등록 /
예제 데이터 체험 / 실제 데이터 백업 / 종료 중에서 고르게 한다. 실제 데이터는 삭제·잠금하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.display import format_dt
from rentdesk.services.licensing import LicenseCheck, LicenseService, format_machine_code
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import button_row


class LicensePanel(QWidget):
    """라이선스 상태·기기 코드·파일 등록. 시작 화면과 설정 화면에서 같이 쓴다."""

    activated = Signal()

    def __init__(self, service: LicenseService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.code = QLineEdit()
        self.code.setReadOnly(True)
        copy_btn = QPushButton("복사")
        copy_btn.clicked.connect(self.copy_code)
        code_row = QHBoxLayout()
        code_row.addWidget(self.code, 1)
        code_row.addWidget(copy_btn)
        self.load_btn = theme.set_variant(QPushButton("라이선스 파일 불러오기…"), "primary")
        self.load_btn.clicked.connect(self.choose_file)
        form = QFormLayout()
        form.addRow("상태", self.status_label)
        form.addRow("이 PC의 기기 코드", code_row)
        hint = QLabel(
            "라이선스는 PC 1대용입니다. 기기 코드를 판매자에게 보내면 이 PC용 라이선스 파일을 받을 수 있습니다. "
            "PC나 운영체제(Mac ↔ Windows)를 바꾸면 기기 코드가 달라지므로 사용권 이전(재발급)을 요청해주세요."
        )
        hint.setWordWrap(True)
        theme.set_tone(hint, "muted")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addLayout(button_row(self.load_btn))
        self.refresh()

    def refresh(self) -> LicenseCheck:
        result = self.service.check()
        text = result.message
        if result.valid and result.info is not None:
            text += f"\n사용자: {result.info.customer_label} · 발급일: {format_dt(result.info.issued_at)}"
            if result.info.expires_at is not None:
                text += f" · 만료: {format_dt(result.info.expires_at)}"
        self.status_label.setText(text)
        theme.set_tone(self.status_label, None if result.valid else "danger")
        code = self.service.machine_code()
        self.code.setText(format_machine_code(code) if code else "확인할 수 없음")
        return result

    def copy_code(self) -> None:
        QGuiApplication.clipboard().setText(self.code.text())

    def choose_file(self) -> None:
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        path, _ = QFileDialog.getOpenFileName(
            self, "라이선스 파일 선택", docs, "라이선스 파일 (*.json);;모든 파일 (*)"
        )
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> bool:
        result = feedback.run(self, lambda: self.service.install(path))
        self.refresh()
        if result is None:
            return False
        feedback.info(self, "라이선스를 등록했습니다. 실제 데이터로 사용할 수 있습니다.", "라이선스 등록")
        self.activated.emit()
        return True


class GateChoice(StrEnum):
    ACTIVATED = "ACTIVATED"
    DEMO = "DEMO"
    QUIT = "QUIT"


class LicenseDialog(QDialog):
    """시작 시 라이선스 확인 실패 화면. `choice`로 다음 동작을 알려준다."""

    def __init__(
        self,
        service: LicenseService,
        parent: QWidget | None = None,
        *,
        backup_real: Callable[[QWidget], None] | None = None,
        quit_label: str = "종료",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("RentDesk Lite 라이선스 확인")
        self.setMinimumWidth(560)
        self.choice = GateChoice.QUIT
        self.panel = LicensePanel(service, self)
        self.panel.activated.connect(lambda: self._finish(GateChoice.ACTIVATED))
        intro = QLabel(
            "정품 라이선스가 확인되지 않아 실제 데이터 화면을 열 수 없습니다. "
            "저장된 실제 데이터는 삭제되거나 변경되지 않습니다."
        )
        intro.setWordWrap(True)
        demo_btn = QPushButton("예제 데이터로 체험")
        demo_btn.clicked.connect(lambda: self._finish(GateChoice.DEMO))
        quit_btn = QPushButton(quit_label)
        quit_btn.clicked.connect(lambda: self._finish(GateChoice.QUIT))
        buttons = [demo_btn]
        if backup_real is not None:
            backup_btn = QPushButton("실제 데이터 백업…")
            backup_btn.setToolTip("라이선스가 없어도 지금까지의 실제 데이터를 백업 파일로 꺼낼 수 있습니다.")
            backup_btn.clicked.connect(lambda: backup_real(self))
            buttons.append(backup_btn)
        buttons.append(quit_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.panel)
        lay.addLayout(button_row(*buttons))

    def _finish(self, choice: GateChoice) -> None:
        self.choice = choice
        self.accept()
