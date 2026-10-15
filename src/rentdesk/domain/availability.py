"""가용성 판단 순수 함수 (PRD 7.1).

모든 구간은 시작 포함, 끝 제외 `[start, end)`. `end=None`은 미래 무한대.
DB나 현재 시각 전역에 의존하지 않으며, `now`는 호출자가 주입한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import ItemStatus, OrderStatus
from rentdesk.domain.timeutil import ensure_aware, floor_minute


class OccupancyKind(StrEnum):
    RESERVATION = "RESERVATION"  # 확정/대여 중 품목의 약정 기간 + 버퍼
    RETURNED = "RETURNED"  # 반납 완료 품목: 실제 반납 시각 + 버퍼까지
    OVERDUE = "OVERDUE"  # 약정 반납 시각이 지난 미반납 품목: 무기한
    MAINTENANCE = "MAINTENANCE"  # 정비/사용 불가 블록


def overlaps(
    a_start: datetime,
    a_end: datetime | None,
    b_start: datetime,
    b_end: datetime | None,
) -> bool:
    """`(A_start < B_end) and (B_start < A_end)`; None 끝은 무한대."""
    a_before_b_end = b_end is None or a_start < b_end
    b_before_a_end = a_end is None or b_start < a_end
    return a_before_b_end and b_before_a_end


def reservation_interval(
    start_at: datetime, due_at: datetime, buffer_minutes: int
) -> tuple[datetime, datetime]:
    """예약이 점유하려는 구간 `[start, due + buffer)`."""
    return start_at, due_at + timedelta(minutes=buffer_minutes)


@dataclass(frozen=True)
class ItemOccupancyInput:
    """기존 예약 품목 1건의 가용성 계산 입력."""

    order_status: OrderStatus
    item_status: ItemStatus
    start_at: datetime
    due_at: datetime
    actual_return_at: datetime | None
    buffer_minutes: int


def item_occupied_interval(
    item: ItemOccupancyInput, now: datetime
) -> tuple[OccupancyKind, datetime, datetime | None] | None:
    """기존 품목이 점유하는 구간. 점유하지 않으면 None.

    - DRAFT/CANCELED 주문 또는 CANCELED 품목: 점유 없음.
    - RETURNED: `[start, 실제 반납 + 버퍼)` (주문이 IN_RENTAL/COMPLETED여도 동일).
    - OUT이고 now > 약정 반납: 연체 미반납 → `[start, ∞)`.
    - CONFIRMED의 RESERVED, IN_RENTAL의 OUT: `[start, due + 버퍼)`.
    """
    buffer = timedelta(minutes=item.buffer_minutes)
    if item.order_status in (OrderStatus.DRAFT, OrderStatus.CANCELED):
        return None
    if item.item_status == ItemStatus.CANCELED:
        return None
    if item.item_status == ItemStatus.RETURNED:
        if item.actual_return_at is None:
            raise ValueError("RETURNED 품목에는 실제 반납 시각이 필요합니다.")
        return OccupancyKind.RETURNED, item.start_at, item.actual_return_at + buffer
    if item.item_status == ItemStatus.OUT and now > item.due_at:
        return OccupancyKind.OVERDUE, item.start_at, None
    if item.item_status in (ItemStatus.RESERVED, ItemStatus.OUT):
        return OccupancyKind.RESERVATION, item.start_at, item.due_at + buffer
    return None


def is_overdue(item_status: ItemStatus, due_at: datetime, now: datetime) -> bool:
    """OUT 품목만 연체 판단 (PRD 6.2)."""
    return item_status == ItemStatus.OUT and now > due_at


@dataclass(frozen=True)
class Occupancy:
    """특정 장비의 점유 구간 1건."""

    asset_id: int
    kind: OccupancyKind
    start_at: datetime
    end_at: datetime | None
    order_id: int | None = None
    order_no: str | None = None
    block_id: int | None = None


def find_conflicts(
    request_start: datetime,
    request_end: datetime | None,
    occupancies: Iterable[Occupancy],
    *,
    exclude_order_id: int | None = None,
) -> list[Occupancy]:
    """요청 구간과 겹치는 점유를 반환. 편집 중인 자신의 주문은 제외."""
    result = []
    for occ in occupancies:
        if exclude_order_id is not None and occ.order_id == exclude_order_id:
            continue
        if overlaps(request_start, request_end, occ.start_at, occ.end_at):
            result.append(occ)
    result.sort(key=lambda o: (o.asset_id, o.start_at))
    return result


def validate_reservation_window(
    start_at: datetime,
    due_at: datetime,
    now: datetime,
    *,
    require_future_start: bool,
) -> None:
    """시작 < 약정 반납, 신규 확정이면 시작 >= 현재(분 단위 절사).

    분 단위로 절사하는 이유: 화면은 분 단위로 입력받으므로, 사용자가 '지금'을
    고르고 몇 초 뒤 저장해도 과거 시작으로 거부되지 않게 하기 위함.
    """
    ensure_aware(start_at)
    ensure_aware(due_at)
    if not start_at < due_at:
        raise ValidationError("due_at", "약정 반납 일시는 시작 일시보다 뒤여야 합니다.")
    if require_future_start and start_at < floor_minute(now):
        raise ValidationError("start_at", "예약 시작 일시는 현재 시각 이전일 수 없습니다.")


class AssetState(StrEnum):
    """화면 표시용 현재 가용 상태(계산값). `assets`에 수동 상태 컬럼을 두지 않는다(W-05)."""

    AVAILABLE = "AVAILABLE"
    RENTED = "RENTED"
    OVERDUE = "OVERDUE"
    MAINTENANCE = "MAINTENANCE"
    RETIRED = "RETIRED"


ASSET_STATE_LABELS = {
    AssetState.AVAILABLE: "사용 가능",
    AssetState.RENTED: "대여 중",
    AssetState.OVERDUE: "연체",
    AssetState.MAINTENANCE: "정비 중",
    AssetState.RETIRED: "은퇴",
}


def current_asset_state(
    *,
    retired: bool,
    out_due_at: datetime | None,
    in_maintenance: bool,
    now: datetime,
) -> AssetState:
    """현재 시각 기준 장비 상태. 우선순위: 대여 중/연체 > 정비 > 은퇴 > 사용 가능.

    대여 중인 장비는 은퇴할 수 없지만, 과거 데이터 이상에 대비해 실물 위치(대여 중)를 먼저 보인다.
    """
    if out_due_at is not None:
        return AssetState.OVERDUE if now > out_due_at else AssetState.RENTED
    if in_maintenance:
        return AssetState.MAINTENANCE
    if retired:
        return AssetState.RETIRED
    return AssetState.AVAILABLE


def block_active_at(start_at: datetime, end_at: datetime | None, at: datetime) -> bool:
    """정비 블록 `[start, end)`가 `at` 시각을 포함하는지."""
    return start_at <= at and (end_at is None or at < end_at)
