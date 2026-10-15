"""ReservationService / AvailabilityService / MaintenanceService (PRD 6.1, 7.1, 7.2).

현재 시각(clock)은 2026-10-19 09:00 KST. 예약 대상일은 2026-10-20.
"""

import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from rentdesk.domain.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from rentdesk.domain.models import ItemStatus, OrderStatus
from rentdesk.persistence.db import connect
from rentdesk.services.assets import AssetInput
from rentdesk.services.reservations import ReservationInput, ReservationService
from tests.support import kst


def _res(customer, assets, start, due, **kw):
    ids = [a.id for a in (assets if isinstance(assets, list) else [assets])]
    return ReservationInput(customer.id, start, due, ids, **kw)


@pytest.fixture
def cam1(svc):
    return svc.add_asset("CAM-1")


@pytest.fixture
def cam2(svc):
    return svc.add_asset("CAM-2")


@pytest.fixture
def kim(svc):
    return svc.add_customer("김고객")


@pytest.fixture
def lee(svc):
    return svc.add_customer("이고객")


# --------------------------------------------------------------------------- PRD T-cases


def test_t02_overlapping_confirmation_rejected_with_conflict_info(svc, cam1, kim, lee):
    first = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 20, 12)))
    with pytest.raises(ConflictError) as exc:
        svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 11), kst(10, 20, 13)))
    conflict = exc.value.conflicts[0]
    assert conflict.order_no == first.order_no
    assert conflict.asset_code == "CAM-1"
    assert conflict.start_at == kst(10, 20, 10)
    assert first.order_no in exc.value.message
    assert "2026-10-20 10:00" in exc.value.message
    # 실패한 확정은 아무것도 남기지 않는다(전체 롤백)
    assert len(svc.reservations.list_orders()) == 1


def test_t02_us02_confirming_existing_draft_rejected(svc, cam1, kim, lee):
    svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    draft = svc.reservations.create_draft(_res(lee, cam1, kst(10, 20, 15), kst(10, 21, 15)))
    with pytest.raises(ConflictError):
        svc.reservations.confirm(draft.id)
    assert svc.reservations.get_order(draft.id).status == OrderStatus.DRAFT


def test_t03_back_to_back_allowed_with_zero_buffer(svc, cam1, kim, lee):
    svc.set_buffer(0)
    svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 20, 12)))
    second = svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 12), kst(10, 20, 14)))
    assert second.status == OrderStatus.CONFIRMED


def test_t04_buffer_60_blocks_1230_start(svc, cam1, kim, lee):
    svc.set_buffer(60)
    svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 20, 12)))
    with pytest.raises(ConflictError):
        svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 12, 30), kst(10, 20, 14)))
    # 버퍼가 끝나는 13:00부터는 가능
    ok = svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 13), kst(10, 20, 14)))
    assert ok.status == OrderStatus.CONFIRMED


def test_t04_new_reservation_buffer_also_protects_following_booking(svc, cam1, kim, lee):
    svc.set_buffer(60)
    svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 13), kst(10, 20, 15)))
    # 신규 10~12 + 버퍼 60 = 13:00 → 13:00 시작 예약과 끝-제외로 인접, 허용
    svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 10), kst(10, 20, 12)))
    with pytest.raises(ConflictError):
        svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 15, 30), kst(10, 20, 16)))


def test_t05_same_model_different_assets_both_allowed(svc, cam1, cam2, kim, lee):
    a = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    b = svc.reservations.create_confirmed(_res(lee, cam2, kst(10, 20, 10), kst(10, 21, 10)))
    assert a.status == b.status == OrderStatus.CONFIRMED


def test_t06_draft_does_not_occupy(svc, cam1, kim, lee):
    draft = svc.reservations.create_draft(_res(kim, cam1, kst(10, 20, 10), kst(10, 20, 12)))
    confirmed = svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 11), kst(10, 20, 13)))
    assert confirmed.status == OrderStatus.CONFIRMED
    # 이후 그 DRAFT를 확정하려면 충돌
    with pytest.raises(ConflictError):
        svc.reservations.confirm(draft.id)


