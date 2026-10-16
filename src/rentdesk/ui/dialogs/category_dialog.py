"""장비 > 카테고리 관리 (MULTI_INDUSTRY_PLAN R1·R2).

카테고리 추가·이름 변경·숨김과 카테고리별 운영 설정(준비 시간·연체 유예·연체 배수).
각 항목은 "가게 기본값 사용"이 기본이며, 가게 기본값과 다른 항목만 표에 굵게 표시한다.
검증·저장은 `AssetService`가 하고 이 화면은 입력과 표시만 한다.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.models import Category, RentalPolicy
from rentdesk.domain.policy import effective_policy, format_minutes
from rentdesk.services.settings import MAX_MINUTES
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import DataTable, FormErrors, button_row

FIELD_WIDTH = 120


def _multiplier_text(value: Decimal) -> str:
    return f"{value.normalize():f}배"


class _PolicyField:
    """한 항목: [가게 기본값 사용] 체크 + 값 입력. 체크하면 값 입력을 막는다."""

    def __init__(self, spin: QSpinBox | QDoubleSpinBox, describe) -> None:
        self.spin = spin
        self.spin.setFixedWidth(FIELD_WIDTH)
        self.describe = describe
        self.use_default = QCheckBox("가게 기본값 사용")
        self.use_default.toggled.connect(lambda on: self.spin.setEnabled(not on))
        self.widget = QWidget()
        lay = QHBoxLayout(self.widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.use_default)
        lay.addWidget(self.spin)
        lay.addStretch(1)

    def load(self, value, shop_value) -> None:
        self.use_default.setText(f"가게 기본값 사용 ({self.describe(shop_value)})")
        self.use_default.setChecked(value is None)
        shown = shop_value if value is None else value
        self.spin.setValue(float(shown) if isinstance(self.spin, QDoubleSpinBox) else int(shown))
        self.spin.setEnabled(value is not None)

    def value(self):
        if self.use_default.isChecked():
            return None
        if isinstance(self.spin, QDoubleSpinBox):
            return f"{self.spin.value():.2f}"
        return self.spin.value()


class CategoryDialog(QDialog):
    HEADERS = ["카테고리", "준비 시간", "기다려 주는 시간", "연체 1일당", "상태"]

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.changed = False
        self.setWindowTitle("카테고리 관리")
        self.setMinimumSize(900, 520)

        intro = QLabel(
            "카테고리마다 반납 후 준비 시간·연체 기준을 다르게 둘 수 있습니다(예: 텐트는 세척·건조 12시간). "
            "비워 두면(가게 기본값 사용) 설정 화면의 값을 따릅니다. 바꾼 값은 이후 확정되는 예약부터 적용됩니다. "
            "'기타'는 어느 카테고리에도 맞지 않는 장비를 넣는 기본 칸이라 이름을 바꾸거나 숨길 수 없습니다."
        )
        intro.setWordWrap(True)
        theme.set_tone(intro, "muted")

        self.show_archived = QCheckBox("숨긴 카테고리도 보기")
        self.show_archived.toggled.connect(self.refresh)
        self.table = DataTable(self.HEADERS)
        self.table.itemSelectionChanged.connect(self._load_selected)
        self.new_name = QLineEdit()
        self.new_name.setMaxLength(50)
        self.new_name.setPlaceholderText("새 카테고리 이름")
        self.add_btn = QPushButton("추가")
        self.add_btn.clicked.connect(self.add_category)
        self.new_name.returnPressed.connect(self.add_category)
        self.archive_btn = QPushButton("숨기기")
        self.archive_btn.clicked.connect(self.toggle_archived)
        add_row = QHBoxLayout()
        add_row.addWidget(self.new_name, 1)
        add_row.addWidget(self.add_btn)
        add_row.addWidget(self.archive_btn)

        left = QVBoxLayout()
        left.addWidget(self.show_archived)
        left.addWidget(self.table, 1)
        left.addLayout(add_row)

        box = QGroupBox("선택한 카테고리")
        form = QFormLayout(box)
        self.errors = FormErrors(form)
        self.name = QLineEdit()
        self.name.setMaxLength(50)
        buffer = QSpinBox()
        buffer.setRange(0, MAX_MINUTES)
        buffer.setSingleStep(60)
        buffer.setSuffix("분")
        grace = QSpinBox()
        grace.setRange(0, MAX_MINUTES)
        grace.setSingleStep(30)
        grace.setSuffix("분")
        multiplier = QDoubleSpinBox()
        multiplier.setRange(0, 100)
        multiplier.setDecimals(2)
        multiplier.setSingleStep(0.5)
        multiplier.setSuffix("배")
        self.buffer = _PolicyField(buffer, format_minutes)
        self.grace = _PolicyField(grace, format_minutes)
        self.multiplier = _PolicyField(multiplier, _multiplier_text)
        self.buffer_hint = QLabel()
        theme.set_tone(self.buffer_hint, "muted")
        buffer.valueChanged.connect(lambda v: self.buffer_hint.setText(f"= {format_minutes(v)}"))
        self.errors.add_row("이름", self.name, "category")
        self.errors.add_row("반납 후 다음 대여까지 준비 시간", self.buffer.widget, "turnaround_buffer_minutes")
        form.addRow("", self.buffer_hint)
        self.errors.add_row("연체료 없이 기다려 주는 시간", self.grace.widget, "grace_minutes")
        self.errors.add_row("연체 1일당 일 대여료의", self.multiplier.widget, "late_multiplier")
        self.save_btn = theme.set_variant(QPushButton("저장"), "primary")
        self.save_btn.clicked.connect(self.save)
        form.addRow(button_row(self.save_btn))

        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(box, 2)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(body, 1)
        lay.addLayout(button_row(close))

        self._categories: dict[int, Category] = {}
        self.refresh()

    # ------------------------------------------------------------------ 표

    def _shop(self) -> RentalPolicy:
        return self.ctx.settings.get_policy()

    def refresh(self, select_id: int | None = None) -> None:
        if select_id is None or isinstance(select_id, bool):
            select_id = self.table.selected_key()
        shop = self._shop()
        categories = self.ctx.assets.list_categories(include_archived=self.show_archived.isChecked())
        self._categories = {c.id: c for c in categories}
        rows = []
        for c in categories:
            eff = effective_policy(shop, c.policy)
            rows.append((c.id, [
                c.name + (" (기본)" if c.is_system else ""),
                format_minutes(eff.turnaround_buffer_minutes),
                format_minutes(eff.grace_minutes),
                _multiplier_text(eff.late_multiplier),
                "숨김" if c.is_archived else "사용",
            ]))
        self.table.set_rows(rows)
        columns = {"turnaround_buffer_minutes": 1, "grace_minutes": 2, "late_multiplier": 3}
        for r, c in enumerate(categories):  # 가게 기본값과 다른 항목만 굵게
            for field, col in columns.items():
                if field in c.policy.overridden:
                    item = self.table.item(r, col)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip("카테고리 설정")
        if select_id in self._categories:
            self.table.select_key(select_id)
        elif categories:
            self.table.selectRow(0)
        self._load_selected()

    def _selected(self) -> Category | None:
        key = self.table.selected_key()
        return self._categories.get(key) if key is not None else None

    def _load_selected(self) -> None:
        self.errors.clear()
        category = self._selected()
        editable = category is not None
        for w in (self.name, self.buffer.widget, self.grace.widget, self.multiplier.widget, self.save_btn):
            w.setEnabled(editable)
        self.archive_btn.setEnabled(editable and not category.is_system)
        if category is None:
            self.name.clear()
            return
        shop = self._shop()
        self.name.setText(category.name)
        self.name.setReadOnly(category.is_system)
        self.name.setToolTip("'기타'는 이름을 바꿀 수 없습니다." if category.is_system else "")
        self.buffer.load(category.policy.turnaround_buffer_minutes, shop.turnaround_buffer_minutes)
        self.grace.load(category.policy.grace_minutes, shop.grace_minutes)
        self.multiplier.load(category.policy.late_multiplier, shop.late_multiplier)
        self.buffer_hint.setText(f"= {format_minutes(self.buffer.spin.value())}")
        self.archive_btn.setText("다시 사용" if category.is_archived else "숨기기")

    # ------------------------------------------------------------------ 동작

    def add_category(self) -> None:
        created = feedback.run(self, lambda: self.ctx.assets.create_category(self.new_name.text()))
        if created is not None:
            self.changed = True
            self.new_name.clear()
            self.refresh(created.id)

    def toggle_archived(self) -> None:
        category = self._selected()
        if category is None:
            return
        if not category.is_archived and not feedback.confirm(
            self,
            f"'{category.name}' 카테고리를 숨깁니다. 장비 등록 창의 목록에서 빠지며, "
            "이미 등록된 장비와 이력은 그대로 둡니다.",
        ):
            return
        updated = feedback.run(
            self, lambda: self.ctx.assets.set_category_archived(category.id, not category.is_archived)
        )
        if updated is not None:
            self.changed = True
            if updated.is_archived and not self.show_archived.isChecked():
                self.refresh(-1)
            else:
                self.refresh(updated.id)

    def save(self) -> bool:
        category = self._selected()
        if category is None:
            return False
        self.errors.clear()
        updated = feedback.run(self, lambda: self.ctx.assets.update_category(
            category.id, name=self.name.text(),
            turnaround_buffer_minutes=self.buffer.value(),
            grace_minutes=self.grace.value(),
            late_multiplier=self.multiplier.value(),
        ), on_domain_error=self.errors.show)
        if updated is None:
            return False
        self.changed = True
        self.refresh(updated.id)
        return True
