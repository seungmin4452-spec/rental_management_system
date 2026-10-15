"""도메인 상태 Enum과 데이터형 (PRD 3, 6, 8.1).

주문(order) 상태와 품목(item) 상태를 분리한다. 부분 반납을 지원하기 위해
주문 상태만으로 장비 가용성을 판단하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    IN_RENTAL = "IN_RENTAL"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"


class ItemStatus(StrEnum):
    RESERVED = "RESERVED"
    OUT = "OUT"
    RETURNED = "RETURNED"
    CANCELED = "CANCELED"


class LifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class CustomerType(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    BUSINESS = "BUSINESS"


class ReturnCondition(StrEnum):
    GOOD = "GOOD"
    MAINTENANCE_REQUIRED = "MAINTENANCE_REQUIRED"


class ChargeKind(StrEnum):
    RENTAL = "RENTAL"
    DISCOUNT = "DISCOUNT"
    LATE = "LATE"
    DAMAGE = "DAMAGE"
    ADJUSTMENT = "ADJUSTMENT"


class MoneyKind(StrEnum):
    RENTAL_RECEIPT = "RENTAL_RECEIPT"
    RENTAL_REFUND = "RENTAL_REFUND"
    DEPOSIT_IN = "DEPOSIT_IN"
    DEPOSIT_OUT = "DEPOSIT_OUT"
    DEPOSIT_APPLIED = "DEPOSIT_APPLIED"
    DEPOSIT_APPLICATION_REVERSAL = "DEPOSIT_APPLICATION_REVERSAL"


class PaymentMethod(StrEnum):
    CASH = "CASH"
    TRANSFER = "TRANSFER"
    CARD = "CARD"
    OTHER = "OTHER"


# PRD 6.1 주문 상태 전이. COMPLETED/CANCELED는 종단 상태.
ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.IN_RENTAL, OrderStatus.CANCELED}),
    OrderStatus.IN_RENTAL: frozenset({OrderStatus.COMPLETED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
}

# PRD 6.2 품목 상태 전이.
ITEM_TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.RESERVED: frozenset({ItemStatus.OUT, ItemStatus.CANCELED}),
    ItemStatus.OUT: frozenset({ItemStatus.RETURNED}),
    ItemStatus.RETURNED: frozenset(),
    ItemStatus.CANCELED: frozenset(),
}

# 출고 전(시간/장비 수정·취소 가능) 주문 상태.
PRE_CHECKOUT_STATUSES = frozenset({OrderStatus.DRAFT, OrderStatus.CONFIRMED})


def can_transition_order(current: OrderStatus, target: OrderStatus) -> bool:
    return target in ORDER_TRANSITIONS[current]


def can_transition_item(current: ItemStatus, target: ItemStatus) -> bool:
    return target in ITEM_TRANSITIONS[current]


ORDER_STATUS_LABELS = {
    OrderStatus.DRAFT: "임시저장",
    OrderStatus.CONFIRMED: "예약확정",
    OrderStatus.IN_RENTAL: "대여중",
    OrderStatus.COMPLETED: "반납완료",
    OrderStatus.CANCELED: "취소",
}


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    is_archived: bool


@dataclass(frozen=True)
class Asset:
    id: int
    asset_code: str
    name: str
    category_id: int
    category_name: str
    brand: str | None
    model: str | None
    serial_no: str | None
    location: str | None
    daily_rate_won: int
    deposit_won: int
    lifecycle_status: LifecycleStatus
    note: str | None
    created_at: datetime


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    customer_type: CustomerType
    phone: str | None
    email: str | None
    note: str | None
    is_archived: bool
    created_at: datetime


@dataclass(frozen=True)
class RentalItem:
    id: int
    order_id: int
    asset_id: int
    asset_code: str
    status: ItemStatus
    daily_rate_snapshot_won: int
    deposit_snapshot_won: int
    late_multiplier_snapshot: Decimal
    grace_minutes_snapshot: int
    actual_return_at: datetime | None
    return_condition: ReturnCondition | None
    return_note: str | None


@dataclass(frozen=True)
class RentalOrder:
    id: int
    order_no: str
    customer_id: int
    status: OrderStatus
    start_at: datetime
    due_at: datetime
    actual_checkout_at: datetime | None
    turnaround_buffer_snapshot_minutes: int
    discount_won: int
    notes: str | None
    created_at: datetime
    updated_at: datetime
    items: tuple[RentalItem, ...] = field(default=())
    checkout_note: str | None = None


@dataclass(frozen=True)
class MaintenanceBlock:
    id: int
    asset_id: int
    start_at: datetime
    end_at: datetime | None
    reason: str
    created_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class ChargeLine:
    """청구 원장 1행 (PRD 7.4). 할인은 음수. 수정·삭제 없이 추가만 한다."""

    id: int
    order_id: int
    rental_item_id: int | None
    kind: ChargeKind
    amount_won: int
    description: str | None
    created_at: datetime


@dataclass(frozen=True)
class MoneyEntry:
    """금전 원장 1행 (PRD 7.4). 금액은 항상 양수이며 효과는 `kind`로 표현한다."""

    id: int
    order_id: int
    kind: MoneyKind
    amount_won: int
    payment_method: PaymentMethod | None
    note: str | None
    created_at: datetime
    related_entry_id: int | None


@dataclass(frozen=True)
class RentalPolicy:
    """예약 확정 시 스냅샷으로 복사되는 운영 설정 (PRD 5 S-09, 7.3)."""

    grace_minutes: int = 60
    late_multiplier: Decimal = Decimal("1")
    turnaround_buffer_minutes: int = 60
