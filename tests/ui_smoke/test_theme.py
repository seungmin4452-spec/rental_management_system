"""디자인 테마: 토큰→QSS 변환, 상태 배지 매핑, 대비, 화면 코드에 색 하드코딩 금지."""

import re
from dataclasses import asdict
from pathlib import Path

import pytest

from rentdesk.domain.availability import ASSET_STATE_LABELS
from rentdesk.domain.models import ORDER_STATUS_LABELS
from rentdesk.ui import theme

UI_DIR = Path(theme.__file__).resolve().parent


def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("tokens", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_stylesheet_has_no_unresolved_tokens_and_arrow_icons_exist(tokens):
    qss = theme.build_stylesheet(tokens)
    assert "$" not in qss
    for path in re.findall(r'url\("([^"]+)"\)', qss):
        assert Path(path).is_file(), path


@pytest.mark.parametrize("tokens", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_text_contrast_meets_wcag_aa(tokens):
    assert contrast(tokens.on_accent, tokens.accent) >= 4.5  # 주요 버튼 글자
    for fg in (tokens.text, tokens.text_sub, tokens.text_muted, tokens.danger, tokens.warning,
               tokens.success, tokens.accent_text):
        assert contrast(fg, tokens.surface) >= 4.5, fg
    for tone in ("accent", "danger", "warning", "success", "purple", "neutral"):
        fg = tokens.accent_text if tone == "accent" else getattr(tokens, tone)
        assert contrast(fg, getattr(tokens, f"{tone}_soft")) >= 4.5, tone  # 상태 배지


def test_light_and_dark_define_the_same_tokens():
    assert asdict(theme.LIGHT).keys() == asdict(theme.DARK).keys()


def test_every_order_and_asset_state_has_a_badge_tone():
    for label in [*ORDER_STATUS_LABELS.values(), *ASSET_STATE_LABELS.values()]:
        assert label in theme.STATUS_TONES, label


def test_ui_code_has_no_hardcoded_colors_or_inline_styles():
    offenders = []
    for path in UI_DIR.rglob("*.py"):
        if path.name == "theme.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "setStyleSheet(" in text or re.search(r'"#[0-9a-fA-F]{3,8}\b', text):
            offenders.append(path.name)
    assert not offenders


def test_apply_theme_switches_palette_and_icons_render(qapp):
    dark = theme.apply_theme(qapp, dark=True)
    assert dark is theme.DARK and theme.current() is theme.DARK
    assert qapp.palette().window().color().name().upper() == theme.DARK.bg
    assert not theme.icon_pixmap("dashboard").toImage().isNull()
    light = theme.apply_theme(qapp, dark=False)
    assert qapp.palette().window().color().name().upper() == light.bg


def test_set_tone_and_variant_set_dynamic_properties(qapp):
    from PySide6.QtWidgets import QLabel, QPushButton

    label = QLabel("x")
    theme.set_tone(label, "danger")
    assert label.property("tone") == "danger"
    button = theme.set_variant(QPushButton("저장"), "primary")
    assert button.property("variant") == "primary"


def test_badge_colors_use_readable_text_color(qapp):
    theme.apply_theme(qapp, dark=True)
    try:
        bg, fg = theme.badge_colors(ORDER_STATUS_LABELS[next(iter(ORDER_STATUS_LABELS))])
        assert contrast(fg.name(), bg.name()) >= 4.5
        _bg, fg = theme.badge_colors("대기")
        assert fg.name().upper() == theme.DARK.accent_text
        assert theme.badge_colors("알 수 없음") is None
    finally:
        theme.apply_theme(qapp, dark=False)
