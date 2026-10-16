"""SettlementService (PRD 7.4, S-07, T-15~T-17) 및 T-24 원자성(예외 주입 롤백)."""

import sqlite3

import pytest

from rentdesk.domain.errors import InvalidStateError, NotFoundError, ValidationError
from rentdesk.domain.models import ItemStatus, MoneyKind, OrderStatus, PaymentMethod, ReturnCondition
from rentdesk.persistence import repositories as repo
from rentdesk.services.checkout import CheckoutInput
from rentdesk.services.reservations import ReservationInput
from rentdesk.services.returns import ReturnItemInput
from rentdesk.services.settlements import MoneyInput
from tests.support import kst

START, DUE = kst(10, 20, 10), kst(10, 21, 10)  # 24시간 → 1일


@pytest.fixture
def cam(svc):
    return svc.add_asset("CAM-1", rate=30_000, deposit=100_000)


@pytest.fixture
def lens(svc):
    return svc.add_asset("LENS-1", rate=20_000, deposit=50_000, category="렌즈", name="렌즈")


@pytest.fixture
def kim(svc):
    return svc.add_customer("김고객")


@pytest.fixture
def out_order(svc, cam, kim):
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id]))
    return svc.checkout(order.id, START)


def _rec(svc, order, kind, amount, **kw):
    return svc.settlements.record(order.id, MoneyInput(kind, amount, **kw))


# --------------------------------------------------------------------------- T-15 ~ T-17


def test_t15_deposit_receive_apply_return(svc, out_order):
    _rec(svc, out_order, MoneyKind.DEPOSIT_IN, 100_000, payment_method=PaymentMethod.CASH)
    _rec(svc, out_order, MoneyKind.DEPOSIT_APPLIED, 30_000)
    _rec(svc, out_order, MoneyKind.DEPOSIT_OUT, 70_000, payment_method=PaymentMethod.CASH)
    s = svc.settlements.summary(out_order.id)
    assert s.deposit_held_won == 0
    assert s.net_deposit_applied_won == 30_000
    assert s.amount_due_won == 0
    assert s.is_settled


def test_t16_deposit_return_over_held_rejected_ledger_unchanged(svc, out_order):
    _rec(svc, out_order, MoneyKind.DEPOSIT_IN, 100_000)
    before = svc.count("money_entries")
    with pytest.raises(ValidationError) as exc:
        _rec(svc, out_order, MoneyKind.DEPOSIT_OUT, 100_001)
    assert "보유잔액" in exc.value.message
    assert svc.count("money_entries") == before
    assert svc.settlements.summary(out_order.id).deposit_held_won == 100_000


def test_t17_refund_over_received_rejected(svc, out_order):
    _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 20_000)
    with pytest.raises(ValidationError):
        _rec(svc, out_order, MoneyKind.RENTAL_REFUND, 20_001)
    _rec(svc, out_order, MoneyKind.RENTAL_REFUND, 20_000)
    assert svc.settlements.summary(out_order.id).net_rental_receipts_won == 0
    with pytest.raises(ValidationError):
        _rec(svc, out_order, MoneyKind.RENTAL_REFUND, 1)


def test_deposit_and_rental_are_tracked_separately(svc, out_order):
    _rec(svc, out_order, MoneyKind.DEPOSIT_IN, 100_000)
    s = svc.settlements.summary(out_order.id)
    assert s.amount_due_won == 30_000  # 보증금 수령은 대여료 미수를 줄이지 않는다
    assert s.deposit_held_won == 100_000
    with pytest.raises(ValidationError):  # 보증금을 대여료 환급으로 빼갈 수 없음
        _rec(svc, out_order, MoneyKind.RENTAL_REFUND, 1)


def test_over_application_rejected(svc, out_order):
    _rec(svc, out_order, MoneyKind.DEPOSIT_IN, 100_000)
    with pytest.raises(ValidationError):
        _rec(svc, out_order, MoneyKind.DEPOSIT_APPLIED, 30_001)  # 미수 30,000 초과
    _rec(svc, out_order, MoneyKind.DEPOSIT_APPLIED, 30_000)
    with pytest.raises(ValidationError):
        _rec(svc, out_order, MoneyKind.DEPOSIT_APPLIED, 1)  # 미수 없음


