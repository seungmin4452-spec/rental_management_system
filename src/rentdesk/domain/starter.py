"""시작 도우미(M4.6 R7): 취급 장비 종류를 고르면 추천 카테고리와 카테고리 설정을 추가한다.

업종별 추천값은 코드가 아니라 데이터 파일(`resources/starter/*.json`)에 둔다(업종 코드 분기 없음).
이 모듈은 데이터 파일 형식 검증과 적용 계획(무엇을 추가하고 무엇을 건너뛸지)만 담당한다.

규칙
- 고른 종류의 카테고리만 추가한다. 이미 있는 이름(숨긴 카테고리 포함, 대소문자 무시)은 건너뛰고
  기존 카테고리의 값은 덮어쓰지 않는다. 그래서 여러 번 적용해도 결과가 같다(멱등).
- "기타"는 항상 있는 시스템 카테고리라 데이터 파일에 넣을 수 없다.
- 여러 종류에 같은 이름이 있으면 먼저 나오는 종류(정렬 순서)의 설정을 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import SYSTEM_CATEGORY_NAME, CategoryPolicy
from rentdesk.domain.rates import RateRule, rate_rule_from_data

STARTER_FORMAT_VERSION = 1
MAX_CATEGORY_NAME = 50
MAX_CATEGORIES_PER_KIND = 30
MAX_POLICY_MINUTES = 7 * 24 * 60  # 가게 설정과 같은 한도
MAX_LATE_MULTIPLIER = Decimal("10")

_KIND_KEYS = {"format", "key", "label", "description", "order", "categories"}
_CATEGORY_KEYS = {"name", "turnaround_buffer_minutes", "grace_minutes", "late_multiplier", "rate_rule"}


@dataclass(frozen=True)
class StarterCategory:
    """추가할 카테고리 1개. 값이 None이면 가게 기본값 사용."""

    name: str
    policy: CategoryPolicy = CategoryPolicy()
    rate_rule: RateRule | None = None


@dataclass(frozen=True)
class StarterKind:
    """장비 종류 1개(카메라·촬영 등)와 그 추천 카테고리."""

    key: str
    label: str
    description: str
    order: int
    categories: tuple[StarterCategory, ...]

    @property
    def category_names(self) -> list[str]:
        return [c.name for c in self.categories]


@dataclass(frozen=True)
class PlannedCategory:
    kind_label: str
    category: StarterCategory


@dataclass(frozen=True)
class StarterPlan:
    """적용 계획. `skipped`는 이미 있어서 건너뛸 이름(기존 값 그대로)."""

    add: tuple[PlannedCategory, ...]
    skipped: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.add


# --------------------------------------------------------------------------- parsing


def _fail(source: str, message: str) -> ValidationError:
    return ValidationError("starter", f"시작 도우미 데이터({source}) 오류: {message}")


def _text(source: str, data: dict, key: str, label: str, limit: int, *, required: bool = True) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise _fail(source, f"{label}은(는) 글자여야 합니다.")
    value = value.strip()
    if required and not value:
        raise _fail(source, f"{label}이(가) 비어 있습니다.")
    if len(value) > limit:
        raise _fail(source, f"{label}은(는) {limit}자 이하여야 합니다.")
    return value


def _minutes(source: str, name: str, value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_POLICY_MINUTES:
        raise _fail(source, f"'{name}'의 {label}은(는) 0 이상 {MAX_POLICY_MINUTES} 이하 정수(분)여야 합니다.")
    return value


def _multiplier(source: str, name: str, value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):  # 십진수는 문자열로 적는다(부동소수 오차 방지)
        raise _fail(source, f"'{name}'의 연체 1일당 배수는 \"1.5\"처럼 따옴표 안의 숫자로 적어야 합니다.")
    try:
        dec = Decimal(value)
    except InvalidOperation:
        raise _fail(source, f"'{name}'의 연체 1일당 배수가 숫자가 아닙니다.") from None
    if not dec.is_finite() or not 0 < dec <= MAX_LATE_MULTIPLIER:
        raise _fail(source, f"'{name}'의 연체 1일당 배수는 0보다 크고 {MAX_LATE_MULTIPLIER} 이하여야 합니다.")
    return dec


def _category(source: str, data: object) -> StarterCategory:
    if not isinstance(data, dict):
        raise _fail(source, "카테고리 항목 형식이 올바르지 않습니다.")
    unknown = set(data) - _CATEGORY_KEYS
    if unknown:
        raise _fail(source, f"알 수 없는 카테고리 항목: {', '.join(sorted(unknown))}")
    name = _text(source, data, "name", "카테고리 이름", MAX_CATEGORY_NAME)
    if name.casefold() == SYSTEM_CATEGORY_NAME.casefold():
        raise _fail(source, f"'{SYSTEM_CATEGORY_NAME}'는 항상 있는 카테고리라 넣을 수 없습니다.")
    policy = CategoryPolicy(
        turnaround_buffer_minutes=_minutes(
            source, name, data.get("turnaround_buffer_minutes"), "반납 후 준비 시간"),
        grace_minutes=_minutes(source, name, data.get("grace_minutes"), "연체료 없이 기다려 주는 시간"),
        late_multiplier=_multiplier(source, name, data.get("late_multiplier")),
    )
    rule_data = data.get("rate_rule")
    try:
        rule = None if rule_data is None else rate_rule_from_data(rule_data)
    except ValidationError as exc:
        raise _fail(source, f"'{name}'의 요금 규칙: {exc.message}") from None
    return StarterCategory(name=name, policy=policy, rate_rule=rule)


def parse_starter_kind(data: object, source: str = "?") -> StarterKind:
    """데이터 파일 1개(JSON 객체) → 검증된 장비 종류. `source`는 오류 문구용 파일 이름."""
    if not isinstance(data, dict):
        raise _fail(source, "파일 형식이 올바르지 않습니다.")
    if data.get("format") != STARTER_FORMAT_VERSION:
        raise _fail(source, f"지원하지 않는 형식 버전입니다(지원: {STARTER_FORMAT_VERSION}).")
    unknown = set(data) - _KIND_KEYS
    if unknown:
        raise _fail(source, f"알 수 없는 항목: {', '.join(sorted(unknown))}")
    key = _text(source, data, "key", "종류 키", 30)
    if not key.isascii() or not key.replace("_", "").isalnum():
        raise _fail(source, "종류 키는 영문·숫자·밑줄만 쓸 수 있습니다.")
    label = _text(source, data, "label", "종류 이름", 30)
    description = _text(source, data, "description", "설명", 200, required=False)
    order = data.get("order", 0)
    if isinstance(order, bool) or not isinstance(order, int):
        raise _fail(source, "정렬 순서는 정수여야 합니다.")
    raw = data.get("categories")
    if not isinstance(raw, list) or not raw:
        raise _fail(source, "카테고리가 하나 이상 있어야 합니다.")
    if len(raw) > MAX_CATEGORIES_PER_KIND:
        raise _fail(source, f"카테고리는 {MAX_CATEGORIES_PER_KIND}개까지 넣을 수 있습니다.")
    categories = tuple(_category(source, c) for c in raw)
    names = [c.name.casefold() for c in categories]
    if len(set(names)) != len(names):
        raise _fail(source, "같은 이름의 카테고리가 두 번 있습니다.")
    return StarterKind(key=key, label=label, description=description, order=order, categories=categories)


def sort_kinds(kinds: Iterable[StarterKind]) -> list[StarterKind]:
    """정렬 순서 → 키. 키가 겹치면 오류(데이터 파일 실수)."""
    result = sorted(kinds, key=lambda k: (k.order, k.key))
    keys = [k.key for k in result]
    if len(set(keys)) != len(keys):
        raise ValidationError("starter", "시작 도우미 데이터에 같은 종류 키가 두 번 있습니다.")
    return result


# --------------------------------------------------------------------------- planning


def plan_starter(
    kinds: Sequence[StarterKind], selected_keys: Iterable[str], existing_names: Iterable[str]
) -> StarterPlan:
    """고른 종류의 카테고리 중 아직 없는 것만 추가하는 계획(순수 함수).

    `existing_names`: DB의 모든 카테고리 이름(숨김 포함). 이름 비교는 대소문자 무시(DB와 같음).
    """
    selected = set(selected_keys)
    known = {k.key for k in kinds}
    unknown = selected - known
    if unknown:
        raise ValidationError("starter", f"알 수 없는 장비 종류입니다: {', '.join(sorted(unknown))}")
    existing = {n.casefold() for n in existing_names}
    planned: set[str] = set()
    add: list[PlannedCategory] = []
    skipped: list[str] = []
    for kind in sort_kinds(kinds):
        if kind.key not in selected:
            continue
        for category in kind.categories:
            folded = category.name.casefold()
            if folded in planned:  # 앞 종류에서 이미 추가하기로 함
                continue
            if folded in existing:
                skipped.append(category.name)
                existing.discard(folded)  # 같은 이름을 두 번 적지 않도록
                planned.add(folded)
                continue
            planned.add(folded)
            add.append(PlannedCategory(kind_label=kind.label, category=category))
    return StarterPlan(add=tuple(add), skipped=tuple(skipped))
