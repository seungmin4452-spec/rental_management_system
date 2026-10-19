"""1개월 라이선스 만료·재등록, 만료 임박 안내, 업데이트 미구매 시 새 버전 차단 (KMONG_LAUNCH_PLAN K4·K5).

크몽 FAQ Q7 약속: 사용 기간이 끝나도 실제 데이터는 지워지지 않고, 새 라이선스를 등록하면 그대로 이어서 쓴다.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
from nacl.signing import SigningKey

from rentdesk.app import AppController, AppPaths, bootstrap_database
from rentdesk.domain.license_terms import expiry_end_utc
from rentdesk.platform.device_id import FakeDeviceIdProvider
from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services.backup import data_summary
from rentdesk.services.licensing import LicenseService, LicenseStatus, issue_license
from rentdesk.ui.dialogs.license_dialog import GateChoice
from tests.support import Services

EXPIRES = expiry_end_utc(date(2026, 11, 19))  # STANDARD 1개월: 2026-10-19 발급분


class MovableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def key():
    return SigningKey.generate()


@pytest.fixture
def clock():
    return MovableClock(datetime(2026, 10, 19, 3, 0, tzinfo=UTC))


@pytest.fixture
def paths(tmp_path) -> AppPaths:
    root = tmp_path / "앱 데이터"
    for sub in ("data", "backups", "demo"):
        (root / sub).mkdir(parents=True)
    return AppPaths(db=root / "data" / "rentdesk.db", backups=root / "backups",
                    demo_db=root / "demo" / "rentdesk_demo.db", license_dir=root / "license",
                    preferences=root / "preferences.json")


def _service(paths, key, clock, version="1.0.0") -> LicenseService:
    return LicenseService(paths.license_dir, FakeDeviceIdProvider("SHOP-PC"),
                          public_key_hex=key.verify_key.encode().hex(), clock=clock, app_version=version)


def _license(tmp_path, key, service, name, **kw):
    path = tmp_path / name
    path.write_text(issue_license(key, machine_code=service.machine_code(), customer_label="OO렌탈",
                                  issued_at=datetime(2026, 10, 19, 3, 0, tzinfo=UTC), **kw),
                    encoding="utf-8")
    return path


@pytest.fixture
def make_controller(qapp, ui, paths):
    made = []

    def make(service):
        conn = bootstrap_database(paths.db, paths.backups)
        ctl = AppController(conn, paths, service, LocalPreferences(paths.preferences))
        made.append(ctl)
        return ctl

    yield make
    for ctl in made:
        if ctl.window is not None:
            ctl.window.close()
        ctl.close()
        ctl.real_conn.close()


def _sidebar(ctl) -> str:
    return ctl.window.sidebar_status.text()


def test_expired_then_new_license_continues_real_data(tmp_path, key, clock, paths, make_controller):
    service = _service(paths, key, clock)
    service.install(_license(tmp_path, key, service, "1개월.json", expires_at=EXPIRES))
    ctl = make_controller(service)
    assert ctl.start() and not ctl.in_demo
    svc = Services(ctl.real_conn, clock)
    svc.add_asset("CAM-001")
    svc.add_asset("CAM-002")
    before = data_summary(ctl.real_conn)
    ctl.window.close()
    ctl.window = None
    ctl.real_conn.close()

    # 만료 다음 날 앱을 다시 켬 → 확인 화면, 실제 DB는 그대로
    clock.now = EXPIRES + timedelta(hours=10)
    ctl = make_controller(service)
    assert service.check().status == LicenseStatus.EXPIRED
    file_hash = hashlib.sha256(paths.db.read_bytes()).hexdigest()
    ctl.run_gate = lambda **kw: GateChoice.QUIT
    assert ctl.start() is False
    assert hashlib.sha256(paths.db.read_bytes()).hexdigest() == file_hash

    # 평생 라이선스(DELUXE)를 새로 받아 확인 화면에서 등록 → 같은 실제 데이터가 열림
    deluxe = _license(tmp_path, key, service, "평생.json")

    def gate(**kw):
        service.install(deluxe)
        return GateChoice.ACTIVATED

    ctl.run_gate = gate
    assert ctl.start() is True and not ctl.in_demo
    assert data_summary(ctl.real_conn) == before
    codes = {r[0] for r in ctl.real_conn.execute("SELECT asset_code FROM assets")}
    assert {"CAM-001", "CAM-002"} <= codes
    assert _sidebar(ctl) == "정품 · 이 PC 1대"


@pytest.mark.parametrize(("now", "expected"), [
    (datetime(2026, 11, 1, 3, 0, tzinfo=UTC), "정품 · 이 PC 1대"),       # 18일 남음
    (datetime(2026, 11, 12, 3, 0, tzinfo=UTC), "사용 기간이 7일 남았습니다"),
    (datetime(2026, 11, 19, 3, 0, tzinfo=UTC), "사용 기간이 오늘 끝납니다"),
])
def test_sidebar_expiry_notice(tmp_path, key, clock, paths, make_controller, now, expected):
    service = _service(paths, key, clock)
    service.install(_license(tmp_path, key, service, "1개월.json", expires_at=EXPIRES))
    clock.now = now
    ctl = make_controller(service)
    assert ctl.start()
    assert _sidebar(ctl) == expected
    panel = ctl.window.page_by_key["settings"].license_panel
    assert "사용 가능 버전: 1.0.x까지" in panel.status_label.text()
    assert ("크몽 주문 메시지" in panel.status_label.text()) == (expected != "정품 · 이 PC 1대")


def test_lifetime_license_has_no_expiry_notice(tmp_path, key, clock, paths, make_controller):
    service = _service(paths, key, clock)
    service.install(_license(tmp_path, key, service, "평생.json"))
    ctl = make_controller(service)
    assert ctl.start()
    assert _sidebar(ctl) == "정품 · 이 PC 1대"
    assert "기간 제한 없음" in ctl.window.page_by_key["settings"].license_panel.status_label.text()


def test_new_version_without_update_purchase_is_gated(tmp_path, key, clock, paths, make_controller):
    old = _service(paths, key, clock, "1.0.0")
    old.install(_license(tmp_path, key, old, "1.0.json", updates_through="1.0"))
    ctl = make_controller(old)
    assert ctl.start()
    Services(ctl.real_conn, clock).add_asset("KEEP-1")
    ctl.window.close()
    ctl.window = None
    ctl.real_conn.close()

    new = _service(paths, key, clock, "1.1.0")  # 1.1 설치 파일로 덮어 설치한 상황
    assert new.check().status == LicenseStatus.UPDATE_NOT_COVERED
    ctl = make_controller(new)
    seen = []
    ctl.run_gate = lambda **kw: seen.append(kw) or GateChoice.QUIT
    assert ctl.start() is False and seen
    assert ctl.auto_backup_on_exit() is None  # 실제 데이터를 쓰지 않았으므로 자동 백업도 건너뜀

    paid = _license(tmp_path, key, new, "1.1.json", updates_through="1.1")

    def gate(**kw):
        new.install(paid)
        return GateChoice.ACTIVATED

    ctl.run_gate = gate
    assert ctl.start() and not ctl.in_demo
    assert ctl.real_conn.execute("SELECT 1 FROM assets WHERE asset_code='KEEP-1'").fetchone()
