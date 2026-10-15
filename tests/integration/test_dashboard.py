"""S-01 대시보드 집계 (P0-07): 대여 중, 오늘 출고/반납, 연체, 미정산, 7일 내 예약, 정비 중."""

from datetime import date

import pytest

from rentdesk.domain.models import OrderStatus
from tests.support import kst


@pytest.fixture
def scenario(svc):
    """시계 10/19 09:00에서 시작해 10/21 12:00(서울)로 이동한 운영 상황."""
    a1 = svc.add_asset("CAM-1")
    a2 = svc.add_asset("CAM-2")
    a3 = svc.add_asset("LEN-1", rate=10_000, deposit=30_000, category="렌즈")
    a5 = svc.add_asset("LGT-1", rate=20_000, category="조명")
    k1 = svc.add_customer("고객A")
    k2 = svc.add_customer("고객B")

    o1 = svc.book(k1.id, [a1.id], kst(10, 19, 10), kst(10, 20, 10))
    future = svc.book(k2.id, [a1.id], kst(10, 25, 10), kst(10, 26, 10))
    o3 = svc.book(k2.id, [a3.id], kst(10, 20, 9), kst(10, 21, 18))
    o2 = svc.book(k1.id, [a2.id], kst(10, 21, 13), kst(10, 22, 13))
    o4 = svc.book(k1.id, [a2.id], kst(10, 19, 10), kst(10, 19, 20))
    svc.checkout(o1.id, kst(10, 19, 10))
    svc.checkout(o4.id, kst(10, 19, 10))
    svc.return_asset(o4.id, a2.id, kst(10, 19, 19))
    svc.checkout(o3.id, kst(10, 20, 9))
    svc.maintenance.create_block(a5.id, kst(10, 20, 9), None, "점검")
    svc.clock.now = kst(10, 21, 12)
    return dict(a1=a1, a2=a2, a3=a3, a5=a5, o1=o1, o2=o2, o3=o3, o4=o4, future=future)


def test_dashboard_counts_today(svc, scenario):
    d = svc.dashboard.snapshot()

    assert d.day == date(2026, 10, 21)
    assert d.rented_asset_count == 2  # CAM-1(연체), LEN-1
    assert [r.order_id for r in d.checkouts_today] == [scenario["o2"].id]
    assert d.pending_checkouts == 1
    assert [r.order_id for r in d.returns_today] == [scenario["o3"].id]
    assert d.pending_returns == 1


def test_dashboard_overdue_with_expected_fee_not_charged(svc, scenario):
    d = svc.dashboard.snapshot()

    assert len(d.overdue) == 1
    row = d.overdue[0]
    assert (row.asset_code, row.customer_name) == ("CAM-1", "고객A")
    assert row.overdue_seconds == 26 * 3600
    # 26시간 지연 - 유예 60분 = 25시간 → 2일 × 30,000원
    assert row.expected_late_fee_won == 60_000
    # 예측 연체료는 청구 원장에 기록하지 않는다 (PRD 7.3)
    assert svc.conn.execute("SELECT COUNT(*) FROM charge_lines WHERE kind = 'LATE'").fetchone()[0] == 0


def test_dashboard_followup_risk_unsettled_upcoming_maintenance(svc, scenario):
    d = svc.dashboard.snapshot()

    assert [r.order_id for r in d.followup_risks] == [scenario["future"].id]
    unsettled = {u.order_id: u for u in d.unsettled}
    assert set(unsettled) == {scenario["o1"].id, scenario["o3"].id, scenario["o4"].id}
    assert d.completed_unsettled_count == 1
    assert unsettled[scenario["o4"].id].status == OrderStatus.COMPLETED
    assert [r.order.id for r in d.upcoming] == [scenario["o2"].id, scenario["future"].id]
    assert [b.asset_code for b in d.maintenance] == ["LGT-1"]


def test_dashboard_return_marks_schedule_done(svc, scenario):
    svc.return_asset(scenario["o3"].id, scenario["a3"].id, kst(10, 21, 12))

    d = svc.dashboard.snapshot()
    assert d.returns_today[0].done
    assert d.pending_returns == 0
    assert d.rented_asset_count == 1


def test_dashboard_other_day_schedule(svc, scenario):
    d = svc.dashboard.snapshot(date(2026, 10, 19))

    assert {r.order_id for r in d.checkouts_today} == {scenario["o1"].id, scenario["o4"].id}
    assert all(r.done for r in d.checkouts_today)
    assert [r.order_id for r in d.returns_today] == [scenario["o4"].id]
    assert d.returns_today[0].done


def test_dashboard_empty_database(svc):
    d = svc.dashboard.snapshot()
    assert d.rented_asset_count == 0
    assert not (d.checkouts_today or d.returns_today or d.overdue or d.unsettled or d.upcoming)


def test_draft_and_canceled_not_in_schedule(svc):
    a = svc.add_asset("CAM-1")
    k = svc.add_customer()
    svc.book(k.id, [a.id], kst(10, 19, 13), kst(10, 19, 18), confirm=False)
    canceled = svc.book(k.id, [a.id], kst(10, 19, 14), kst(10, 19, 16))
    svc.reservations.cancel(canceled.id)

    d = svc.dashboard.snapshot()
    assert d.checkouts_today == ()
    assert d.returns_today == ()
