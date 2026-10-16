"""앱 전체 디자인 테마: 색 토큰(라이트/다크) → Qt 스타일시트(QSS), 아이콘, 상태 배지.

디자인 방향은 'A · 클린 블루'(밝은 회색 바탕, 흰 카드, 둥근 모서리, 파란 강조색 1개).

규칙
- 화면 코드에 색 값(#xxxxxx)을 직접 쓰지 않는다. 역할만 지정한다.
    라벨 색:   set_tone(label, "muted" | "danger" | "warning" | "success")
    버튼 종류: set_variant(button, "primary" | "danger")
    안내 띠:   QFrame에 setProperty("banner", "warning" | "info")
    카드/제목: objectName "Card", "PageTitle", "StatCard" 등 (아래 QSS 참고)
- 색을 바꾸려면 `LIGHT` / `DARK` 토큰만 고친다. QSS는 `$토큰이름`으로 참조한다.
- 다크 모드는 OS 설정을 따르며 실행 중 바뀌면 다시 적용된다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from string import Template

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from rentdesk.domain.availability import ASSET_STATE_LABELS, AssetState
from rentdesk.domain.models import ORDER_STATUS_LABELS, OrderStatus
from rentdesk.platform.resources import get_resources_dir

FONT_FAMILIES = ["Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR"]


@dataclass(frozen=True)
class Tokens:
    name: str            # "light" | "dark" (화살표 아이콘 파일 선택)
    bg: str              # 창 바탕
    surface: str         # 카드·표·입력칸
    surface_alt: str     # 마우스 오버, 비활성 입력칸
    border: str
    divider: str         # 표 행 구분선
    text: str
    text_sub: str        # 보조 글자(메뉴, 카드 제목)
    text_muted: str      # 안내문, 표 머리글
    accent: str          # 주요 버튼 채움(흰 글자와 4.5:1 이상)
    accent_hover: str
    accent_text: str     # 선택된 메뉴·링크 글자
    accent_soft: str     # 선택 배경
    on_accent: str
    danger: str
    danger_soft: str
    warning: str
    warning_soft: str
    success: str
    success_soft: str
    purple: str
    purple_soft: str
    neutral: str
    neutral_soft: str


LIGHT = Tokens(
    name="light",
    bg="#F2F4F6", surface="#FFFFFF", surface_alt="#F9FAFB", border="#E5E8EB", divider="#F2F4F6",
    text="#191F28", text_sub="#4E5968", text_muted="#6B7684",
    accent="#1B64DA", accent_hover="#174EA6", accent_text="#1B64DA", accent_soft="#E8F3FF",
    on_accent="#FFFFFF",
    danger="#C62828", danger_soft="#FFEBEE", warning="#A6540A", warning_soft="#FFF4E5",
    success="#1E7B3A", success_soft="#E6F4EA", purple="#5B21B6", purple_soft="#EDE9FE",
    neutral="#4E5968", neutral_soft="#F2F4F6",
)

DARK = Tokens(
    name="dark",
    bg="#101114", surface="#1B1D22", surface_alt="#23262C", border="#2E3138", divider="#26292F",
    text="#ECEEF1", text_sub="#B0B8C1", text_muted="#8B95A1",
    accent="#2F6FDB", accent_hover="#3D7CE8", accent_text="#7FB0FF", accent_soft="#1E2B44",
    on_accent="#FFFFFF",
    danger="#FF7B7B", danger_soft="#3A1E21", warning="#F5B14C", warning_soft="#3A2C14",
    success="#5EDB8C", success_soft="#17301F", purple="#C3AEFF", purple_soft="#2B2340",
    neutral="#B0B8C1", neutral_soft="#2A2D33",
)

# 표의 상태 글자 → 배지 색 계열. 도메인 라벨을 그대로 키로 쓴다.
STATUS_TONES: dict[str, str] = {
    ORDER_STATUS_LABELS[OrderStatus.DRAFT]: "neutral",
    ORDER_STATUS_LABELS[OrderStatus.CONFIRMED]: "accent",
    ORDER_STATUS_LABELS[OrderStatus.IN_RENTAL]: "purple",
    ORDER_STATUS_LABELS[OrderStatus.COMPLETED]: "success",
    ORDER_STATUS_LABELS[OrderStatus.CANCELED]: "neutral",
    ASSET_STATE_LABELS[AssetState.AVAILABLE]: "success",
    ASSET_STATE_LABELS[AssetState.RENTED]: "purple",
    ASSET_STATE_LABELS[AssetState.OVERDUE]: "danger",
    ASSET_STATE_LABELS[AssetState.MAINTENANCE]: "warning",
    ASSET_STATE_LABELS[AssetState.RETIRED]: "neutral",
    "운영 중": "success",
    "연체": "danger",
    "대기": "accent",
    "처리 완료": "success",
    "미정산": "warning",
    "출고 전": "accent",
    "완료": "success",
    "예정": "accent",
    "진행 중": "warning",
    "종료": "neutral",
}

# Lucide 아이콘(ISC 라이선스) 경로 데이터. 24×24, 선 아이콘.
ICON_PATHS: dict[str, str] = {
    "brand": "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6"
             "a2 2 0 0 1 2-2zM9 15l2 2 4-4",
    "dashboard": "M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z",
    "assets": "M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3"
              "l-2.5-3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    "customers": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"
                 "M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
    "reservations": "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6"
                    "a2 2 0 0 1 2-2z",
    "calendar": "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6"
                "a2 2 0 0 1 2-2zM7 14h6M11 18h6",
    "rentals": "M8 3L4 7l4 4M4 7h16M16 21l4-4-4-4M20 17H4",
    "settlements": "M5 2h14v20l-3-2-2 2-2-2-2 2-2-2-3 2zM9 7h6M9 11h6M9 15h4",
    "maintenance": "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94"
                   "l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z",
    "reports": "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 18v-4"
               "M12 18v-6M16 18v-2",
    "settings": "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
    "search": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35",
    "plus": "M12 5v14M5 12h14",
}

QSS = Template("""
QWidget { color: $text; font-size: 14px; }
QMainWindow, #Content { background: $bg; }
QDialog { background: $surface; }

