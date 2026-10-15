"""예제 데이터 모드: 별도 DB에 실제 서비스로 시연 데이터를 만든다."""

import pytest

from rentdesk.app import create_demo_database
from rentdesk.domain.models import OrderStatus
from rentdesk.domain.timeutil import to_utc_str
from rentdesk.persistence.db import open_database
from rentdesk.services.dashboard import DashboardService
from rentdesk.services.sample_data import SAMPLE_CUSTOMERS, load_sample_data
from tests.support import FakeClock, kst


@pytest.mark.parametrize("now", [kst(10, 15, 9, 0), kst(10, 15, 23, 55), kst(1, 1, 0, 5)])
def test_sample_data_covers_demo_scenarios(tmp_path, now):
    conn = open_database(tmp_path / "demo.db")
    try:
        load_sample_data(conn, now)
        statuses = {r[0] for r in conn.execute("SELECT status FROM rental_orders")}
        assert statuses == {s.value for s in OrderStatus}
        d = DashboardService(conn, FakeClock(now)).snapshot()
        assert d.overdue
        assert d.followup_risks
        assert d.completed_unsettled_count >= 1
        assert d.maintenance
        assert d.upcoming
        # 부분 반납 중인 대여 주문
        partial = conn.execute(
            "SELECT COUNT(*) FROM rental_orders o WHERE o.status = 'IN_RENTAL' AND EXISTS"
            " (SELECT 1 FROM rental_items i WHERE i.order_id = o.id AND i.status = 'RETURNED')"
        ).fetchone()[0]
        assert partial == 1
        # 미래 시각 출고/반납 기록 없음
        assert conn.execute(
            "SELECT COUNT(*) FROM rental_items WHERE actual_return_at_utc > ?",
            (to_utc_str(now),),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_sample_customers_are_clearly_fictional_without_contacts(tmp_path):
    conn = open_database(tmp_path / "demo.db")
    try:
        load_sample_data(conn, kst(10, 15, 9))
        rows = conn.execute("SELECT name, phone, email FROM customers").fetchall()
        assert len(rows) == len(SAMPLE_CUSTOMERS)
        assert all("예제" in r["name"] and r["phone"] is None and r["email"] is None for r in rows)
    finally:
        conn.close()


def test_sample_data_refuses_non_empty_database(svc):
    svc.add_asset("CAM-1")
    with pytest.raises(RuntimeError):
        load_sample_data(svc.conn, kst(10, 19, 9))


def test_demo_database_is_recreated_and_never_the_real_db(tmp_path):
    demo = tmp_path / "demo" / "rentdesk_demo.db"
    demo.parent.mkdir()
    conn = create_demo_database(demo)
    conn.execute("INSERT INTO categories (name, is_archived) VALUES ('임시', 0)")
    conn.close()
    conn = create_demo_database(demo)  # 다시 시작하면 초기화
    try:
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE name = '임시'").fetchone()[0] == 0
    finally:
        conn.close()

    real = tmp_path / "data" / "rentdesk.db"
    real.parent.mkdir()
    real.write_bytes(b"real user data")
    with pytest.raises(ValueError):
        create_demo_database(real)
    assert real.read_bytes() == b"real user data"