def test_t13_overdue_unreturned_blocks_new_confirmation_and_warns(svc, cam1, kim, lee):
    svc.set_buffer(0)
    out = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 19, 10), kst(10, 20, 10)))
    follow = svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 22, 10), kst(10, 23, 10)))
    svc.force_checkout(out.id, kst(10, 19, 10))

    # 약정 반납 전: 후속 기간은 예약 가능
    assert svc.availability.overdue_items() == []
    assert cam1.id in {a.id for a in svc.availability.available_assets(kst(10, 25, 10), kst(10, 26, 10))}

    # 약정 반납 시각 1초 경과 → 미반납 연체
    svc.clock.now = kst(10, 20, 10, 0, 1)
    overdue = svc.availability.overdue_items()
    assert [(o.asset_code, o.order_no) for o in overdue] == [("CAM-1", out.order_no)]

    with pytest.raises(ConflictError) as exc:
        svc.reservations.create_confirmed(_res(lee, cam1, kst(11, 5, 10), kst(11, 6, 10)))
    assert exc.value.conflicts[0].kind == "OVERDUE"
    assert "연체" in exc.value.message
    assert svc.availability.available_assets(kst(11, 5, 10), kst(11, 6, 10)) == []

    # 기존 후속 확정 예약은 자동 취소하지 않고 경고
    risks = svc.availability.overdue_followup_risks()
    assert [(r.order_no, r.overdue.order_no) for r in risks] == [(follow.order_no, out.order_no)]
    assert svc.reservations.get_order(follow.id).status == OrderStatus.CONFIRMED

    # 실제 반납 후에는 버퍼 이후부터 다시 예약 가능
    svc.force_return(out.id, cam1.id, kst(10, 20, 11))
    svc.clock.now = kst(10, 20, 11, 30)
    assert svc.availability.overdue_items() == []
    ok = svc.reservations.create_confirmed(_res(lee, cam1, kst(11, 5, 10), kst(11, 6, 10)))
    assert ok.status == OrderStatus.CONFIRMED


def test_t14_maintenance_block_rejects_new_reservation(svc, cam1, kim):
    result = svc.maintenance.create_block(cam1.id, kst(10, 20, 0), None, "렌즈 파손 점검")
    assert result.affected_reservations == ()
    with pytest.raises(ConflictError) as exc:
        svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 25, 10), kst(10, 26, 10)))
    assert exc.value.conflicts[0].kind == "MAINTENANCE"
    assert "정비" in exc.value.message

    # 블록 이전 기간은 가능
    svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 19, 10), kst(10, 19, 20)))

    # 종료 시각 지정 후 그 시각부터 예약 가능
    svc.maintenance.close_block(result.block.id, kst(10, 24, 0))
    ok = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 24, 0), kst(10, 25, 0)))
    assert ok.status == OrderStatus.CONFIRMED


def test_maintenance_block_warns_existing_confirmed_reservation(svc, cam1, kim):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 22, 10), kst(10, 23, 10)))
    assert [c.order_no for c in svc.maintenance.preview_affected(cam1.id, kst(10, 21, 0), None)] == [
        order.order_no
    ]
    result = svc.maintenance.create_block(cam1.id, kst(10, 21, 0), kst(10, 22, 12), "점검")
    assert [c.order_no for c in result.affected_reservations] == [order.order_no]
    assert svc.reservations.get_order(order.id).status == OrderStatus.CONFIRMED


# --------------------------------------------------------------------------- lifecycle


