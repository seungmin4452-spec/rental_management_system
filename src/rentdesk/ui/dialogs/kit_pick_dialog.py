"""예약 마법사 [세트 추가] (MULTI_INDUSTRY_PLAN R4).

세트를 고르면 구성 장비마다 이 기간에 예약할 수 있는지 보여 준다. 안 되는 장비는
같은 카테고리의 가능한 장비로 바꾸거나 빼고 진행한다. 실제 확정 때는 서비스가 트랜잭션 안에서
가용성과 대체 규칙을 다시 검사한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_won
from rentdesk.services.kits import KitPick, KitPlan
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import button_row

HEADERS = ["구성 장비", "장비명", "이 기간", "담을 장비"]
DROP = "빼고 진행"


class KitPickDialog(QDialog):
    def __init__(
        self,
        ctx: AppContext,
        start_at: datetime,
        due_at: datetime,
        *,
        order_id: int | None = None,
        taken: Sequence[int] = (),
        locked: Sequence[int] = (),
        exclude_kits: Sequence[int] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._args = dict(order_id=order_id, taken=list(taken), locked=list(locked))
        self._period = (start_at, due_at)
        self.plan: KitPlan | None = None
        self.result_pick: KitPick | None = None
        self.choices: list[QComboBox] = []
        self.setWindowTitle("세트 추가")
        self.setMinimumSize(720, 420)

        self.kit = QComboBox()
        for kit in ctx.kits.list_kits():
            if kit.id not in exclude_kits:
                self.kit.addItem(f"{kit.name} (할인 {kit.discount.label})", kit.id)
        self.kit.currentIndexChanged.connect(self.load_plan)
        top = QHBoxLayout()
        top.addWidget(QLabel("세트"))
        top.addWidget(self.kit, 1)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.checklist = QLabel()
        self.checklist.setWordWrap(True)
        theme.set_tone(self.checklist, "muted")
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        theme.set_tone(self.notice, "warning")
        self.errors = QLabel()
        self.errors.setWordWrap(True)
        theme.set_tone(self.errors, "danger")

        self.ok_btn = theme.set_variant(QPushButton("예약에 담기"), "primary")
        self.ok_btn.clicked.connect(self.accept_pick)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.checklist)
        lay.addWidget(self.notice)
        lay.addWidget(self.errors)
        lay.addLayout(button_row(cancel, self.ok_btn))
        if self.kit.count() == 0:
            self.notice.setText("담을 수 있는 세트가 없습니다. 장비 관리 > 세트 탭에서 먼저 만들어주세요.")
            self.ok_btn.setEnabled(False)
        else:
            self.load_plan()

    def load_plan(self) -> None:
        kit_id = self.kit.currentData()
        if kit_id is None:
            return
        plan = feedback.run(self, lambda: self.ctx.kits.plan(kit_id, *self._period, **self._args))
        if plan is None:
            return
        self.plan = plan
        self.errors.clear()
        self.choices = []
        self.table.setRowCount(len(plan.slots))
        for r, slot in enumerate(plan.slots):
            member = slot.member
            status = "사용 가능" if slot.available else f"사용 불가: {slot.reason}"
            for c, text in enumerate([member.asset_code, member.name, status]):
                cell = QTableWidgetItem(text)
                if not slot.available:
                    cell.setForeground(theme.color("danger"))
                self.table.setItem(r, c, cell)
            combo = QComboBox()
            if slot.available:
                combo.addItem(f"{member.asset_code} (원래 장비)", member.id)
            for cand in slot.candidates:
                combo.addItem(f"{cand.asset_code} · {cand.name} ({format_won(cand.daily_rate_won)}/일)", cand.id)
            combo.addItem(DROP, None)
            combo.setCurrentIndex(max(0, combo.findData(slot.default_choice)))
            if slot.default_choice is None:
                combo.setCurrentIndex(combo.count() - 1)
            combo.currentIndexChanged.connect(self.errors.clear)
            self.table.setCellWidget(r, 3, combo)
            self.choices.append(combo)
        self.table.resizeColumnsToContents()
        self.checklist.setText(
            "구성품: " + ", ".join(plan.kit.checklist) if plan.kit.checklist else "구성품 체크리스트 없음"
        )
        blocked = [s for s in plan.slots if not s.available]
        self.notice.setText(
            "이 기간에 사용할 수 없는 장비가 있습니다. 같은 카테고리의 다른 장비로 바꾸거나 빼고 진행하세요.\n"
            + "\n".join(
                f"- {s.member.asset_code}: {s.reason}"
                + ("" if s.candidates else " (같은 카테고리에 바꿀 장비 없음)")
                for s in blocked
            )
            if blocked else ""
        )

    def chosen_ids(self) -> list[int]:
        return [c.currentData() for c in self.choices if c.currentData() is not None]

    def accept_pick(self) -> bool:
        if self.plan is None:
            return False
        ids = self.chosen_ids()
        if not ids:
            self.errors.setText("담을 장비가 없습니다. 세트를 담지 않으려면 취소를 누르세요.")
            return False
        if len(set(ids)) != len(ids):
            self.errors.setText("같은 장비를 두 번 고를 수 없습니다. 담을 장비를 다시 골라주세요.")
            return False
        self.result_pick = KitPick(self.plan.kit.id, ids)
        self.accept()
        return True
