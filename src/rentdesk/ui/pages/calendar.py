"""예약 달력(U9): 행 = 장비, 열 = 날짜. 예약·정비를 막대로 보여준다.

- 주(7일)/월 보기, 이전·오늘·다음, 카테고리·검색 필터.
- 막대 더블클릭 → 예약 상세(정비는 정비 관리). 빈 칸을 끌어 고른 뒤 [선택 기간으로 새 예약]
  또는 빈 칸 더블클릭 → 그 장비·기간으로 예약 마법사.
- 화면에는 SQL·가용성 판정이 없다. 데이터는 `CalendarService`, 예약 가능 여부 최종 판단은 예약 마법사·서비스.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain import calendar as cal
from rentdesk.domain.display import seoul_today
from rentdesk.domain.errors import DomainError
from rentdesk.services.calendar import CalendarBar, CalendarView
from rentdesk.ui import feedback, theme
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import empty_hint

WEEKDAYS = "월화수목금토일"
BAR_ROLE = Qt.ItemDataRole.UserRole + 1
TODAY_ROLE = Qt.ItemDataRole.UserRole + 2
LANE_HEIGHT = 34
MONTH_COLUMN_WIDTH = 76
BAR_TONES = {
    cal.BarKind.DRAFT: "neutral",
    cal.BarKind.CONFIRMED: "accent",
    cal.BarKind.IN_RENTAL: "purple",
    cal.BarKind.OVERDUE: "danger",
    cal.BarKind.RETURNED: "success",
    cal.BarKind.MAINTENANCE: "warning",
}
MODE_WEEK, MODE_MONTH = "week", "month"
SELECT_HINT = "빈 칸을 끌어 장비와 날짜를 고른 뒤 [선택 기간으로 새 예약]을 누르세요. 빈 칸 더블클릭은 하루 예약입니다."


def bar_colors(kind: cal.BarKind) -> tuple[QColor, QColor]:
    """(막대 바탕, 글자·테두리) 색."""
    tone = BAR_TONES[kind]
    t = theme.current()
    fg = t.accent_text if tone == "accent" else getattr(t, tone)
    return QColor(getattr(t, f"{tone}_soft")), QColor(fg)


class CalendarDelegate(QStyledItemDelegate):
    """오늘 열 바탕을 칠하고, 막대 칸(여러 날짜에 걸친 칸)은 둥근 막대로 그린다."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        t = theme.current()
        if index.data(TODAY_ROLE):
            painter.fillRect(option.rect, QColor(t.accent_soft))
        bar: CalendarBar | None = index.data(BAR_ROLE)
        if bar is None:
            super().paint(painter, option, index)
            return
        bg, fg = bar_colors(bar.kind)
        rect = QRectF(option.rect).adjusted(3, 4, -3, -4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(bg)
        pen = QPen(fg, 1.2)
        if bar.kind == cal.BarKind.DRAFT:
            pen.setStyle(Qt.PenStyle.DashLine)
        else:
            pen = QPen(Qt.PenStyle.NoPen)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 6, 6)
        if bar.kind != cal.BarKind.DRAFT:  # 왼쪽 색 띠(시작 표시). 보기 이전에 시작했으면 생략
            if not bar.starts_before_view:
                painter.setBrush(fg)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(rect.left(), rect.top(), 4, rect.height()), 2, 2)
        font = QFont(option.font)
        font.setPixelSize(12)
        font.setBold(bar.kind != cal.BarKind.RETURNED)
        painter.setFont(font)
        painter.setPen(fg)
        text = ("◀ " if bar.starts_before_view else "") + bar.label
        text_rect = rect.adjusted(10, 0, -6, 0)
        elided = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(t.accent), 2))
            painter.drawRoundedRect(rect, 6, 6)
        painter.restore()


