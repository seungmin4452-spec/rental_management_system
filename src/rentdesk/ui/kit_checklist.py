"""출고·반납 창의 세트 구성품 체크리스트 (MULTI_INDUSTRY_PLAN R4).

예약에 담긴 세트마다 구성품 체크박스를 보여 주고, 체크 결과(order_kits.id → 줄마다 True/False)를
서비스에 넘긴다. 빠진 구성품은 경고만 하며 출고·반납을 막지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from rentdesk.domain.kits import OrderKit, missing_items
from rentdesk.ui import theme

COLUMNS = 3


class KitChecklistBox(QWidget):
    def __init__(self, kits: Sequence[OrderKit], *, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kits = [k for k in kits if k.checklist]
        self.boxes: dict[int, list[QCheckBox]] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for kit in self.kits:
            group = QGroupBox(f"{title}: {kit.name}")
            grid = QGridLayout(group)
            boxes = []
            for n, label in enumerate(kit.checklist):
                box = QCheckBox(label)
                box.toggled.connect(self._update_warning)
                grid.addWidget(box, n // COLUMNS, n % COLUMNS)
                boxes.append(box)
            self.boxes[kit.id] = boxes
            lay.addWidget(group)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        theme.set_tone(self.warning, "warning")
        lay.addWidget(self.warning)
        self.setVisible(bool(self.kits))
        self._update_warning()

    def checks(self) -> dict[int, list[bool]]:
        return {kit_id: [b.isChecked() for b in boxes] for kit_id, boxes in self.boxes.items()}

    def check_all(self) -> None:
        for boxes in self.boxes.values():
            for b in boxes:
                b.setChecked(True)

    def missing(self) -> list[tuple[str, list[str]]]:
        """(세트 이름, 체크하지 않은 구성품) — 빠진 것이 있는 세트만."""
        checks = self.checks()
        result = []
        for kit in self.kits:
            labels = missing_items(kit.checklist, checks[kit.id])
            if labels:
                result.append((kit.name, labels))
        return result

    def missing_text(self) -> str:
        return "\n".join(f"- {name}: {', '.join(labels)}" for name, labels in self.missing())

    def _update_warning(self) -> None:
        if not hasattr(self, "warning"):
            return
        text = self.missing_text()
        self.warning.setText(f"확인하지 않은 구성품이 있습니다.\n{text}" if text else "")