# --------------------------------------------------------------------------- 역거래 / 입력 규칙


def test_correction_by_reversal_entry(svc, out_order):
    wrong = _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 300_000, note="0 하나 더 입력")
    fix = _rec(
        svc, out_order, MoneyKind.RENTAL_REFUND, 270_000, related_entry_id=wrong.id, note="정정"
    )
    assert fix.related_entry_id == wrong.id
    with pytest.raises(ValidationError):  # 원거래의 남은 금액(30,000) 초과
        _rec(svc, out_order, MoneyKind.RENTAL_REFUND, 30_001, related_entry_id=wrong.id)
    s = svc.settlements.summary(out_order.id)
    assert s.net_rental_receipts_won == 30_000 and s.amount_due_won == 0
    # 원장 행은 수정·삭제 불가
    with pytest.raises(sqlite3.IntegrityError):
        svc.conn.execute("UPDATE money_entries SET amount_won = 1 WHERE id = ?", (wrong.id,))
    with pytest.raises(sqlite3.IntegrityError):
        svc.conn.execute("DELETE FROM money_entries WHERE id = ?", (wrong.id,))


def test_application_reversal_links_to_application(svc, out_order):
    _rec(svc, out_order, MoneyKind.DEPOSIT_IN, 100_000)
    applied = _rec(svc, out_order, MoneyKind.DEPOSIT_APPLIED, 30_000)
    deposit = svc.settlements.get_settlement(out_order.id).entries[0]
    with pytest.raises(ValidationError):  # 종류가 맞지 않는 원거래
        _rec(svc, out_order, MoneyKind.DEPOSIT_APPLICATION_REVERSAL, 10_000,
             related_entry_id=deposit.id)
    _rec(svc, out_order, MoneyKind.DEPOSIT_APPLICATION_REVERSAL, 30_000, related_entry_id=applied.id)
    s = svc.settlements.summary(out_order.id)
    assert s.deposit_held_won == 100_000 and s.amount_due_won == 30_000


def test_reversal_must_reference_same_order(svc, cam, lens, kim, out_order):
    other = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [lens.id]))
    other = svc.checkout(other.id, START)
    receipt = _rec(svc, other, MoneyKind.RENTAL_RECEIPT, 20_000)
    _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 30_000)
    with pytest.raises(ValidationError):
        _rec(svc, out_order, MoneyKind.RENTAL_REFUND, 1_000, related_entry_id=receipt.id)


def test_internal_transfer_has_no_payment_method(svc, out_order):
    _rec(svc, out_order, MoneyKind.DEPOSIT_IN, 100_000)
    with pytest.raises(ValidationError) as exc:
        _rec(svc, out_order, MoneyKind.DEPOSIT_APPLIED, 10_000, payment_method=PaymentMethod.CASH)
    assert exc.value.field == "payment_method"


@pytest.mark.parametrize("amount", [0, -5_000, 10.5, "1000"])
def test_money_amount_must_be_positive_integer(svc, out_order, amount):
    with pytest.raises(ValidationError):
        _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, amount)
    assert svc.count("money_entries") == 0


def test_invalid_kind_and_method(svc, out_order):
    with pytest.raises(ValidationError):
        _rec(svc, out_order, "GIFT", 1_000)
    with pytest.raises(ValidationError):
        _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 1_000, payment_method="BITCOIN")


