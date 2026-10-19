"""장비 관리 > 세트 탭 (MULTI_INDUSTRY_PLAN R4): 세트 목록·만들기·수정·숨김."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from rentdesk.context import AppContext
from rentdesk.domain.display import format_won
from rentdesk.services.kits import KitRow
from rentdesk.ui import feedback, theme
from rentdesk.ui.dialogs.kit_dialog import KitDialog
from rentdesk.ui.widgets import DataTable, empty_hint

HEADERS = ["세트 이름", "구성 장비", "구성품", "세트 할인", "1일 요금 합계", "상태"]


class KitsPanel(QWidget):
    def __init__(self, ctx: AppContext, on_changed, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._on_changed = on_changed
        self.rows: dict[int, KitRow] = {}

        self.show_archived = QCheckBox("숨긴 세트도 보기")
        self.show_archived.toggled.connect(self.refresh)
        self.new_btn = theme.set_variant(QPushButton("새 세트"), "primary")
        self.edit_btn = QPushButton("수정")
        self.archive_btn = QPushButton("숨기기")
        self.new_btn.clicked.connect(self.create)
        self.edit_btn.clicked.connect(self.edit)
        self.archive_btn.clicked.connect(self.toggle_archived)
        actions = QHBoxLayout()
        for b in (self.new_btn, self.edit_btn, self.archive_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.show_archived)

        self.table = DataTable(HEADERS)
        self.table.activated_key.connect(lambda _: self.edit())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.empty = empty_hint(
            "등록된 세트가 없습니다. 자주 함께 나가는 장비를 세트로 묶으면 예약할 때 한 번에 담을 수 있습니다.",
            "새 세트", self.create,
        )
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        theme.set_tone(self.warnings, "warning")

        lay = QVBoxLayout(self)
        lay.addLayout(actions)
        lay.addWidget(self.empty)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.warnings)
        self.refresh()

    def refresh(self) -> None:
        rows = self.ctx.kits.list_kit_rows(include_archived=self.show_archived.isChecked())
        self.rows = {r.kit.id: r for r in rows}
        self.table.set_rows([
            (r.kit.id, [
                r.kit.name,
                ", ".join(a.asset_code for a in r.assets),
                ", ".join(r.kit.checklist) or "-",
                r.kit.discount.label,
                format_won(r.daily_total_won),
                "숨김" if r.kit.is_archived else ("주의" if r.warning else "사용"),
            ])
            for r in rows
        ], highlight=lambda k: bool(self.rows[k].warning), align_right=(4,))
        self.warnings.setText("\n".join(f"{r.kit.name}: {r.warning}" for r in rows if r.warning))
        self.empty.setVisible(not rows and not self.show_archived.isChecked())
        self._update_buttons()

    def selected(self) -> KitRow | None:
        key = self.table.selected_key()
        return self.rows.get(key) if key is not None else None

    def _update_buttons(self) -> None:
        row = self.selected()
        self.edit_btn.setEnabled(row is not None)
        self.archive_btn.setEnabled(row is not None)
        self.archive_btn.setText("다시 보이기" if row is not None and row.kit.is_archived else "숨기기")

    def create(self) -> None:
        dlg = KitDialog(self.ctx, parent=self)
        if dlg.exec():
            self._changed(dlg.result_kit.id if dlg.result_kit else None)

    def edit(self) -> None:
        row = self.selected()
        if row is None:
            feedback.warn(self, "세트를 먼저 선택해주세요.")
            return
        if KitDialog(self.ctx, row.kit, self).exec():
            self._changed(row.kit.id)

    def toggle_archived(self) -> None:
        row = self.selected()
        if row is None:
            return
        archived = not row.kit.is_archived
        if archived and not feedback.confirm(
            self, f"세트 '{row.kit.name}'을(를) 숨깁니다. 새 예약에 보이지 않으며, 이미 담긴 예약은 그대로입니다.",
            "세트 숨기기",
        ):
            return
        if feedback.run(self, lambda: self.ctx.kits.set_kit_archived(row.kit.id, archived)) is not None:
            self._changed(row.kit.id)

    def _changed(self, select_id: int | None) -> None:
        self._on_changed()  # 장비 화면 전체 새로 고침(이 표도 다시 그림)
        self.refresh()
        if select_id is not None:
            self.table.select_key(select_id)
