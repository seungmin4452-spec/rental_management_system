"""CheckoutService / ReturnService (PRD S-05, S-06, 6.1, 6.2, 7.2, 7.3).

현재 시각(clock)은 2026-10-19 09:00 KST. 기본 예약은 10-20 10:00 ~ 10-21 18:00 (32시간 → 2일).
"""

import threading
from decimal import Decimal

import pytest

from rentdesk.domain.errors import InvalidStateError, NotFoundError, ValidationError
from rentdesk.domain.models import (
    ChargeKind,
    ItemStatus,
    MoneyKind,
    OrderStatus,
    PaymentMethod,
    ReturnCondition,
)
from rentdesk.persistence.db import connect
from rentdesk.services.checkout import CheckoutInput, CheckoutService
from rentdesk.services.reservations import ReservationInput
from rentdesk.services.returns import ReturnItemInput
from tests.support import kst

START, DUE = kst(10, 20, 10), kst(10, 21, 18)


@pytest.fixture
def cam(svc):
    return svc.add_asset("CAM-1", rate=30_000, deposit=100_000)


@pytest.fixture
def lens(svc):
    return svc.add_asset("LENS-1", rate=20_000, deposit=50_000, category="렌즈", name="렌즈")


@pytest.fixture
def light(svc):
    return svc.add_asset("LIGHT-1", rate=10_000, deposit=30_000, category="조명", name="조명")


@pytest.fixture
def kim(svc):
    return svc.add_customer("김고객")


@pytest.fixture
def lee(svc):
    return svc.add_customer("이고객")


def _confirm(svc, customer, assets, start=START, due=DUE, **kw):
    return svc.reservations.create_confirmed(
        ReservationInput(customer.id, start, due, [a.id for a in assets], **kw)
    )


def _charges(svc, order_id):
    return [(c.kind, c.amount_won) for c in svc.settlements.get_settlement(order_id).charges]


def _item(order, asset):
    return next(i for i in order.items if i.asset_id == asset.id)


# --------------------------------------------------------------------------- checkout


def test_us03_checkout_all_items_and_charge_once(svc, cam, lens, light, kim):
    order = _confirm(svc, kim, [cam, lens, light], discount_won=10_000)
    out = svc.checkout(order.id, kst(10, 20, 10, 5), note="외관 양호, 배터리 2개")

    assert out.status == OrderStatus.IN_RENTAL
    assert out.actual_checkout_at == kst(10, 20, 10, 5)
    assert out.checkout_note == "외관 양호, 배터리 2개"
    assert {i.status for i in out.items} == {ItemStatus.OUT}
    assert sorted(_charges(svc, order.id)) == sorted([
        (ChargeKind.RENTAL, 60_000), (ChargeKind.RENTAL, 40_000), (ChargeKind.RENTAL, 20_000),
        (ChargeKind.DISCOUNT, -10_000),
    ])
    summary = svc.settlements.summary(order.id)
    assert summary.charge_total_won == 110_000
    assert summary.amount_due_won == 110_000


def test_t07_repeated_checkout_never_duplicates_charges(svc, cam, kim):
    order = _confirm(svc, kim, [cam], discount_won=5_000)
    svc.checkout(order.id, START)
    for _ in range(3):
        with pytest.raises(InvalidStateError) as exc:
            svc.checkout_svc.checkout(order.id)
        assert "이미 출고" in exc.value.message
    assert svc.count("charge_lines") == 2  # RENTAL 1 + DISCOUNT 1
    assert _charges(svc, order.id) == [(ChargeKind.RENTAL, 60_000), (ChargeKind.DISCOUNT, -5_000)]


