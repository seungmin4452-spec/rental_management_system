"""S-02 장비 관리."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.availability import ASSET_STATE_LABELS, AssetState
from rentdesk.domain.display import format_won
from rentdesk.domain.models import Asset, LifecycleStatus
from rentdesk.ui import feedback, theme
from rentdesk.ui.dialogs.asset_dialog import AssetDialog
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DataTable, empty_hint

LIFECYCLE_LABELS = {LifecycleStatus.ACTIVE: "운영 중", LifecycleStatus.RETIRED: "사용 중지"}


class AssetsPage(Page):
    title = "장비 관리"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        self.query = QLineEdit()
        self.query.setPlaceholderText("장비번호·이름·모델·브랜드·시리얼 검색")
        self.query.setClearButtonEnabled(True)
        self.query.textChanged.connect(self.refresh)
        self.category = QComboBox()
        self.category.currentIndexChanged.connect(self.refresh)
        self.include_retired = QCheckBox("사용 중지 장비 포함")
        self.include_retired.toggled.connect(self.refresh)
        filters = QHBoxLayout()
        filters.addWidget(self.query, 1)
        filters.addWidget(self.category)
        filters.addWidget(self.include_retired)

        self.table = DataTable(
            ["관리번호", "장비명", "카테고리", "모델명", "일 대여료", "보증금", "가용 상태", "운영 상태"],
            badge_columns=(6, 7),
        )
        self.table.activated_key.connect(lambda key: self.nav.open_asset(int(key)))
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.empty = empty_hint("등록된 장비가 없습니다.", "장비 등록", self.create)

        self.new_btn = theme.set_variant(QPushButton("장비 등록"), "primary")
        self.edit_btn = QPushButton("수정")
        self.detail_btn = QPushButton("상세/이력")
        self.retire_btn = theme.set_variant(QPushButton("사용 중지"), "danger")
        self.delete_btn = theme.set_variant(QPushButton("삭제"), "danger")
        self.new_btn.clicked.connect(self.create)
        self.edit_btn.clicked.connect(self.edit)
        self.detail_btn.clicked.connect(lambda: self._with_selected(lambda a: self.nav.open_asset(a.id)))
        self.retire_btn.clicked.connect(self.toggle_retire)
        self.delete_btn.clicked.connect(self.delete)
        actions = QHBoxLayout()
        for b in (self.new_btn, self.edit_btn, self.detail_btn, self.retire_btn, self.delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(filters)
        lay.addLayout(actions)
        lay.addWidget(self.empty)
        lay.addWidget(self.table, 1)
        self._load_categories()
        self.refresh()

    def _load_categories(self) -> None:
        current = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("전체 카테고리", None)
        for c in self.ctx.assets.list_categories():
            self.category.addItem(c.name, c.id)
        idx = self.category.findData(current)
        self.category.setCurrentIndex(max(0, idx))
        self.category.blockSignals(False)

    def refresh(self) -> None:
        self._load_categories()
        self.assets = {
            a.id: a for a in self.ctx.assets.search_assets(
                self.query.text(), include_retired=self.include_retired.isChecked(),
                category_id=self.category.currentData(),
            )
        }
        states = self.ctx.availability.asset_states(list(self.assets.values()))
        self.table.set_rows([
            (a.id, [a.asset_code, a.name, a.category_name, a.model or "", format_won(a.daily_rate_won),
                    format_won(a.deposit_won), ASSET_STATE_LABELS[states[a.id]],
                    LIFECYCLE_LABELS[a.lifecycle_status]])
            for a in self.assets.values()
        ], highlight=lambda k: states[k] == AssetState.OVERDUE, align_right=(4, 5))
        filtered = bool(self.query.text().strip()) or self.category.currentData() is not None
        self.empty.setVisible(not self.assets and not filtered)
        self._update_buttons()

    def selected_asset(self) -> Asset | None:
        key = self.table.selected_key()
        return self.assets.get(key) if key is not None else None

    def _with_selected(self, fn) -> None:
        asset = self.selected_asset()
        if asset is None:
            feedback.warn(self, "장비를 먼저 선택해주세요.")
            return
        fn(asset)

    def _update_buttons(self) -> None:
        asset = self.selected_asset()
        for b in (self.edit_btn, self.detail_btn, self.retire_btn, self.delete_btn):
            b.setEnabled(asset is not None)
        retired = asset is not None and asset.lifecycle_status == LifecycleStatus.RETIRED
        self.retire_btn.setText("다시 사용" if retired else "사용 중지")

    def create(self) -> None:
        dlg = AssetDialog(self.ctx, parent=self)
        if dlg.exec():
            self.nav.data_changed()
            if dlg.result_asset:
                self.table.select_key(dlg.result_asset.id)

    def edit(self) -> None:
        def run(asset: Asset) -> None:
            if AssetDialog(self.ctx, asset, self).exec():
                self.nav.data_changed()
        self._with_selected(run)

    def toggle_retire(self) -> None:
        def run(asset: Asset) -> None:
            if asset.lifecycle_status == LifecycleStatus.RETIRED:
                action = lambda: self.ctx.assets.reactivate_asset(asset.id)  # noqa: E731
                text = f"{asset.asset_code}을(를) 다시 사용합니다. 신규 예약에 다시 표시됩니다."
            else:
                action = lambda: self.ctx.assets.retire_asset(asset.id)  # noqa: E731
                text = (f"{asset.asset_code}을(를) 사용 중지합니다. 사용 중지한 장비는 신규 예약에 표시되지 않으며 "
                        "이력은 보존됩니다.")
            if feedback.confirm(self, text, "운영 상태 변경") and feedback.run(self, action):
                self.nav.data_changed()
        self._with_selected(run)

    def delete(self) -> None:
        def run(asset: Asset) -> None:
            if not feedback.confirm(
                self, f"{asset.asset_code}을(를) 삭제합니다. 예약·정비 이력이 없는 장비만 삭제됩니다.",
                "장비 삭제",
            ):
                return
            if feedback.run(self, lambda: self.ctx.assets.delete_asset(asset.id) or True):
                self.nav.data_changed()
        self._with_selected(run)
