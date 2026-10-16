"""M4.6 R2: 가게 기본값 → 카테고리 설정 상속 순수 함수."""

from decimal import Decimal

from rentdesk.domain.models import RentalPolicy
from rentdesk.domain.policy import (
    CategoryPolicy,
    buffer_basis,
    buffer_summary,
    effective_policy,
    format_minutes,
)

SHOP = RentalPolicy(grace_minutes=60, late_multiplier=Decimal("1"), turnaround_buffer_minutes=60)


def test_empty_category_uses_shop_values():
    assert effective_policy(SHOP, CategoryPolicy()) == SHOP
    assert effective_policy(SHOP, None) == SHOP


def test_category_values_win_field_by_field():
    cat = CategoryPolicy(late_multiplier=Decimal("1.5"), turnaround_buffer_minutes=0)
    result = effective_policy(SHOP, cat)
    assert result == RentalPolicy(grace_minutes=60, late_multiplier=Decimal("1.5"), turnaround_buffer_minutes=0)
    assert cat.overridden == {"late_multiplier", "turnaround_buffer_minutes"}


def test_zero_is_a_real_override_not_default():
    assert effective_policy(SHOP, CategoryPolicy(grace_minutes=0)).grace_minutes == 0


def test_format_minutes():
    assert [format_minutes(m) for m in (0, 45, 60, 90, 720, 1440)] == [
        "0분", "45분", "1시간", "1시간 30분", "12시간", "24시간"]


def test_buffer_basis_shows_source():
    assert buffer_basis("텐트", CategoryPolicy(turnaround_buffer_minutes=720), SHOP) == "텐트: 준비 12시간(카테고리 설정)"
    assert buffer_basis("카메라", CategoryPolicy(grace_minutes=10), SHOP) == "카메라: 준비 1시간(가게 기본값)"


def test_buffer_summary():
    assert buffer_summary([60, 60]) == "1시간"
    assert buffer_summary([720, 60, 1440]) == "장비별 1시간~24시간"
    assert buffer_summary([]) == "-"