class CalendarPage(Page):
    title = "예약 달력"

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        today = seoul_today(ctx.now())
        self.anchor: date = today
        self.view: CalendarView | None = None
        self._row_assets: list[int] = []  # 표 행 → 장비 id(장비마다 겹침 줄 수만큼 행)

        self.prev_btn = QPushButton("◀ 이전")
        self.today_btn = QPushButton("오늘")
        self.next_btn = QPushButton("다음 ▶")
        self.prev_btn.clicked.connect(lambda: self.move(-1))
        self.next_btn.clicked.connect(lambda: self.move(1))
        self.today_btn.clicked.connect(self.go_today)
        self.period_label = QLabel()
        self.period_label.setObjectName("SectionTitle")
        self.mode = QComboBox()
        self.mode.addItem("주 보기", MODE_WEEK)
        self.mode.addItem("월 보기", MODE_MONTH)
        self.mode.currentIndexChanged.connect(self.refresh)
        nav_row = QHBoxLayout()
        for w in (self.prev_btn, self.today_btn, self.next_btn):
            nav_row.addWidget(w)
        nav_row.addSpacing(12)
        nav_row.addWidget(self.period_label)
        nav_row.addStretch(1)
        nav_row.addWidget(self.mode)

        self.query = QLineEdit()
        self.query.setPlaceholderText("장비번호/이름 검색")
        self.query.setClearButtonEnabled(True)
        self.query.textChanged.connect(self.refresh)
        self.category = QComboBox()
        self.category.currentIndexChanged.connect(self.refresh)
        self.show_drafts = QCheckBox("임시 저장 표시")
        self.show_drafts.setChecked(True)
        self.show_returned = QCheckBox("반납 완료 표시")
        self.show_returned.setChecked(True)
        for box in (self.show_drafts, self.show_returned):
            box.toggled.connect(self.refresh)
        filters = QHBoxLayout()
        filters.addWidget(self.query, 1)
        filters.addWidget(self.category)
        filters.addWidget(self.show_drafts)
        filters.addWidget(self.show_returned)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setShowGrid(True)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setWordWrap(False)
        self.table.setItemDelegate(CalendarDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(LANE_HEIGHT)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setMinimumWidth(170)
        self.table.horizontalHeader().setMinimumSectionSize(40)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._update_selection)

        legend = QHBoxLayout()
        legend.setSpacing(6)
        for kind in cal.BarKind:
            chip = theme.set_chip(QLabel(cal.BAR_KIND_LABELS[kind]), BAR_TONES[kind])
            legend.addWidget(chip)
        legend.addStretch(1)
        self.selection_label = QLabel(SELECT_HINT)
        self.selection_label.setWordWrap(True)
        theme.set_tone(self.selection_label, "muted")
        self.new_btn = theme.set_variant(QPushButton("선택 기간으로 새 예약"), "primary")
        self.new_btn.setEnabled(False)
        self.new_btn.clicked.connect(self.new_from_selection)
        bottom = QHBoxLayout()
        bottom.addWidget(self.selection_label, 1)
        bottom.addWidget(self.new_btn)

        self.empty = empty_hint("표시할 장비가 없습니다.", "장비 관리로", lambda: self.nav.goto("assets"))
        lay = QVBoxLayout(self)
        lay.addLayout(nav_row)
        lay.addLayout(filters)
        lay.addLayout(legend)
        lay.addWidget(self.empty)
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)
        self._load_categories()
        self.refresh()

    # ------------------------------------------------------------------ 기간

    def period(self) -> tuple[date, int]:
        if self.mode.currentData() == MODE_MONTH:
            return cal.month_days(self.anchor)
        return cal.week_start(self.anchor), cal.WEEK_DAYS

    def move(self, step: int) -> None:
        if self.mode.currentData() == MODE_MONTH:
            first, days = cal.month_days(self.anchor)
            target = first + timedelta(days=days) if step > 0 else first - timedelta(days=1)
            self.anchor = target.replace(day=1)
        else:
            self.anchor += timedelta(days=7 * step)
        self.refresh()

    def go_today(self) -> None:
        self.anchor = seoul_today(self.ctx.now())
        self.refresh()

    def apply_filters(self, **filters: object) -> None:
        if "day" in filters and isinstance(filters["day"], date):
            self.anchor = filters["day"]
        if "query" in filters:
            self.query.setText(str(filters["query"]))

    # ------------------------------------------------------------------ 그리기

    def _load_categories(self) -> None:
        current = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("전체 카테고리", None)
        for c in self.ctx.assets.list_categories():
            self.category.addItem(c.name, c.id)
        self.category.setCurrentIndex(max(0, self.category.findData(current)))
        self.category.blockSignals(False)

    def refresh(self) -> None:
        self._load_categories()
        first, days = self.period()
        view = self.ctx.calendar.view(
            first, days, query=self.query.text(), category_id=self.category.currentData(),
            include_drafts=self.show_drafts.isChecked(),
            include_returned=self.show_returned.isChecked(),
        )
        self.view = view
        last = view.days[-1]
        if self.mode.currentData() == MODE_MONTH:
            self.period_label.setText(f"{first.year}년 {first.month}월")
        else:
            end = f"{last.month}월 {last.day}일" if last.month != first.month else f"{last.day}일"
            self.period_label.setText(f"{first.year}년 {first.month}월 {first.day}일 ~ {end}")
        self._render(view)
        no_filter = not self.query.text().strip() and self.category.currentData() is None
        self.empty.setVisible(not view.rows and no_filter)
        self._update_selection()

    def _render(self, view: CalendarView) -> None:
        table = self.table
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.clearSpans()
            table.clear()
            row_count = sum(r.lane_count for r in view.rows)
            table.setRowCount(row_count)
            table.setColumnCount(len(view.days))
            today_col = next((i for i, d in enumerate(view.days) if d == view.today), None)
            for col, day in enumerate(view.days):
                weekday = "오늘" if col == today_col else WEEKDAYS[day.weekday()]
                table.setHorizontalHeaderItem(col, QTableWidgetItem(f"{day.month}/{day.day}\n{weekday}"))
            self._row_assets = []
            row = 0
            for cal_row in view.rows:
                a = cal_row.asset
                for lane in range(cal_row.lane_count):
                    head = QTableWidgetItem(f"{a.asset_code}  {a.name}" if lane == 0 else "")
                    head.setToolTip(f"{a.asset_code} {a.name} ({a.category_name})")
                    table.setVerticalHeaderItem(row + lane, head)
                    self._row_assets.append(a.id)
                    if today_col is not None:
                        marker = QTableWidgetItem()
                        marker.setData(TODAY_ROLE, True)
                        table.setItem(row + lane, today_col, marker)
                for bar in cal_row.bars:
                    item = QTableWidgetItem(bar.label)
                    item.setData(BAR_ROLE, bar)
                    item.setToolTip(bar.tooltip)
                    table.setItem(row + bar.lane, bar.first_col, item)
                    width = bar.last_col - bar.first_col + 1
                    if width > 1:
                        table.setSpan(row + bar.lane, bar.first_col, 1, width)
                row += cal_row.lane_count
            header = table.horizontalHeader()
            if self.mode.currentData() == MODE_MONTH:
                header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
                header.setDefaultSectionSize(MONTH_COLUMN_WIDTH)
            else:
                header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(True)
        if today_col is not None and self.mode.currentData() == MODE_MONTH:
            # 월 보기는 옆으로 넘치므로 오늘 열이 보이게(오늘 이틀 전부터)
            QTimer.singleShot(0, lambda: self._scroll_to_column(max(0, today_col - 2)))

    def _scroll_to_column(self, col: int) -> None:
        if col < self.table.columnCount():
            pos = self.table.horizontalHeader().sectionPosition(col)
            self.table.horizontalScrollBar().setValue(pos)

    # ------------------------------------------------------------------ 선택·열기

    def bar_at(self, row: int, col: int) -> CalendarBar | None:
        item = self.table.item(row, col)
        return item.data(BAR_ROLE) if item is not None else None

    def selected_cells(self) -> tuple[int, int, int] | None:
        """(장비 id, 첫 열, 마지막 열). 한 장비의 한 줄만 고른 경우만."""
        ranges = self.table.selectedRanges()
        if len(ranges) != 1:
            return None
        rng = ranges[0]
        if rng.topRow() != rng.bottomRow() or rng.topRow() >= len(self._row_assets):
            return None
        return self._row_assets[rng.topRow()], rng.leftColumn(), rng.rightColumn()

    def _busy_bars(self, asset_id: int, first: int, last: int) -> list[CalendarBar]:
        """고른 날짜에 걸친 그 장비의 막대(임시 저장 제외 — 점유하지 않음)."""
        if self.view is None:
            return []
        row = next((r for r in self.view.rows if r.asset.id == asset_id), None)
        if row is None:
            return []
        return [b for b in row.bars if b.kind != cal.BarKind.DRAFT
                and b.first_col <= last and first <= b.last_col]

    def _update_selection(self) -> None:
        sel = self.selected_cells()
        if sel is None or self.view is None:
            self.new_btn.setEnabled(False)
            self.selection_label.setText(SELECT_HINT)
            theme.set_tone(self.selection_label, "muted")
            return
        asset_id, first, last = sel
        code = next(r.asset.asset_code for r in self.view.rows if r.asset.id == asset_id)
        d1, d2 = self.view.days[first], self.view.days[last]
        text = f"{code} · {d1.month}/{d1.day}" + (f" ~ {d2.month}/{d2.day}" if last > first else "")
        busy = self._busy_bars(asset_id, first, last)
        if busy:
            text += " — 겹치는 예약·정비가 있습니다. 시각이 겹치지 않으면 다음 단계에서 고를 수 있습니다."
        self.selection_label.setText(text)
        theme.set_tone(self.selection_label, "warning" if busy else None)
        self.new_btn.setEnabled(True)

    def new_from_selection(self) -> None:
        sel = self.selected_cells()
        if sel is None or self.view is None:
            feedback.warn(self, "장비 한 대의 날짜 칸을 먼저 끌어서 골라주세요.")
            return
        asset_id, first, last = sel
        self._open_wizard(asset_id, self.view.days[first], self.view.days[last])

    def _open_wizard(self, asset_id: int, first_day: date, last_day: date) -> None:
        try:
            start, due = self.ctx.calendar.new_reservation_period(first_day, last_day)
        except DomainError as exc:
            feedback.warn(self, exc.message)
            return
        self.nav.new_reservation(asset_ids=[asset_id], start_at=start, due_at=due)

    def _on_double_click(self, row: int, col: int) -> None:
        bar = self.bar_at(row, col)
        if bar is None:
            if self.view is not None and row < len(self._row_assets):
                day = self.view.days[col]
                self._open_wizard(self._row_assets[row], day, day)
            return
        if bar.order_id is not None:
            self.nav.open_order(bar.order_id)
        else:
            self.nav.goto("maintenance")
