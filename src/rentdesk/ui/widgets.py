"""공통 위젯: 서울 시각 입력, 원 단위 금액 입력, 읽기 전용 표, 필드 옆 오류 표시.

화면 표시 형식은 `domain.display`를 사용하고, 업무 계산은 하지 않는다.
색·모양은 `ui.theme`이 담당한다(여기에 색 값을 쓰지 않는다).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDateTimeEdit,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.errors import DomainError, ValidationError
from rentdesk.domain.timeutil import SEOUL, floor_minute, to_seoul
from rentdesk.ui import theme

MAX_WON = 2_000_000_000  # QSpinBox 상한(약 20억 원). 소규모 렌탈 단일 금액에 충분.
ROW_HEIGHT = 44


class SeoulDateTimeEdit(QDateTimeEdit):
    """분 단위 서울 시각 입력. PC 시간대와 무관하게 '서울 벽시계 시각'으로 읽고 쓴다."""

    def __init__(self, value: datetime | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # PC 시간대의 일광절약 간극에 영향받지 않도록 UTC 표기로 '벽시계 값'만 보관한다.
        self.setTimeSpec(Qt.TimeSpec.UTC)
        self.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.setCalendarPopup(True)
        if value is not None:
            self.set_value(value)

    def set_value(self, value: datetime) -> None:
        local = to_seoul(value)
        self.setDateTime(QDateTime(
            QDate(local.year, local.month, local.day), QTime(local.hour, local.minute),
            Qt.TimeSpec.UTC,
        ))

    def value(self) -> datetime:
        qd = self.dateTime().date()
        qt = self.dateTime().time()
        return datetime(qd.year(), qd.month(), qd.day(), qt.hour(), qt.minute(), tzinfo=SEOUL)


class DateEdit(QDateEdit):
    def __init__(self, value: date | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setCalendarPopup(True)
        if value is not None:
            self.set_value(value)

    def set_value(self, value: date) -> None:
        self.setDate(QDate(value.year, value.month, value.day))

    def value(self) -> date:
        d = self.date()
        return date(d.year(), d.month(), d.day())


class WonSpinBox(QSpinBox):
    """0 이상 정수 원 입력(음수 입력 불가)."""

    def __init__(self, value: int = 0, parent: QWidget | None = None, *, step: int = 1000) -> None:
        super().__init__(parent)
        self.setRange(0, MAX_WON)
        self.setSingleStep(step)
        self.setSuffix("원")
        self.setGroupSeparatorShown(True)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setValue(value)


def now_seoul_minute(now: datetime) -> datetime:
    return floor_minute(to_seoul(now))


def next_hour(now: datetime) -> datetime:
    local = to_seoul(now)
    return local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


class DataTable(QTableWidget):
    """읽기 전용 행 선택 표. 각 행에 key(엔티티 ID 등)를 저장한다."""

    activated_key = Signal(object)

    def __init__(self, headers: Sequence[str], parent: QWidget | None = None, *,
                 badge_columns: Sequence[int] = ()) -> None:
        """badge_columns: 상태 글자를 배지로 그릴 열 번호(`theme.STATUS_TONES`에 있는 글자만)."""
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(list(headers))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        self._badge_delegate = theme.StatusBadgeDelegate(self)
        for col in badge_columns:
            self.setItemDelegateForColumn(col, self._badge_delegate)
        self._keys: list[object] = []
        self.cellDoubleClicked.connect(self._on_double_click)

    def set_rows(
        self,
        rows: Sequence[tuple[object, Sequence[object]]],
        *,
        highlight: Callable[[object], bool] | None = None,
        align_right: Sequence[int] = (),
    ) -> None:
        """rows: (key, 셀 값들). 값은 str로 표시한다."""
        self.setRowCount(0)
        self._keys = []
        self.setRowCount(len(rows))
        for r, (key, values) in enumerate(rows):
            self._keys.append(key)
            mark = highlight(key) if highlight else False
            for c, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                if c in align_right:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if mark:
                    item.setForeground(theme.color("danger"))
                self.setItem(r, c, item)
        self.resizeColumnsToContents()
        header = self.horizontalHeader()
        if header.stretchLastSection():
            # 내용 맞춤 후 마지막 열이 다시 남은 폭을 채우도록(화면 전환 후 표가 덜 차는 문제)
            header.setStretchLastSection(False)
            header.setStretchLastSection(True)

    def key_at(self, row: int) -> object | None:
        return self._keys[row] if 0 <= row < len(self._keys) else None

    def selected_key(self) -> object | None:
        rows = self.selectionModel().selectedRows()
        return self.key_at(rows[0].row()) if rows else None

    def keys(self) -> list[object]:
        return list(self._keys)

    def select_key(self, key: object) -> None:
        if key in self._keys:
            self.selectRow(self._keys.index(key))

    def _on_double_click(self, row: int, _col: int) -> None:
        key = self.key_at(row)
        if key is not None:
            self.activated_key.emit(key)


class FormErrors:
    """QFormLayout 필드 아래에 한국어 오류를 표시 (PRD 7.5 '필드 옆 한국어 메시지')."""

    def __init__(self, form: QFormLayout) -> None:
        self._form = form
        self._labels: dict[str, QLabel] = {}

    def add_row(self, label: str, widget: QWidget, *fields: str) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(widget)
        err = QLabel()
        theme.set_tone(err, "danger")
        err.setWordWrap(True)
        err.hide()
        lay.addWidget(err)
        self._form.addRow(label, box)
        for f in fields:
            self._labels[f] = err
        return box

    def clear(self) -> None:
        for lab in self._labels.values():
            lab.clear()
            lab.hide()

    def show(self, exc: DomainError) -> bool:
        """필드 오류면 해당 위치에 표시하고 True. 아니면 False(호출자가 메시지 상자 표시)."""
        self.clear()
        if isinstance(exc, ValidationError) and exc.field in self._labels:
            lab = self._labels[exc.field]
            lab.setText(exc.message)
            lab.show()
            return True
        return False


class StatCard(QFrame):
    """대시보드 요약 카드. 클릭 시 clicked 신호."""

    clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("StatTitle")
        self.value = QLabel("0")
        self.value.setObjectName("StatValue")
        lay.addWidget(self.title)
        lay.addWidget(self.value)

    def set_value(self, value: int, *, alert: bool | str = False) -> None:
        """alert: True면 0보다 클 때 빨강, "warning"이면 주황."""
        self.value.setText(str(value))
        tone = None
        if alert and value:
            tone = alert if isinstance(alert, str) else "danger"
        theme.set_tone(self.value, tone)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.clicked.emit()
        super().mouseReleaseEvent(event)


def card(*, margins: tuple[int, int, int, int] = (20, 16, 20, 16)) -> tuple[QFrame, QVBoxLayout]:
    """흰 카드(둥근 모서리) 상자와 그 안의 세로 레이아웃."""
    box = QFrame()
    box.setObjectName("Card")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(*margins)
    lay.setSpacing(10)
    return box, lay


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    theme.set_tone(label, "muted")
    return label


def button_row(*buttons: QPushButton, stretch_first: bool = True) -> QHBoxLayout:
    lay = QHBoxLayout()
    if stretch_first:
        lay.addStretch(1)
    for b in buttons:
        lay.addWidget(b)
    return lay


def empty_hint(text: str, action_label: str | None = None,
               action: Callable[[], None] | None = None) -> QWidget:
    """빈 목록 안내 + 다음 행동 버튼 (PRD 5 '비어 있는 목록에는 다음 행동 버튼')."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 4, 0, 4)
    label = QLabel(text)
    theme.set_tone(label, "muted")
    lay.addWidget(label)
    if action_label and action:
        btn = QPushButton(action_label)
        btn.clicked.connect(action)
        lay.addWidget(btn)
    lay.addStretch(1)
    return box
