"""M3 화면용 조회 API: 예약 목록 행, 장비 상태/선택지/이력, 정비 목록, 정산 목록, 검색, 설정."""

import pytest

from rentdesk.domain.availability import AssetState
from rentdesk.domain.errors import ConflictError, InvalidStateError, ValidationError
from rentdesk.domain.models import MoneyKind, OrderStatus
from rentdesk.services.assets import AssetInput
from rentdesk.services.customers import CustomerInput
from rentdesk.services.reservations import ReservationInput
from rentdesk.services.search import ResultKind
from rentdesk.services.settlements import MoneyInput
from tests.support import kst


def test_list_order_rows_query_and_progress(svc):
    a1, a2 = svc.add_asset("CAM-1"), svc.add_asset("CAM-2")
    kim, lee = svc.add_customer("김하늘"), svc.add_customer("이바다")
    o1 = svc.book(kim.id, [a1.id, a2.id], kst(10, 19, 10), kst(10, 20, 11), discount=5_000)
    o2 = svc.book(lee.id, [a1.id], kst(10, 25, 10), kst(10, 26, 10), confirm=False)
    svc.checkout(o1.id, kst(10, 19, 10))
    svc.return_asset(o1.id, a1.id, kst(10, 19, 15))

    rows = {r.order.id: r for r in svc.reservations.list_order_rows()}
    r1 = rows[o1.id]
    assert r1.customer_name == "김하늘"
    assert r1.item_count == 2
    assert r1.expected_total_rental_fee_won == 2 * 30_000 * 2 - 5_000  # 25시간 → 2일
    assert r1.progress_label == "부분 반납 (1/2)"
    assert rows[o2.id].progress_label == "출고 전"

    assert [r.order.id for r in svc.reservations.list_order_rows(query="바다")] == [o2.id]
    assert [r.order.id for r in svc.reservations.list_order_rows(query=o1.order_no)] == [o1.id]
    assert svc.reservations.list_order_rows(query="100%") == []  # LIKE 와일드카드 이스케이프


