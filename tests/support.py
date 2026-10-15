"""테스트 공통 도우미: 서울 시각 헬퍼, 주입 가능한 시계, 서비스 묶음."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from rentdesk.domain.models import Asset, Customer, RentalOrder
from rentdesk.domain.timeutil import SEOUL
from rentdesk.services.assets import AssetInput, AssetService
from rentdesk.services.availability import AvailabilityService
from rentdesk.services.checkout import CheckoutInput, CheckoutService
from rentdesk.services.customers import CustomerInput, CustomerService
from rentdesk.services.dashboard import DashboardService
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
        self.customers = CustomerService(conn, clock)
        self.availability = AvailabilityService(conn, clock)
        self.reservations = ReservationService(conn, clock)
        self.maintenance = MaintenanceService(conn, clock)
        self.settings = SettingsService(conn, clock)
        self.checkout_svc = CheckoutService(conn, clock)
        self.returns = ReturnService(conn, clock)
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