def test_money_rules_by_status_before_checkout(svc, cam, kim):
    """U5: 임시 저장은 거래 불가, 확정 예약은 선입금·보증금 수령과 그 환급·반환만."""
    draft = svc.reservations.create_draft(ReservationInput(kim.id, START, DUE, [cam.id]))
    with pytest.raises(InvalidStateError, match="임시 저장"):
        _rec(svc, draft, MoneyKind.RENTAL_RECEIPT, 10_000)
    svc.reservations.cancel(draft.id)
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id]))
    _rec(svc, order, MoneyKind.RENTAL_RECEIPT, 10_000, payment_method=PaymentMethod.TRANSFER)
    _rec(svc, order, MoneyKind.DEPOSIT_IN, 100_000)
    _rec(svc, order, MoneyKind.RENTAL_REFUND, 1_000)
    with pytest.raises(InvalidStateError, match="출고 전"):
        _rec(svc, order, MoneyKind.DEPOSIT_APPLIED, 1_000)
    assert svc.settlements.allowed_kinds(order.id) == {
        MoneyKind.RENTAL_RECEIPT, MoneyKind.RENTAL_REFUND, MoneyKind.DEPOSIT_IN, MoneyKind.DEPOSIT_OUT}
    with pytest.raises(NotFoundError):
        svc.settlements.record(9999, MoneyInput(MoneyKind.RENTAL_RECEIPT, 1))


def test_preview_does_not_write(svc, out_order):
    after = svc.settlements.preview(out_order.id, MoneyInput(MoneyKind.RENTAL_RECEIPT, 30_000))
    assert after.amount_due_won == 0
    assert svc.count("money_entries") == 0
    with pytest.raises(ValidationError):
        svc.settlements.preview(out_order.id, MoneyInput(MoneyKind.DEPOSIT_OUT, 1))


def test_completed_order_still_accepts_settlement_until_settled(svc, cam, out_order):
    svc.return_asset(out_order.id, cam.id, kst(10, 21, 9))
    assert svc.reservations.get_order(out_order.id).status == OrderStatus.COMPLETED
    assert [u.order_id for u in svc.settlements.unsettled_orders()] == [out_order.id]
    _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 30_000)
    assert svc.settlements.unsettled_orders() == []


def test_unsettled_includes_held_deposit(svc, cam, out_order):
    _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 30_000)
    _rec(svc, out_order, MoneyKind.DEPOSIT_IN, 100_000)
    unsettled = svc.settlements.unsettled_orders()
    assert [(u.order_no, u.summary.deposit_held_won) for u in unsettled] == [
        (out_order.order_no, 100_000)
    ]


# --------------------------------------------------------------------------- T-24 원자성


class Boom(RuntimeError):
    pass


def _fail_on_call(monkeypatch, name: str, nth: int) -> None:
    """repositories.<name>을 n번째 호출에서 예외를 던지도록 바꾼다."""
    original = getattr(repo, name)
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == nth:
            raise Boom("주입된 저장 실패")
        return original(*args, **kwargs)

    monkeypatch.setattr(repo, name, wrapper)


def _snapshot(conn):
    tables = ("rental_orders", "rental_items", "charge_lines", "money_entries",
              "maintenance_blocks", "audit_events")
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}


@pytest.mark.parametrize(
    ("target", "nth"),
    [
        ("insert_charge", 2),          # 두 번째 RENTAL 청구 직후
        ("mark_order_checked_out", 1),  # 품목 OUT 전환 후 주문 갱신 단계
        ("insert_money_entry", 2),     # 수금 기록 후 보증금 기록 단계
    ],
)
def test_t24_checkout_failure_rolls_back_everything(svc, cam, lens, kim, monkeypatch, target, nth):
    order = svc.reservations.create_confirmed(
        ReservationInput(kim.id, START, DUE, [cam.id, lens.id], discount_won=5_000)
    )
    svc.clock.now = START
    before = _snapshot(svc.conn)
    _fail_on_call(monkeypatch, target, nth)
    with pytest.raises(Boom):
        svc.checkout_svc.checkout(
            order.id, CheckoutInput(rental_receipt_won=10_000, deposit_in_won=150_000)
        )
    assert _snapshot(svc.conn) == before
    assert not svc.conn.in_transaction
    monkeypatch.undo()
    assert svc.checkout_svc.checkout(order.id).status == OrderStatus.IN_RENTAL


