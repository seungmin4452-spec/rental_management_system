"""요금 규칙 입력 위젯 (MULTI_INDUSTRY_PLAN R3). 설정 > 요금 계산 방식과 카테고리 관리 창이 함께 쓴다.

입력과 미리보기 표시만 한다. 값 검증은 서비스(`validate_rate_rule`)가 하고,
예시 금액은 도메인 순수 함수(`rate_examples`)로 계산한다.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.display import format_won
from rentdesk.domain.errors import DomainError
from rentdesk.domain.pricing import rate_examples
from rentdesk.domain.rates import (
    DAY_COUNT_LABELS,
    LATE_METHOD_LABELS,
    MINUTES_PER_DAY,
    WEEKDAY_LABELS,
    DayCountMethod,
    LateFeeMethod,
    RateRule,
)
from rentdesk.ui import theme
from rentdesk.ui.widgets import FormErrors

FIELD_WIDTH = 110
EXAMPLE_DAILY_RATE = 10_000


def _multiplier_spin(maximum: int) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.01, maximum)
    spin.setDecimals(2)
    spin.setSingleStep(0.5)
    spin.setPrefix("일 요금 × ")
    spin.setFixedWidth(FIELD_WIDTH + 40)
    return spin


def _weekday_combo() -> QComboBox:
    combo = QComboBox()
    for i, label in enumerate(WEEKDAY_LABELS):
        combo.addItem(f"{label}요일", i)
    return combo


def _time_edit() -> QTimeEdit:
    edit = QTimeEdit()
    edit.setDisplayFormat("HH:mm")
    return edit


def _row(*widgets: QWidget) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return box


def _decimal(spin: QDoubleSpinBox) -> Decimal:
    return Decimal(f"{spin.value():.2f}").normalize()


def _minute(edit: QTimeEdit) -> int:
    t = edit.time()
    return t.hour() * 60 + t.minute()


class RateRuleEditor(QWidget):
    """대여일수 계산·주말·주·월 단가·연체료 방식 입력 + 예시 계산 미리보기."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout()
        self.errors = FormErrors(form)

        self.day_count = QComboBox()
        for method in DayCountMethod:
            self.day_count.addItem(DAY_COUNT_LABELS[method], method.value)
        self.return_grace = QSpinBox()
        self.return_grace.setRange(0, MINUTES_PER_DAY - 1)
        self.return_grace.setSingleStep(10)
        self.return_grace.setSuffix("분")
        self.return_grace.setFixedWidth(FIELD_WIDTH)
        grace_label = QLabel("이내 초과는 같은 날로 봄")
        theme.set_tone(grace_label, "muted")

        self.weekend_on = QCheckBox("사용")
        self.weekend_start_day = _weekday_combo()
        self.weekend_start_time = _time_edit()
        self.weekend_end_day = _weekday_combo()
        self.weekend_end_time = _time_edit()
        self.weekend_multiplier = _multiplier_spin(7)
        start_label, end_label = QLabel("이후 시작 ~"), QLabel("이전 반납")

        self.weekly_on = QCheckBox("사용 (7일 요금)")
        self.weekly_multiplier = _multiplier_spin(7)
        self.monthly_on = QCheckBox("사용 (30일 요금)")
        self.monthly_multiplier = _multiplier_spin(30)

        self.late_method = QComboBox()
        for method in LateFeeMethod:
            self.late_method.addItem(LATE_METHOD_LABELS[method], method.value)
        self.late_divisor = QSpinBox()
        self.late_divisor.setRange(1, 24)
        self.late_divisor.setPrefix("1시간 = 일 요금 ÷ ")
        self.late_divisor.setFixedWidth(FIELD_WIDTH + 90)
        divisor_note = QLabel("(하루치 일 요금이 상한)")
        theme.set_tone(divisor_note, "muted")

        self.errors.add_row("대여일수 계산", _row(self.day_count), "day_count")
        self.errors.add_row("반납 여유 시간", _row(self.return_grace, grace_label), "return_grace_minutes")
        self.errors.add_row(
            "주말 요금",
            _row(self.weekend_on, self.weekend_multiplier),
            "weekend_enabled", "weekend_multiplier",
        )
        self.errors.add_row(
            "주말 범위",
            _row(self.weekend_start_day, self.weekend_start_time, start_label,
                 self.weekend_end_day, self.weekend_end_time, end_label),
            "weekend_start_weekday", "weekend_start_minute", "weekend_end_weekday", "weekend_end_minute",
        )
        self.errors.add_row("주 단가", _row(self.weekly_on, self.weekly_multiplier),
                            "weekly_enabled", "weekly_multiplier")
        self.errors.add_row("월 단가", _row(self.monthly_on, self.monthly_multiplier),
                            "monthly_enabled", "monthly_multiplier")
        self.errors.add_row("연체료 계산", _row(self.late_method, self.late_divisor, divisor_note),
                            "late_method", "late_hourly_divisor")
        self._weekend_widgets = (
            self.weekend_multiplier, self.weekend_start_day, self.weekend_start_time,
            self.weekend_end_day, self.weekend_end_time, start_label, end_label,
        )

        note = QLabel(
            "주말·주·월 요금은 장비마다 '일 대여료 × 배수'로 정해지며, 고객에게 가장 싼 방식이 자동 적용됩니다."
        )
        note.setWordWrap(True)
        theme.set_tone(note, "muted")
        self.preview_title = QLabel()
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        theme.set_tone(self.preview, "muted")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(self.preview_title)
        lay.addWidget(self.preview)

        for signal in (
            self.day_count.currentIndexChanged, self.return_grace.valueChanged, self.weekend_on.toggled,
            self.weekend_start_day.currentIndexChanged, self.weekend_start_time.timeChanged,
            self.weekend_end_day.currentIndexChanged, self.weekend_end_time.timeChanged,
            self.weekend_multiplier.valueChanged, self.weekly_on.toggled, self.weekly_multiplier.valueChanged,
            self.monthly_on.toggled, self.monthly_multiplier.valueChanged, self.late_method.currentIndexChanged,
            self.late_divisor.valueChanged,
        ):
            signal.connect(self._on_changed)
        self.set_rule(RateRule())

    # ------------------------------------------------------------------ 값

    def set_rule(self, rule: RateRule) -> None:
        self.blockSignals(True)
        try:
            self.day_count.setCurrentIndex(self.day_count.findData(rule.day_count.value))
            self.return_grace.setValue(rule.return_grace_minutes)
            self.weekend_on.setChecked(rule.weekend_enabled)
            self.weekend_start_day.setCurrentIndex(rule.weekend_start_weekday)
            self.weekend_start_time.setTime(QTime(rule.weekend_start_minute // 60, rule.weekend_start_minute % 60))
            self.weekend_end_day.setCurrentIndex(rule.weekend_end_weekday)
            self.weekend_end_time.setTime(QTime(rule.weekend_end_minute // 60, rule.weekend_end_minute % 60))
            self.weekend_multiplier.setValue(float(rule.weekend_multiplier))
            self.weekly_on.setChecked(rule.weekly_enabled)
            self.weekly_multiplier.setValue(float(rule.weekly_multiplier))
            self.monthly_on.setChecked(rule.monthly_enabled)
            self.monthly_multiplier.setValue(float(rule.monthly_multiplier))
            self.late_method.setCurrentIndex(self.late_method.findData(rule.late_method.value))
            self.late_divisor.setValue(rule.late_hourly_divisor)
        finally:
            self.blockSignals(False)
        self._sync()

    def rule(self) -> RateRule:
        """입력값 그대로의 규칙(검증은 서비스가 한다)."""
        return RateRule(
            day_count=DayCountMethod(self.day_count.currentData()),
            return_grace_minutes=self.return_grace.value(),
            weekend_enabled=self.weekend_on.isChecked(),
            weekend_start_weekday=self.weekend_start_day.currentData(),
            weekend_start_minute=_minute(self.weekend_start_time),
            weekend_end_weekday=self.weekend_end_day.currentData(),
            weekend_end_minute=_minute(self.weekend_end_time),
            weekend_multiplier=_decimal(self.weekend_multiplier),
            weekly_enabled=self.weekly_on.isChecked(),
            weekly_multiplier=_decimal(self.weekly_multiplier),
            monthly_enabled=self.monthly_on.isChecked(),
            monthly_multiplier=_decimal(self.monthly_multiplier),
            late_method=LateFeeMethod(self.late_method.currentData()),
            late_hourly_divisor=self.late_divisor.value(),
        )

    def show_error(self, exc: DomainError) -> bool:
        return self.errors.show(exc)

    def clear_errors(self) -> None:
        self.errors.clear()

    # ------------------------------------------------------------------ 표시

    def _on_changed(self, *_args: object) -> None:
        self._sync()
        if not self.signalsBlocked():
            self.changed.emit()

    def _sync(self) -> None:
        hours_24 = self.day_count.currentData() == DayCountMethod.HOURS_24.value
        self.return_grace.setEnabled(hours_24)
        for w in self._weekend_widgets:
            w.setEnabled(self.weekend_on.isChecked())
        self.weekly_multiplier.setEnabled(self.weekly_on.isChecked())
        self.monthly_multiplier.setEnabled(self.monthly_on.isChecked())
        self.late_divisor.setEnabled(self.late_method.currentData() == LateFeeMethod.HOURLY.value)
        self._update_preview()

    def _update_preview(self) -> None:
        self.preview_title.setText(f"예시 계산 (일 대여료 {format_won(EXAMPLE_DAILY_RATE)} 장비)")
        try:
            examples = rate_examples(EXAMPLE_DAILY_RATE, self.rule())
        except (DomainError, ValueError):
            self.preview.setText("입력값을 확인해주세요.")
            return
        self.preview.setText("\n".join(
            f"· {e.label}: {format_won(e.fee.amount_won)} — {e.fee.basis}" for e in examples
        ))
