"""M4.6 R4: 세트 도메인 순수 함수 — 할인 계산·한도, 체크리스트, 대체 규칙, 전개 계획."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.kits import (
    MAX_CHECKLIST_ITEMS,
    KitDiscount,
    KitDiscountKind,
    OrderKit,
    check_kit_pick,
    clean_checklist,
    kit_discount_won,
    missing_items,
    plan_kit,
    validate_checks,
    validate_kit_discount,
)
from rentdesk.domain.pricing import KitQuoteInput, PricedItem, quote_order
from rentdesk.domain.rates import RateRule
from tests.support import kst

FIXED_5000 = KitDiscount(KitDiscountKind.FIXED, 5_000)
TEN_PERCENT = KitDiscount(KitDiscountKind.PERCENT, 0, Decimal("10"))
START = kst(10, 20, 10)


# --------------------------------------------------------------------------- 할인 검증·계산


def test_fixed_discount_validation():
    assert validate_kit_discount("FIXED", 3_000) == KitDiscount(KitDiscountKind.FIXED, 3_000)
    assert validate_kit_discount(KitDiscountKind.FIXED, 0).is_none
    for bad in (-1, 1.5, "1000", True):
        with pytest.raises(ValidationError):
            validate_kit_discount("FIXED", bad)


@pytest.mark.parametrize("value", ["0.01", "10", "33.33", "100", Decimal("50")])
def test_percent_discount_accepts_range(value):
    d = validate_kit_discount("PERCENT", 0, value)
    assert d.kind == KitDiscountKind.PERCENT and d.percent == Decimal(str(value)) and d.won == 0


@pytest.mark.parametrize("value", ["0", "-1", "100.01", "abc", "NaN", "Infinity", "10.005", None, True])
def test_percent_discount_rejects(value):
    with pytest.raises(ValidationError):
        validate_kit_discount("PERCENT", 0, value)


def test_unknown_discount_kind_rejected():
    with pytest.raises(ValidationError):
        validate_kit_discount("HALF", 0)


def test_discount_labels():
    assert KitDiscount().label == "없음"
    assert FIXED_5000.label == "5,000원"
    assert validate_kit_discount("PERCENT", 0, "12.50").label == "12.5%"


def test_fixed_discount_capped_by_kit_fee():
    assert kit_discount_won(FIXED_5000, 30_000) == 5_000
    assert kit_discount_won(FIXED_5000, 4_000) == 4_000  # 대체·제외로 세트 요금이 줄면 그만큼만
    assert kit_discount_won(FIXED_5000, 0) == 0


def test_percent_discount_rounds_half_up():
    assert kit_discount_won(TEN_PERCENT, 30_000) == 3_000
    assert kit_discount_won(TEN_PERCENT, 12_345) == 1_235  # 1,234.5 → 1,235
    assert kit_discount_won(TEN_PERCENT, 12_344) == 1_234
    assert kit_discount_won(KitDiscount(KitDiscountKind.PERCENT, 0, Decimal("100")), 9_999) == 9_999


# --------------------------------------------------------------------------- 견적


def _items(kit_key=1):
    return [
        PricedItem(10_000, 50_000, kit_key=kit_key),
        PricedItem(20_000, 50_000, kit_key=kit_key),
        PricedItem(30_000, 0),
    ]


def test_quote_applies_kit_discount_on_kit_items_only():
    q = quote_order(_items(), START, START + timedelta(days=2), 0, [KitQuoteInput(1, "카메라 세트", TEN_PERCENT)])
    assert q.base_rental_fee_won == 120_000
    assert [(k.name, k.amount_won) for k in q.kit_discounts] == [("카메라 세트", 6_000)]  # (2+4만) × 10%
    assert q.expected_total_rental_fee_won == 114_000 and q.total_discount_won == 6_000


def test_quote_order_and_kit_discounts_are_separate_lines():
    q = quote_order(_items(), START, START + timedelta(days=1), 2_000, [KitQuoteInput(1, "세트", FIXED_5000)])
    assert (q.discount_won, q.kit_discount_won) == (2_000, 5_000)
    assert q.expected_total_rental_fee_won == 60_000 - 7_000


def test_total_discount_cannot_exceed_base():
    kits = [KitQuoteInput(1, "세트", KitDiscount(KitDiscountKind.FIXED, 30_000))]
    q = quote_order(_items(), START, START + timedelta(days=1), 30_000, kits)
    assert q.expected_total_rental_fee_won == 0
    with pytest.raises(ValidationError) as exc:
        quote_order(_items(), START, START + timedelta(days=1), 30_001, kits)
    assert "세트 할인(30,000원)" in exc.value.message


def test_kit_without_items_gives_no_discount():
    q = quote_order(_items(kit_key=None), START, START + timedelta(days=1), 0, [KitQuoteInput(1, "빈 세트", FIXED_5000)])
    assert q.kit_discount_won == 0


def test_kit_discount_uses_long_rate_fee():
    weekly = RateRule(weekly_enabled=True)
    items = [PricedItem(10_000, 0, weekly, kit_key=1)]
    q = quote_order(items, START, START + timedelta(days=7), 0, [KitQuoteInput(1, "세트", TEN_PERCENT)])
    assert q.base_rental_fee_won == 50_000 and q.kit_discount_won == 5_000  # 주 단가 기준 10%


# --------------------------------------------------------------------------- 체크리스트


def test_clean_checklist_trims_and_skips_blank_lines():
    assert clean_checklist([" 배터리 2개 ", "", "충전기", "   "]) == ("배터리 2개", "충전기")
    assert clean_checklist([]) == ()


def test_clean_checklist_rejects_bad_input():
    with pytest.raises(ValidationError):
        clean_checklist("배터리")
    with pytest.raises(ValidationError):
        clean_checklist(["가방", "가방 "])
    with pytest.raises(ValidationError):
        clean_checklist(["Bag", "bag"])
    with pytest.raises(ValidationError):
        clean_checklist(["가" * 101])
    with pytest.raises(ValidationError):
        clean_checklist([f"부속 {n}" for n in range(MAX_CHECKLIST_ITEMS + 1)])
    with pytest.raises(ValidationError):
        clean_checklist([1])


def test_checks_validation_and_missing_items():
    checklist = ("배터리", "충전기", "가방")
    assert validate_checks(checklist, [True, False, True]) == (True, False, True)
    with pytest.raises(ValidationError):
        validate_checks(checklist, [True, False])
    with pytest.raises(ValidationError):
        validate_checks(checklist, [1, 0, 1])
    assert missing_items(checklist, (True, False, False)) == ["충전기", "가방"]
    assert missing_items(checklist, None) == []
    kit = OrderKit(1, 1, 1, "세트", KitDiscount(), checklist, checkout_checks=(True, True, False))
    assert kit.checkout_missing == ["가방"] and kit.return_missing == []


# --------------------------------------------------------------------------- 대체 규칙


CAMERA, LENS = 1, 2


def test_pick_with_all_members_ok():
    check_kit_pick({10: CAMERA, 11: LENS}, {10: CAMERA, 11: LENS})
    check_kit_pick({10: CAMERA, 11: LENS}, {10: CAMERA})  # 빼고 진행


def test_pick_substitute_same_category_ok():
    check_kit_pick({10: CAMERA, 11: LENS}, {20: CAMERA, 11: LENS})


def test_pick_substitute_other_category_rejected():
    with pytest.raises(ValidationError):
        check_kit_pick({10: CAMERA, 11: LENS}, {21: LENS, 11: LENS})


def test_pick_more_substitutes_than_missing_rejected():
    with pytest.raises(ValidationError):
        check_kit_pick({10: CAMERA, 11: LENS}, {10: CAMERA, 20: CAMERA, 11: LENS})


def test_empty_pick_rejected():
    with pytest.raises(ValidationError) as exc:
        check_kit_pick({10: CAMERA}, {}, kit_name="카메라 세트")
    assert "카메라 세트" in exc.value.message


# --------------------------------------------------------------------------- 전개 계획


def test_plan_all_available_keeps_members():
    slots = plan_kit([(10, CAMERA), (11, LENS)], {10: CAMERA, 11: LENS, 20: CAMERA})
    assert [s.default_choice for s in slots] == [10, 11]
    assert slots[0].candidates == (20,)


def test_plan_unavailable_member_suggests_same_category_substitute():
    slots = plan_kit([(10, CAMERA), (11, LENS)], {11: LENS, 20: CAMERA, 21: LENS})
    assert not slots[0].available and slots[0].candidates == (20,) and slots[0].default_choice == 20
    assert slots[1].default_choice == 11


def test_plan_without_candidates_drops_member():
    slots = plan_kit([(10, CAMERA), (11, LENS)], {11: LENS})
    assert slots[0].default_choice is None


def test_plan_does_not_reuse_substitute_twice():
    slots = plan_kit([(10, CAMERA), (11, CAMERA)], {20: CAMERA})
    assert [s.default_choice for s in slots] == [20, None]
    assert slots[1].candidates == ()


def test_plan_moves_individually_taken_member_and_skips_taken_candidates():
    slots = plan_kit([(10, CAMERA)], {10: CAMERA, 20: CAMERA}, taken=[10, 20])
    assert slots[0].available and slots[0].default_choice == 10 and slots[0].candidates == ()


def test_plan_locked_member_needs_substitute():
    slots = plan_kit([(10, CAMERA)], {10: CAMERA, 20: CAMERA}, locked=[10])
    assert not slots[0].available and slots[0].default_choice == 20
