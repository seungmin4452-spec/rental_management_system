"""M4 화면 흐름: 백업/복원(S-09), 라이선스 확인 화면, 앱 컨트롤러, 패키징 자기 점검."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from rentdesk.app import AppController, AppPaths, bootstrap_database, run_self_check
from rentdesk.context import AppContext
from rentdesk.domain.timeutil import now_utc
from rentdesk.platform.device_id import FakeDeviceIdProvider
from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services.backup import data_summary
from rentdesk.services.licensing import LicenseService, issue_license
from rentdesk.ui.dialogs.license_dialog import GateChoice, LicenseDialog
from rentdesk.ui.main_window import MainWindow
from tests.support import Services


@pytest.fixture
def key():
    return SigningKey.generate()


@pytest.fixture
def license_service(tmp_path, key):
    return LicenseService(tmp_path / "license", FakeDeviceIdProvider("UI-PC"),
                          public_key_hex=key.verify_key.encode().hex())


def _license_file(tmp_path, key, service) -> Path:
    path = tmp_path / "받은 라이선스.json"
    path.write_text(issue_license(key, machine_code=service.machine_code(), customer_label="OO렌탈",
                                  issued_at=now_utc()), encoding="utf-8")
    return path


@pytest.fixture
def paths(tmp_path) -> AppPaths:
    root = tmp_path / "앱 데이터"
    (root / "data").mkdir(parents=True)
    (root / "backups").mkdir()
    (root / "demo").mkdir()
    return AppPaths(db=root / "data" / "rentdesk.db", backups=root / "backups",
                    demo_db=root / "demo" / "rentdesk_demo.db", license_dir=root / "license",
                    preferences=root / "preferences.json")


@pytest.fixture
def controller(qapp, ui, paths, license_service):
    conn = bootstrap_database(paths.db, paths.backups)
    ctl = AppController(conn, paths, license_service, LocalPreferences(paths.preferences))
    yield ctl
    if ctl.window is not None:
        ctl.window.close()
    ctl.close()
    ctl.real_conn.close()


# ------------------------------------------------------------------ 설정 화면 백업/복원


def test_settings_backup_and_banner(ctx, svc, ui, tmp_path):
    window = MainWindow(ctx)
    assert not window.backup_banner.isHidden()  # 첫 실행 백업 권고
    page = window.page_by_key["settings"]
    assert page.last_backup_label.text() == "아직 없음"
    svc.add_asset("CAM-1")
    result = page.backup_to(tmp_path / "설정 백업.zip")
    assert result is not None and result.path.exists()
    assert any("암호화되어 있지 않습니다" in t for t in ui.texts("info"))
    assert page.last_backup_label.text() != "아직 없음"
    assert window.backup_banner.isHidden()

    assert page.backup_to(tmp_path / "설정 백업.zip") is not None  # 덮어쓰기 확인(answer=True)
    assert any("덮어쓸까요" in t for t in ui.texts("confirm"))


def test_settings_restore_validates_and_emits(ctx, svc, ui, tmp_path):
    page = MainWindow(ctx).page_by_key["settings"]
    backup = page.backup_to(tmp_path / "b.zip").path
    emitted = []
    page.restore_requested.connect(emitted.append)

    ui.answer = False
    assert page.restore_from(backup) is None and not emitted
    ui.answer = True
    validated = page.restore_from(backup)
    assert emitted == [validated]
    assert any("보호 백업" in t for t in ui.texts("confirm"))
    validated.cleanup()

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"garbage")
    assert page.restore_from(bad) is None
    assert any("손상" in t for t in ui.texts("warn"))


def test_settings_backup_disabled_in_demo(conn, clock, ui, tmp_path):
    page = MainWindow(AppContext(conn, clock, is_demo=True)).page_by_key["settings"]
    assert not page.backup_btn.isEnabled() and not page.restore_btn.isEnabled()
    assert page.restore_from(tmp_path / "x.zip") is None


def test_settings_license_panel(conn, clock, ui, tmp_path, key, license_service):
    page = MainWindow(AppContext(conn, clock, license=license_service)).page_by_key["settings"]
    panel = page.license_panel
    assert "등록된 라이선스가 없습니다" in panel.status_label.text()
    assert panel.code.text().count("-") == 4
    other = tmp_path / "other.json"
    other.write_text(issue_license(key, machine_code="A" * 20, customer_label="x", issued_at=now_utc()),
                     encoding="utf-8")
    assert not panel.load_file(other)
    assert any("다른 PC용" in t for t in ui.texts("warn"))
    assert panel.load_file(_license_file(tmp_path, key, license_service))
    assert "OO렌탈" in panel.status_label.text()


# ------------------------------------------------------------------ 컨트롤러


def test_unlicensed_start_offers_demo_and_never_touches_real_data(controller, paths):
    svc = Services(controller.real_conn, now_utc)
    svc.add_asset("REAL-1")
    before = data_summary(controller.real_conn)
    file_hash = hashlib.sha256(paths.db.read_bytes()).hexdigest()

    controller.run_gate = lambda **kw: GateChoice.QUIT
    assert controller.start() is False and controller.window is None

    controller.run_gate = lambda **kw: GateChoice.DEMO
    assert controller.start() is True
    assert controller.in_demo
    # 예제 모드에서 '실제 데이터로 돌아가기' → 라이선스 없으면 다시 확인 화면, 예제 모드 유지
    seen = []
    controller.run_gate = lambda **kw: seen.append(kw) or GateChoice.QUIT
    controller.toggle_demo()
    assert seen == [{"from_demo": True}] and controller.in_demo

    assert data_summary(controller.real_conn) == before
    assert hashlib.sha256(paths.db.read_bytes()).hexdigest() == file_hash


def test_activation_from_gate_opens_real_data(controller, tmp_path, key, license_service):
    lic = _license_file(tmp_path, key, license_service)

    def gate(**kw):
        license_service.install(lic)
        return GateChoice.ACTIVATED

    controller.run_gate = gate
    assert controller.start() is True
    assert not controller.in_demo
    assert controller.window.page_by_key["settings"].license_panel is not None


def test_licensed_start_and_restore_restarts_ui(controller, paths, tmp_path, key, license_service, ui):
    license_service.install(_license_file(tmp_path, key, license_service))
    assert controller.start() and not controller.in_demo
    svc = Services(controller.real_conn, now_utc)
    svc.add_asset("BEFORE-BACKUP")
    page = controller.window.page_by_key["settings"]
    backup = page.backup_to(tmp_path / "복원용.zip").path
    svc.add_asset("AFTER-BACKUP")
    old_window = controller.window

    validated = page.restore_from(backup)  # restore_requested → controller.restore
    assert validated is not None
    assert controller.window is not old_window
    codes = {r[0] for r in controller.real_conn.execute("SELECT asset_code FROM assets")}
    assert codes == {"BEFORE-BACKUP"}
    assert any("복원했습니다" in t for t in ui.texts("info"))
    protections = list(paths.backups.glob("pre_restore_*.zip"))
    assert len(protections) == 1


def test_gate_dialog_choices(qapp, ui, license_service):
    called = []
    dlg = LicenseDialog(license_service, backup_real=called.append)
    assert dlg.choice == GateChoice.QUIT
    dlg._finish(GateChoice.DEMO)
    assert dlg.choice == GateChoice.DEMO


def test_self_check_writes_ok_result(qapp, tmp_path):
    out = tmp_path / "결과" / "selfcheck.json"
    assert run_self_check(out) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["checks"]["backup_counts"]["assets"] > 0
