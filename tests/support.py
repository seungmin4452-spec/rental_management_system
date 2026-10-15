"""테스트 공통 도우미: 서울 시각 헬퍼, 주입 가능한 시계, 서비스 묶음."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from rentdesk.domain.models import Asset, Customer
from rentdesk.domain.timeutil import SEOUL, to_utc_str
from rentdesk.services.assets import AssetInput, AssetService
from rentdesk.services.availability import AvailabilityService
from rentdesk.services.customers import CustomerInput, CustomerService
from rentdesk.services.maintenance import MaintenanceService
from rentdesk.services.reservations import ReservationService
from rentdesk.services.settings import SettingsService


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

    def add_asset(self, code: str, *, rate: int = 30_000, deposit: int = 100_000,
                  category: str = "카메라", name: str = "미러리스 카메라 A") -> Asset:
        return self.assets.create_asset(
            AssetInput(name=name, category_name=category, daily_rate_won=rate,
                       deposit_won=deposit, asset_code=code)
        )

    def add_customer(self, name: str = "김고객") -> Customer:
        return self.customers.create_customer(CustomerInput(name=name))

    def set_buffer(self, minutes: int) -> None:
        policy = self.settings.get_policy()
        self.settings.update_policy(
            grace_minutes=policy.grace_minutes,
            late_multiplier=policy.late_multiplier,
            turnaround_buffer_minutes=minutes,
        )

    # M2(CheckoutService/ReturnService) 이전이므로 출고/반납 상태는 SQL로 직접 만든다.
    def force_checkout(self, order_id: int, at: datetime) -> None:
        self.conn.execute(
            "UPDATE rental_orders SET status = 'IN_RENTAL', actual_checkout_at_utc = ? WHERE id = ?",
            (to_utc_str(at), order_id),
        )
        self.conn.execute(
            "UPDATE rental_items SET status = 'OUT' WHERE order_id = ? AND status = 'RESERVED'",
            (order_id,),
        )

    def force_return(self, order_id: int, asset_id: int, at: datetime) -> None:
        self.conn.execute(
            "UPDATE rental_items SET status = 'RETURNED', actual_return_at_utc = ?,"
            " return_condition = 'GOOD' WHERE order_id = ? AND asset_id = ?",
            (to_utc_str(at), order_id, asset_id),
        )
