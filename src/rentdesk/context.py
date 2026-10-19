"""앱 서비스 묶음(DI). 화면은 이 객체를 통해서만 서비스에 접근한다."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services.assets import AssetService
from rentdesk.services.availability import AvailabilityService
from rentdesk.services.backup import BackupService
from rentdesk.services.calendar import CalendarService
from rentdesk.services.bulk_import import ImportService
from rentdesk.services.checkout import CheckoutService
from rentdesk.services.common import Clock, current_time, default_clock
from rentdesk.services.customers import CustomerService
from rentdesk.services.dashboard import DashboardService
from rentdesk.services.documents import DocumentService
from rentdesk.services.extensions import ExtensionService
from rentdesk.services.kits import KitService
from rentdesk.services.licensing import LicenseService
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
    license: LicenseService | None = None  # 앱 실행 시에만 주입(DB와 무관한 기기 단위 정보)
    preferences: LocalPreferences | None = None
    # 엑셀 가져오기 전 백업 폴더. 실제 데이터일 때만 앱이 넣는다(예제 데이터는 백업하지 않음).
    import_backup_dir: Path | None = None
    assets: AssetService = field(init=False)
    kits: KitService = field(init=False)
    customers: CustomerService = field(init=False)
    availability: AvailabilityService = field(init=False)
    reservations: ReservationService = field(init=False)
    calendar: CalendarService = field(init=False)
    checkout: CheckoutService = field(init=False)
    returns: ReturnService = field(init=False)
    extensions: ExtensionService = field(init=False)
    settlements: SettlementService = field(init=False)
    maintenance: MaintenanceService = field(init=False)
    settings: SettingsService = field(init=False)
    dashboard: DashboardService = field(init=False)
    reports: ReportService = field(init=False)
    documents: DocumentService = field(init=False)
    search: SearchService = field(init=False)
    backup: BackupService = field(init=False)
    imports: ImportService = field(init=False)

    def __post_init__(self) -> None:
        c, k = self.conn, self.clock
        self.assets = AssetService(c, k)
        self.kits = KitService(c, k)
        self.customers = CustomerService(c, k)
        self.availability = AvailabilityService(c, k)
        self.reservations = ReservationService(c, k)
        self.calendar = CalendarService(c, k)
        self.checkout = CheckoutService(c, k)
        self.returns = ReturnService(c, k)
        self.extensions = ExtensionService(c, k)
        self.settlements = SettlementService(c, k)
        self.maintenance = MaintenanceService(c, k)
        self.settings = SettingsService(c, k)
        self.dashboard = DashboardService(c, k)
        self.reports = ReportService(c, k)
        self.documents = DocumentService(c, k)
        self.search = SearchService(c)
        self.backup = BackupService(c, k)
        self.imports = ImportService(c, k)

    def now(self) -> datetime:
        return current_time(self.clock)
