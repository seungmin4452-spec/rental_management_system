"""반납 처리 (S-06, W-03). `OUT` 장비만 나열하고 일부만 선택해 반납할 수 있다.

품목별 실제 반납 시각·반납 상태·파손비·메모를 입력하고, 예측 연체료와 계산 근거를 보여준다.
저장은 서비스가 하나의 트랜잭션으로 처리한다(상태 변경 + 연체/파손 청구 + 정비 블록).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.errors import DomainError
from rentdesk.domain.models import ReturnCondition
from rentdesk.services.availability import describe_conflict
from rentdesk.services.returns import ReturnItemInput, ReturnResult
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import SeoulDateTimeEdit, WonSpinBox, button_row, now_seoul_minute

CONDITION_LABELS = {ReturnCondition.GOOD: "정상", ReturnCondition.MAINTENANCE_REQUIRED: "정비 필요"}


@dataclass
class _Row:
    item_id: int
    asset_code: str
    check: QCheckBox
    returned_at: SeoulDateTimeEdit
    condition: QComboBox
    damage: WonSpinBox
    note: QLineEdit
    late: QLabel
    late_fee_won: int = 0


class ReturnDialog(QDialog):
    def __init__(self, ctx: AppContext, order_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.order_id = order_id
        self.result: ReturnResult | None = None
        order = ctx.reservations.get_order(order_id)
        self.setWindowTitle(f"반납 처리 {order.order_no}")
        self.setMinimumSize(900, 480)

        info = QLabel(
            f"<b>{order.order_no}</b> · 고객 {ctx.reservations.customer_name(order)} · "
            f"약정 반납 {format_dt(order.due_at)} · 출고 {format_dt(order.actual_checkout_at)}"
        )
        returned = [i for i in order.items if i.status.value == "RETURNED"]
        done = QLabel(
            "이미 반납된 장비: " + ", ".join(
                f"{i.asset_code}({format_dt(i.actual_return_at)})" for i in returned
            ) if returned else ""
        )
        theme.set_tone(done, "muted")

        grid_box = QWidget()
        grid = QGridLayout(grid_box)
        for col, head in enumerate(
            ["반납", "장비번호", "실제 반납 시각", "반납 상태", "파손비", "반납 메모", "연체료(예상)"]
        ):
            grid.addWidget(QLabel(f"<b>{head}</b>"), 0, col)
        now = now_seoul_minute(ctx.now())
        self.rows: list[_Row] = []
        for r, item in enumerate(ctx.returns.returnable_items(order_id), start=1):
            row = _Row(
                item_id=item.id, asset_code=item.asset_code, check=QCheckBox(),
                returned_at=SeoulDateTimeEdit(now), condition=QComboBox(), damage=WonSpinBox(),
                note=QLineEdit(), late=QLabel(),
            )
            for cond, label in CONDITION_LABELS.items():
                row.condition.addItem(label, cond)
            row.note.setMaxLength(1000)
            row.late.setWordWrap(True)
            row.returned_at.dateTimeChanged.connect(lambda _=None, rr=row: self._preview(rr))
            row.check.toggled.connect(self._update_total)
            row.damage.valueChanged.connect(self._update_total)
            for col, w in enumerate([row.check, QLabel(item.asset_code), row.returned_at,
                                     row.condition, row.damage, row.note, row.late]):
                grid.addWidget(w, r, col)
            self.rows.append(row)
            self._preview(row)
        grid.setRowStretch(len(self.rows) + 1, 1)  # 행을 위쪽에 모아 표시
        grid.setColumnStretch(5, 1)
        grid.setColumnStretch(6, 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_box)

        self.total = QLabel()
        select_all = QPushButton("전체 선택")
        select_all.clicked.connect(lambda: [r.check.setChecked(True) for r in self.rows])
        self.submit_btn = theme.set_variant(QPushButton("선택 장비 반납 확정"), "primary")
        self.submit_btn.clicked.connect(self.submit)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(done)
        if not self.rows:
            lay.addWidget(QLabel("반납할 대여 중 장비가 없습니다."))
        lay.addWidget(scroll, 1)
        lay.addWidget(self.total)
        lay.addLayout(button_row(select_all, cancel, self.submit_btn))
        self._update_total()

    def _preview(self, row: _Row) -> None:
        try:
            p = self.ctx.returns.preview_late_fee(self.order_id, row.item_id, row.returned_at.value())
        except DomainError as exc:
            row.late_fee_won = 0
            row.late.setText(exc.message)
            theme.set_tone(row.late, "danger")
            return
        row.late_fee_won = p.late.late_fee_won
        row.late.setText(f"{format_won(p.late.late_fee_won)}\n{p.basis}")
        theme.set_tone(row.late, "danger" if p.late.late_fee_won else None)
        self._update_total()

    def selected(self) -> list[_Row]:
        return [r for r in self.rows if r.check.isChecked()]

    def _update_total(self) -> None:
        if not hasattr(self, "total"):
            return
        sel = self.selected()
        late = sum(r.late_fee_won for r in sel)
        damage = sum(r.damage.value() for r in sel)
        self.total.setText(
            f"선택 {len(sel)}개 · 예상 연체료 {format_won(late)} · 파손비 {format_won(damage)}"
        )
        self.submit_btn.setEnabled(bool(sel))

    def submit(self) -> bool:
        sel = self.selected()
        if not sel:
            feedback.warn(self, "반납할 장비를 1개 이상 선택해주세요.")
            return False
        inputs = [
            ReturnItemInput(
                item_id=r.item_id, returned_at=r.returned_at.value(),
                condition=r.condition.currentData(), damage_fee_won=r.damage.value(),
                note=r.note.text(),
            )
            for r in sel
        ]
        lines = []
        for r in sel:
            extra = []
            if r.late_fee_won:
                extra.append(f"연체료 {format_won(r.late_fee_won)}")
            if r.damage.value():
                extra.append(f"파손비 {format_won(r.damage.value())}")
            if r.condition.currentData() == ReturnCondition.MAINTENANCE_REQUIRED:
                extra.append("정비 블록 생성")
            lines.append(f"- {r.asset_code} {format_dt(r.returned_at.value())}"
                         + (f" ({', '.join(extra)})" if extra else ""))
        text = ("아래 장비를 반납 처리합니다. 반납은 되돌릴 수 없으며 연체료·파손비가 청구됩니다.\n\n"
                + "\n".join(lines))
        if not feedback.confirm(self, text, "반납 확인"):
            return False
        self.submit_btn.setEnabled(False)
        result = feedback.run(self, lambda: self.ctx.returns.return_items(self.order_id, inputs))
        if result is None:
            self._update_total()
            return False
        self.result = result
        self._report(result)
        self.accept()
        return True

    def _report(self, result: ReturnResult) -> None:
        msg = [f"{len(result.lines)}개 장비 반납이 완료되었습니다."]
        if result.completed:
            summary = self.ctx.settlements.summary(self.order_id)
            msg.append("모든 장비가 반납되어 예약이 '반납완료' 상태가 되었습니다.")
            if not summary.is_settled:
                msg.append(
                    f"※ 미정산: 미수금 {format_won(summary.amount_due_won)}, "
                    f"보증금 보유 {format_won(summary.deposit_held_won)} — 정산 화면에서 처리해주세요."
                )
        feedback.info(self, "\n".join(msg), "반납 완료")
        if result.affected_reservations:
            feedback.warn(
                self,
                "아래 예약이 이번 반납(정비 필요/반납 후 준비 시간)의 영향을 받을 수 있습니다. "
                "예약은 자동 취소되지 않았으니 고객과 일정을 조정해주세요.\n\n"
                + "\n".join(f"- {describe_conflict(c)}" for c in result.affected_reservations),
                "영향받는 예약",
            )
