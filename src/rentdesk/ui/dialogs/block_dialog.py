"""정비/사용 불가 블록 등록 (S-08). 등록 전 겹치는 확정 예약을 보여주고 확인을 받는다."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.errors import ValidationError
from rentdesk.services.availability import describe_conflict
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import FormErrors, SeoulDateTimeEdit, now_seoul_minute


class BlockDialog(QDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None, *, asset_id: int | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("정비/사용 불가 등록")
        self.setMinimumWidth(460)
        form = QFormLayout()
        self.errors = FormErrors(form)

        self.asset = QComboBox()
        for a in ctx.assets.search_assets(include_retired=True):
            self.asset.addItem(f"{a.asset_code}  {a.name}", a.id)
        if asset_id is not None:
            self.asset.setCurrentIndex(self.asset.findData(asset_id))
            self.asset.setEnabled(False)
        now = now_seoul_minute(ctx.now())
        self.start = SeoulDateTimeEdit(now)
        self.has_end = QCheckBox("종료 예정 시각 지정")
        self.end = SeoulDateTimeEdit(now + timedelta(days=1))
        self.end.setEnabled(False)
        self.has_end.toggled.connect(self.end.setEnabled)
        self.reason = QLineEdit()
        self.reason.setMaxLength(500)
        self.reason.setPlaceholderText("예: 렌즈 점검, 분실 확인 중")

        self.errors.add_row("장비", self.asset, "asset_id")
        self.errors.add_row("시작", self.start, "start_at")
        form.addRow("", self.has_end)
        self.errors.add_row("종료 예정", self.end, "end_at")
        self.errors.add_row("사유 *", self.reason, "reason")

        buttons = QDialogButtonBox()
        ok = buttons.addButton("등록", QDialogButtonBox.ButtonRole.AcceptRole)
        theme.set_variant(ok, "primary")
        buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _end(self) -> datetime | None:
        return self.end.value() if self.has_end.isChecked() else None

    def submit(self) -> bool:
        self.errors.clear()
        asset_id = self.asset.currentData()
        if asset_id is None:
            self.errors.show(ValidationError("asset_id", "장비를 선택해주세요."))
            return False
        start, end = self.start.value(), self._end()
        if end is not None and not start < end:
            self.errors.show(ValidationError("end_at", "종료 예정 시각은 시작 시각보다 뒤여야 합니다."))
            return False
        if not self.reason.text().strip():
            self.errors.show(ValidationError("reason", "사유를 입력해주세요."))
            return False
        affected = feedback.run(self, lambda: self.ctx.maintenance.preview_affected(asset_id, start, end))
        if affected is None:
            return False
        text = "이 기간 동안 해당 장비의 신규 예약 확정이 불가능해집니다. 등록할까요?"
        if affected:
            lines = "\n".join(f"- {describe_conflict(c)}" for c in affected)
            text = (
                "아래 확정 예약과 겹칩니다. 예약은 자동 취소되지 않으며 고객과 일정 조정이 필요합니다.\n\n"
                f"{lines}\n\n그래도 등록할까요?"
            )
        if not feedback.confirm(self, text, "정비 블록 등록 확인"):
            return False
        result = feedback.run(
            self, lambda: self.ctx.maintenance.create_block(asset_id, start, end, self.reason.text()),
            on_domain_error=self.errors.show,
        )
        if result is None:
            return False
        self.accept()
        return True