def test_t07_concurrent_checkout_on_two_connections(db_path, svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    svc.clock.now = kst(10, 20, 10)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker():
        conn = connect(db_path)
        try:
            service = CheckoutService(conn, svc.clock)
            barrier.wait()
            try:
                service.checkout(order.id)
                results.append("ok")
            except InvalidStateError:
                results.append("rejected")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == ["ok", "rejected"]
    assert svc.count("charge_lines") == 1


def test_checkout_with_receipt_and_deposit_records_money(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    svc.checkout(
        order.id, START, rental_receipt_won=60_000, deposit_in_won=100_000,
        payment_method=PaymentMethod.TRANSFER,
    )
    settlement = svc.settlements.get_settlement(order.id)
    assert [(e.kind, e.amount_won, e.payment_method) for e in settlement.entries] == [
        (MoneyKind.RENTAL_RECEIPT, 60_000, PaymentMethod.TRANSFER),
        (MoneyKind.DEPOSIT_IN, 100_000, PaymentMethod.TRANSFER),
    ]
    assert settlement.summary.amount_due_won == 0
    assert settlement.summary.deposit_held_won == 100_000


def test_checkout_preview_shows_amounts_and_blockers(svc, cam, lens, kim):
    order = _confirm(svc, kim, [cam, lens], discount_won=10_000)
    preview = svc.checkout_svc.preview(order.id)  # 현재 10-19 09:00: 시작 전
    assert preview.quote.expected_total_rental_fee_won == 90_000
    assert preview.quote.expected_deposit_won == 150_000
    assert not preview.can_checkout
    assert "시작 시각" in preview.blockers[0]
    svc.clock.now = START
    assert svc.checkout_svc.preview(order.id).can_checkout


@pytest.mark.parametrize(
    ("now", "at", "fragment"),
    [
        (kst(10, 20, 9, 59), None, "시작 시각"),        # 조기 출고
        (kst(10, 21, 18), None, "약정 반납"),          # 약정 기간 경과
        (kst(10, 20, 11), kst(10, 20, 12), "현재 시각"),  # 미래 출고 시각
        (kst(10, 20, 11), kst(10, 20, 9), "시작 시각"),  # 시작 전 시각 입력
    ],
)
def test_checkout_time_window(svc, cam, kim, now, at, fragment):
    order = _confirm(svc, kim, [cam])
    svc.clock.now = now
    with pytest.raises(InvalidStateError) as exc:
        svc.checkout_svc.checkout(order.id, CheckoutInput(checkout_at=at))
    assert fragment in exc.value.message
    assert svc.reservations.get_order(order.id).status == OrderStatus.CONFIRMED
    assert svc.count("charge_lines") == 0


def test_checkout_rejects_draft_canceled_and_unknown(svc, cam, kim):
    draft = svc.reservations.create_draft(ReservationInput(kim.id, START, DUE, [cam.id]))
    svc.clock.now = START
    with pytest.raises(InvalidStateError) as exc:
        svc.checkout_svc.checkout(draft.id)
    assert "확정" in exc.value.message
    svc.reservations.cancel(draft.id)
    with pytest.raises(InvalidStateError):
        svc.checkout_svc.checkout(draft.id)
    with pytest.raises(NotFoundError):
        svc.checkout_svc.checkout(9999)


def test_checkout_rejected_while_asset_still_out_elsewhere(svc, cam, kim, lee):
    svc.set_buffer(0)
    first = _confirm(svc, kim, [cam], kst(10, 20, 10), kst(10, 20, 12))
    second = _confirm(svc, lee, [cam], kst(10, 20, 12), kst(10, 20, 18))
    svc.checkout(first.id, kst(10, 20, 10))
    svc.clock.now = kst(10, 20, 12, 30)  # 첫 예약 반납 지연
    with pytest.raises(InvalidStateError) as exc:
        svc.checkout_svc.checkout(second.id)
    assert first.order_no in exc.value.message
    svc.return_asset(first.id, cam.id, kst(10, 20, 12, 40))
    assert svc.checkout_svc.checkout(second.id).status == OrderStatus.IN_RENTAL


def test_checkout_rejected_during_maintenance_block(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    result = svc.maintenance.create_block(cam.id, kst(10, 19, 12), None, "센서 점검")
    assert [c.order_no for c in result.affected_reservations] == [order.order_no]
    svc.clock.now = START
    with pytest.raises(InvalidStateError) as exc:
        svc.checkout_svc.checkout(order.id)
    assert "정비" in exc.value.message
    svc.maintenance.close_block(result.block.id, kst(10, 20, 9))
    assert svc.checkout_svc.checkout(order.id).status == OrderStatus.IN_RENTAL


def test_checkout_input_validation(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    svc.clock.now = START
    with pytest.raises(ValidationError):
        svc.checkout_svc.checkout(order.id, CheckoutInput(rental_receipt_won=-1))
    with pytest.raises(ValidationError):
        svc.checkout_svc.checkout(order.id, CheckoutInput(deposit_in_won=1.5))
    with pytest.raises(ValidationError):
        svc.checkout_svc.checkout(order.id, CheckoutInput(note="x" * 1001))
    with pytest.raises(ValidationError):
        svc.checkout_svc.checkout(order.id, CheckoutInput(checkout_at=START.replace(tzinfo=None)))
    assert svc.reservations.get_order(order.id).status == OrderStatus.CONFIRMED


def test_checkout_uses_snapshot_not_current_rate(svc, cam, kim):
    from rentdesk.services.assets import AssetInput

    order = _confirm(svc, kim, [cam])
    svc.assets.update_asset(
        cam.id, AssetInput(name=cam.name, category_name="카메라", daily_rate_won=99_000,
                           deposit_won=1, asset_code=cam.asset_code),
    )
    svc.checkout(order.id, START)
    assert _charges(svc, order.id) == [(ChargeKind.RENTAL, 60_000)]


# --------------------------------------------------------------------------- returns


def test_t08_partial_return_keeps_others_out(svc, cam, lens, light, kim):
    order = _confirm(svc, kim, [cam, lens, light])
    svc.checkout(order.id, START)
    result = svc.return_asset(order.id, cam.id, kst(10, 21, 9))

    after = result.order
    assert after.status == OrderStatus.IN_RENTAL
    assert not result.completed
    assert _item(after, cam).status == ItemStatus.RETURNED
    assert _item(after, cam).actual_return_at == kst(10, 21, 9)
    assert _item(after, cam).return_condition == ReturnCondition.GOOD
    assert {_item(after, lens).status, _item(after, light).status} == {ItemStatus.OUT}
    assert [i.asset_id for i in svc.returns.returnable_items(order.id)] == [lens.id, light.id]


def test_t09_last_return_completes_but_settlement_separate(svc, cam, lens, kim):
    order = _confirm(svc, kim, [cam, lens])
    svc.checkout(order.id, START, rental_receipt_won=50_000, deposit_in_won=150_000)
    svc.return_asset(order.id, cam.id, kst(10, 21, 9))
    svc.clock.now = kst(10, 21, 10)
    lens_item = _item(svc.reservations.get_order(order.id), lens)
    result = svc.returns.return_items(order.id, [ReturnItemInput(item_id=lens_item.id)])

    assert result.completed
    assert result.order.status == OrderStatus.COMPLETED
    assert _item(result.order, lens).actual_return_at == kst(10, 21, 10)  # 기본값 = 현재 시각
    summary = svc.settlements.summary(order.id)
    assert summary.amount_due_won == 100_000 - 50_000
    assert summary.deposit_held_won == 150_000
    assert not summary.is_settled
    assert [u.order_id for u in svc.settlements.unsettled_orders()] == [order.id]


def test_return_several_items_at_once(svc, cam, lens, kim):
    order = _confirm(svc, kim, [cam, lens])
    out = svc.checkout(order.id, START)
    svc.clock.now = kst(10, 21, 12)
    result = svc.returns.return_items(
        order.id, [ReturnItemInput(item_id=i.id) for i in out.items]
    )
    assert result.completed
    assert len(result.lines) == 2


def test_us05_t10_return_exactly_at_grace_end_no_late_fee(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    svc.checkout(order.id, START)
    result = svc.return_asset(order.id, cam.id, kst(10, 21, 19, 0))
    assert result.lines[0].late_fee_won == 0
    assert "연체 없음" in result.lines[0].basis
    assert ChargeKind.LATE not in [k for k, _ in _charges(svc, order.id)]


def test_us05_t11_one_second_past_grace_charges_one_day(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    svc.checkout(order.id, START)
    result = svc.return_asset(order.id, cam.id, kst(10, 21, 19, 0, 1))
    assert result.lines[0].late_fee_won == 30_000
    assert (ChargeKind.LATE, 30_000) in _charges(svc, order.id)


def test_late_fee_uses_item_snapshots_not_current_settings(svc, cam, kim):
    order = _confirm(svc, kim, [cam])  # 확정 시 유예 60분, 배수 1
    svc.settings.update_policy(
        grace_minutes=0, late_multiplier=Decimal("2"), turnaround_buffer_minutes=60
    )
    svc.checkout(order.id, START)
    # 19:01 + 24시간 = 2일 연체(유예 60분 스냅샷 기준)
    result = svc.return_asset(order.id, cam.id, kst(10, 22, 19, 1))
    assert result.lines[0].late_fee_won == 60_000


def test_late_fee_preview_does_not_write(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    out = svc.checkout(order.id, START)
    svc.clock.now = kst(10, 22, 20)
    preview = svc.returns.preview_late_fee(order.id, out.items[0].id)
    assert preview.late.late_days == 2
    assert preview.late.late_fee_won == 60_000
    assert "2일" in preview.basis
    assert svc.count("charge_lines") == 1
    assert svc.reservations.get_order(order.id).items[0].status == ItemStatus.OUT


def test_t12_second_return_rejected_no_double_charge(svc, cam, lens, kim):
    order = _confirm(svc, kim, [cam, lens])
    svc.checkout(order.id, START)
    svc.return_asset(order.id, cam.id, kst(10, 22, 20), damage_fee_won=15_000)
    before = _charges(svc, order.id)

    with pytest.raises(InvalidStateError) as exc:
        svc.return_asset(order.id, cam.id, kst(10, 22, 21), damage_fee_won=15_000)
    assert "이미 반납" in exc.value.message
    assert _charges(svc, order.id) == before
    late = [a for k, a in before if k == ChargeKind.LATE]
    damage = [a for k, a in before if k == ChargeKind.DAMAGE]
    assert late == [60_000] and damage == [15_000]
    assert svc.reservations.get_order(order.id).status == OrderStatus.IN_RENTAL


def test_t12_retry_after_completion_rejected(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    svc.checkout(order.id, START)
    svc.return_asset(order.id, cam.id, kst(10, 21, 12))
    with pytest.raises(InvalidStateError) as exc:
        svc.return_asset(order.id, cam.id, kst(10, 21, 13))
    assert "이미 반납" in exc.value.message


def test_return_time_validation(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    out = svc.checkout(order.id, kst(10, 20, 11))
    svc.clock.now = kst(10, 21, 12)
    item_id = out.items[0].id
    with pytest.raises(ValidationError) as exc:  # 출고 전 시각
        svc.returns.return_items(order.id, [ReturnItemInput(item_id, returned_at=kst(10, 20, 10, 59))])
    assert "출고 시각" in exc.value.message
    with pytest.raises(ValidationError) as exc:  # 미래 시각
        svc.returns.return_items(order.id, [ReturnItemInput(item_id, returned_at=kst(10, 21, 12, 1))])
    assert "현재 시각" in exc.value.message
    assert svc.reservations.get_order(order.id).items[0].status == ItemStatus.OUT


def test_return_input_validation(svc, cam, lens, kim, lee):
    order = _confirm(svc, kim, [cam])
    other = _confirm(svc, lee, [lens])
    out = svc.checkout(order.id, START)
    other_out = svc.checkout(other.id, START)
    item_id = out.items[0].id
    with pytest.raises(ValidationError):
        svc.returns.return_items(order.id, [])
    with pytest.raises(ValidationError):
        svc.returns.return_items(order.id, [ReturnItemInput(item_id), ReturnItemInput(item_id)])
    with pytest.raises(ValidationError):  # 다른 예약의 품목
        svc.returns.return_items(order.id, [ReturnItemInput(other_out.items[0].id)])
    with pytest.raises(ValidationError):
        svc.returns.return_items(order.id, [ReturnItemInput(item_id, damage_fee_won=-1)])
    with pytest.raises(ValidationError):
        svc.returns.return_items(order.id, [ReturnItemInput(item_id, condition="BROKEN")])


def test_return_rejected_for_unchecked_out_order(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    with pytest.raises(InvalidStateError) as exc:
        svc.returns.return_items(order.id, [ReturnItemInput(order.items[0].id)])
    assert "출고되지 않은" in exc.value.message


def test_us06_damaged_return_creates_block_and_warns_future_reservation(svc, cam, lens, kim, lee):
    order = _confirm(svc, kim, [cam, lens])
    future = _confirm(svc, lee, [lens], kst(10, 25, 10), kst(10, 26, 10))
    svc.checkout(order.id, START)

    result = svc.return_asset(
        order.id, lens.id, kst(10, 21, 12), condition=ReturnCondition.MAINTENANCE_REQUIRED,
        damage_fee_won=50_000, note="렌즈 앞 유리 파손", maintenance_reason="렌즈 파손 수리",
    )
    assert len(result.maintenance_blocks) == 1
    block = result.maintenance_blocks[0]
    assert block.asset_id == lens.id and block.start_at == kst(10, 21, 12) and block.end_at is None
    assert block.reason == "렌즈 파손 수리"
    assert [c.order_no for c in result.affected_reservations] == [future.order_no]
    # 자동 취소하지 않음
    assert svc.reservations.get_order(future.id).status == OrderStatus.CONFIRMED
    # 블록 해제 전 신규 확정 불가
    from rentdesk.domain.errors import ConflictError

    with pytest.raises(ConflictError):
        _confirm(svc, lee, [lens], kst(10, 28, 10), kst(10, 29, 10))
    item = _item(result.order, lens)
    assert item.return_condition == ReturnCondition.MAINTENANCE_REQUIRED
    assert item.return_note == "렌즈 앞 유리 파손"
    assert (ChargeKind.DAMAGE, 50_000) in _charges(svc, order.id)


def test_maintenance_block_default_reason(svc, cam, kim):
    order = _confirm(svc, kim, [cam])
    svc.checkout(order.id, START)
    result = svc.return_asset(
        order.id, cam.id, kst(10, 21, 12), condition=ReturnCondition.MAINTENANCE_REQUIRED
    )
    assert order.order_no in result.maintenance_blocks[0].reason


def test_late_return_warns_followup_within_buffer(svc, cam, kim, lee):
    order = _confirm(svc, kim, [cam], kst(10, 20, 10), kst(10, 21, 10))
    follow = _confirm(svc, lee, [cam], kst(10, 21, 11), kst(10, 22, 10))  # 버퍼 60분 딱 맞춤
    svc.checkout(order.id, kst(10, 20, 10))
    result = svc.return_asset(order.id, cam.id, kst(10, 21, 10, 30))  # 30분 지연
    assert [c.order_no for c in result.affected_reservations] == [follow.order_no]
    assert svc.reservations.get_order(follow.id).status == OrderStatus.CONFIRMED


def test_early_return_frees_asset_after_buffer(svc, cam, kim, lee):
    order = _confirm(svc, kim, [cam])
    svc.checkout(order.id, START)
    result = svc.return_asset(order.id, cam.id, kst(10, 20, 15))
    assert result.affected_reservations == ()
    # 반납 15:00 + 버퍼 60분 이후 재예약 가능 (조기 반납 대여료 환급은 하지 않음)
    ok = _confirm(svc, lee, [cam], kst(10, 20, 16), kst(10, 20, 20))
    assert ok.status == OrderStatus.CONFIRMED
    assert _charges(svc, order.id) == [(ChargeKind.RENTAL, 60_000)]
