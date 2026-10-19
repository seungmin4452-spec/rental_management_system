"""M4.6 R5: 반납 추가 비용(파손·분실·세척·구성품 누락·기타) 청구·정산 합계·보고서 표기."""

from datetime import date

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.extra_charges import ExtraChargeReason, reason_of
from rentdesk.domain.models import ChargeKind, ItemStatus
from rentdesk.services.kits import KitInput, KitPick
from rentdesk.services.reservations import ReservationInput
from rentdesk.services.returns import ReturnItemInput
from tests.support import kst

R = ExtraChargeReason
START, DUE = kst(10, 20, 10), kst(10, 21, 10)


@pytest.fixture
def out_order(svc):
    assets = [svc.add_asset(f"CAM-{n}", rate=30_000) for n in range(1, 6)]
    order = svc.book(svc.add_customer().id, [a.id for a in assets], START, DUE)
    svc.checkout(order.id, START)
    svc.clock.now = kst(10, 21, 9)
    return order


def _damage_lines(svc, order_id):
    return [c for c in svc.settlements.get_settlement(order_id).charges if c.kind == ChargeKind.DAMAGE]


def test_each_reason_charges_and_sums_into_settlement(svc, out_order):
    amounts = {R.DAMAGE: 50_000, R.LOSS: 80_000, R.CLEANING: 10_000, R.MISSING_PART: 5_000,
               R.OTHER: 3_000}
    items = sorted(out_order.items, key=lambda i: i.asset_code)
    before = svc.settlements.summary(out_order.id).charge_total_won
    result = svc.returns.return_items(out_order.id, [
        ReturnItemInput(item.id, extra_charge_reason=reason, damage_fee_won=amount,
                        extra_charge_memo="포장 박스 훼손" if reason == R.OTHER else None)
        for item, (reason, amount) in zip(items, amounts.items())
    ])
    assert [line.extra_charge_reason for line in result.lines] == list(amounts)

    lines = _damage_lines(svc, out_order.id)
    assert {reason_of(c.description): c.amount_won for c in lines} == amounts
    assert {c.description for c in lines} >= {"CAM-3 추가 비용(세척)", "CAM-5 추가 비용(기타): 포장 박스 훼손"}
    summary = svc.settlements.summary(out_order.id)
    assert summary.damage_charges_won == sum(amounts.values())
    assert summary.charge_total_won == before + sum(amounts.values())
    assert summary.amount_due_won == summary.charge_total_won


def test_default_reason_is_damage_and_zero_amount_has_no_line(svc, out_order):
    a, b = out_order.items[:2]
    result = svc.returns.return_items(out_order.id, [
        ReturnItemInput(a.id, damage_fee_won=20_000, extra_charge_memo="액정 깨짐"),
        ReturnItemInput(b.id, extra_charge_reason=R.CLEANING, extra_charge_memo="무시됨"),
    ])
    assert [line.extra_charge_reason for line in result.lines] == [R.DAMAGE, None]
    assert [c.description for c in _damage_lines(svc, out_order.id)] == [
        f"{a.asset_code} 추가 비용(파손): 액정 깨짐"
    ]


@pytest.mark.parametrize("data, field", [
    (dict(extra_charge_reason="WASH", damage_fee_won=1_000), "extra_charge_reason"),
    (dict(extra_charge_reason=R.OTHER, damage_fee_won=1_000), "extra_charge_memo"),
    (dict(extra_charge_memo="가" * 201, damage_fee_won=1_000), "extra_charge_memo"),
    (dict(damage_fee_won=-1), "damage_fee_won"),
])
def test_invalid_input_rejected_without_changes(svc, out_order, data, field):
    item = out_order.items[0]
    with pytest.raises(ValidationError) as exc:
        svc.returns.return_items(out_order.id, [ReturnItemInput(item.id, **data)])
    assert exc.value.field == field
    after = svc.reservations.get_order(out_order.id)
    assert all(i.status == ItemStatus.OUT for i in after.items)
    assert _damage_lines(svc, out_order.id) == []


def test_negative_amount_message_says_extra_charge(svc, out_order):
    with pytest.raises(ValidationError, match="추가 비용"):
        svc.returns.return_items(out_order.id, [ReturnItemInput(out_order.items[0].id, damage_fee_won=-1)])


def _kit_order(svc):
    cam = svc.add_asset("CAM-1", rate=30_000)
    lens = svc.add_asset("LENS-1", rate=10_000, category="렌즈", name="렌즈")
    kit = svc.kits.create_kit(KitInput(name="카메라 세트", asset_ids=[cam.id, lens.id],
                                       checklist=["배터리", "충전기", "가방"]))
    order = svc.reservations.create_confirmed(ReservationInput(
        customer_id=svc.add_customer().id, start_at=START, due_at=DUE,
        asset_ids=[cam.id, lens.id], kits=[KitPick(kit.id, [cam.id, lens.id])],
    ))
    svc.checkout(order.id, START, kit_checks={order.kits[0].id: [True, True, True]})
    svc.clock.now = kst(10, 21, 9)
    return order


def test_missing_part_without_memo_uses_kit_missing_items(svc):
    order = _kit_order(svc)
    kit_id = order.kits[0].id
    item = next(i for i in order.items if i.asset_code == "CAM-1")
    svc.returns.return_items(
        order.id, [ReturnItemInput(item.id, extra_charge_reason=R.MISSING_PART, damage_fee_won=15_000)],
        kit_checks={kit_id: [False, True, False]},
    )
    assert [c.description for c in _damage_lines(svc, order.id)] == [
        "CAM-1 추가 비용(구성품 누락): 카메라 세트: 배터리, 가방"
    ]


def test_missing_part_memo_given_is_kept_and_no_kit_check_means_plain(svc):
    order = _kit_order(svc)
    cam, lens = sorted(order.items, key=lambda i: i.asset_code)
    svc.returns.return_items(order.id, [
        ReturnItemInput(cam.id, extra_charge_reason=R.MISSING_PART, damage_fee_won=1_000,
                        extra_charge_memo="충전 케이블"),
        ReturnItemInput(lens.id, extra_charge_reason=R.MISSING_PART, damage_fee_won=2_000),
    ])  # 구성품 확인 결과를 안 넘기면 자동 메모 없음
    assert sorted(c.description for c in _damage_lines(svc, order.id)) == [
        "CAM-1 추가 비용(구성품 누락): 충전 케이블", "LENS-1 추가 비용(구성품 누락)",
    ]


def test_settlement_report_shows_extra_charge_column_and_total(svc, out_order):
    a, b = out_order.items[:2]
    svc.returns.return_items(out_order.id, [
        ReturnItemInput(a.id, extra_charge_reason=R.CLEANING, damage_fee_won=10_000),
        ReturnItemInput(b.id, extra_charge_reason=R.LOSS, damage_fee_won=70_000),
    ])
    t = svc.reports.settlement_report(date(2026, 10, 1), date(2026, 10, 31))
    names = [c.header for c in t.columns]
    assert "파손비" not in names
    col = names.index("추가 비용")
    assert names[col - 1] == "청구 합계"
    assert t.rows[0][col] == 80_000
    assert ("추가 비용 합계", 80_000) in t.totals
    assert any("구성품 누락" in f for f in t.filters)
