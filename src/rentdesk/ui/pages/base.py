"""업무 화면 공통 기반."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtWidgets import QWidget

from rentdesk.context import AppContext


class Navigator(Protocol):
    def open_order(self, order_id: int) -> None: ...
    def open_asset(self, asset_id: int) -> None: ...
    def open_customer(self, customer_id: int) -> None: ...
    def new_reservation(self, customer_id: int | None = None) -> None: ...
    def goto(self, page: str, **filters: object) -> None: ...
    def data_changed(self) -> None: ...


class Page(QWidget):
    """각 화면은 `refresh()`로 서비스에서 다시 읽어 그린다(화면에 SQL·계산 없음)."""

    title = ""

    def __init__(self, ctx: AppContext, nav: Navigator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.nav = nav

    def refresh(self) -> None:  # pragma: no cover - 하위 클래스 구현
        raise NotImplementedError

    def apply_filters(self, **filters: object) -> None:
        """다른 화면에서 필터를 지정해 이동할 때(대시보드 카드 클릭 등)."""
