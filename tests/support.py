"""테스트 공통 도우미: 서울 시각 헬퍼, 주입 가능한 시계, 서비스 묶음."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from rentdesk.domain.models import Asset, Customer, RentalOrder
from rentdesk.domain.timeutil import SEOUL, to_utc_str
from rentdesk.persistence.db import transaction
from rentdesk.services.assets import AssetInput, AssetService
from rentdesk.services.availability import AvailabilityService
from rentdesk.services.calendar import CalendarService
from rentdesk.services.checkout import CheckoutInput, CheckoutService
from rentdesk.services.customers import CustomerInput, CustomerService
from rentdesk.services.dashboard import DashboardService
from rentdesk.services.extensions import ExtensionService
from rentdesk.services.kits import KitService
from rentdesk.services.maintenance import MaintenanceService
from rentdesk.services.reports import ReportService
from rentdesk.services.reservations import ReservationInput, ReservationService
from rentdesk.services.returns import ReturnItemInput, ReturnResult, ReturnService
from rentdesk.services.search import SearchService
from rentdesk.services.settings import SettingsService
from rentdesk.services.settlements import SettlementService


def kst(month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """2026년 서울 시각 헬퍼."""
    return datetime(2026, month, day, hour, minute, second, tzinfo=SEOUL)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


class Services:
    def __init__(self, conn: sqlite3.Connection, clock: FakeClock) -> None:
        self.conn = conn
        self.clock = clock
        self.assets = AssetService(conn, clock)
        self.kits = KitService(conn, clock)
        self.customers = CustomerService(conn, clock)
        self.availability = AvailabilityService(conn, clock)
        self.reservations = ReservationService(conn, clock)
        self.calendar = CalendarService(conn, clock)
        self.maintenance = MaintenanceService(conn, clock)
        self.settings = SettingsService(conn, clock)
        self.checkout_svc = CheckoutService(conn, clock)
        self.returns = ReturnService(conn, clock)
        self.extensions = ExtensionService(conn, clock)
        self.settlements = SettlementService(conn, clock)
        self.dashboard = DashboardService(conn, clock)
        self.reports = ReportService(conn, clock)
        self.search = SearchService(conn)

    def add_asset(self, code: str, *, rate: int = 30_000, deposit: int = 100_000,
                  category: str = "카메라", name: str = "미러리스 카메라 A") -> Asset:
        return self.assets.create_asset(
            AssetInput(name=name, category_name=category, daily_rate_won=rate,
                       deposit_won=deposit, asset_code=code)
        )

    def add_customer(self, name: str = "김고객") -> Customer:
        return self.customers.create_customer(CustomerInput(name=name))

    def book(self, customer_id: int, asset_ids: list[int], start: datetime, due: datetime,
             *, confirm: bool = True, discount: int = 0) -> RentalOrder:
        data = ReservationInput(customer_id=customer_id, start_at=start, due_at=due,
                                asset_ids=asset_ids, discount_won=discount)
        if confirm:
            return self.reservations.create_confirmed(data)
        return self.reservations.create_draft(data)

    def set_buffer(self, minutes: int) -> None:
        policy = self.settings.get_policy()
        self.settings.update_policy(
            grace_minutes=policy.grace_minutes,
            late_multiplier=policy.late_multiplier,
            turnaround_buffer_minutes=minutes,
        )

    def checkout(self, order_id: int, at: datetime, **kwargs: object) -> RentalOrder:
        """실제 CheckoutService로 출고. 시계가 출고 시각보다 이르면 출고 시각으로 맞춘다."""
        if self.clock.now < at:
            self.clock.now = at
        return self.checkout_svc.checkout(order_id, CheckoutInput(checkout_at=at, **kwargs))

    def return_asset(self, order_id: int, asset_id: int, at: datetime, **kwargs: object) -> ReturnResult:
        """실제 ReturnService로 장비 1대 반납. 시계가 반납 시각보다 이르면 반납 시각으로 맞춘다."""
        if self.clock.now < at:
            self.clock.now = at
        order = self.reservations.get_order(order_id)
        item = next(i for i in order.items if i.asset_id == asset_id)
        return self.returns.return_items(
            order_id, [ReturnItemInput(item_id=item.id, returned_at=at, **kwargs)]
        )

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def bulk_load_orders(conn: sqlite3.Connection, assets: int, orders: int) -> None:
    """성능 측정용 데이터를 SQL로 한 번에 넣는다(서비스 경로는 다른 테스트가 검증)."""
    created = "2026-01-01T00:00:00Z"
    with transaction(conn):  # 연결이 autocommit이라 명시적으로 묶는다
        conn.execute("INSERT INTO categories (name) VALUES ('카메라')")
        conn.execute(
            "INSERT INTO customers (name, created_at_utc) VALUES ('대량고객', ?)", (created,)
        )
        conn.executemany(
            "INSERT INTO assets (asset_code, name, category_id, daily_rate_won, deposit_won,"
            " created_at_utc) VALUES (?, '장비', 1, 30000, 100000, ?)",
            [(f"AST-{i:06d}", created) for i in range(1, assets + 1)],
        )
        base = kst(1, 1, 10)
        for n in range(orders):
            asset_id = n % assets + 1
            start = base + timedelta(days=(n // assets) * 15)
            due = start + timedelta(days=3)
            past = due < kst(10, 19)
            cur = conn.execute(
                "INSERT INTO rental_orders (order_no, customer_id, status, start_at_utc, due_at_utc,"
                " actual_checkout_at_utc, turnaround_buffer_snapshot_minutes, created_at_utc,"
                " updated_at_utc) VALUES (?, 1, ?, ?, ?, ?, 0, ?, ?)",
                (f"BULK-{n:06d}", "COMPLETED" if past else "CONFIRMED", to_utc_str(start),
                 to_utc_str(due), to_utc_str(start) if past else None, created, created),
            )
            conn.execute(
                "INSERT INTO rental_items (order_id, asset_id, status, daily_rate_snapshot_won,"
                " deposit_snapshot_won, late_multiplier_snapshot, grace_minutes_snapshot,"
                " actual_return_at_utc, return_condition) VALUES (?, ?, ?, 30000, 100000, '1', 60, ?, ?)",
                (cur.lastrowid, asset_id, "RETURNED" if past else "RESERVED",
                 to_utc_str(due) if past else None, "GOOD" if past else None),
            )