@pytest.mark.parametrize(
    ("target", "nth"),
    [
        ("insert_charge", 2),     # LATE 저장 후 DAMAGE 저장 단계
        ("insert_block", 1),      # 정비 블록 저장 단계
        ("set_order_status", 1),  # 마지막 반납 후 COMPLETED 전환 단계
        ("mark_item_returned", 2),  # 두 번째 품목 반납 단계
    ],
)
def test_t24_return_failure_rolls_back_everything(svc, cam, lens, kim, monkeypatch, target, nth):
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id, lens.id]))
    out = svc.checkout(order.id, START)
    svc.clock.now = kst(10, 23, 12)  # 연체
    before = _snapshot(svc.conn)
    _fail_on_call(monkeypatch, target, nth)
    inputs = [
        ReturnItemInput(i.id, condition=ReturnCondition.MAINTENANCE_REQUIRED, damage_fee_won=10_000)
        for i in out.items
    ]
    with pytest.raises(Boom):
        svc.returns.return_items(order.id, inputs)
    assert _snapshot(svc.conn) == before
    assert not svc.conn.in_transaction
    assert {i.status for i in svc.reservations.get_order(order.id).items} == {ItemStatus.OUT}
    monkeypatch.undo()
    assert svc.returns.return_items(order.id, inputs).completed


def test_t24_money_entry_failure_rolls_back(svc, out_order, monkeypatch):
    before = _snapshot(svc.conn)
    _fail_on_call(monkeypatch, "insert_audit", 1)
    with pytest.raises(Boom):
        _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 30_000)
    assert _snapshot(svc.conn) == before


# --------------------------------------------------------------------------- 정산 마무리 (U4)


def _completed(svc, out_order, cam, *, late_hours=0, deposit=100_000, paid=0):
    if deposit:
        _rec(svc, out_order, MoneyKind.DEPOSIT_IN, deposit, payment_method=PaymentMethod.TRANSFER)
    if paid:
        _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, paid)
    svc.return_asset(out_order.id, cam.id, kst(10, 21, 10 + late_hours))
    return out_order.id


def test_finalize_applies_deposit_and_returns_rest_atomically(svc, out_order, cam):
    oid = _completed(svc, out_order, cam, late_hours=5)  # 대여료 30,000 + 연체 30,000(1일) 미수
    plan = svc.settlements.final_plan(oid)
    assert plan.deposit_applied_won == 60_000 and plan.deposit_return_won == 40_000
    after = svc.settlements.finalize(oid, plan, payment_method=PaymentMethod.CASH)
    assert after.is_settled
    entries = svc.settlements.get_settlement(oid).entries
    out = [e for e in entries if e.kind == MoneyKind.DEPOSIT_OUT][0]
    applied = [e for e in entries if e.kind == MoneyKind.DEPOSIT_APPLIED][0]
    assert out.payment_method == PaymentMethod.CASH and applied.payment_method is None
    assert out.note == "정산 마무리"


def test_finalize_collect_remaining_when_deposit_short(svc, out_order, cam):
    oid = _completed(svc, out_order, cam, deposit=10_000)
    plan = svc.settlements.final_plan(oid)
    assert plan.remaining_due_won == 20_000
    not_collected = svc.settlements.finalize(oid, plan)
    assert not_collected.amount_due_won == 20_000  # 받지 않았으면 미수로 남김
    plan2 = svc.settlements.final_plan(oid)
    assert plan2.steps == () and plan2.remaining_due_won == 20_000
    done = svc.settlements.finalize(oid, plan2, payment_method=PaymentMethod.CARD, collect_remaining=True)
    assert done.is_settled


def test_finalize_rejects_stale_plan_and_records_nothing(svc, out_order, cam):
    oid = _completed(svc, out_order, cam)
    plan = svc.settlements.final_plan(oid)
    _rec(svc, out_order, MoneyKind.RENTAL_RECEIPT, 30_000)  # 다른 창에서 수금
    before = svc.count("money_entries")
    with pytest.raises(InvalidStateError, match="정산 내용이 바뀌었습니다"):
        svc.settlements.finalize(oid, plan)
    assert svc.count("money_entries") == before


def test_finalize_only_for_completed_orders(svc, out_order):
    with pytest.raises(InvalidStateError, match="모든 장비가 반납되었거나 취소된"):
        svc.settlements.final_plan(out_order.id)


