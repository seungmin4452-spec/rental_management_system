"""화면 배율: PC 화면 크기에 맞춰 앱 전체를 줄이거나 사용자가 고른 배율로 키운다.

Qt는 Windows/macOS의 배율(125%, 150% 등)을 이미 반영한다. 그런데 14인치 FHD 노트북
기본 배율 150%처럼 논리 화면이 작으면(작업 영역 약 1280×672) 메인 창 최소 크기가 들어가지
않는다. 그래서 시작할 때 작업 영역을 재고, 모자라면 `QT_SCALE_FACTOR`로 앱 전체를 줄인다.

`QT_SCALE_FACTOR`는 QApplication을 만들기 전에만 적용되므로, 측정용 QApplication을
만들었다가 지우고 배율을 정한 뒤 다시 만든다. 배율 선택값은 기기마다 다르므로 DB가 아니라
기기별 설정(`LocalPreferences`, 키 `ui_scale`)에 둔다.
"""

from __future__ import annotations

import math
import os

PREF_KEY = "ui_scale"
AUTO = "auto"
# 설정 화면 선택지(퍼센트). 자동은 화면에 맞춰 줄이기만 하고 키우지는 않는다.
MANUAL_CHOICES = (80, 90, 100, 110, 125, 150)
MIN_FACTOR = 0.7

# 메인 창 최소 크기(main_window.MIN_WIDTH/HEIGHT)에 창 테두리·제목 표시줄 여유를 더한 값
REQUIRED_WIDTH = 1100 + 20
REQUIRED_HEIGHT = 700 + 45


def auto_factor(avail_width: int, avail_height: int) -> float:
    """작업 영역(논리 픽셀)에 메인 창이 들어가도록 하는 배율. 0.05 단위로 내림, 최대 1.0."""
    if avail_width <= 0 or avail_height <= 0:
        return 1.0
    need = min(avail_width / REQUIRED_WIDTH, avail_height / REQUIRED_HEIGHT)
    if need >= 1.0:
        return 1.0
    return max(MIN_FACTOR, math.floor(need * 20) / 20)


def normalize_choice(value: object) -> str | int:
    """저장된 선택값을 검사한다. 모르는 값이면 자동."""
    if isinstance(value, int) and not isinstance(value, bool) and value in MANUAL_CHOICES:
        return value
    return AUTO


def resolve_factor(choice: object, avail_width: int, avail_height: int) -> float:
    c = normalize_choice(choice)
    if c == AUTO:
        return auto_factor(avail_width, avail_height)
    return c / 100


def choice_label(choice: str | int) -> str:
    return "자동(화면에 맞춤)" if choice == AUTO else f"{choice}%"


def create_application(args: list[str], read_choice):
    """배율을 적용한 QApplication을 만든다.

    read_choice: QApplication이 있는 상태에서 호출되어 저장된 선택값을 돌려주는 함수
    (기기별 설정 경로는 Qt 앱 식별 정보가 정해진 뒤에야 알 수 있다).
    사용자가 이미 `QT_SCALE_FACTOR` 환경변수를 지정했으면 그대로 따른다.
    """
    import shiboken6
    from PySide6.QtWidgets import QApplication

    app = QApplication(args)
    if os.environ.get("QT_SCALE_FACTOR"):
        app.setProperty("rentdesk_scale_factor", float(os.environ["QT_SCALE_FACTOR"] or 1))
        return app
    try:
        choice = read_choice()
    except Exception:  # noqa: BLE001 - 설정을 못 읽어도 앱은 떠야 한다
        choice = AUTO
    screen = app.primaryScreen()
    avail = screen.availableGeometry() if screen is not None else None
    factor = resolve_factor(choice, avail.width() if avail else 0, avail.height() if avail else 0)
    if abs(factor - 1.0) < 1e-9:
        app.setProperty("rentdesk_scale_factor", 1.0)
        return app
    shiboken6.delete(app)
    del app
    os.environ["QT_SCALE_FACTOR"] = f"{factor:g}"
    app = QApplication(args)
    app.setProperty("rentdesk_scale_factor", factor)
    return app


def current_factor() -> float:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    value = app.property("rentdesk_scale_factor") if app is not None else None
    return float(value) if value else 1.0
