"""장비 세트(키트) 순수 함수 (MULTI_INDUSTRY_PLAN R4).

세트 = 이름 + 구성 장비(관리번호가 있는 실물 장비) + 구성품 체크리스트(관리번호 없는 부속) + 세트 할인.
세트는 예약을 빠르게 만드는 묶음일 뿐이며 가용성·출고·반납·요금은 구성 장비 하나하나 기준이다.
DB 접근이 없다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.money import round_half_up, validate_won

MAX_KIT_ASSETS = 30  # 한 예약에 담을 수 있는 장비 수와 같다
MAX_CHECKLIST_ITEMS = 50
MAX_CHECKLIST_LABEL = 100
MAX_KIT_NAME = 50
MAX_PERCENT = Decimal("100")


class KitDiscountKind(StrEnum):
    FIXED = "FIXED"  # 정액(원)
    PERCENT = "PERCENT"  # 비율(%) — 세트 구성 품목 기간 요금 합 기준


KIT_DISCOUNT_KIND_LABELS = {KitDiscountKind.FIXED: "정액(원)", KitDiscountKind.PERCENT: "비율(%)"}


@dataclass(frozen=True)
class KitDiscount:
    kind: KitDiscountKind = KitDiscountKind.FIXED
    won: int = 0  # 정액일 때만
    percent: Decimal | None = None  # 비율일 때만, 0 초과 100 이하

    @property
    def is_none(self) -> bool:
        return self.kind == KitDiscountKind.FIXED and self.won == 0

    @property
    def label(self) -> str:
        """"없음" / "5,000원" / "10%"."""
        if self.kind == KitDiscountKind.PERCENT and self.percent is not None:
            return f"{self.percent.normalize():f}%"
        return f"{self.won:,}원" if self.won else "없음"


NO_DISCOUNT = KitDiscount()


@dataclass(frozen=True)
class Kit:
    id: int
    name: str
    discount: KitDiscount
    asset_ids: tuple[int, ...]  # 구성 장비(등록 순서)
    checklist: tuple[str, ...]  # 관리번호 없는 구성품
    note: str | None = None
    is_archived: bool = False


@dataclass(frozen=True)
class OrderKit:
    """예약에 담긴 세트 스냅샷. 세트를 나중에 고쳐도 기존 예약은 바뀌지 않는다."""

    id: int
    order_id: int
    kit_id: int
    name: str
    discount: KitDiscount
    checklist: tuple[str, ...]
    checkout_checks: tuple[bool, ...] | None = None  # None = 아직 확인 안 함
    return_checks: tuple[bool, ...] | None = None

    @property
    def checkout_missing(self) -> list[str]:
        return missing_items(self.checklist, self.checkout_checks)

    @property
    def return_missing(self) -> list[str]:
        return missing_items(self.checklist, self.return_checks)


def parse_percent(value: object) -> Decimal:
    try:
        dec = Decimal(str(value)) if not isinstance(value, Decimal) else value
    except (InvalidOperation, ValueError):
        raise ValidationError("discount_percent", "세트 할인 비율은 숫자여야 합니다.") from None
    if isinstance(value, bool) or not dec.is_finite() or not (0 < dec <= MAX_PERCENT):
        raise ValidationError("discount_percent", "세트 할인 비율은 0%보다 크고 100% 이하여야 합니다.")
    if dec.as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValidationError("discount_percent", "세트 할인 비율은 소수 둘째 자리까지 입력할 수 있습니다.")
    return dec


def validate_kit_discount(kind: object, won: object = 0, percent: object = None) -> KitDiscount:
    """세트 할인 검증. 정액은 0 이상 정수 원(0 = 할인 없음), 비율은 0 초과 100 이하."""
    try:
        kind = KitDiscountKind(kind)
    except ValueError:
        raise ValidationError("discount_kind", "세트 할인 방식을 선택해주세요.") from None
    if kind == KitDiscountKind.FIXED:
        return KitDiscount(kind, validate_won("discount_won", won, label="세트 할인 금액"))
    if percent is None:
        raise ValidationError("discount_percent", "세트 할인 비율을 입력해주세요.")
    return KitDiscount(kind, 0, parse_percent(percent))


def kit_discount_won(discount: KitDiscount, kit_fee_won: int) -> int:
    """세트 할인액. 정액은 세트 구성 품목 기간 요금 합을 넘지 않게 줄이고, 비율은 그 합 × %(반올림).

    대체·제외된 장비가 있으면 호출자가 실제로 담긴 품목의 요금 합만 넘긴다.
    """
    if kit_fee_won <= 0:
        return 0
    if discount.kind == KitDiscountKind.PERCENT and discount.percent is not None:
        return min(kit_fee_won, round_half_up(Decimal(kit_fee_won) * discount.percent / MAX_PERCENT))
    return min(discount.won, kit_fee_won)


def clean_checklist(labels: object) -> tuple[str, ...]:
    """구성품 체크리스트 줄 정리: 앞뒤 공백 제거, 빈 줄 무시, 같은 줄(대소문자 무시) 거부."""
    if isinstance(labels, str) or not isinstance(labels, Sequence):
        raise ValidationError("checklist", "구성품 목록 값이 올바르지 않습니다.")
    result: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        if not isinstance(raw, str):
            raise ValidationError("checklist", "구성품 목록 값이 올바르지 않습니다.")
        label = raw.strip()
        if not label:
            continue
        if len(label) > MAX_CHECKLIST_LABEL:
            raise ValidationError(
                "checklist", f"구성품 이름은 최대 {MAX_CHECKLIST_LABEL}자까지 입력할 수 있습니다: {label[:20]}…"
            )
        if label.casefold() in seen:
            raise ValidationError("checklist", f"같은 구성품이 두 번 들어 있습니다: {label}")
        seen.add(label.casefold())
        result.append(label)
    if len(result) > MAX_CHECKLIST_ITEMS:
        raise ValidationError("checklist", f"구성품은 최대 {MAX_CHECKLIST_ITEMS}개까지 넣을 수 있습니다.")
    return tuple(result)


def validate_checks(checklist: Sequence[str], checks: object) -> tuple[bool, ...]:
    """체크 결과(구성품 줄마다 True/False) 검증."""
    if isinstance(checks, str) or not isinstance(checks, Sequence):
        raise ValidationError("kit_checks", "구성품 확인 결과가 올바르지 않습니다.")
    if len(checks) != len(checklist) or any(not isinstance(c, bool) for c in checks):
        raise ValidationError("kit_checks", "구성품 확인 결과가 체크리스트와 맞지 않습니다.")
    return tuple(checks)


def missing_items(checklist: Sequence[str], checks: Sequence[bool] | None) -> list[str]:
    """체크하지 않은 구성품. 아직 확인하지 않았으면(None) 빈 목록."""
    if checks is None:
        return []
    return [label for label, ok in zip(checklist, checks, strict=True) if not ok]


def check_kit_pick(members: Mapping[int, int], picked: Mapping[int, int], *, kit_name: str = "") -> None:
    """예약에 담는 세트 장비 검증. `members`·`picked`는 장비 id → 카테고리 id.

    세트 구성 장비가 아닌 장비는 빠진 구성 장비를 같은 카테고리로 대체한 것이어야 한다
    (카테고리마다 대체 장비 수 ≤ 빠진 장비 수). 하나도 담지 않은 세트는 거부한다.
    """
    name = f"'{kit_name}' " if kit_name else ""
    if not picked:
        raise ValidationError("kits", f"세트 {name}에 담긴 장비가 없습니다. 세트를 빼고 진행해주세요.")
    missing = Counter(cat for asset_id, cat in members.items() if asset_id not in picked)
    extra = Counter(cat for asset_id, cat in picked.items() if asset_id not in members)
    if any(count > missing[cat] for cat, count in extra.items()):
        raise ValidationError(
            "kits", f"세트 {name}의 장비는 빠진 구성 장비와 같은 카테고리의 장비로만 바꿀 수 있습니다."
        )


@dataclass(frozen=True)
class KitSlot:
    """세트 구성 장비 1개의 예약 가능 여부와 같은 카테고리의 대체 후보."""

    member_id: int
    category_id: int
    available: bool
    candidates: tuple[int, ...]

    @property
    def default_choice(self) -> int | None:
        """기본 선택: 쓸 수 있으면 원래 장비, 아니면 첫 대체 후보, 후보도 없으면 빼기(None)."""
        if self.available:
            return self.member_id
        return self.candidates[0] if self.candidates else None


def plan_kit(
    members: Sequence[tuple[int, int]],
    available: Mapping[int, int],
    *,
    taken: Sequence[int] = (),
    locked: Sequence[int] = (),
) -> list[KitSlot]:
    """세트 → 예약 품목 전개 계획.

    `members`: 구성 장비 (id, 카테고리 id) 순서대로. `available`: 이 기간에 예약 가능한 장비 id → 카테고리 id
    (사용 중지 장비는 넣지 않는다). `taken`: 예약에 따로 담긴 장비 — 구성 장비면 그대로 세트로 옮긴다.
    `locked`: 같은 예약의 다른 세트에 담긴 장비 — 쓸 수 없다.
    대체 후보는 같은 카테고리의 가능한 장비 중 다른 구성 장비·이미 담긴 장비가 아닌 것(id 순).
    기본 선택이 겹치지 않도록 앞 슬롯이 고른 후보는 뒤 슬롯의 후보에서 뺀다.
    """
    member_ids = {m for m, _ in members}
    blocked = set(locked)
    used = set(taken) | blocked
    slots: list[KitSlot] = []
    for member_id, category_id in members:
        ok = member_id in available and member_id not in blocked
        candidates = tuple(sorted(
            a for a, cat in available.items()
            if cat == category_id and a not in member_ids and a not in used
        ))
        slot = KitSlot(member_id, category_id, ok, candidates)
        choice = slot.default_choice
        if choice is not None:
            used.add(choice)
        slots.append(slot)
    return slots
