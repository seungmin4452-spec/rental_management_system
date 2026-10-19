"""반납 처리 (S-06, W-03). `OUT` 장비만 나열하고 일부만 선택해 반납할 수 있다.

품목별 실제 반납 시각·반납 상태·추가 비용(사유·내용)·메모를 입력하고, 예측 연체료와 계산 근거를
보여준다. 저장은 서비스가 하나의 트랜잭션으로 처리한다(상태 변경 + 연체/추가 비용 청구 + 정비 블록).
세트가 담긴 예약은 구성품 체크리스트를 다시 확인하고 결과를 저장한다(R4). 빠진 구성품은
추가 비용 사유 '구성품 누락'으로 바로 청구할 수 있다(R5).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.documents import DocumentKind
from rentdesk.domain.errors import DomainError
from rentdesk.domain.extra_charges import (
    EXTRA_CHARGE_LABEL,
    EXTRA_CHARGE_REASON_LABELS,
    MEMO_MAX,
    ExtraChargeReason,
    missing_part_memo,
)
from rentdesk.domain.models import ReturnCondition
from rentdesk.services.availability import describe_conflict
from rentdesk.services.returns import ReturnItemInput, ReturnResult
from rentdesk.ui import feedback, theme
from rentdesk.ui.kit_checklist import KitChecklistBox
from rentdesk.ui.widgets import SeoulDateTimeEdit, WonSpinBox, button_row, now_seoul_minute

OPEN_DOC_PREF = "open_return_doc_after_return"  # 기기별 체크 상태 기억
CONDITION_LABELS = {ReturnCondition.GOOD: "정상", ReturnCondition.MAINTENANCE_REQUIRED: "정비 필요"}


@dataclass
class _Row:
    item_id: int
    asset_code: str
    check: QCheckBox
    returned_at: SeoulDateTimeEdit
    condition: QComboBox
    damage: WonSpinBox  # 추가 비용 금액(코드 이름은 예전 파손비 그대로)
    reason: QComboBox
    extra_memo: QLineEdit
    note: QLineEdit
    late: QLabel
    order_kit_id: int | None = None
    late_fee_won: int = 0


def _extra_cell(row: _Row) -> QWidget:
    """추가 비용 칸: [금액][사유] + 내용 한 줄. 금액이 0이면 사유·내용은 잠근다."""
    cell = QWidget()
    lay = QVBoxLayout(cell)
    lay.setContentsMargins(0, 0, 0, 0)
    top = QHBoxLayout()
    top.addWidget(row.damage)
    top.addWidget(row.reason)
    lay.addLayout(top)
    lay.addWidget(row.extra_memo)
    return cell


class ReturnDialog(QDialog):
    def __init__(self, ctx: AppContext, order_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.order_id = order_id
        self.result: ReturnResult | None = None
        order = ctx.reservations.get_order(order_id)
        self.setWindowTitle(f"반납 처리 {order.order_no}")
        self.setMinimumSize(1000, 480)

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
            ["반납", "장비번호", "실제 반납 시각", "반납 상태", f"{EXTRA_CHARGE_LABEL}(사유·내용)",
             "반납 메모", "연체료(예상)"]
        ):
            grid.addWidget(QLabel(f"<b>{head}</b>"), 0, col)
        now = now_seoul_minute(ctx.now())
        self.rows: list[_Row] = []
        for r, item in enumerate(ctx.returns.returnable_items(order_id), start=1):
            row = _Row(
                item_id=item.id, asset_code=item.asset_code, check=QCheckBox(),
                returned_at=SeoulDateTimeEdit(now), condition=QComboBox(), damage=WonSpinBox(),
                reason=QComboBox(), extra_memo=QLineEdit(), note=QLineEdit(), late=QLabel(),
                order_kit_id=item.order_kit_id,
            )
            for cond, label in CONDITION_LABELS.items():
                row.condition.addItem(label, cond)
            for reason, label in EXTRA_CHARGE_REASON_LABELS.items():
                row.reason.addItem(label, reason)
            row.extra_memo.setMaxLength(MEMO_MAX)
            row.extra_memo.setPlaceholderText("추가 비용 내용(기타는 필수)")
            row.damage.valueChanged.connect(lambda _=None, rr=row: self._sync_extra(rr))
            row.reason.currentIndexChanged.connect(lambda _=None, rr=row: self._fill_missing_memo(rr))
            row.note.setMaxLength(1000)
            row.late.setWordWrap(True)
            row.returned_at.dateTimeChanged.connect(lambda _=None, rr=row: self._preview(rr))
            row.check.toggled.connect(self._update_total)
            row.damage.valueChanged.connect(self._update_total)
            for col, w in enumerate([row.check, QLabel(item.asset_code), row.returned_at,
                                     row.condition, _extra_cell(row), row.note, row.late]):
                grid.addWidget(w, r, col)
            self.rows.append(row)
            self._sync_extra(row)
            self._preview(row)
        grid.setRowStretch(len(self.rows) + 1, 1)  # 행을 위쪽에 모아 표시
        grid.setColumnStretch(5, 1)
        grid.setColumnStretch(6, 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_box)

        out_kits = {i.order_kit_id for i in order.items if i.status.value == "OUT"}
        self.kit_checks = KitChecklistBox(
            [k for k in order.kits if k.id in out_kits], title="반납 구성품 확인"
        )
        self.total = QLabel()
        select_all = QPushButton("전체 선택")
        select_all.clicked.connect(lambda: [r.check.setChecked(True) for r in self.rows])
        self.open_doc = QCheckBox("반납 후 반납확인서 열기 (PDF 저장·인쇄)")
        prefs = ctx.preferences
        self.open_doc.setChecked(bool(prefs.get(OPEN_DOC_PREF, True)) if prefs is not None else True)
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
        lay.addWidget(self.kit_checks)
        lay.addWidget(self.total)
        lay.addWidget(self.open_doc)
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

    @staticmethod
    def _sync_extra(row: _Row) -> None:
        on = row.damage.value() > 0
        row.reason.setEnabled(on)
        row.extra_memo.setEnabled(on)

    def _fill_missing_memo(self, row: _Row) -> None:
        """사유를 '구성품 누락'으로 고르면 비어 있는 내용 칸에 그 세트의 빠진 구성품을 채운다."""
        if row.reason.currentData() != ExtraChargeReason.MISSING_PART or row.extra_memo.text().strip():
            return
        kit = next((k for k in self.kit_checks.kits if k.id == row.order_kit_id), None)
        if kit is None:
            return
        missing = dict(self.kit_checks.missing()).get(kit.name)
        memo = missing_part_memo([(kit.name, missing or [])])
        if memo:
            row.extra_memo.setText(memo)

    def selected(self) -> list[_Row]:
        return [r for r in self.rows if r.check.isChecked()]

    def _update_total(self) -> None:
        if not hasattr(self, "total"):
            return
        sel = self.selected()
        late = sum(r.late_fee_won for r in sel)
        damage = sum(r.damage.value() for r in sel)
        self.total.setText(
            f"선택 {len(sel)}개 · 예상 연체료 {format_won(late)} · {EXTRA_CHARGE_LABEL} {format_won(damage)}"
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
                note=r.note.text(), extra_charge_reason=r.reason.currentData(),
                extra_charge_memo=r.extra_memo.text(),
            )
            for r in sel
        ]
        lines = []
        for r in sel:
            extra = []
            if r.late_fee_won:
                extra.append(f"연체료 {format_won(r.late_fee_won)}")
            if r.damage.value():
                extra.append(f"{EXTRA_CHARGE_LABEL}({r.reason.currentText()}) "
                             f"{format_won(r.damage.value())}")
            if r.condition.currentData() == ReturnCondition.MAINTENANCE_REQUIRED:
                extra.append("정비 블록 생성")
            lines.append(f"- {r.asset_code} {format_dt(r.returned_at.value())}"
                         + (f" ({', '.join(extra)})" if extra else ""))
        text = ("아래 장비를 반납 처리합니다. 반납은 되돌릴 수 없으며 연체료·추가 비용이 청구됩니다.\n\n"
                + "\n".join(lines))
        missing = self.kit_checks.missing_text()
        if missing:
            text += ("\n\n확인하지 않은 구성품:\n" + missing
                     + "\n빠진 구성품 비용은 장비 행의 추가 비용에서 사유 '구성품 누락'으로 청구할 수 있습니다.")
        if not feedback.confirm(self, text, "반납 확인"):
            return False
        self.submit_btn.setEnabled(False)
        kit_checks = self.kit_checks.checks()
        result = feedback.run(
            self, lambda: self.ctx.returns.return_items(self.order_id, inputs, kit_checks=kit_checks)
        )
        if result is None:
            self._update_total()
            return False
        self.result = result
        self._report(result)
        self.accept()
        if self.ctx.preferences is not None:
            feedback.run(self, lambda: self.ctx.preferences.set(OPEN_DOC_PREF, self.open_doc.isChecked()))
        if self.open_doc.isChecked():
            self.show_document()
        return True

    def show_document(self) -> None:
        """반납 직후 반납확인서 미리보기(테스트에서 바꿔 끼울 수 있다)."""
        from rentdesk.ui.dialogs.document_dialog import open_document

        open_document(self.parentWidget() or self, self.ctx, self.order_id, DocumentKind.RETURN)

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
