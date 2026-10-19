"""KitService: 장비 세트(키트) 등록·수정·숨김과 예약 전개 계획 (MULTI_INDUSTRY_PLAN R4).

- 세트 구성은 특정 장비 고정(7장 결정 4). 예약할 때 그 기간에 안 되는 장비는 같은 카테고리의
  가능한 장비로 바꾸거나 빼고 진행한다(`plan`). 실제 저장·가용성 재확인은 `ReservationService`가
  예약 트랜잭션 안에서 한다.
- 검증: 빈 세트 금지, 같은 장비 중복 금지, 사용 중지 장비 추가 금지(이미 들어 있던 장비는 경고만).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from rentdesk.domain.errors import DuplicateError, NotFoundError, ValidationError
from rentdesk.domain.kits import (
    MAX_KIT_ASSETS,
    MAX_KIT_NAME,
    Kit,
    KitDiscount,
    KitDiscountKind,
    clean_checklist,
    kit_discount_won,
    plan_kit,
    validate_kit_discount,
)
from rentdesk.domain.models import Asset, LifecycleStatus
from rentdesk.domain.timeutil import to_utc
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.availability import AvailabilityService
from rentdesk.services.common import Clock, clean_optional, clean_required, current_time, default_clock


@dataclass(frozen=True)
class KitInput:
    name: str
    asset_ids: Sequence[int]
    checklist: Sequence[str] = ()
    discount_kind: KitDiscountKind | str = KitDiscountKind.FIXED
    discount_won: int = 0
    discount_percent: str | Decimal | None = None
    note: str | None = None


@dataclass(frozen=True)
class KitPick:
    """예약에 담는 세트 1개: 세트와 실제로 담을 장비(대체·제외 반영)."""

    kit_id: int
    asset_ids: Sequence[int]


@dataclass(frozen=True)
class KitRow:
    """세트 목록 1행: 구성 장비와 사용 중지 장비 경고."""

    kit: Kit
    assets: tuple[Asset, ...]

    @property
    def retired_codes(self) -> list[str]:
        return [a.asset_code for a in self.assets if a.lifecycle_status != LifecycleStatus.ACTIVE]

    @property
    def daily_total_won(self) -> int:
        return sum(a.daily_rate_won for a in self.assets)

    @property
    def warning(self) -> str:
        codes = self.retired_codes
        if not codes:
            return ""
        return f"사용 중지 장비 포함: {', '.join(codes)} — 예약할 때 다른 장비로 바꿔야 합니다."


@dataclass(frozen=True)
class KitSlotView:
    """세트 구성 장비 1개의 예약 가능 여부·불가 이유·같은 카테고리 대체 후보."""

    member: Asset
    available: bool
    reason: str
    candidates: tuple[Asset, ...]
    default_choice: int | None  # None = 빼고 진행


@dataclass(frozen=True)
class KitPlan:
    kit: Kit
    slots: tuple[KitSlotView, ...]

    @property
    def all_available(self) -> bool:
        return all(s.available for s in self.slots)


@dataclass(frozen=True)
class _CleanKit:
    name: str
    asset_ids: tuple[int, ...]
    checklist: tuple[str, ...]
    discount: KitDiscount
    note: str | None


def _clean(data: KitInput) -> _CleanKit:
    if not isinstance(data, KitInput):
        raise ValidationError("kit", "세트 입력 값이 올바르지 않습니다.")
    asset_ids = data.asset_ids
    if isinstance(asset_ids, str) or not isinstance(asset_ids, Sequence):
        raise ValidationError("asset_ids", "세트 장비 값이 올바르지 않습니다.")
    asset_ids = tuple(asset_ids)
    if any(isinstance(a, bool) or not isinstance(a, int) for a in asset_ids):
        raise ValidationError("asset_ids", "세트 장비 값이 올바르지 않습니다.")
    if not asset_ids:
        raise ValidationError("asset_ids", "세트에 장비를 1개 이상 넣어주세요.")
    if len(asset_ids) > MAX_KIT_ASSETS:
        raise ValidationError("asset_ids", f"세트에는 장비를 최대 {MAX_KIT_ASSETS}개까지 넣을 수 있습니다.")
    if len(set(asset_ids)) != len(asset_ids):
        raise ValidationError("asset_ids", "같은 장비를 세트에 두 번 넣을 수 없습니다.")
    return _CleanKit(
        name=clean_required(data.name, "name", "세트 이름", MAX_KIT_NAME),
        asset_ids=asset_ids,
        checklist=clean_checklist(data.checklist),
        discount=validate_kit_discount(data.discount_kind, data.discount_won, data.discount_percent),
        note=clean_optional(data.note, "note", "메모", 1000),
    )


class KitService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    # ------------------------------------------------------------------ queries

    def list_kits(self, *, include_archived: bool = False) -> list[Kit]:
        return repo.list_kits(self._conn, include_archived=include_archived)

    def list_kit_rows(self, *, include_archived: bool = False) -> list[KitRow]:
        kits = self.list_kits(include_archived=include_archived)
        assets = repo.get_assets(self._conn, sorted({a for k in kits for a in k.asset_ids}))
        return [KitRow(k, tuple(assets[a] for a in k.asset_ids if a in assets)) for k in kits]

    def get_kit(self, kit_id: int) -> Kit:
        kit = repo.get_kit(self._conn, kit_id)
        if kit is None:
            raise NotFoundError("세트를 찾을 수 없습니다.")
        return kit

    def kit_row(self, kit_id: int) -> KitRow:
        kit = self.get_kit(kit_id)
        assets = repo.get_assets(self._conn, list(kit.asset_ids))
        return KitRow(kit, tuple(assets[a] for a in kit.asset_ids if a in assets))

    def daily_preview(
        self, asset_ids: Sequence[int], discount_kind: KitDiscountKind | str, discount_won: int = 0,
        discount_percent: str | Decimal | None = None,
    ) -> tuple[int, int]:
        """세트 화면 미리보기: (구성 장비 1일 요금 합계, 1일 기준 세트 할인액)."""
        discount = validate_kit_discount(discount_kind, discount_won, discount_percent)
        assets = repo.get_assets(self._conn, list(dict.fromkeys(asset_ids)))
        total = sum(a.daily_rate_won for a in assets.values())
        return total, kit_discount_won(discount, total)

    def plan(
        self,
        kit_id: int,
        start_at: datetime,
        due_at: datetime,
        *,
        order_id: int | None = None,
        taken: Sequence[int] = (),
        locked: Sequence[int] = (),
    ) -> KitPlan:
        """예약 마법사 [세트 추가]: 구성 장비별 가능 여부와 같은 카테고리 대체 후보.

        `taken`: 예약에 따로 담긴 장비(구성 장비면 세트로 옮김), `locked`: 같은 예약의 다른 세트 장비.
        """
        kit = self.get_kit(kit_id)
        options = AvailabilityService(self._conn, self._clock).asset_options(
            to_utc(start_at), to_utc(due_at), exclude_order_id=order_id
        )
        by_id = {o.asset.id: o for o in options}
        available = {o.asset.id: o.asset.category_id for o in options if o.available}
        members = repo.get_assets(self._conn, list(kit.asset_ids))
        slots = plan_kit(
            [(a, members[a].category_id) for a in kit.asset_ids if a in members],
            available, taken=taken, locked=locked,
        )
        assets = {o.asset.id: o.asset for o in options}
        locked_set = set(locked)
        views = []
        for slot in slots:
            member = members[slot.member_id]
            if slot.available:
                reason = ""
            elif member.lifecycle_status != LifecycleStatus.ACTIVE:
                reason = "사용 중지된 장비"
            elif slot.member_id in locked_set:
                reason = "이 예약의 다른 세트에 담긴 장비"
            else:
                option = by_id.get(slot.member_id)
                reason = option.short_reason if option is not None else "예약할 수 없는 장비"
            views.append(KitSlotView(
                member=member, available=slot.available, reason=reason,
                candidates=tuple(assets[c] for c in slot.candidates), default_choice=slot.default_choice,
            ))
        return KitPlan(kit, tuple(views))

    # ------------------------------------------------------------------ commands

    def create_kit(self, data: KitInput) -> Kit:
        clean = _clean(data)
        now = current_time(self._clock)
        with transaction(self._conn):
            self._check_name(clean.name, None)
            self._check_assets(clean.asset_ids, previous=())
            kit_id = repo.insert_kit(
                self._conn, name=clean.name, discount=clean.discount, note=clean.note, now=now
            )
            repo.replace_kit_contents(self._conn, kit_id, clean.asset_ids, clean.checklist)
            repo.insert_audit(
                self._conn, entity_type="kit", entity_id=kit_id, action="CREATE", description=clean.name, now=now,
            )
        return self.get_kit(kit_id)

    def update_kit(self, kit_id: int, data: KitInput) -> Kit:
        """세트 수정. 이미 예약에 담긴 세트는 예약 쪽 스냅샷이라 바뀌지 않는다."""
        clean = _clean(data)
        now = current_time(self._clock)
        with transaction(self._conn):
            kit = self.get_kit(kit_id)
            self._check_name(clean.name, kit_id)
            self._check_assets(clean.asset_ids, previous=kit.asset_ids)
            repo.update_kit(
                self._conn, kit_id, name=clean.name, discount=clean.discount, note=clean.note, now=now
            )
            repo.replace_kit_contents(self._conn, kit_id, clean.asset_ids, clean.checklist)
            repo.insert_audit(
                self._conn, entity_type="kit", entity_id=kit_id, action="UPDATE", description=clean.name, now=now,
            )
        return self.get_kit(kit_id)

    def set_kit_archived(self, kit_id: int, archived: bool) -> Kit:
        """숨긴 세트는 새 예약에 보이지 않는다. 이미 담긴 예약은 그대로."""
        now = current_time(self._clock)
        with transaction(self._conn):
            kit = self.get_kit(kit_id)
            repo.set_kit_archived(self._conn, kit_id, bool(archived), now=now)
            repo.insert_audit(
                self._conn, entity_type="kit", entity_id=kit_id,
                action="ARCHIVE" if archived else "UNARCHIVE", description=kit.name, now=now,
            )
        return self.get_kit(kit_id)

    # ------------------------------------------------------------------ internals

    def _check_name(self, name: str, kit_id: int | None) -> None:
        same = repo.get_kit_by_name(self._conn, name)
        if same is not None and same.id != kit_id:
            raise DuplicateError(f"이미 같은 이름의 세트가 있습니다: {name}")

    def _check_assets(self, asset_ids: Sequence[int], *, previous: Sequence[int]) -> None:
        assets = repo.get_assets(self._conn, list(asset_ids))
        if any(a not in assets for a in asset_ids):
            raise ValidationError("asset_ids", "존재하지 않는 장비가 포함되어 있습니다.")
        kept = set(previous)
        retired = [
            assets[a].asset_code for a in asset_ids
            if assets[a].lifecycle_status != LifecycleStatus.ACTIVE and a not in kept
        ]
        if retired:
            raise ValidationError(
                "asset_ids", f"사용 중지된 장비는 세트에 넣을 수 없습니다: {', '.join(sorted(retired))}"
            )
