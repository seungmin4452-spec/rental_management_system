"""사용자 알림·확인 대화상자 (PRD 5 '성공/실패 이유를 알림으로 제시', 7.5 '스택트레이스 비노출').

모든 화면은 이 모듈을 통해서만 메시지 상자를 띄운다. 자동 테스트는 `set_handler()`로
대화상자를 띄우지 않는 처리기를 주입한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, TypeVar

from PySide6.QtWidgets import QMessageBox, QWidget

from rentdesk.domain.errors import DomainError

log = logging.getLogger("rentdesk.ui")

UNEXPECTED_MESSAGE = (
    "예기치 못한 오류로 작업을 완료하지 못했습니다. 저장된 데이터는 변경되지 않았습니다.\n"
    "같은 문제가 반복되면 프로그램을 다시 시작해주세요."
)

T = TypeVar("T")


class Handler(Protocol):
    def info(self, parent: QWidget | None, title: str, text: str) -> None: ...
    def warn(self, parent: QWidget | None, title: str, text: str) -> None: ...
    def confirm(self, parent: QWidget | None, title: str, text: str) -> bool: ...


class QtHandler:
    def info(self, parent: QWidget | None, title: str, text: str) -> None:
        QMessageBox.information(parent, title, text)

    def warn(self, parent: QWidget | None, title: str, text: str) -> None:
        QMessageBox.warning(parent, title, text)

    def confirm(self, parent: QWidget | None, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
        yes = box.addButton("확인", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes


_handler: Handler = QtHandler()


def set_handler(handler: Handler) -> Handler:
    global _handler
    previous, _handler = _handler, handler
    return previous


def info(parent: QWidget | None, text: str, title: str = "완료") -> None:
    _handler.info(parent, title, text)


def warn(parent: QWidget | None, text: str, title: str = "확인 필요") -> None:
    _handler.warn(parent, title, text)


def confirm(parent: QWidget | None, text: str, title: str = "확인") -> bool:
    return _handler.confirm(parent, title, text)


def run(
    parent: QWidget | None,
    action: Callable[[], T],
    *,
    on_domain_error: Callable[[DomainError], bool] | None = None,
) -> T | None:
    """서비스 호출을 감싸 도메인 오류는 한국어 메시지로, 그 외 오류는 일반 안내로 보여준다.

    `on_domain_error`가 True를 반환하면(필드 옆 표시 등) 메시지 상자를 생략한다.
    실패 시 None을 반환한다.
    """
    try:
        return action()
    except DomainError as exc:
        if on_domain_error is None or not on_domain_error(exc):
            warn(parent, exc.message, "처리할 수 없습니다")
        return None
    except Exception:  # noqa: BLE001 - 사용자에게 스택트레이스를 보이지 않기 위함
        log.exception("unexpected UI action failure")
        warn(parent, UNEXPECTED_MESSAGE, "오류")
        return None
