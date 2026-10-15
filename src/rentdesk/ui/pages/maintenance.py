"""S-08 정비/사용 불가 관리."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.display import format_dt
from rentdesk.ui import feedback, theme
from rentdesk.ui.dialogs.block_dialog import BlockDialog
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DataTable, SeoulDateTimeEdit, empty_hint, now_seoul_minute


class CloseBlockDialog(QDialog):
    def __init__(self, ctx, block_id: int, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.block_id = block_id
        self.setWindowTitle("정비 종료")
        form = QFormLayout()
        form.addRow(QLabel(label))
        self.end = SeoulDateTimeEdit(now_seoul_minute(ctx.now()))
        form.addRow("종료 시각", self.end)
        form.addRow(QLabel("종료 시각부터 신규 예약 가능 여부가 다시 계산됩니다."))
        buttons = QDialogButtonBox()
        buttons.addButton("정비 종료", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def submit(self) -> bool:
        if feedback.run(self, lambda: self.ctx.maintenance.close_block(self.block_id, self.end.value())):
            self.accept()
            return True
        return False


class MaintenancePage(Page):
    title = "정비 관리"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        self.include_closed = QCheckBox("종료된 정비 포함")
        self.include_closed.toggled.connect(self.refresh)
        add_btn = theme.set_variant(QPushButton("정비/사용 불가 등록"), "primary")
        add_btn.clicked.connect(self.add)
        close_btn = QPushButton("정비 종료")
        close_btn.clicked.connect(self.close_selected)
        top = QHBoxLayout()
        top.addWidget(add_btn)
        top.addWidget(close_btn)
        top.addStretch(1)
        top.addWidget(self.include_closed)
        self.table = DataTable(["장비번호", "장비명", "시작", "종료(예정)", "사유", "상태"],
                               badge_columns=(5,))
        self.table.activated_key.connect(self._open_asset)
        self.empty = empty_hint("진행 중인 정비 일정이 없습니다.", "정비 등록", self.add)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.empty)
        lay.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        now = self.ctx.now()
        self.blocks = {b.block_id: b for b in self.ctx.maintenance.list_blocks(
            include_closed=self.include_closed.isChecked()
        )}
        self.table.set_rows([
            (b.block_id, [b.asset_code, b.asset_name, format_dt(b.start_at),
                          format_dt(b.end_at, "미정"), b.reason, b.state_label(now)])
            for b in self.blocks.values()
        ])
        self.empty.setVisible(not self.blocks)

    def _open_asset(self, block_id: object) -> None:
        self.nav.open_asset(self.blocks[int(block_id)].asset_id)

    def add(self) -> None:
        if BlockDialog(self.ctx, self).exec():
            self.nav.data_changed()

    def close_selected(self) -> None:
        key = self.table.selected_key()
        if key is None:
            feedback.warn(self, "종료할 정비 일정을 먼저 선택해주세요.")
            return
        b = self.blocks[int(key)]
        if b.closed_at is not None:
            feedback.warn(self, "이미 종료된 정비 일정입니다.")
            return
        label = f"{b.asset_code} · {b.reason} (시작 {format_dt(b.start_at)})"
        if CloseBlockDialog(self.ctx, b.block_id, label, self).exec():
            self.nav.data_changed()
