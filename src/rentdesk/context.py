"""앱 서비스 묶음(DI). 화면은 이 객체를 통해서만 서비스에 접근한다."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from rentdesk.services.assets import AssetService
from rentdesk.services.availability import AvailabilityService
from rentdesk.services.checkout import CheckoutService
from rentdesk.services.common import Clock, current_time, default_clock
from rentdesk.services.customers import CustomerService
from rentdesk.services.dashboard import DashboardService
from rentdesk.services.maintenance import MaintenanceService
from rentdesk.services.reports import ReportService
from rentdesk.services.reservations import ReservationService
from rentdesk.services.returns import ReturnService
from rentdesk.services.search import SearchService
from rentdesk.services.settings import SettingsService
from rentdesk.services.settlements import SettlementService


@dataclass
class AppContext:
    conn: sqlite3.Connection
    clock: Clock = default_clock
    is_demo: bool = False
    assets: AssetService = field(init=False)
    customers: CustomerService = field(init=False)
    availability: AvailabilityService = field(init=False)
    reservations: ReservationService = field(init=False)
    checkout: CheckoutService = field(init=False)
    returns: ReturnService = field(init=False)
    settlements: SettlementService = field(init=False)
    maintenance: MaintenanceService = field(init=False)
    settings: SettingsService = field(init=False)
    dashboard: DashboardService = field(init=False)
    reports: ReportService = field(init=False)
    search: SearchService = field(init=False)

    def __post_init__(self) -> None:
        c, k = self.conn, self.clock
        self.assets = AssetService(c, k)
        self.customers = CustomerService(c, k)
        self.availability = AvailabilityService(c, k)
        self.reservations = ReservationService(c, k)
        self.checkout = CheckoutService(c, k)
        self.returns = ReturnService(c, k)
        self.settlements = SettlementService(c, k)
        self.maintenance = MaintenanceService(c, k)
        self.settings = SettingsService(c, k)
        self.dashboard = DashboardService(c, k)
        self.reports = ReportService(c, k)
        self.search = SearchService(c)

    def now(self) -> datetime:
        return current_time(self.clock)