def test_draft_then_confirm_snapshots_current_rates_and_policy(svc, cam1, kim):
    draft = svc.reservations.create_draft(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 11)))
    assert draft.status == OrderStatus.DRAFT
    assert draft.order_no == "R-20261019-0001"

    svc.assets.update_asset(cam1.id, AssetInput("카메라", "카메라", 40_000, 150_000))
    svc.settings.update_policy(grace_minutes=30, late_multiplier="1.5", turnaround_buffer_minutes=15)
    confirmed = svc.reservations.confirm(draft.id)

    item = confirmed.items[0]
    assert (item.daily_rate_snapshot_won, item.deposit_snapshot_won) == (40_000, 150_000)
    assert item.late_multiplier_snapshot == Decimal("1.5")
    assert item.grace_minutes_snapshot == 30
    assert confirmed.turnaround_buffer_snapshot_minutes == 15
    quote = svc.reservations.quote(confirmed)
    assert (quote.rental_days, quote.base_rental_fee_won) == (2, 80_000)


def test_snapshot_not_changed_by_later_rate_or_policy_change(svc, cam1, kim):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 11)))
    svc.assets.update_asset(cam1.id, AssetInput("카메라", "카메라", 99_000, 1))
    svc.settings.update_policy(grace_minutes=0, late_multiplier="3", turnaround_buffer_minutes=600)
    again = svc.reservations.get_order(order.id)
    assert again.items[0].daily_rate_snapshot_won == 30_000
    assert again.items[0].late_multiplier_snapshot == Decimal("1")
    assert again.turnaround_buffer_snapshot_minutes == 60
    assert svc.reservations.quote(again).base_rental_fee_won == 60_000


def test_t23_quote_for_25_hours(svc, cam1, kim):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 11)))
    assert svc.reservations.quote(order).expected_total_rental_fee_won == 60_000


def test_multi_asset_order_and_preview_quote(svc, cam1, cam2, kim):
    quote = svc.reservations.preview_quote([cam1.id, cam2.id], kst(10, 20, 10), kst(10, 21, 10), 5_000)
    assert quote.base_rental_fee_won == 60_000
    assert quote.expected_total_rental_fee_won == 55_000
    assert quote.expected_deposit_won == 200_000
    order = svc.reservations.create_confirmed(
        _res(kim, [cam1, cam2], kst(10, 20, 10), kst(10, 21, 10), discount_won=5_000)
    )
    assert {i.asset_code for i in order.items} == {"CAM-1", "CAM-2"}
    assert all(i.status == ItemStatus.RESERVED for i in order.items)


def test_order_numbers_sequential_per_seoul_date(svc, cam1, kim):
    a = svc.reservations.create_draft(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    b = svc.reservations.create_draft(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    svc.clock.now = kst(10, 20, 0, 30)  # UTC로는 아직 10-19지만 서울 기준 10-20
    c = svc.reservations.create_draft(_res(kim, cam1, kst(10, 22, 10), kst(10, 23, 10)))
    assert [a.order_no, b.order_no, c.order_no] == [
        "R-20261019-0001", "R-20261019-0002", "R-20261020-0001",
    ]


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("asset_ids", {"asset_ids": []}),
        ("asset_ids", {"asset_ids": "dup"}),
        ("asset_ids", {"asset_ids": [999]}),
        ("due_at", {"due_at": kst(10, 20, 10)}),
        ("discount_won", {"discount_won": 30_001}),
        ("discount_won", {"discount_won": -1}),
        ("customer_id", {"customer_id": 999}),
    ],
)
def test_input_validation(svc, cam1, kim, field, kwargs):
    data = dict(customer_id=kim.id, start_at=kst(10, 20, 10), due_at=kst(10, 20, 20),
                asset_ids=[cam1.id], discount_won=0)
    if kwargs.get("asset_ids") == "dup":
        kwargs = {"asset_ids": [cam1.id, cam1.id]}
    data.update(kwargs)
    with pytest.raises(ValidationError) as exc:
        svc.reservations.create_draft(ReservationInput(**data))
    assert exc.value.field == field
    assert svc.reservations.list_orders() == []


def test_max_30_assets_per_order(svc, kim):
    assets = [svc.add_asset(f"A-{i:02d}") for i in range(31)]
    with pytest.raises(ValidationError):
        svc.reservations.create_draft(_res(kim, assets, kst(10, 20, 10), kst(10, 21, 10)))
    order = svc.reservations.create_confirmed(_res(kim, assets[:30], kst(10, 20, 10), kst(10, 21, 10)))
    assert len(order.items) == 30


