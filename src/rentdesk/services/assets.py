"""AssetService: 장비·카테고리 등록/수정/검색/은퇴 (PRD P0-01, S-02)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from rentdesk.domain.errors import (
    DuplicateError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from rentdesk.domain.models import (
    Asset,
    Category,
    CategoryPolicy,
    ItemStatus,
    LifecycleStatus,
    OrderStatus,
)
from rentdesk.domain.pricing import validate_won
from rentdesk.domain.rates import RateRule, rate_rule_to_json, validate_rate_rule
from rentdesk.domain.timeutil import format_seoul, parse_utc_str, parse_utc_str_opt
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import (
    Clock,
    clean_optional,
    clean_required,
    current_time,
    default_clock,
)
from rentdesk.services.settings import validate_minutes, validate_multiplier

AUTO_CODE_PREFIX = "AST-"


@dataclass(frozen=True)
class AssetInput:
    name: str
    category_name: str
    daily_rate_won: int
    deposit_won: int
    asset_code: str | None = None  # 비우면 AST-000001 형태로 자동 생성. 수정 시 무시.
    brand: str | None = None
    model: str | None = None
    serial_no: str | None = None
    location: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class _CleanAsset:
    name: str
    category_name: str
    daily_rate_won: int
    deposit_won: int
    brand: str | None
    model: str | None
    serial_no: str | None
    location: str | None
    note: str | None


def _clean(data: AssetInput) -> _CleanAsset:
    return _CleanAsset(
        name=clean_required(data.name, "name", "장비명", 100),
        category_name=clean_required(data.category_name, "category", "카테고리", 50),
        daily_rate_won=validate_won("daily_rate_won", data.daily_rate_won, label="일 대여료"),
        deposit_won=validate_won("deposit_won", data.deposit_won, label="보증금"),
        brand=clean_optional(data.brand, "brand", "브랜드", 100),
        model=clean_optional(data.model, "model", "모델명", 100),
        serial_no=clean_optional(data.serial_no, "serial_no", "시리얼 번호", 100),
        location=clean_optional(data.location, "location", "보관 위치", 100),
        note=clean_optional(data.note, "note", "메모", 1000),
    )


def _clean_category_policy(
    turnaround_buffer_minutes: int | None, grace_minutes: int | None, late_multiplier: str | Decimal | None
) -> CategoryPolicy:
    """카테고리 운영 설정 검증(가게 기본값과 같은 범위). None은 "가게 기본값 사용"."""
    return CategoryPolicy(
        turnaround_buffer_minutes=None if turnaround_buffer_minutes is None else validate_minutes(
            "turnaround_buffer_minutes", "반납 후 준비 시간", turnaround_buffer_minutes),
        grace_minutes=None if grace_minutes is None else validate_minutes(
            "grace_minutes", "연체료 없이 기다려 주는 시간", grace_minutes),
        late_multiplier=None if late_multiplier is None else validate_multiplier(late_multiplier),
    )


UNCHANGED = object()  # update_category: 요금 규칙을 건드리지 않음


def _clean_rate_rule(rule: object) -> RateRule | None:
    if rule is None:
        return None
    if not isinstance(rule, RateRule):
        raise ValidationError("rate_rule", "요금 규칙이 올바르지 않습니다.")
    return validate_rate_rule(rule)


@dataclass(frozen=True)
class AssetOrderRow:
    """장비 상세(W-05)의 예약/대여 이력 1행."""

    order_id: int
    order_no: str
    order_status: OrderStatus
    item_status: ItemStatus
    customer_name: str
    start_at: datetime
    due_at: datetime
    actual_checkout_at: datetime | None
    actual_return_at: datetime | None

    @property
    def is_upcoming(self) -> bool:
        """출고 전 확정·임시 예약(예정 예약 탭)."""
        return self.order_status in (OrderStatus.DRAFT, OrderStatus.CONFIRMED)


class AssetService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    # ------------------------------------------------------------------ categories

    def list_categories(self, *, include_archived: bool = False) -> list[Category]:
        return repo.list_categories(self._conn, include_archived=include_archived)

    def create_category(self, name: str) -> Category:
        clean = clean_required(name, "category", "카테고리", 50)
        with transaction(self._conn):
            if repo.get_category_by_name(self._conn, clean) is not None:
                raise DuplicateError(f"이미 존재하는 카테고리입니다: {clean}")
            category_id = repo.insert_category(self._conn, clean)
        return repo.get_category(self._conn, category_id)

    def system_category(self) -> Category:
        """항상 있는 "기타" 카테고리."""
        return repo.get_system_category(self._conn)

    def rename_category(self, category_id: int, name: str) -> Category:
        clean = clean_required(name, "category", "카테고리", 50)
        now = current_time(self._clock)
        with transaction(self._conn):
            self._rename_in_txn(category_id, clean, now)
        return repo.get_category(self._conn, category_id)

    def update_category_policy(
        self,
        category_id: int,
        *,
        turnaround_buffer_minutes: int | None,
        grace_minutes: int | None,
        late_multiplier: str | Decimal | None,
    ) -> Category:
        """카테고리별 운영 설정(MULTI_INDUSTRY_PLAN R2). None = 가게 기본값 사용.

        이미 확정된 예약은 품목별 스냅샷을 쓰므로 바뀌지 않는다. "기타"도 설정할 수 있다.
        """
        policy = _clean_category_policy(turnaround_buffer_minutes, grace_minutes, late_multiplier)
        now = current_time(self._clock)
        with transaction(self._conn):
            self._set_policy_in_txn(category_id, policy, now)
        return repo.get_category(self._conn, category_id)

    def update_category(
        self,
        category_id: int,
        *,
        name: str,
        turnaround_buffer_minutes: int | None,
        grace_minutes: int | None,
        late_multiplier: str | Decimal | None,
        rate_rule: RateRule | None | object = UNCHANGED,
    ) -> Category:
        """카테고리 관리 창의 [저장]: 이름(바뀐 경우)·운영 설정·요금 규칙을 한 트랜잭션으로 저장.

        `rate_rule`: 규칙 = 카테고리 요금 규칙, None = 가게 기본 규칙 사용, 생략 = 그대로 둠.
        """
        clean = clean_required(name, "category", "카테고리", 50)
        policy = _clean_category_policy(turnaround_buffer_minutes, grace_minutes, late_multiplier)
        rule = rate_rule if rate_rule is UNCHANGED else _clean_rate_rule(rate_rule)
        now = current_time(self._clock)
        with transaction(self._conn):
            current = repo.get_category(self._conn, category_id)
            if current is None:
                raise NotFoundError("카테고리를 찾을 수 없습니다.")
            if clean != current.name:
                self._rename_in_txn(category_id, clean, now)
            self._set_policy_in_txn(category_id, policy, now)
            if rule is not UNCHANGED:
                self._set_rate_rule_in_txn(category_id, rule, now)  # type: ignore[arg-type]
        return repo.get_category(self._conn, category_id)

    def update_category_rate_rule(self, category_id: int, rule: RateRule | None) -> Category:
        """카테고리 요금 규칙(MULTI_INDUSTRY_PLAN R3). None = 가게 기본 규칙 사용.

        이미 확정된 예약은 품목별 요금 규칙 스냅샷을 쓰므로 바뀌지 않는다.
        """
        clean = _clean_rate_rule(rule)
        now = current_time(self._clock)
        with transaction(self._conn):
            if repo.get_category(self._conn, category_id) is None:
                raise NotFoundError("카테고리를 찾을 수 없습니다.")
            self._set_rate_rule_in_txn(category_id, clean, now)
        return repo.get_category(self._conn, category_id)

    def _set_rate_rule_in_txn(self, category_id: int, rule: RateRule | None, now: datetime) -> None:
        repo.update_category_rate_rule(self._conn, category_id, rule)
        repo.insert_audit(
            self._conn, entity_type="category", entity_id=category_id, action="UPDATE_RATE_RULE",
            description="가게 기본값" if rule is None else rate_rule_to_json(rule)[:500], now=now,
        )

    def _rename_in_txn(self, category_id: int, name: str, now: datetime) -> None:
        category = self._editable_category(category_id, "이름을 바꿀")
        other = repo.get_category_by_name(self._conn, name)
        if other is not None and other.id != category_id:
            raise DuplicateError(f"이미 존재하는 카테고리입니다: {other.name}")
        repo.rename_category(self._conn, category_id, name)
        repo.insert_audit(
            self._conn, entity_type="category", entity_id=category_id, action="RENAME",
            description=f"{category.name} → {name}", now=now,
        )

    def _set_policy_in_txn(self, category_id: int, policy: CategoryPolicy, now: datetime) -> None:
        if repo.get_category(self._conn, category_id) is None:
            raise NotFoundError("카테고리를 찾을 수 없습니다.")
        repo.update_category_policy(self._conn, category_id, policy)
        repo.insert_audit(
            self._conn, entity_type="category", entity_id=category_id, action="UPDATE_POLICY",
            description=(
                f"buffer={policy.turnaround_buffer_minutes}, grace={policy.grace_minutes},"
                f" multiplier={policy.late_multiplier}"
            ), now=now,
        )

    def set_category_archived(self, category_id: int, archived: bool) -> Category:
        with transaction(self._conn):
            if archived:
                self._editable_category(category_id, "숨길")
            elif repo.get_category(self._conn, category_id) is None:
                raise NotFoundError("카테고리를 찾을 수 없습니다.")
            repo.set_category_archived(self._conn, category_id, archived)
        return repo.get_category(self._conn, category_id)

    def _editable_category(self, category_id: int, verb: str) -> Category:
        category = repo.get_category(self._conn, category_id)
        if category is None:
            raise NotFoundError("카테고리를 찾을 수 없습니다.")
        if category.is_system:
            raise InvalidStateError(
                f"'{category.name}' 카테고리는 어느 카테고리에도 맞지 않는 장비를 넣는 기본 칸이라 "
                f"{verb} 수 없습니다."
            )
        return category

    def _resolve_category(self, name: str) -> int:
        """이름으로 카테고리를 찾고 없으면 생성(트랜잭션 내부에서 호출)."""
        category = repo.get_category_by_name(self._conn, name)
        if category is None:
            return repo.insert_category(self._conn, name)
        if category.is_archived:
            raise ValidationError("category", f"숨김 처리된 카테고리입니다: {category.name}")
        return category.id

    # ------------------------------------------------------------------ assets

    def get_asset(self, asset_id: int) -> Asset:
        asset = repo.get_asset(self._conn, asset_id)
        if asset is None:
            raise NotFoundError("장비를 찾을 수 없습니다.")
        return asset

    def asset_orders(self, asset_id: int) -> list[AssetOrderRow]:
        self.get_asset(asset_id)
        return [
            AssetOrderRow(
                order_id=r["order_id"], order_no=r["order_no"],
                order_status=OrderStatus(r["order_status"]),
                item_status=ItemStatus(r["item_status"]), customer_name=r["customer_name"],
                start_at=parse_utc_str(r["start_at_utc"]), due_at=parse_utc_str(r["due_at_utc"]),
                actual_checkout_at=parse_utc_str_opt(r["actual_checkout_at_utc"]),
                actual_return_at=parse_utc_str_opt(r["actual_return_at_utc"]),
            )
            for r in repo.asset_order_rows(self._conn, asset_id)
        ]

    def search_assets(
        self, query: str = "", *, include_retired: bool = False, category_id: int | None = None
    ) -> list[Asset]:
        return repo.search_assets(
            self._conn, query=query.strip(), include_retired=include_retired,
            category_id=category_id,
        )

    def create_asset(self, data: AssetInput) -> Asset:
        clean = _clean(data)
        code = clean_optional(data.asset_code, "asset_code", "장비번호", 30)
        now = current_time(self._clock)
        with transaction(self._conn):
            if code is None:
                code = f"{AUTO_CODE_PREFIX}{repo.max_auto_asset_number(self._conn) + 1:06d}"
            elif repo.get_asset_by_code(self._conn, code) is not None:
                raise DuplicateError(f"이미 등록된 장비번호입니다: {code}")
            category_id = self._resolve_category(clean.category_name)
            try:
                asset_id = repo.insert_asset(
                    self._conn,
                    asset_code=code,
                    name=clean.name,
                    category_id=category_id,
                    brand=clean.brand,
                    model=clean.model,
                    serial_no=clean.serial_no,
                    location=clean.location,
                    daily_rate_won=clean.daily_rate_won,
                    deposit_won=clean.deposit_won,
                    note=clean.note,
                    created_at=now,
                )
            except sqlite3.IntegrityError as exc:
                if "asset_code" in str(exc):
                    raise DuplicateError(f"이미 등록된 장비번호입니다: {code}") from exc
                raise
            repo.insert_audit(
                self._conn, entity_type="asset", entity_id=asset_id, action="CREATE",
                description=code, now=now,
            )
        return self.get_asset(asset_id)

    def update_asset(self, asset_id: int, data: AssetInput) -> Asset:
        """장비번호는 변경 불가(PRD 7.5). `data.asset_code`가 다르면 거부."""
        clean = _clean(data)
        now = current_time(self._clock)
        with transaction(self._conn):
            existing = repo.get_asset(self._conn, asset_id)
            if existing is None:
                raise NotFoundError("장비를 찾을 수 없습니다.")
            code = clean_optional(data.asset_code, "asset_code", "장비번호", 30)
            if code is not None and code != existing.asset_code:
                raise ValidationError("asset_code", "장비번호는 변경할 수 없습니다.")
            category_id = (
                existing.category_id
                if clean.category_name.casefold() == existing.category_name.casefold()
                else self._resolve_category(clean.category_name)
            )
            repo.update_asset(
                self._conn,
                asset_id,
                name=clean.name,
                category_id=category_id,
                brand=clean.brand,
                model=clean.model,
                serial_no=clean.serial_no,
                location=clean.location,
                daily_rate_won=clean.daily_rate_won,
                deposit_won=clean.deposit_won,
                note=clean.note,
            )
            repo.insert_audit(
                self._conn, entity_type="asset", entity_id=asset_id, action="UPDATE",
                description=existing.asset_code, now=now,
            )
        return self.get_asset(asset_id)

    def retire_asset(self, asset_id: int) -> Asset:
        """대여 중이거나 출고 전 확정 예약이 남은 장비는 은퇴 불가(해당 예약 안내)."""
        now = current_time(self._clock)
        with transaction(self._conn):
            asset = repo.get_asset(self._conn, asset_id)
            if asset is None:
                raise NotFoundError("장비를 찾을 수 없습니다.")
            if asset.lifecycle_status == LifecycleStatus.RETIRED:
                return asset
            if repo.asset_has_out_items(self._conn, asset_id):
                raise InvalidStateError(
                    f"{asset.asset_code} 장비가 현재 대여 중이어서 사용 중지할 수 없습니다."
                )
            confirmed = repo.confirmed_orders_for_asset(self._conn, asset_id)
            if confirmed:
                lines = "\n".join(
                    f"- {r['order_no']} ({format_seoul(parse_utc_str(r['start_at_utc']))} ~ "
                    f"{format_seoul(parse_utc_str(r['due_at_utc']))})"
                    for r in confirmed
                )
                raise InvalidStateError(
                    f"{asset.asset_code} 장비에 확정된 예약이 남아 있어 사용 중지할 수 없습니다.\n"
                    f"{lines}"
                )
            repo.set_asset_lifecycle(self._conn, asset_id, LifecycleStatus.RETIRED)
            repo.insert_audit(
                self._conn, entity_type="asset", entity_id=asset_id, action="RETIRE",
                description=asset.asset_code, now=now,
            )
        return self.get_asset(asset_id)

    def reactivate_asset(self, asset_id: int) -> Asset:
        now = current_time(self._clock)
        with transaction(self._conn):
            asset = repo.get_asset(self._conn, asset_id)
            if asset is None:
                raise NotFoundError("장비를 찾을 수 없습니다.")
            repo.set_asset_lifecycle(self._conn, asset_id, LifecycleStatus.ACTIVE)
            repo.insert_audit(
                self._conn, entity_type="asset", entity_id=asset_id, action="REACTIVATE",
                description=asset.asset_code, now=now,
            )
        return self.get_asset(asset_id)

    def delete_asset(self, asset_id: int) -> None:
        """예약·정비 이력이 전혀 없는 잘못 등록된 장비만 물리 삭제 가능."""
        now = current_time(self._clock)
        with transaction(self._conn):
            asset = repo.get_asset(self._conn, asset_id)
            if asset is None:
                raise NotFoundError("장비를 찾을 수 없습니다.")
            if repo.asset_has_history(self._conn, asset_id):
                raise InvalidStateError(
                    "예약 또는 정비 이력이 있는 장비는 삭제할 수 없습니다. 사용 중지를 이용해주세요."
                )
            kits = repo.kit_names_by_asset(self._conn, include_archived=True).get(asset_id)
            if kits:
                raise InvalidStateError(
                    f"세트({', '.join(kits)})에 들어 있는 장비는 삭제할 수 없습니다. 세트에서 먼저 빼주세요."
                )
            repo.delete_asset(self._conn, asset_id)
            repo.insert_audit(
                self._conn, entity_type="asset", entity_id=asset_id, action="DELETE",
                description=asset.asset_code, now=now,
            )
