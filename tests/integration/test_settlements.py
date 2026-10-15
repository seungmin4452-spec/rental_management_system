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


def test_money_only_after_checkout(svc, cam, kim):
    order = svc.reservations.create_confirmed(ReservationInput(kim.id, START, DUE, [cam.id]))
    with pytest.raises(InvalidStateError):
        _rec(svc, order, MoneyKind.RENTAL_RECEIPT, 10_000)
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
