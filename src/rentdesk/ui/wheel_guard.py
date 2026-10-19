"""마우스 휠로 값이 실수로 바뀌지 않게 한다.

숫자·날짜·선택 칸(스핀박스·날짜 입력·콤보박스)은 클릭이나 Tab으로 선택(포커스)했을 때만 휠로 값을 바꾼다.
선택하지 않은 칸 위에서 휠을 굴리면 값은 그대로 두고 화면(스크롤 영역)이 스크롤된다.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox

_GUARDED = (QAbstractSpinBox, QComboBox)


class WheelGuard(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype == QEvent.Type.Polish and isinstance(obj, _GUARDED):
            # 기본값(WheelFocus)은 휠만으로도 포커스를 가져가므로 클릭·Tab으로만 선택되게 한다
            obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        elif etype == QEvent.Type.Wheel and isinstance(obj, _GUARDED) and not obj.hasFocus():
            event.ignore()  # 처리하지 않고 부모(스크롤 영역)로 넘긴다
            return True
        return False


def install_wheel_guard(app: QApplication) -> None:
    """앱 전체에 한 번만 설치한다(여러 번 불러도 안전)."""
    if app.property("rentdesk_wheel_guard"):
        return
    app.setProperty("rentdesk_wheel_guard", True)
    app.installEventFilter(WheelGuard(app))
