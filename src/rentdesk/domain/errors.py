"""사용자에게 보여줄 수 있는 도메인 오류.

모든 메시지는 한국어이며 스택트레이스 없이 UI에 그대로 표시할 수 있어야 한다.
고객 연락처 등 개인정보를 메시지에 넣지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


class DomainError(Exception):
    """사용자 표시용 오류의 기반 클래스."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(DomainError):
    """입력값 검증 실패. `field`는 화면에서 오류를 표시할 필드명."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class NotFoundError(DomainError):
    pass


class DuplicateError(DomainError):
    pass


class InvalidStateError(DomainError):
    """현재 상태에서 허용되지 않는 동작(상태 전이 위반 등)."""


@dataclass(frozen=True)
class ConflictInfo:
    """가용성 충돌 1건. order_id/order_no는 정비 블록이면 None."""

    asset_id: int
    asset_code: str
    kind: str  # availability.OccupancyKind 값
    start_at: datetime
    end_at: datetime | None  # None = 무기한
    order_id: int | None = None
    order_no: str | None = None
    block_id: int | None = None


class ConflictError(DomainError):
    """예약 확정 시 가용성 충돌."""

    def __init__(self, message: str, conflicts: Sequence[ConflictInfo]) -> None:
        super().__init__(message)
        self.conflicts = tuple(conflicts)