/* 좌측 메뉴 */
#Sidebar { background: $surface; border: none; border-right: 1px solid $border; }
#BrandMark { background: $accent; border-radius: 10px; }
#BrandName { font-size: 16px; font-weight: 700; }
QListWidget#SideNav { background: transparent; border: none; outline: 0; }
#SideNav::item { min-height: 44px; padding: 0 10px; border-radius: 10px; color: $text_sub; }
#SideNav::item:hover { background: $surface_alt; }
#SideNav::item:selected { background: $accent_soft; color: $accent_text; }
#SidebarFooter { background: $surface_alt; border-radius: 12px; }

/* 제목·글자 역할 */
#PageTitle { font-size: 24px; font-weight: 700; }
#SectionTitle { font-size: 16px; font-weight: 700; }
QLabel[tone="muted"] { color: $text_muted; }
QLabel[tone="danger"] { color: $danger; }
QLabel[tone="warning"] { color: $warning; }
QLabel[tone="success"] { color: $success; }

/* 안내 띠 */
QFrame[banner="warning"] { background: $warning_soft; border-radius: 10px; }
QFrame[banner="warning"] QLabel { color: $warning; font-weight: 600; }
QFrame[banner="info"] { background: $accent_soft; border-radius: 10px; }
QFrame[banner="info"] QLabel { color: $accent_text; font-weight: 600; }

/* 범례 칩(예약 달력): 상태 색 계열별 알약 모양 */
QLabel[chip] {
    border-radius: 9px; padding: 1px 8px; font-size: 12px; font-weight: 600;
    border: 1px solid transparent;
}
QLabel[chip="neutral"] { background: $neutral_soft; color: $neutral; border: 1px dashed $neutral; }
QLabel[chip="accent"] { background: $accent_soft; color: $accent_text; }
QLabel[chip="purple"] { background: $purple_soft; color: $purple; }
QLabel[chip="danger"] { background: $danger_soft; color: $danger; }
QLabel[chip="success"] { background: $success_soft; color: $success; }
QLabel[chip="warning"] { background: $warning_soft; color: $warning; }

/* 카드 */
#Card, #StatCard { background: $surface; border: 1px solid $surface; border-radius: 16px; }
#StatCard:hover { border-color: $border; }
#StatTitle { color: $text_sub; font-weight: 500; }
#StatValue { font-size: 28px; font-weight: 700; }
#StatValue[tone="danger"] { color: $danger; }
#StatValue[tone="warning"] { color: $warning; }

