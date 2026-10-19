"""장비 > 세트 만들기·수정 (MULTI_INDUSTRY_PLAN R4).

세트 = 이름 + 구성 장비(관리번호 있는 실물) + 구성품 체크리스트(관리번호 없는 부속) + 세트 할인(정액/비율).
검증·저장은 `KitService`가 하고, 이 화면은 입력과 미리보기만 한다.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_won
from rentdesk.domain.errors import DomainError
from rentdesk.domain.kits import KIT_DISCOUNT_KIND_LABELS, Kit, KitDiscountKind
from rentdesk.domain.models import Asset, LifecycleStatus
from rentdesk.services.kits import KitInput
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import DataTable, FormErrors, WonSpinBox, button_row

MEMBER_HEADERS = ["관리번호", "장비명", "카테고리", "일 대여료", "상태"]


class KitDialog(QDialog):
    def __init__(self, ctx: AppContext, kit: Kit | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.kit = kit
        self.result_kit: Kit | None = None
        self.setWindowTitle(f"세트 수정 — {kit.name}" if kit else "새 세트")
        self.setMinimumSize(760, 600)
        self._assets: dict[int, Asset] = {
            a.id: a for a in ctx.assets.search_assets(include_retired=True)
        }
        self._members: list[int] = list(kit.asset_ids) if kit else []

        intro = QLabel(
            "자주 함께 나가는 장비를 세트로 묶어 두면 예약할 때 한 번에 담을 수 있습니다. "
            "가용성·출고·반납·요금은 지금처럼 장비 하나하나 기준이며, 예약 기간에 안 되는 장비는 "
            "같은 카테고리의 다른 장비로 바꾸거나 빼고 진행할 수 있습니다."
        )
        intro.setWordWrap(True)
        theme.set_tone(intro, "muted")

        form = QFormLayout()
        self.errors = FormErrors(form)
        self.name = QLineEdit(kit.name if kit else "")
        self.name.setMaxLength(50)
        self.name.setPlaceholderText("예: 카메라 기본 세트")
        self.discount_kind = QComboBox()
        for kind, label in KIT_DISCOUNT_KIND_LABELS.items():
            self.discount_kind.addItem(label, kind)
        self.discount_won = WonSpinBox()
        self.discount_percent = QDoubleSpinBox()
        self.discount_percent.setRange(0.01, 100)
        self.discount_percent.setDecimals(2)
        self.discount_percent.setSingleStep(5)
        self.discount_percent.setSuffix("%")
        self.discount_percent.setValue(10)
        discount_row = QWidget()
        dl = QHBoxLayout(discount_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(self.discount_kind)
        dl.addWidget(self.discount_won)
        dl.addWidget(self.discount_percent)
        dl.addStretch(1)
        if kit:
            self.discount_kind.setCurrentIndex(self.discount_kind.findData(kit.discount.kind))
            self.discount_won.setValue(kit.discount.won)
            if kit.discount.percent is not None:
                self.discount_percent.setValue(float(kit.discount.percent))
        self.discount_kind.currentIndexChanged.connect(self._sync_discount_widgets)
        self.discount_won.valueChanged.connect(self._update_preview)
        self.discount_percent.valueChanged.connect(self._update_preview)
        self.note = QLineEdit((kit.note or "") if kit else "")
        self.note.setMaxLength(1000)
        self.errors.add_row("세트 이름 *", self.name, "name")
        self.errors.add_row("세트 할인", discount_row, "discount_kind", "discount_won", "discount_percent")
        self.errors.add_row("메모", self.note, "note")

        # 구성 장비
        self.picker = QComboBox()
        self.picker.setEditable(True)
        self.picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.picker.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.picker.completer().setCompletionMode(self.picker.completer().CompletionMode.PopupCompletion)
        for a in self._assets.values():
            if a.lifecycle_status == LifecycleStatus.ACTIVE:
                self.picker.addItem(f"{a.asset_code} · {a.name} ({a.category_name})", a.id)
        self.picker.setCurrentIndex(-1)
        self.picker.lineEdit().setPlaceholderText("장비번호·이름으로 찾아 추가")
        self.add_btn = QPushButton("장비 추가")
        self.add_btn.clicked.connect(self.add_selected)
        self.remove_btn = QPushButton("선택 장비 빼기")
        self.remove_btn.clicked.connect(self.remove_selected)
        picker_row = QHBoxLayout()
        picker_row.addWidget(self.picker, 1)
        picker_row.addWidget(self.add_btn)
        picker_row.addWidget(self.remove_btn)
        self.members = DataTable(MEMBER_HEADERS)
        self.member_errors = QLabel()
        theme.set_tone(self.member_errors, "danger")
        self.member_errors.setWordWrap(True)

        # 구성품 체크리스트
        self.checklist = QPlainTextEdit("\n".join(kit.checklist) if kit else "")
        self.checklist.setPlaceholderText("한 줄에 하나씩: 배터리 2개 / 충전기 / 메모리카드 / 가방")
        self.checklist.setFixedHeight(96)
        self.checklist_errors = QLabel()
        theme.set_tone(self.checklist_errors, "danger")
        self.checklist_errors.setWordWrap(True)

        self.preview = QLabel()
        self.preview.setWordWrap(True)

        self.save_btn = theme.set_variant(QPushButton("저장"), "primary")
        self.save_btn.clicked.connect(self.save)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(QLabel("<b>구성 장비</b> (관리번호가 있는 장비)"))
        lay.addLayout(picker_row)
        lay.addWidget(self.members, 1)
        lay.addWidget(self.member_errors)
        lay.addWidget(QLabel("<b>구성품 체크리스트</b> (관리번호 없는 부속 — 출고·반납 때 확인)"))
        lay.addWidget(self.checklist)
        lay.addWidget(self.checklist_errors)
        lay.addWidget(self.preview)
        lay.addLayout(button_row(cancel, self.save_btn))
        self._sync_discount_widgets()
        self._refresh_members()

    # ------------------------------------------------------------------ 입력

    def _discount_kind(self) -> KitDiscountKind:
        return self.discount_kind.currentData()

    def _percent_text(self) -> str:
        return f"{Decimal(str(self.discount_percent.value())).quantize(Decimal('0.01')).normalize():f}"

    def _sync_discount_widgets(self) -> None:
        percent = self._discount_kind() == KitDiscountKind.PERCENT
        self.discount_won.setVisible(not percent)
        self.discount_percent.setVisible(percent)
        self._update_preview()

    def add_asset(self, asset_id: int) -> None:
        if asset_id in self._members:
            self.member_errors.setText("이미 세트에 들어 있는 장비입니다.")
            return
        self._members.append(asset_id)
        self._refresh_members()

    def add_selected(self) -> None:
        idx = self.picker.currentIndex()
        if idx < 0 or self.picker.itemText(idx) != self.picker.currentText():
            self.member_errors.setText("목록에서 장비를 골라주세요.")
            return
        self.add_asset(self.picker.itemData(idx))
        self.picker.setCurrentIndex(-1)

    def remove_selected(self) -> None:
        key = self.members.selected_key()
        if key in self._members:
            self._members.remove(key)
            self._refresh_members()

    def _refresh_members(self) -> None:
        self.member_errors.clear()
        rows = []
        for asset_id in self._members:
            a = self._assets[asset_id]
            retired = a.lifecycle_status != LifecycleStatus.ACTIVE
            rows.append((asset_id, [a.asset_code, a.name, a.category_name, format_won(a.daily_rate_won),
                                    "사용 중지 — 예약 때 다른 장비로 바꿔야 함" if retired else "운영 중"]))
        self.members.set_rows(
            rows, align_right=(3,),
            highlight=lambda k: self._assets[k].lifecycle_status != LifecycleStatus.ACTIVE,
        )
        self._update_preview()

    def _update_preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        try:
            total, discount = self.ctx.kits.daily_preview(
                self._members, self._discount_kind(), self.discount_won.value(), self._percent_text()
            )
        except DomainError as exc:
            self.preview.setText(exc.message)
            return
        text = f"구성 장비 {len(self._members)}개 · 1일 요금 합계 <b>{format_won(total)}</b>"
        if discount:
            text += f" − 세트 할인 {format_won(discount)} = <b>{format_won(total - discount)}</b> (1일 기준)"
        self.preview.setText(text)

    def _input(self) -> KitInput:
        percent = self._discount_kind() == KitDiscountKind.PERCENT
        return KitInput(
            name=self.name.text(),
            asset_ids=list(self._members),
            checklist=self.checklist.toPlainText().splitlines(),
            discount_kind=self._discount_kind(),
            discount_won=0 if percent else self.discount_won.value(),
            discount_percent=self._percent_text() if percent else None,
            note=self.note.text(),
        )

    def _route_error(self, exc: DomainError) -> bool:
        field = getattr(exc, "field", None)
        if field == "asset_ids":
            self.member_errors.setText(exc.message)
            return True
        if field == "checklist":
            self.checklist_errors.setText(exc.message)
            return True
        return self.errors.show(exc)

    def save(self) -> bool:
        self.errors.clear()
        self.member_errors.clear()
        self.checklist_errors.clear()
        data = self._input()
        if self.kit is None:
            action = lambda: self.ctx.kits.create_kit(data)  # noqa: E731
        else:
            kit_id = self.kit.id
            action = lambda: self.ctx.kits.update_kit(kit_id, data)  # noqa: E731
        result = feedback.run(self, action, on_domain_error=self._route_error)
        if result is None:
            return False
        self.result_kit = result
        self.accept()
        return True