def test_confirmation_start_cannot_be_in_past(svc, cam1, kim):
    with pytest.raises(ValidationError) as exc:
        svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 19, 8), kst(10, 20, 10)))
    assert exc.value.field == "start_at"
    draft = svc.reservations.create_draft(_res(kim, cam1, kst(10, 19, 8), kst(10, 20, 10)))
    with pytest.raises(ValidationError):
        svc.reservations.confirm(draft.id)


def test_retired_asset_cannot_be_reserved(svc, cam1, kim):
    svc.assets.retire_asset(cam1.id)
    with pytest.raises(ValidationError):
        svc.reservations.create_draft(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    assert svc.availability.available_assets(kst(10, 20, 10), kst(10, 21, 10)) == []


def test_available_assets_lists_only_free_assets(svc, cam1, cam2, kim):
    svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    free = svc.availability.available_assets(kst(10, 20, 12), kst(10, 20, 18))
    assert [a.asset_code for a in free] == ["CAM-2"]


# --------------------------------------------------------------------------- update / cancel


def test_update_confirmed_rechecks_and_excludes_self(svc, cam1, cam2, kim, lee):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    other = svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 22, 10), kst(10, 23, 10)))
    # 자기 자신과는 충돌하지 않음: 기간 연장
    longer = svc.reservations.update_order(order.id, _res(kim, cam1, kst(10, 20, 10), kst(10, 21, 20)))
    assert longer.due_at == kst(10, 21, 20)
    assert svc.reservations.quote(longer).base_rental_fee_won == 60_000

    # 다른 확정 예약과 겹치게 연장 → 거부, 기존 값 유지
    with pytest.raises(ConflictError) as exc:
        svc.reservations.update_order(order.id, _res(kim, cam1, kst(10, 20, 10), kst(10, 22, 12)))
    assert exc.value.conflicts[0].order_no == other.order_no
    assert svc.reservations.get_order(order.id).due_at == kst(10, 21, 20)


def test_update_confirmed_keeps_existing_snapshot_new_item_gets_current_rate(svc, cam1, cam2, kim):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    svc.assets.update_asset(cam1.id, AssetInput("카메라", "카메라", 50_000, 0))
    svc.assets.update_asset(cam2.id, AssetInput("카메라", "카메라", 20_000, 0))
    updated = svc.reservations.update_order(
        order.id, _res(kim, [cam1, cam2], kst(10, 20, 10), kst(10, 21, 10))
    )
    rates = {i.asset_code: i.daily_rate_snapshot_won for i in updated.items}
    assert rates == {"CAM-1": 30_000, "CAM-2": 20_000}
    assert updated.status == OrderStatus.CONFIRMED


