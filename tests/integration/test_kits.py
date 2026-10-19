"""M4.6 R4: 장비 세트 — 등록 검증, 예약 전개·대체, 스냅샷, 할인 청구, 체크리스트 저장, DB 제약."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from rentdesk.domain.errors import (
    ConflictError,
    DuplicateError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from rentdesk.domain.kits import KitDiscount, KitDiscountKind
from rentdesk.domain.models import ChargeKind, OrderStatus
from rentdesk.persistence import db
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import apply_migrations, connect
from rentdesk.services.checkout import CheckoutInput
from rentdesk.services.kits import KitInput, KitPick
from rentdesk.services.reservations import ReservationInput
from rentdesk.services.returns import ReturnItemInput
from tests.support import kst

START, DUE = kst(10, 20, 10), kst(10, 22, 10)  # 2일
CHECKLIST = ("배터리 2개", "충전기", "가방")


@pytest.fixture
def gear(svc):
    """카메라 2대·렌즈 2개·삼각대 1개. 세트 = CAM-1 + LENS-1 + TRI-1."""
    a = {
        "CAM-1": svc.add_asset("CAM-1", rate=30_000, deposit=200_000, category="카메라"),
        "CAM-2": svc.add_asset("CAM-2", rate=30_000, deposit=200_000, category="카메라"),
        "LENS-1": svc.add_asset("LENS-1", rate=10_000, deposit=50_000, category="렌즈", name="렌즈"),
        "LENS-2": svc.add_asset("LENS-2", rate=10_000, deposit=50_000, category="렌즈", name="렌즈"),
        "TRI-1": svc.add_asset("TRI-1", rate=5_000, deposit=0, category="삼각대", name="삼각대"),
    }
    return a


@pytest.fixture
def kit(svc, gear):
    return svc.kits.create_kit(KitInput(
        name="카메라 세트", asset_ids=[gear["CAM-1"].id, gear["LENS-1"].id, gear["TRI-1"].id],
        checklist=list(CHECKLIST), discount_kind="FIXED", discount_won=5_000,
    ))


def _ids(gear, *codes):
    return [gear[c].id for c in codes]


def _book_kit(svc, kit, asset_ids, *, confirm=True, discount=0, extra=()):
    customer = svc.add_customer()
    data = ReservationInput(
        customer_id=customer.id, start_at=START, due_at=DUE, asset_ids=[*asset_ids, *extra],
        discount_won=discount, kits=[KitPick(kit.id, asset_ids)],
    )
    return svc.reservations.create_confirmed(data) if confirm else svc.reservations.create_draft(data)


# --------------------------------------------------------------------------- 세트 등록


def test_create_kit_saves_members_checklist_and_discount(svc, gear, kit):
    assert kit.name == "카메라 세트"
    assert kit.asset_ids == tuple(_ids(gear, "CAM-1", "LENS-1", "TRI-1"))
    assert kit.checklist == CHECKLIST
    assert kit.discount == KitDiscount(KitDiscountKind.FIXED, 5_000)
    row = svc.kits.list_kit_rows()[0]
    assert row.daily_total_won == 45_000 and row.warning == ""
    action = svc.conn.execute("SELECT action FROM audit_events WHERE entity_type = 'kit'").fetchone()[0]
    assert action == "CREATE"


@pytest.mark.parametrize("asset_ids, message", [
    ([], "1개 이상"),
    (None, "올바르지"),
    ("1,2", "올바르지"),
    ([True], "올바르지"),
])
def test_kit_asset_input_validation(svc, gear, asset_ids, message):
    with pytest.raises(ValidationError) as exc:
        svc.kits.create_kit(KitInput(name="세트", asset_ids=asset_ids))
    assert message in exc.value.message


def test_kit_rejects_duplicate_unknown_and_retired_assets(svc, gear):
    cam = gear["CAM-1"].id
    with pytest.raises(ValidationError, match="두 번"):
        svc.kits.create_kit(KitInput(name="세트", asset_ids=[cam, cam]))
    with pytest.raises(ValidationError, match="존재하지"):
        svc.kits.create_kit(KitInput(name="세트", asset_ids=[cam, 9999]))
    svc.assets.retire_asset(gear["CAM-2"].id)
    with pytest.raises(ValidationError, match="사용 중지"):
        svc.kits.create_kit(KitInput(name="세트", asset_ids=[gear["CAM-2"].id]))
    assert svc.kits.list_kits() == []


def test_kit_name_and_discount_validation(svc, gear, kit):
    cam = gear["CAM-2"].id
    with pytest.raises(DuplicateError):
        svc.kits.create_kit(KitInput(name="카메라 세트", asset_ids=[cam]))
    with pytest.raises(ValidationError):
        svc.kits.create_kit(KitInput(name=" ", asset_ids=[cam]))
    with pytest.raises(ValidationError):
        svc.kits.create_kit(KitInput(name="세트2", asset_ids=[cam], discount_kind="PERCENT", discount_percent="0"))
    with pytest.raises(ValidationError):
        svc.kits.create_kit(KitInput(name="세트2", asset_ids=[cam], discount_kind="PERCENT", discount_percent="101"))
    with pytest.raises(ValidationError):
        svc.kits.create_kit(KitInput(name="세트2", asset_ids=[cam], checklist=["가방", "가방"]))
    saved = svc.kits.create_kit(
        KitInput(name="세트2", asset_ids=[cam], discount_kind="PERCENT", discount_percent="12.5")
    )
    assert saved.discount.percent == Decimal("12.5")


def test_update_kit_keeps_retired_member_but_rejects_new_retired(svc, gear, kit):
    svc.assets.retire_asset(gear["TRI-1"].id)
    row = svc.kits.list_kit_rows()[0]
    assert row.retired_codes == ["TRI-1"] and "TRI-1" in row.warning
    updated = svc.kits.update_kit(kit.id, KitInput(
        name="카메라 세트 A", asset_ids=_ids(gear, "CAM-1", "TRI-1"), checklist=["가방"],
    ))
    assert updated.asset_ids == tuple(_ids(gear, "CAM-1", "TRI-1")) and updated.checklist == ("가방",)
    svc.assets.retire_asset(gear["LENS-2"].id)
    with pytest.raises(ValidationError, match="LENS-2"):
        svc.kits.update_kit(kit.id, KitInput(name="카메라 세트 A", asset_ids=_ids(gear, "CAM-1", "LENS-2")))


def test_archive_hides_kit(svc, kit):
    svc.kits.set_kit_archived(kit.id, True)
    assert svc.kits.list_kits() == []
    assert [k.id for k in svc.kits.list_kits(include_archived=True)] == [kit.id]
    assert svc.kits.set_kit_archived(kit.id, False).is_archived is False
    with pytest.raises(NotFoundError):
        svc.kits.get_kit(9999)


def test_asset_in_kit_cannot_be_deleted(svc, gear, kit):
    with pytest.raises(InvalidStateError, match="카메라 세트"):
        svc.assets.delete_asset(gear["CAM-1"].id)
    svc.assets.delete_asset(gear["CAM-2"].id)  # 세트에 없는 장비는 그대로 삭제 가능


def test_daily_preview(svc, gear, kit):
    assert svc.kits.daily_preview(kit.asset_ids, "FIXED", 5_000) == (45_000, 5_000)
    assert svc.kits.daily_preview(kit.asset_ids, "PERCENT", 0, "10") == (45_000, 4_500)


# --------------------------------------------------------------------------- 예약에 세트 담기


def test_reservation_with_kit_snapshots_and_discount(svc, gear, kit):
    order = _book_kit(svc, kit, kit.asset_ids, extra=_ids(gear, "CAM-2"))
    assert len(order.kits) == 1
    snap = order.kits[0]
    assert (snap.name, snap.kit_id, snap.checklist) == ("카메라 세트", kit.id, CHECKLIST)
    in_kit = {i.asset_code for i in order.items if i.order_kit_id == snap.id}
    assert in_kit == {"CAM-1", "LENS-1", "TRI-1"}
    q = svc.reservations.quote(order)
    assert q.base_rental_fee_won == (45_000 + 30_000) * 2
    assert q.kit_discount_won == 5_000 and q.expected_total_rental_fee_won == 145_000
    row = svc.reservations.list_order_rows()[0]
    assert row.expected_total_rental_fee_won == 145_000  # 목록 행에도 세트 할인 반영


def test_kit_substitute_same_category_and_drop(svc, gear, kit):
    order = _book_kit(svc, kit, _ids(gear, "CAM-2", "LENS-1"))  # CAM-1 → CAM-2 대체, 삼각대 빼기
    assert {i.asset_code for i in order.items} == {"CAM-2", "LENS-1"}
    assert all(i.order_kit_id == order.kits[0].id for i in order.items)


def test_kit_substitute_other_category_rejected(svc, gear, kit):
    with pytest.raises(ValidationError, match="같은 카테고리"):
        _book_kit(svc, kit, _ids(gear, "LENS-2", "LENS-1", "TRI-1"))
    assert svc.count("rental_orders") == 0 and svc.count("order_kits") == 0


@pytest.mark.parametrize("picks, message", [
    (lambda k, g: [KitPick(k.id, [])], "장비가 하나도"),
    (lambda k, g: [KitPick(k.id, [g["CAM-1"].id]), KitPick(k.id, [g["LENS-1"].id])], "두 번"),
    (lambda k, g: [KitPick(k.id, [g["CAM-2"].id])], "목록에 없습니다"),
    (lambda k, g: [KitPick(9999, [g["CAM-1"].id])], "찾을 수 없습니다"),
    (lambda k, g: ["kit"], "올바르지"),
])
def test_kit_pick_input_validation(svc, gear, kit, picks, message):
    customer = svc.add_customer()
    data = ReservationInput(
        customer_id=customer.id, start_at=START, due_at=DUE, asset_ids=_ids(gear, "CAM-1", "LENS-1"),
        kits=picks(kit, gear),
    )
    with pytest.raises(ValidationError) as exc:
        svc.reservations.create_draft(data)
    assert message in exc.value.message


def test_archived_kit_cannot_be_booked(svc, kit):
    svc.kits.set_kit_archived(kit.id, True)
    with pytest.raises(ValidationError, match="숨긴 세트"):
        _book_kit(svc, kit, kit.asset_ids)


def test_order_discount_plus_kit_discount_limited_to_base(svc, gear, kit):
    with pytest.raises(ValidationError) as exc:
        _book_kit(svc, kit, kit.asset_ids, discount=90_000 - 5_000 + 1)
    assert exc.value.field == "discount_won"
    assert svc.count("rental_orders") == 0
    order = _book_kit(svc, kit, kit.asset_ids, discount=85_000)
    assert svc.reservations.quote(order).expected_total_rental_fee_won == 0


def test_kit_edit_after_confirm_keeps_order_snapshot(svc, gear, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    svc.kits.update_kit(kit.id, KitInput(
        name="새 이름", asset_ids=_ids(gear, "CAM-1"), checklist=["메모리"], discount_kind="PERCENT",
        discount_percent="50",
    ))
    again = svc.reservations.get_order(order.id)
    assert again.kits[0].name == "카메라 세트" and again.kits[0].checklist == CHECKLIST
    assert svc.reservations.quote(again).kit_discount_won == 5_000


def test_draft_kit_snapshot_refreshed_on_confirm(svc, gear, kit):
    draft = _book_kit(svc, kit, kit.asset_ids, confirm=False)
    svc.kits.update_kit(kit.id, KitInput(
        name="카메라 세트", asset_ids=list(kit.asset_ids), checklist=["가방"], discount_kind="FIXED",
        discount_won=7_000,
    ))
    confirmed = svc.reservations.confirm(draft.id)
    assert confirmed.kits[0].checklist == ("가방",)
    assert svc.reservations.quote(confirmed).kit_discount_won == 7_000


def test_two_orders_same_kit_cannot_both_confirm(svc, kit):
    first = _book_kit(svc, kit, kit.asset_ids, confirm=False)
    second = _book_kit(svc, kit, kit.asset_ids, confirm=False)
    svc.reservations.confirm(first.id)
    with pytest.raises(ConflictError):
        svc.reservations.confirm(second.id)
    assert svc.reservations.get_order(second.id).status == OrderStatus.DRAFT


def test_plan_marks_unavailable_member_with_substitute(svc, gear, kit):
    _book_kit(svc, kit, _ids(gear, "CAM-1"))  # CAM-1만 다른 예약에 확정
    plan = svc.kits.plan(kit.id, START, DUE)
    cam = plan.slots[0]
    assert cam.member.asset_code == "CAM-1" and not cam.available and "예약" in cam.reason
    assert [c.asset_code for c in cam.candidates] == ["CAM-2"] and cam.default_choice == gear["CAM-2"].id
    assert plan.slots[1].available and plan.slots[1].default_choice == gear["LENS-1"].id
    assert not plan.all_available


def test_plan_retired_member_and_locked(svc, gear, kit):
    svc.assets.retire_asset(gear["TRI-1"].id)
    plan = svc.kits.plan(kit.id, START, DUE, locked=[gear["LENS-1"].id])
    tri, lens = plan.slots[2], plan.slots[1]
    assert tri.reason == "사용 중지된 장비" and tri.default_choice is None
    assert "다른 세트" in lens.reason and lens.default_choice == gear["LENS-2"].id


def test_update_order_removes_kit_and_keeps_items(svc, gear, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    data = ReservationInput(
        customer_id=order.customer_id, start_at=START, due_at=DUE, asset_ids=list(kit.asset_ids),
    )
    updated = svc.reservations.update_order(order.id, data)
    assert updated.kits == () and all(i.order_kit_id is None for i in updated.items)
    assert svc.reservations.quote(updated).kit_discount_won == 0


def test_update_order_kept_kit_drop_and_substitute(svc, gear, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    kept_id = order.kits[0].id
    picked = _ids(gear, "CAM-2", "LENS-1")  # CAM-1 → CAM-2, 삼각대 빼기
    data = ReservationInput(
        customer_id=order.customer_id, start_at=START, due_at=DUE, asset_ids=picked,
        kits=[KitPick(kit.id, picked)],
    )
    updated = svc.reservations.update_order(order.id, data)
    assert updated.kits[0].id == kept_id  # 스냅샷 유지
    assert {i.asset_code: i.order_kit_id for i in updated.items} == {"CAM-2": kept_id, "LENS-1": kept_id}
    bad = ReservationInput(
        customer_id=order.customer_id, start_at=START, due_at=DUE, asset_ids=_ids(gear, "LENS-2", "LENS-1"),
        kits=[KitPick(kit.id, _ids(gear, "LENS-2", "LENS-1"))],
    )
    with pytest.raises(ValidationError):
        svc.reservations.update_order(order.id, bad)
    assert {i.asset_code for i in svc.reservations.get_order(order.id).items} == {"CAM-2", "LENS-1"}


def test_preview_quote_with_new_kit(svc, gear, kit):
    q = svc.reservations.preview_quote(
        list(kit.asset_ids), START, DUE, 0, kits=[KitPick(kit.id, list(kit.asset_ids))]
    )
    assert q.kit_discount_won == 5_000 and q.kit_discounts[0].name == "카메라 세트"


# --------------------------------------------------------------------------- 출고·반납


def test_checkout_charges_kit_discount_line_and_saves_checks(svc, gear, kit):
    order = _book_kit(svc, kit, kit.asset_ids, discount=1_000)
    kit_id = order.kits[0].id
    svc.checkout(order.id, START, kit_checks={kit_id: [True, False, True]})
    charges = svc.settlements.get_settlement(order.id).charges
    discounts = sorted((c.amount_won, c.description) for c in charges if c.kind == ChargeKind.DISCOUNT)
    assert discounts == [(-5_000, "세트 할인: 카메라 세트 (5,000원)"), (-1_000, "예약 할인")]
    summary = svc.settlements.summary(order.id)
    assert summary.discount_charges_won == -6_000 and summary.charge_total_won == 90_000 - 6_000
    after = svc.reservations.get_order(order.id)
    assert after.kits[0].checkout_checks == (True, False, True)
    assert after.kits[0].checkout_missing == ["충전기"]  # 빠져도 출고는 됨(경고만)
    assert after.status == OrderStatus.IN_RENTAL


def test_checkout_without_checks_leaves_unchecked(svc, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    svc.checkout(order.id, START)
    assert svc.reservations.get_order(order.id).kits[0].checkout_checks is None


def test_checkout_rejects_bad_checks(svc, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    kit_id = order.kits[0].id
    for checks in ({kit_id: [True]}, {9999: [True, True, True]}, {kit_id: ["y", "n", "y"]}):
        with pytest.raises(ValidationError):
            svc.checkout(order.id, START, kit_checks=checks)
    assert svc.reservations.get_order(order.id).status == OrderStatus.CONFIRMED
    assert svc.count("charge_lines") == 0


def test_percent_kit_discount_on_actual_items(svc, gear):
    pct = svc.kits.create_kit(KitInput(
        name="비율 세트", asset_ids=_ids(gear, "CAM-1", "LENS-1"), discount_kind="PERCENT", discount_percent="10",
    ))
    order = _book_kit(svc, pct, _ids(gear, "LENS-1"), extra=_ids(gear, "CAM-2"))  # 카메라는 세트에서 빠짐
    assert svc.reservations.quote(order).kit_discount_won == 2_000  # 렌즈 2일 20,000 × 10%
    svc.checkout(order.id, START)
    line = next(c for c in svc.settlements.get_settlement(order.id).charges if c.kind == ChargeKind.DISCOUNT)
    assert line.amount_won == -2_000 and "(10%)" in line.description


def test_return_saves_kit_checks(svc, gear, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    kit_id = order.kits[0].id
    svc.checkout(order.id, START)
    svc.clock.now = kst(10, 22, 9)
    item = next(i for i in order.items if i.asset_code == "CAM-1")
    svc.returns.return_items(
        order.id, [ReturnItemInput(item_id=item.id, returned_at=kst(10, 22, 9))],
        kit_checks={kit_id: [True, True, False]},
    )
    after = svc.reservations.get_order(order.id)
    assert after.kits[0].return_missing == ["가방"]
    with pytest.raises(ValidationError):
        rest = [ReturnItemInput(item_id=i.id, returned_at=kst(10, 22, 9)) for i in after.items if i.asset_code != "CAM-1"]
        svc.returns.return_items(order.id, rest, kit_checks={kit_id: [True]})
    assert svc.reservations.get_order(order.id).status == OrderStatus.IN_RENTAL  # 검증 실패 시 반납 롤백


def test_cancel_keeps_kit_history(svc, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    canceled = svc.reservations.cancel(order.id)
    assert canceled.kits[0].name == "카메라 세트"


# --------------------------------------------------------------------------- 보고서


def test_reports_show_kit_names(svc, gear, kit):
    _book_kit(svc, kit, kit.asset_ids)
    assets = svc.reports.assets_report()
    col = [c.header for c in assets.columns].index("세트")
    by_code = {r[0]: r[col] for r in assets.rows}
    assert by_code["CAM-1"] == "카메라 세트" and by_code["CAM-2"] == ""
    reservations = svc.reports.reservations_report(kst(10, 1).date(), kst(10, 31).date())
    col = [c.header for c in reservations.columns].index("세트")
    assert reservations.rows[0][col] == "카메라 세트"


# --------------------------------------------------------------------------- DB 제약·마이그레이션


def test_order_kit_cannot_be_deleted_after_checkout(svc, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    svc.checkout(order.id, START)
    with pytest.raises(sqlite3.IntegrityError):
        svc.conn.execute("DELETE FROM order_kits WHERE id = ?", (order.kits[0].id,))


def test_item_kit_must_belong_to_same_order(svc, gear, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    other = svc.book(svc.add_customer("다른 고객").id, _ids(gear, "CAM-2"), START, DUE)
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_item_kit(svc.conn, other.items[0].id, order.kits[0].id)


def test_one_discount_line_per_kit(svc, kit):
    order = _book_kit(svc, kit, kit.asset_ids)
    svc.checkout(order.id, START)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_charge(
            svc.conn, order_id=order.id, rental_item_id=None, kind=ChargeKind.DISCOUNT, amount_won=-1,
            description="중복", now=START, order_kit_id=order.kits[0].id,
        )
    with pytest.raises(sqlite3.IntegrityError):  # 세트 연결은 할인 줄에만
        repo.insert_charge(
            svc.conn, order_id=order.id, rental_item_id=None, kind=ChargeKind.ADJUSTMENT, amount_won=1,
            description="조정", now=START, order_kit_id=order.kits[0].id,
        )


def test_migration_keeps_existing_discount_rule(tmp_path, monkeypatch):
    """버전 6 DB(주문 할인 청구가 있는 상태)에서 007로 올려도 주문당 주문 할인 1건 제약이 유지된다."""
    original = db.load_migrations()
    monkeypatch.setattr(db, "load_migrations", lambda: [m for m in original if m.version <= 6])
    conn = connect(tmp_path / "old.db")
    try:
        apply_migrations(conn)
        ts = "2026-01-01T00:00:00Z"
        conn.execute("INSERT INTO customers (name, created_at_utc) VALUES ('고객', ?)", (ts,))
        conn.execute(
            "INSERT INTO rental_orders (order_no, customer_id, status, start_at_utc, due_at_utc,"
            " actual_checkout_at_utc, turnaround_buffer_snapshot_minutes, discount_won, created_at_utc,"
            " updated_at_utc) VALUES ('R-1', 1, 'IN_RENTAL', ?, '2026-01-03T00:00:00Z', ?, 60, 1000, ?, ?)",
            (ts, ts, ts, ts),
        )
        conn.execute(
            "INSERT INTO charge_lines (order_id, kind, amount_won, description, created_at_utc)"
            " VALUES (1, 'DISCOUNT', -1000, '예약 할인', ?)", (ts,),
        )
        monkeypatch.setattr(db, "load_migrations", lambda: original)
        assert apply_migrations(conn) == [7]
        order = repo.get_order(conn, 1)
        assert order.kits == ()
        assert repo.list_charges(conn, 1)[0].amount_won == -1000
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO charge_lines (order_id, kind, amount_won, created_at_utc)"
                " VALUES (1, 'DISCOUNT', -1, ?)", (ts,),
            )
    finally:
        conn.close()


def test_checkout_input_default_kit_checks_is_empty():
    assert dict(CheckoutInput().kit_checks) == {}