def test_canceled_order_row_keeps_item_count(svc):
    a = svc.add_asset("CAM-1")
    o = svc.book(svc.add_customer().id, [a.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.reservations.cancel(o.id)
    row = svc.reservations.list_order_rows(statuses=[OrderStatus.CANCELED])[0]
    assert row.item_count == 1
    assert row.progress_label == "-"


def test_asset_states_are_computed(svc):
    free, rented, maint, retired = (svc.add_asset(c) for c in ("A-1", "A-2", "A-3", "A-4"))
    k = svc.add_customer()
    o = svc.book(k.id, [rented.id], kst(10, 19, 10), kst(10, 20, 10))
    svc.checkout(o.id, kst(10, 19, 10))
    svc.maintenance.create_block(maint.id, kst(10, 19, 10), None, "점검")
    svc.assets.retire_asset(retired.id)
    assets = svc.assets.search_assets(include_retired=True)

    states = svc.availability.asset_states(assets)
    assert states[free.id] == AssetState.AVAILABLE
    assert states[rented.id] == AssetState.RENTED
    assert states[maint.id] == AssetState.MAINTENANCE
    assert states[retired.id] == AssetState.RETIRED

    svc.clock.now = kst(10, 20, 10, 0, 1)
    assert svc.availability.asset_states(assets)[rented.id] == AssetState.OVERDUE


def test_asset_options_show_conflict_reason(svc):
    a1, a2 = svc.add_asset("CAM-1"), svc.add_asset("CAM-2")
    k = svc.add_customer()
    existing = svc.book(k.id, [a1.id], kst(10, 20, 10), kst(10, 20, 12))

    options = {o.asset.id: o for o in svc.availability.asset_options(kst(10, 20, 11), kst(10, 20, 13))}
    assert not options[a1.id].available
    assert existing.order_no in options[a1.id].reason
    assert options[a2.id].available
    # 편집 중인 자기 예약은 제외
    own = {o.asset.id: o for o in svc.availability.asset_options(
        kst(10, 20, 11), kst(10, 20, 13), exclude_order_id=existing.id)}
    assert own[a1.id].available


def test_asset_orders_history(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer("고객A")
    past = svc.book(k.id, [a.id], kst(10, 19, 10), kst(10, 19, 12))
    future = svc.book(k.id, [a.id], kst(10, 22, 10), kst(10, 23, 10))
    svc.checkout(past.id, kst(10, 19, 10))
    svc.return_asset(past.id, a.id, kst(10, 19, 11))

    rows = svc.assets.asset_orders(a.id)
    assert [r.order_id for r in rows] == [future.id, past.id]
    assert rows[0].is_upcoming and not rows[1].is_upcoming
    assert rows[1].actual_return_at == kst(10, 19, 11)


def test_maintenance_list_and_state_label(svc):
    a = svc.add_asset("CAM-1")
    active = svc.maintenance.create_block(a.id, kst(10, 19, 8), None, "고장").block
    planned = svc.maintenance.create_block(a.id, kst(10, 25, 8), kst(10, 26, 8), "정기").block
    closed = svc.maintenance.create_block(a.id, kst(10, 18, 8), kst(10, 18, 9), "완료").block
    svc.maintenance.close_block(closed.id, kst(10, 18, 9))

    open_rows = {b.block_id: b for b in svc.maintenance.list_blocks()}
    assert set(open_rows) == {active.id, planned.id}
    now = svc.clock.now
    assert open_rows[active.id].state_label(now) == "진행 중"
    assert open_rows[planned.id].state_label(now) == "예정"
    all_rows = {b.block_id: b for b in svc.maintenance.list_blocks(include_closed=True)}
    assert all_rows[closed.id].state_label(now) == "종료"


def test_update_order_with_confirm_is_atomic(svc):
    a1, a2 = svc.add_asset("CAM-1"), svc.add_asset("CAM-2")
    k = svc.add_customer()
    svc.book(k.id, [a2.id], kst(10, 20, 10), kst(10, 21, 10))
    draft = svc.book(k.id, [a1.id], kst(10, 20, 10), kst(10, 21, 10), confirm=False)

    with pytest.raises(ConflictError):
        svc.reservations.update_order(
            draft.id, ReservationInput(k.id, kst(10, 20, 10), kst(10, 21, 10), [a1.id, a2.id]),
            confirm=True,
        )
    after = svc.reservations.get_order(draft.id)
    assert after.status == OrderStatus.DRAFT
    assert [i.asset_id for i in after.items] == [a1.id]  # 수정도 함께 롤백

    ok = svc.reservations.update_order(
        draft.id, ReservationInput(k.id, kst(10, 22, 10), kst(10, 23, 10), [a1.id, a2.id]),
        confirm=True,
    )
    assert ok.status == OrderStatus.CONFIRMED


def test_update_confirm_rejects_non_draft(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer()
    o = svc.book(k.id, [a.id], kst(10, 20, 10), kst(10, 21, 10))
    with pytest.raises(InvalidStateError):
        svc.reservations.update_order(
            o.id, ReservationInput(k.id, kst(10, 20, 10), kst(10, 21, 10), [a.id]), confirm=True
        )


def test_preview_quote_uses_snapshot_for_confirmed_edit(svc):
    a1, a2 = svc.add_asset("CAM-1", rate=30_000), svc.add_asset("CAM-2", rate=20_000)
    k = svc.add_customer()
    o = svc.book(k.id, [a1.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.assets.update_asset(a1.id, _with_rate(svc, a1.id, 50_000))

    q_edit = svc.reservations.preview_quote([a1.id, a2.id], kst(10, 20, 10), kst(10, 21, 10), order_id=o.id)
    assert q_edit.base_rental_fee_won == 30_000 + 20_000  # 기존 품목은 스냅샷 단가
    q_new = svc.reservations.preview_quote([a1.id, a2.id], kst(10, 20, 10), kst(10, 21, 10))
    assert q_new.base_rental_fee_won == 50_000 + 20_000


def _with_rate(svc, asset_id, rate):
    a = svc.assets.get_asset(asset_id)
    return AssetInput(name=a.name, category_name=a.category_name, daily_rate_won=rate,
                      deposit_won=a.deposit_won)


def test_settlement_list_rows(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer("고객A")
    o = svc.book(k.id, [a.id], kst(10, 19, 10), kst(10, 20, 10))
    svc.checkout(o.id, kst(10, 19, 10))
    rows = svc.settlements.list_rows()
    assert [(r.order.id, r.customer_name, r.summary.amount_due_won) for r in rows] == [
        (o.id, "고객A", 30_000)
    ]
    svc.settlements.record(o.id, MoneyInput(kind=MoneyKind.RENTAL_RECEIPT, amount_won=30_000))
    assert svc.settlements.list_rows() == []
    assert len(svc.settlements.list_rows(unsettled_only=False)) == 1


def test_global_search_finds_asset_customer_order_without_contacts(svc):
    a = svc.add_asset("CAM-0001", name="미러리스")
    k = svc.customers.create_customer(CustomerInput(name="홍길동", phone="010-1234-5678"))
    o = svc.book(k.id, [a.id], kst(10, 20, 10), kst(10, 21, 10))

    kinds = {(r.kind, r.entity_id) for r in svc.search.search("CAM-0001")}
    assert (ResultKind.ASSET, a.id) in kinds
    by_name = svc.search.search("길동")
    assert [(r.kind, r.entity_id) for r in by_name] == [(ResultKind.CUSTOMER, k.id)]
    assert all("010" not in r.title + r.detail for r in by_name)
    by_phone = svc.search.search("1234-5678")  # 전화로 찾되 결과에 노출하지 않음
    assert [r.entity_id for r in by_phone] == [k.id]
    by_order = svc.search.search(o.order_no)
    assert [(r.kind, r.entity_id) for r in by_order] == [(ResultKind.ORDER, o.id)]
    assert svc.search.search("   ") == []


def test_business_info_round_trip_and_validation(svc):
    assert svc.settings.get_business_info().name == ""
    info = svc.settings.update_business_info(name="  예제 렌탈  ", phone="02-000-0000")
    assert (info.name, info.phone) == ("예제 렌탈", "02-000-0000")
    with pytest.raises(ValidationError) as exc:
        svc.settings.update_business_info(name="가" * 101, phone="")
    assert exc.value.field == "business_name"