/* 버튼 */
QPushButton {
    background: $surface; color: $text; border: 1px solid $border; border-radius: 10px;
    padding: 7px 16px; font-weight: 600; min-height: 20px;
}
QPushButton:hover { background: $surface_alt; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $text_muted; background: $surface_alt; }
QPushButton[variant="primary"] { background: $accent; border-color: $accent; color: $on_accent; }
QPushButton[variant="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="danger"] { color: $danger; }
QPushButton[variant="danger"]:hover { background: $danger_soft; }
QPushButton[variant="primary"]:disabled, QPushButton[variant="danger"]:disabled {
    background: $surface_alt; border-color: $border; color: $text_muted;
}

/* 입력칸 */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTextEdit, QPlainTextEdit {
    background: $surface; border: 1px solid $border; border-radius: 10px; padding: 6px 10px;
    selection-background-color: $accent_soft; selection-color: $text;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QDateTimeEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled,
QDateTimeEdit:disabled { background: $surface_alt; color: $text_muted; }
QLineEdit#SearchBox { border-radius: 12px; padding: 9px 12px; }
QComboBox::drop-down, QDateEdit::drop-down, QDateTimeEdit::drop-down { border: none; width: 26px; }
QComboBox::down-arrow, QDateEdit::down-arrow, QDateTimeEdit::down-arrow {
    image: url("$chevron_down"); width: 12px; height: 12px;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border; width: 22px; border: none; background: transparent;
}
QAbstractSpinBox::up-button { subcontrol-position: top right; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; }
QAbstractSpinBox::up-arrow { image: url("$chevron_up"); width: 10px; height: 10px; }
QAbstractSpinBox::down-arrow { image: url("$chevron_down"); width: 10px; height: 10px; }
QComboBox QAbstractItemView {
    background: $surface; border: 1px solid $border; selection-background-color: $accent_soft;
    selection-color: $text; outline: 0;
}

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* 표 */
QTableView, QListView {
    background: $surface; alternate-background-color: $surface_alt; border: 1px solid $border;
    border-radius: 12px; gridline-color: $divider; selection-background-color: $accent_soft;
    selection-color: $text; outline: 0;
}
QTableView::item { padding: 0 8px; border-bottom: 1px solid $divider; }
QTableView::item:selected { background: $accent_soft; color: $text; }
QListView::item { min-height: 32px; padding: 0 8px; }
QListView::item:selected { background: $accent_soft; color: $text; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: $surface; color: $text_muted; font-size: 13px; font-weight: 600;
    border: none; border-bottom: 1px solid $border; padding: 8px;
}
QTableCornerButton::section { background: $surface; border: none; }

/* 탭: 알약 모양 */
QTabWidget::pane { border: 1px solid $border; border-radius: 12px; background: $surface; top: 8px; }
QTabBar::tab {
    background: transparent; color: $text_sub; padding: 7px 14px; margin-right: 4px;
    border-radius: 8px; font-weight: 600;
}
QTabBar::tab:hover { background: $surface; }
QTabBar::tab:selected { background: $accent_soft; color: $accent_text; }

/* 묶음 상자 */
QGroupBox {
    background: $surface; border: 1px solid $border; border-radius: 12px;
    margin-top: 26px; padding: 14px 12px 12px;
}
QGroupBox::title { subcontrol-origin: margin; left: 4px; padding: 0 4px 6px; color: $text; }

/* 스크롤바 */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle { background: $border; border-radius: 3px; min-height: 30px; min-width: 30px; }
QScrollBar::handle:hover { background: $text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QToolTip { background: $text; color: $surface; border: none; padding: 6px 8px; }
QProgressBar { background: $surface_alt; border: none; border-radius: 6px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 6px; }
""")

_current: Tokens = LIGHT


def current() -> Tokens:
    return _current


def color(name: str) -> QColor:
    return QColor(getattr(_current, name))


def build_stylesheet(tokens: Tokens) -> str:
    icons = get_resources_dir() / "icons"
    return QSS.substitute(
        asdict(tokens),
        chevron_down=(icons / f"chevron_down_{tokens.name}.svg").as_posix(),
        chevron_up=(icons / f"chevron_up_{tokens.name}.svg").as_posix(),
    )


def _font_families() -> list[str]:
    """설치된 글꼴만 넘긴다(없는 이름은 Qt가 매번 대체 글꼴을 찾느라 느려지고 경고를 남긴다)."""
    installed = set(QFontDatabase.families())
    return [f for f in FONT_FAMILIES if f in installed] or FONT_FAMILIES[-1:]


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    return hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark


def _palette(t: Tokens) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.bg, QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.surface, QPalette.ColorRole.AlternateBase: t.surface_alt,
        QPalette.ColorRole.Text: t.text, QPalette.ColorRole.Button: t.surface,
        QPalette.ColorRole.ButtonText: t.text, QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.on_accent, QPalette.ColorRole.ToolTipBase: t.text,
        QPalette.ColorRole.ToolTipText: t.surface, QPalette.ColorRole.PlaceholderText: t.text_muted,
        QPalette.ColorRole.Link: t.accent_text, QPalette.ColorRole.Mid: t.border,
        QPalette.ColorRole.Midlight: t.divider, QPalette.ColorRole.Light: t.surface,
        QPalette.ColorRole.Dark: t.border,
    }
    for role, value in roles.items():
        p.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.text_muted))
    return p


def apply_theme(app: QApplication, *, dark: bool | None = None, follow_system: bool = True) -> Tokens:
    """Fusion 스타일 + 팔레트 + QSS를 적용한다. 맥/윈도우에서 같은 모양이 되도록 Fusion으로 통일."""
    global _current
    _current = DARK if (system_is_dark() if dark is None else dark) else LIGHT
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(_font_families())
    font.setPixelSize(14)
    app.setFont(font)
    app.setPalette(_palette(_current))
    app.setStyleSheet(build_stylesheet(_current))
    if follow_system and dark is None and not app.property("rentdesk_theme_hooked"):
        app.setProperty("rentdesk_theme_hooked", True)
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(lambda _scheme: apply_theme(app, follow_system=False))
    return _current


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_tone(widget: QWidget, tone: str | None) -> None:
    """글자 색 역할 지정: muted / danger / warning / success / None(기본)."""
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        repolish(widget)


def set_chip(label: QWidget, tone: str) -> QWidget:
    """알약 모양 범례 칩: neutral(점선) / accent / purple / danger / success / warning."""
    label.setProperty("chip", tone)
    repolish(label)
    return label


def set_variant(button: QWidget, variant: str) -> QWidget:
    """버튼 종류: primary(주요 동작, 화면당 1~2개) / danger(되돌리기 어려운 동작)."""
    button.setProperty("variant", variant)
    repolish(button)
    return button


def icon_pixmap(name: str, tone: str = "text_sub", size: int = 20, ratio: float = 2.0,
                *, stroke: str | None = None) -> QPixmap:
    hex_color = stroke or getattr(_current, tone)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{hex_color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{ICON_PATHS[name]}"/></svg>'
    )
    px = QPixmap(round(size * ratio), round(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name: str, tone: str = "text_sub", *, selected_tone: str | None = None,
         size: int = 20) -> QIcon:
    result = QIcon()
    result.addPixmap(icon_pixmap(name, tone, size), QIcon.Mode.Normal)
    if selected_tone:
        result.addPixmap(icon_pixmap(name, selected_tone, size), QIcon.Mode.Selected)
    return result


def badge_colors(text: str) -> tuple[QColor, QColor] | None:
    """상태 글자의 (배경, 글자) 색. 모르는 글자면 None."""
    tone = STATUS_TONES.get(text)
    if tone is None:
        return None
    fg = _current.accent_text if tone == "accent" else getattr(_current, tone)
    return QColor(getattr(_current, f"{tone}_soft")), QColor(fg)


class RowHighlightDelegate(QStyledItemDelegate):
    """칸의 BackgroundRole(연체 행 강조 등)을 먼저 칠한다.

    표에 QSS `::item` 규칙이 있으면 Qt 스타일이 칸 배경색을 무시하므로 여기서 직접 그린다.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        brush = index.data(Qt.ItemDataRole.BackgroundRole)
        if brush is not None:
            painter.fillRect(option.rect, brush)
        super().paint(painter, option, index)


class StatusBadgeDelegate(RowHighlightDelegate):
    """표 칸의 상태 글자(예약확정·대여중·연체 등)를 알약 모양 배지로 그린다."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        text = index.data() or ""
        colors = badge_colors(str(text))
        if colors is None:
            super().paint(painter, option, index)
            return
        brush = index.data(Qt.ItemDataRole.BackgroundRole)
        if brush is not None:
            painter.fillRect(option.rect, brush)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        bg, fg = colors
        font = QFont(opt.font)
        font.setPixelSize(12)
        font.setBold(True)
        width = QFontMetrics(font).horizontalAdvance(text) + 20
        height = 22
        rect = QRectF(opt.rect.left() + 8, opt.rect.center().y() - height / 2 + 0.5, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setFont(font)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802 - Qt API
        size = super().sizeHint(option, index)
        size.setWidth(size.width() + 24)
        return size
