"""S-09 보고서: 5개 표준 엑셀 보고서 저장."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.display import seoul_today
from rentdesk.domain.models import ORDER_STATUS_LABELS, LifecycleStatus, OrderStatus
from rentdesk.services.reports import DISCLAIMER, ReportExistsError, ReportTable
from rentdesk.ui import feedback, theme
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import DateEdit, SeoulDateTimeEdit, now_seoul_minute

REPORT_KINDS = ("assets", "reservations", "schedule", "overdue", "settlements")


class ReportsPage(Page):
    title = "보고서"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        today = seoul_today(ctx.now())
        self.last_dir: Path | None = None
        self.last_saved: Path | None = None

        # 1. 장비 목록
        self.asset_category = QComboBox()
        self.asset_lifecycle = QComboBox()
        self.asset_lifecycle.addItem("전체", None)
        self.asset_lifecycle.addItem("활성", LifecycleStatus.ACTIVE)
        self.asset_lifecycle.addItem("은퇴", LifecycleStatus.RETIRED)
        g1 = self._group("장비 목록", [("카테고리", self.asset_category),
                                       ("운영 상태", self.asset_lifecycle)], "assets")
        # 2. 예약 목록
        self.res_from = DateEdit(today.replace(day=1))
        self.res_to = DateEdit(today + timedelta(days=30))
        self.res_status = QComboBox()
        self.res_status.addItem("전체", None)
        for s, label in ORDER_STATUS_LABELS.items():
            self.res_status.addItem(label, s)
        g2 = self._group("예약 목록", [("시작일", self.res_from), ("종료일", self.res_to),
                                       ("주문 상태", self.res_status)], "reservations")
        # 3. 오늘 출고·반납
        self.schedule_day = DateEdit(today)
        g3 = self._group("오늘 출고·반납", [("기준일", self.schedule_day)], "schedule")
        # 4. 연체 목록
        self.overdue_at = SeoulDateTimeEdit(now_seoul_minute(ctx.now()))
        g4 = self._group("연체 목록", [("기준 시각", self.overdue_at)], "overdue")
        # 5. 정산 목록
        self.settle_from = DateEdit(today.replace(day=1))
        self.settle_to = DateEdit(today)
        self.settle_unsettled = QCheckBox("미정산만")
        g5 = self._group("정산 목록", [("시작일", self.settle_from), ("종료일", self.settle_to),
                                       ("", self.settle_unsettled)], "settlements")

        body = QWidget()
        lay = QVBoxLayout(body)
        notice = QLabel(f"엑셀(.xlsx)로 저장합니다. {DISCLAIMER}")
        notice.setWordWrap(True)
        theme.set_tone(notice, "muted")
        lay.addWidget(notice)
        for g in (g1, g2, g3, g4, g5):
            lay.addWidget(g)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.refresh()

    def _group(self, title: str, fields, kind: str) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        for label, widget in fields:
            form.addRow(label, widget)
        btn = theme.set_variant(QPushButton("엑셀로 저장…"), "primary")
        btn.clicked.connect(lambda: self.save_dialog(kind))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        form.addRow(row)
        return box

    def refresh(self) -> None:
        current = self.asset_category.currentData()
        self.asset_category.clear()
        self.asset_category.addItem("전체", None)
        for c in self.ctx.assets.list_categories(include_archived=True):
            self.asset_category.addItem(c.name, c.id)
        self.asset_category.setCurrentIndex(max(0, self.asset_category.findData(current)))

    def build(self, kind: str) -> ReportTable:
        r = self.ctx.reports
        if kind == "assets":
            return r.assets_report(category_id=self.asset_category.currentData(),
                                   lifecycle=self.asset_lifecycle.currentData())
        if kind == "reservations":
            status = self.res_status.currentData()
            return r.reservations_report(self.res_from.value(), self.res_to.value(),
                                         statuses=[OrderStatus(status)] if status else None)
        if kind == "schedule":
            return r.daily_schedule_report(self.schedule_day.value())
        if kind == "overdue":
            return r.overdue_report(self.overdue_at.value())
        if kind == "settlements":
            return r.settlement_report(self.settle_from.value(), self.settle_to.value(),
                                       unsettled_only=self.settle_unsettled.isChecked())
        raise ValueError(kind)

    def _default_dir(self) -> Path:
        if self.last_dir is not None:
            return self.last_dir
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        return Path(docs) if docs else Path.home()

    def save_dialog(self, kind: str) -> None:
        table = feedback.run(self, lambda: self.build(kind))
        if table is None:
            return
        # Qt 표준(Finder/탐색기) 저장 대화상자. 같은 이름이 있으면 대화상자가 덮어쓰기를 확인한다.
        path, _ = QFileDialog.getSaveFileName(
            self, f"{table.title} 저장", str(self._default_dir() / f"{table.file_stem}.xlsx"),
            "Excel 통합 문서 (*.xlsx)",
        )
        if path:
            self.export_to(kind, Path(path), overwrite=True, table=table)

    def export_to(self, kind: str, path: Path, *, overwrite: bool = False,
                  table: ReportTable | None = None) -> Path | None:
        table = table or feedback.run(self, lambda: self.build(kind))
        if table is None:
            return None

        def on_error(exc) -> bool:
            if isinstance(exc, ReportExistsError) and feedback.confirm(
                self, f"{exc.message}\n덮어쓸까요?", "덮어쓰기 확인"
            ):
                self.export_to(kind, path, overwrite=True, table=table)
                return True
            return False

        saved = feedback.run(
            self, lambda: self.ctx.reports.export(table, path, overwrite=overwrite),
            on_domain_error=on_error,
        )
        if saved is not None:
            self.last_dir = saved.parent
            self.last_saved = saved
            feedback.info(self, f"{table.title}을(를) 저장했습니다 ({len(table.rows)}건).\n{saved}")
        return saved
