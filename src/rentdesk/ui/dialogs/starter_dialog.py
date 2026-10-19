"""시작 도우미 창(M4.6 R7): 취급 장비 종류를 여러 개 골라 추천 카테고리·설정을 추가한다.

첫 실행(실제 데이터가 비어 있음)에 한 번 뜨고, 도움말 > 시작 도우미에서 언제든 다시 열 수 있다.
계산·저장은 모두 `StarterService`. 이 화면은 선택과 미리보기 문구만 다룬다.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.policy import format_minutes
from rentdesk.domain.rates import rate_rule_short
from rentdesk.domain.starter import StarterCategory, StarterKind
from rentdesk.services.starter import StarterResult
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import button_row


def category_hint(category: StarterCategory) -> str:
    """"텐트·타프(준비 24시간, 날짜 기준 · 주말)"처럼 추천 설정을 짧게."""
    parts = []
    p = category.policy
    if p.turnaround_buffer_minutes is not None:
        parts.append(f"준비 {format_minutes(p.turnaround_buffer_minutes)}")
    if p.grace_minutes is not None:
        parts.append(f"연체료 없이 {format_minutes(p.grace_minutes)}")
    if p.late_multiplier is not None:
        parts.append(f"연체 {p.late_multiplier.normalize():f}배")
    if category.rate_rule is not None:
        parts.append(rate_rule_short(category.rate_rule))
    return f"{category.name}({', '.join(parts)})" if parts else category.name


class StarterDialog(QDialog):
    def __init__(self, ctx: AppContext, kinds: list[StarterKind], parent: QWidget | None = None,
                 *, first_run: bool = False) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.kinds = kinds
        self.first_run = first_run
        self.result_data: StarterResult | None = None
        self.setWindowTitle("시작 도우미")
        self.setMinimumWidth(820)

        title = QLabel("취급하는 장비를 모두 고르세요")
        title.setObjectName("SectionTitle")
        intro = QLabel(
            "고른 종류에 맞는 카테고리와 추천 설정(반납 후 준비 시간·요금 계산 방식 등)을 추가합니다. "
            "여러 개를 골라도 되고, 건너뛰어도 됩니다. '기타' 카테고리는 항상 있습니다."
        )
        intro.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(intro)

        # 1366×768 화면에서도 한눈에 보이도록 2열. 카테고리별 추천값은 말풍선(툴팁)으로.
        grid = QGridLayout()
        grid.setSpacing(10)
        self.checks: dict[str, QCheckBox] = {}
        for index, kind in enumerate(kinds):
            box = QFrame()
            box.setObjectName("Card")
            box.setToolTip("\n".join(category_hint(c) for c in kind.categories))
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 8, 12, 8)
            bl.setSpacing(2)
            check = QCheckBox(kind.label)
            check.toggled.connect(self._update_preview)
            self.checks[kind.key] = check
            bl.addWidget(check)
            detail = QLabel(
                (kind.description + "\n" if kind.description else "")
                + "카테고리: " + ", ".join(kind.category_names)
            )
            detail.setWordWrap(True)
            theme.set_tone(detail, "muted")
            bl.addWidget(detail)
            bl.addStretch(1)
            grid.addWidget(box, index // 2, index % 2)
        lay.addLayout(grid)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)
        notice = QLabel(
            "추천값은 일반적인 예시입니다. 이미 있는 카테고리는 그대로 두며, "
            "추가한 뒤에도 장비 관리 > 카테고리 관리에서 언제든 바꿀 수 있습니다."
        )
        notice.setWordWrap(True)
        theme.set_tone(notice, "muted")
        lay.addWidget(notice)

        self.skip_btn = QPushButton("건너뛰기" if first_run else "닫기")
        self.skip_btn.clicked.connect(self.reject)
        self.apply_btn = theme.set_variant(QPushButton("선택한 종류 추가"), "primary")
        self.apply_btn.clicked.connect(self.apply)
        lay.addLayout(button_row(self.skip_btn, self.apply_btn))
        self._update_preview()

    def selected_keys(self) -> list[str]:
        return [key for key, check in self.checks.items() if check.isChecked()]

    def _update_preview(self) -> None:
        keys = self.selected_keys()
        if not keys:
            self.preview.setText("고른 종류가 없습니다.")
            theme.set_tone(self.preview, "muted")
            self.apply_btn.setEnabled(False)
            return
        plan = feedback.run(self, lambda: self.ctx.starter.plan(keys))
        if plan is None:
            self.apply_btn.setEnabled(False)
            return
        text = f"카테고리 {len(plan.add)}개를 추가합니다."
        if plan.skipped:
            text += f" 이미 있는 {len(plan.skipped)}개({', '.join(plan.skipped)})는 그대로 둡니다."
        if plan.is_empty:
            text = "고른 종류의 카테고리가 모두 이미 있습니다. 추가할 것이 없습니다."
        self.preview.setText(text)
        theme.set_tone(self.preview, None)
        self.apply_btn.setEnabled(not plan.is_empty)

    def apply(self) -> StarterResult | None:
        keys = self.selected_keys()
        result = feedback.run(self, lambda: self.ctx.starter.apply(keys))
        if result is None:
            return None
        self.result_data = result
        feedback.info(self, result.message, "시작 도우미")
        self.accept()
        return result

    def reject(self) -> None:
        """첫 실행에서 건너뛰거나 창을 닫으면 다시 묻지 않는다."""
        if self.first_run and self.result_data is None:
            feedback.run(self, self.ctx.starter.mark_offered)
        super().reject()
