"""장비 등록/수정 (S-02) 및 장비 상세 (W-05)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.availability import ASSET_STATE_LABELS
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.models import ORDER_STATUS_LABELS, Asset, LifecycleStatus
from rentdesk.services.assets import AssetInput
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import DataTable, FormErrors, WonSpinBox, button_row

ITEM_STATUS_LABELS = {"RESERVED": "예약", "OUT": "대여 중", "RETURNED": "반납", "CANCELED": "취소"}


class AssetDialog(QDialog):
    def __init__(self, ctx: AppContext, asset: Asset | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.asset = asset
        self.result_asset: Asset | None = None
        self.setWindowTitle("장비 수정" if asset else "장비 등록")
        self.setMinimumWidth(460)

        form = QFormLayout()
        self.errors = FormErrors(form)
        self.code = QLineEdit()
        self.code.setMaxLength(30)
        self.code.setPlaceholderText("비우면 AST-000001 형태로 자동 생성")
        self.name = QLineEdit()
        self.name.setMaxLength(100)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems([c.name for c in ctx.assets.list_categories()])
        self.category.setCurrentText("")
        self.brand = QLineEdit()
        self.model = QLineEdit()
        self.serial = QLineEdit()
        self.location = QLineEdit()
        self.rate = WonSpinBox()
        self.deposit = WonSpinBox()
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(60)

        self.errors.add_row("장비번호", self.code, "asset_code")
        self.errors.add_row("장비명 *", self.name, "name")
        self.errors.add_row("카테고리 *", self.category, "category")
        self.errors.add_row("브랜드", self.brand, "brand")
        self.errors.add_row("모델명", self.model, "model")
        self.errors.add_row("시리얼 번호", self.serial, "serial_no")
        self.errors.add_row("보관 위치", self.location, "location")
        self.errors.add_row("일 대여료 *", self.rate, "daily_rate_won")
        self.errors.add_row("보증금 *", self.deposit, "deposit_won")
        self.errors.add_row("메모", self.note, "note")

        if asset:
            self.code.setText(asset.asset_code)
            self.code.setReadOnly(True)
            self.code.setToolTip("장비번호는 이력 보존을 위해 변경할 수 없습니다.")
            self.name.setText(asset.name)
            self.category.setCurrentText(asset.category_name)
            self.brand.setText(asset.brand or "")
            self.model.setText(asset.model or "")
            self.serial.setText(asset.serial_no or "")
            self.location.setText(asset.location or "")
            self.rate.setValue(asset.daily_rate_won)
            self.deposit.setValue(asset.deposit_won)
            self.note.setPlainText(asset.note or "")

        buttons = QDialogButtonBox()
        ok = buttons.addButton("저장", QDialogButtonBox.ButtonRole.AcceptRole)
        theme.set_variant(ok, "primary")
        buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _input(self) -> AssetInput:
        return AssetInput(
            asset_code=self.code.text() or None,
            name=self.name.text(),
            category_name=self.category.currentText(),
            brand=self.brand.text(),
            model=self.model.text(),
            serial_no=self.serial.text(),
            location=self.location.text(),
            daily_rate_won=self.rate.value(),
            deposit_won=self.deposit.value(),
            note=self.note.toPlainText(),
        )

    def submit(self) -> bool:
        self.errors.clear()
        data = self._input()
        if self.asset:
            action = lambda: self.ctx.assets.update_asset(self.asset.id, data)  # noqa: E731
        else:
            action = lambda: self.ctx.assets.create_asset(data)  # noqa: E731
        result = feedback.run(self, action, on_domain_error=self.errors.show)
        if result is None:
            return False
        self.result_asset = result
        self.accept()
        return True


class AssetDetailDialog(QDialog):
    """장비 상세: 현재 가용성, 예정 예약, 대여 이력, 정비 이력."""

    def __init__(self, ctx: AppContext, asset_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.asset_id = asset_id
        self.changed = False
        self.setWindowTitle("장비 상세")
        self.resize(820, 520)

        self.header = QLabel()
        self.header.setWordWrap(True)
        self.tabs = QTabWidget()
        self.upcoming = DataTable(["예약번호", "고객", "시작", "반납 예정", "예약 상태"],
                                  badge_columns=(4,))
        self.history = DataTable(["예약번호", "고객", "출고", "약정 반납", "실제 반납", "품목 상태"])
        self.blocks = DataTable(["시작", "종료(예정)", "사유", "상태"], badge_columns=(3,))
        self.tabs.addTab(self.upcoming, "예정 예약")
        self.tabs.addTab(self.history, "대여 이력")
        self.tabs.addTab(self.blocks, "정비 이력")
        for table in (self.upcoming, self.history):
            table.activated_key.connect(self._open_order)

        add_block = QPushButton("정비 블록 추가")
        add_block.clicked.connect(self._add_block)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(button_row(add_block, close))
        self.refresh()

    def refresh(self) -> None:
        asset = self.ctx.assets.get_asset(self.asset_id)
        state = self.ctx.availability.asset_states([asset])[asset.id]
        lifecycle = "활성" if asset.lifecycle_status == LifecycleStatus.ACTIVE else "은퇴"
        parts = [
            f"<b>{asset.asset_code}</b> | {asset.name}  (운영: {lifecycle})",
            f"카테고리 {asset.category_name} · 모델 {asset.model or '-'} · 위치 {asset.location or '-'}"
            f" · 일 대여료 {format_won(asset.daily_rate_won)} · 보증금 {format_won(asset.deposit_won)}",
            f"현재 가용성: <b>{ASSET_STATE_LABELS[state]}</b>",
        ]
        self.header.setText("<br>".join(parts))
        rows = self.ctx.assets.asset_orders(self.asset_id)
        self.upcoming.set_rows([
            (r.order_id, [r.order_no, r.customer_name, format_dt(r.start_at), format_dt(r.due_at),
                          ORDER_STATUS_LABELS[r.order_status]])
            for r in rows if r.is_upcoming
        ])
        self.history.set_rows([
            (r.order_id, [r.order_no, r.customer_name, format_dt(r.actual_checkout_at),
                          format_dt(r.due_at), format_dt(r.actual_return_at),
                          ITEM_STATUS_LABELS[r.item_status.value]])
            for r in rows if r.actual_checkout_at is not None
        ])
        now = self.ctx.now()
        self.blocks.set_rows([
            (b.block_id, [format_dt(b.start_at), format_dt(b.end_at, "미정"), b.reason,
                          b.state_label(now)])
            for b in self.ctx.maintenance.list_blocks(include_closed=True, asset_id=self.asset_id)
        ])

    def _open_order(self, order_id: object) -> None:
        from rentdesk.ui.dialogs.order_detail import OrderDetailDialog

        dlg = OrderDetailDialog(self.ctx, int(order_id), self)
        dlg.exec()
        self.changed |= dlg.changed
        self.refresh()

    def _add_block(self) -> None:
        from rentdesk.ui.dialogs.block_dialog import BlockDialog

        dlg = BlockDialog(self.ctx, self, asset_id=self.asset_id)
        if dlg.exec():
            self.changed = True
            self.refresh()