def test_update_confirmed_removing_asset_frees_it(svc, cam1, cam2, kim, lee):
    order = svc.reservations.create_confirmed(
        _res(kim, [cam1, cam2], kst(10, 20, 10), kst(10, 21, 10))
    )
    svc.reservations.update_order(order.id, _res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    ok = svc.reservations.create_confirmed(_res(lee, cam2, kst(10, 20, 10), kst(10, 21, 10)))
    assert ok.status == OrderStatus.CONFIRMED


def test_update_confirmed_cannot_move_start_into_past(svc, cam1, kim):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    with pytest.raises(ValidationError):
        svc.reservations.update_order(order.id, _res(kim, cam1, kst(10, 19, 8), kst(10, 21, 10)))


def test_draft_update_can_overlap_without_conflict(svc, cam1, kim, lee):
    svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    draft = svc.reservations.create_draft(_res(lee, cam1, kst(10, 25, 10), kst(10, 26, 10)))
    moved = svc.reservations.update_order(draft.id, _res(lee, cam1, kst(10, 20, 12), kst(10, 21, 12)))
    assert moved.status == OrderStatus.DRAFT


def test_cancel_before_checkout_frees_asset(svc, cam1, kim, lee):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    canceled = svc.reservations.cancel(order.id)
    assert canceled.status == OrderStatus.CANCELED
    assert all(i.status == ItemStatus.CANCELED for i in canceled.items)
    assert svc.conn.execute("SELECT COUNT(*) FROM charge_lines").fetchone()[0] == 0
    svc.reservations.create_confirmed(_res(lee, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    with pytest.raises(InvalidStateError):
        svc.reservations.cancel(order.id)
    with pytest.raises(InvalidStateError):
        svc.reservations.update_order(order.id, _res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))


def test_t18_cancel_after_checkout_rejected(svc, cam1, kim):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    svc.force_checkout(order.id, kst(10, 20, 10))
    with pytest.raises(InvalidStateError) as exc:
        svc.reservations.cancel(order.id)
    assert "출고" in exc.value.message
    with pytest.raises(InvalidStateError):
        svc.reservations.update_order(order.id, _res(kim, cam1, kst(10, 20, 10), kst(10, 22, 10)))
    assert svc.reservations.get_order(order.id).status == OrderStatus.IN_RENTAL


def test_confirm_twice_rejected(svc, cam1, kim):
    order = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    with pytest.raises(InvalidStateError):
        svc.reservations.confirm(order.id)


def test_unknown_order(svc):
    with pytest.raises(NotFoundError):
        svc.reservations.get_order(12345)


def test_list_orders_filters(svc, cam1, cam2, kim, lee):
    a = svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
    b = svc.reservations.create_draft(_res(lee, cam2, kst(10, 25, 10), kst(10, 26, 10)))
    assert [o.id for o in svc.reservations.list_orders(statuses=[OrderStatus.DRAFT])] == [b.id]
    assert [o.id for o in svc.reservations.list_orders(customer_id=kim.id)] == [a.id]
    in_period = svc.reservations.list_orders(period_start=kst(10, 21, 10), period_end=kst(10, 25, 10))
    assert in_period == []  # 둘 다 끝-제외 경계에 닿기만 함
    assert len(svc.reservations.list_orders(period_start=kst(10, 21, 9), period_end=kst(10, 25, 11))) == 2


# --------------------------------------------------------------------------- concurrency


def test_concurrent_confirmations_on_separate_connections_only_one_wins(db_path, svc, cam1, kim, lee):
    """두 연결이 동시에 같은 장비·겹치는 기간을 확정 → 정확히 1건만 성공 (PRD 1.4, 7.1)."""
    barrier = threading.Barrier(2)
    results: list[object] = []

    def worker(customer_id: int) -> None:
        conn = connect(db_path)
        try:
            service = ReservationService(conn, svc.clock)
            barrier.wait()
            try:
                service.create_confirmed(
                    ReservationInput(customer_id, kst(10, 20, 10), kst(10, 21, 10), [cam1.id])
                )
                results.append("ok")
            except ConflictError:
                results.append("conflict")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(c.id,)) for c in (kim, lee)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == ["conflict", "ok"]
    confirmed = svc.reservations.list_orders(statuses=[OrderStatus.CONFIRMED])
    assert len(confirmed) == 1


def test_stale_ui_precheck_does_not_bypass_transaction_recheck(db_path, svc, cam1, kim, lee):
    """화면 사전검사 시점엔 가능했어도, 저장 직전 다른 연결이 확정했다면 거부."""
    assert svc.availability.find_conflicts([cam1.id], kst(10, 20, 10), kst(10, 21, 10)) == []
    other = connect(db_path)
    try:
        ReservationService(other, svc.clock).create_confirmed(
            ReservationInput(lee.id, kst(10, 20, 9), kst(10, 20, 12), [cam1.id])
        )
    finally:
        other.close()
    with pytest.raises(ConflictError):
        svc.reservations.create_confirmed(_res(kim, cam1, kst(10, 20, 10), kst(10, 21, 10)))