def test_finalize_nothing_to_do(svc, out_order, cam):
    oid = _completed(svc, out_order, cam, deposit=0, paid=30_000)
    plan = svc.settlements.final_plan(oid)
    assert plan.is_empty
    with pytest.raises(InvalidStateError, match="정산할 금액이 없습니다"):
        svc.settlements.finalize(oid, plan)


def test_finalize_rolls_back_all_steps_on_failure(svc, out_order, cam, monkeypatch):
    from rentdesk.services import settlements as mod

    oid = _completed(svc, out_order, cam, late_hours=5)
    plan = svc.settlements.final_plan(oid)
    real = mod.record_in_txn
    calls = []

    def flaky(conn, order, data, now):
        calls.append(data.kind)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(conn, order, data, now)

    monkeypatch.setattr(mod, "record_in_txn", flaky)
    before = svc.count("money_entries")
    with pytest.raises(sqlite3.OperationalError):
        svc.settlements.finalize(oid, plan)
    assert svc.count("money_entries") == before  # 첫 거래도 함께 취소


# --------------------------------------------------------------------------- 선입금·취소 (U5)


def test_prepaid_then_checkout_and_settle(svc, cam, kim):
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id]))
    _rec(svc, order, MoneyKind.RENTAL_RECEIPT, 30_000, payment_method=PaymentMethod.TRANSFER)
    _rec(svc, order, MoneyKind.DEPOSIT_IN, 100_000, payment_method=PaymentMethod.TRANSFER)
    assert svc.settlements.unsettled_orders() == []  # 출고 전 선입금은 정산 대상 아님
    assert svc.settlements.list_rows(unsettled_only=True) == []
    assert [r.order.id for r in svc.settlements.list_rows(unsettled_only=False)] == [order.id]
    svc.checkout(order.id, START)
    svc.return_asset(order.id, cam.id, kst(10, 21, 9))
    s = svc.settlements.summary(order.id)
    assert (s.amount_due_won, s.deposit_held_won) == (0, 100_000)
    plan = svc.settlements.final_plan(order.id)
    assert plan.deposit_return_won == 100_000
    assert svc.settlements.finalize(order.id, plan).is_settled


def test_cancel_prepaid_with_fee_then_refund_rest(svc, cam, kim):
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id]))
    _rec(svc, order, MoneyKind.RENTAL_RECEIPT, 30_000)
    _rec(svc, order, MoneyKind.DEPOSIT_IN, 100_000)
    canceled = svc.reservations.cancel(order.id, cancel_fee_won=10_000)
    assert canceled.status == OrderStatus.CANCELED
    charges = svc.settlements.get_settlement(order.id).charges
    assert [(c.kind.value, c.amount_won, c.description) for c in charges] == [
        ("ADJUSTMENT", 10_000, "예약 취소 수수료")]
    assert [u.order_id for u in svc.settlements.unsettled_orders()] == [order.id]
    with pytest.raises(InvalidStateError, match="취소된 예약"):
        _rec(svc, order, MoneyKind.DEPOSIT_IN, 1_000)
    plan = svc.settlements.final_plan(order.id)
    assert plan.rental_refund_won == 20_000 and plan.deposit_return_won == 100_000
    assert svc.settlements.finalize(order.id, plan, payment_method=PaymentMethod.TRANSFER).is_settled
    assert svc.settlements.unsettled_orders() == []


def test_cancel_fee_without_prepayment_is_owed(svc, cam, kim):
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id]))
    svc.reservations.cancel(order.id, cancel_fee_won=5_000)
    plan = svc.settlements.final_plan(order.id)
    assert plan.steps == () and plan.remaining_due_won == 5_000
    assert svc.settlements.finalize(order.id, plan, collect_remaining=True).is_settled


def test_cancel_without_money_not_listed_and_fee_validated(svc, cam, kim):
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id]))
    with pytest.raises(ValidationError):
        svc.reservations.cancel(order.id, cancel_fee_won=-1)
    assert svc.reservations.get_order(order.id).status == OrderStatus.CONFIRMED  # 검증 실패 시 변경 없음
    svc.reservations.cancel(order.id)
    assert svc.settlements.list_rows(unsettled_only=False) == []
    assert svc.count("charge_lines") == 0
