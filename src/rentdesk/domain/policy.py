"""운영 설정의 2단계 상속: 가게 기본값 → 카테고리 설정 (MULTI_INDUSTRY_PLAN R2).

카테고리 값이 비어 있으면(None) 가게 기본값을 쓴다. 장비별 설정은 두지 않는다.
예약 확정 시 품목마다 여기서 구한 실제 적용값을 스냅샷으로 저장한다.
"""

from __future__ import annotations

from rentdesk.domain.models import CategoryPolicy, RentalPolicy
from rentdesk.domain.rates import RateRule, rate_rule_summary

__all__ = [
    "CategoryPolicy", "buffer_basis", "buffer_summary", "effective_policy", "effective_rate_rule",
    "format_minutes", "policy_basis",
]

POLICY_FIELD_LABELS = {
    "turnaround_buffer_minutes": "반납 후 준비 시간",
    "grace_minutes": "연체료 없이 기다려 주는 시간",
    "late_multiplier": "연체 1일당 일 대여료의 배수",
}


def effective_policy(shop: RentalPolicy, category: CategoryPolicy | None) -> RentalPolicy:
    """카테고리 값이 있으면 그 값, 없으면 가게 값."""
    if category is None:
        return shop
    return RentalPolicy(
        grace_minutes=shop.grace_minutes if category.grace_minutes is None else category.grace_minutes,
        late_multiplier=(
            shop.late_multiplier if category.late_multiplier is None else category.late_multiplier
        ),
        turnaround_buffer_minutes=(
            shop.turnaround_buffer_minutes
            if category.turnaround_buffer_minutes is None
            else category.turnaround_buffer_minutes
        ),
    )


def effective_rate_rule(shop: RateRule, category: RateRule | None) -> RateRule:
    """요금 규칙(R3)은 항목별이 아니라 통째로 덮어쓴다: 카테고리 규칙이 있으면 그것, 없으면 가게 규칙."""
    return shop if category is None else category


def format_minutes(minutes: int) -> str:
    """60 → "1시간", 90 → "1시간 30분", 1440 → "24시간", 45 → "45분"."""
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}시간 {rest}분"
    if hours:
        return f"{hours}시간"
    return f"{rest}분"


def buffer_basis(category_name: str, category: CategoryPolicy | None, shop: RentalPolicy) -> str:
    """계산 근거 문구: "텐트: 준비 12시간(카테고리 설정)" / "카메라: 준비 1시간(가게 기본값)"."""
    minutes = effective_policy(shop, category).turnaround_buffer_minutes
    source = (
        "카테고리 설정"
        if category is not None and category.turnaround_buffer_minutes is not None
        else "가게 기본값"
    )
    return f"{category_name}: 준비 {format_minutes(minutes)}({source})"


def buffer_summary(buffer_minutes: list[int] | tuple[int, ...]) -> str:
    """품목별 준비 시간 요약: 모두 같으면 "1시간", 다르면 "장비별 1시간~12시간"."""
    values = sorted(set(buffer_minutes))
    if not values:
        return "-"
    if len(values) == 1:
        return format_minutes(values[0])
    return f"장비별 {format_minutes(values[0])}~{format_minutes(values[-1])}"


def policy_basis(
    category_name: str,
    category: CategoryPolicy | None,
    shop: RentalPolicy,
    rate_rule: RateRule | None = None,
) -> str:
    """예약 마법사 근거 문구: 준비 시간 + 카테고리가 바꾼 연체 기준·요금 규칙.

    예: "텐트: 준비 12시간(카테고리 설정)", "공구: 준비 1시간(가게 기본값) · 연체 1.5배(카테고리 설정)",
    "캠핑: … · 요금 날짜 기준(1박2일 = 1일) · 주말(…)(카테고리 설정)".
    """
    text = buffer_basis(category_name, category, shop)
    if rate_rule is not None:
        text += f" · 요금 {rate_rule_summary(rate_rule)}(카테고리 설정)"
    if category is None:
        return text
    if category.grace_minutes is not None:
        text += f" · 연체료 없이 기다려 주는 시간 {format_minutes(category.grace_minutes)}(카테고리 설정)"
    if category.late_multiplier is not None:
        text += f" · 연체 1일당 {category.late_multiplier.normalize():f}배(카테고리 설정)"
    return text
