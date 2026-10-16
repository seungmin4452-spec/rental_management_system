"""M4 화면 흐름: 백업/복원(S-09), 라이선스 확인 화면, 앱 컨트롤러, 패키징 자기 점검."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

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


# ------------------------------------------------------------------ 화면 크기


def test_settings_scale_choice_saved_per_device(conn, clock, ui, tmp_path):
    from rentdesk.ui import display_scale

    prefs = LocalPreferences(tmp_path / "preferences.json")
    window = MainWindow(AppContext(conn, clock, preferences=prefs))
    page = window.page_by_key["settings"]
    assert page.scale.currentData() == display_scale.AUTO
    page.scale.setCurrentIndex(page.scale.findData(110))
    assert page.save_scale()
    assert prefs.get(display_scale.PREF_KEY) == 110
    assert any("다시 시작" in t for t in ui.texts("info"))
    page.refresh()
    assert page.scale.currentData() == 110


def test_main_window_fits_available_screen(ctx):
    from PySide6.QtGui import QGuiApplication

    window = MainWindow(ctx)
    avail = QGuiApplication.primaryScreen().availableGeometry()
    assert window.minimumHeight() <= avail.height()
    assert window.minimumWidth() <= avail.width()


# ------------------------------------------------------------------ 도움말·화면 다듬기 (U2)


def test_help_menu_has_about_with_support_contact(ctx):
    from rentdesk.platform.resources import SupportInfo
    from rentdesk.ui.dialogs.about_dialog import AboutDialog

    window = MainWindow(ctx)
    menus = [a.text() for a in window.menuBar().actions()]
    assert menus == ["파일", "도움말"]
    dlg = AboutDialog(window, support=SupportInfo(contact="크몽 메시지로 문의"))
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    assert any("크몽 메시지로 문의" in t for t in texts)
    assert not [b for b in dlg.findChildren(QPushButton) if b.text() == "사용 안내 열기"]
    with_manual = AboutDialog(window, support=SupportInfo(contact="x", manual_url="https://example.com"))
    assert [b for b in with_manual.findChildren(QPushButton) if b.text() == "사용 안내 열기"]


def test_support_info_falls_back_on_bad_file(tmp_path, monkeypatch):
    from rentdesk.platform import resources

    (tmp_path / "support").mkdir()
    monkeypatch.setattr(resources, "get_resources_dir", lambda: tmp_path)
    assert resources.get_support_info() == resources.DEFAULT_SUPPORT  # 파일 없음
    (tmp_path / "support" / "support.json").write_text("{깨진 json", encoding="utf-8")
    assert resources.get_support_info() == resources.DEFAULT_SUPPORT
    (tmp_path / "support" / "support.json").write_text(
        '{"contact": "문의처", "manual_url": "http://insecure"}', encoding="utf-8")
    info = resources.get_support_info()
    assert info.contact == "문의처" and info.manual_url == ""  # https만 허용


def test_bundled_support_file_is_valid():
    from rentdesk.platform.resources import DEFAULT_SUPPORT, get_support_info

    assert get_support_info() != DEFAULT_SUPPORT


def test_highlighted_rows_get_soft_background(qapp):
    from rentdesk.ui import theme
    from rentdesk.ui.widgets import DataTable

    table = DataTable(["a", "b"])
    table.set_rows([(1, ["x", "y"]), (2, ["z", "w"])], highlight=lambda k: k == 1)
    assert table.item(0, 0).background().color() == theme.color("danger_soft")
    assert table.item(1, 0).background().style() == Qt.BrushStyle.NoBrush


def test_settings_number_fields_not_full_width(ctx):
    from rentdesk.ui.pages.settings import FIELD_WIDTH

    page = MainWindow(ctx).page_by_key["settings"]
    for w in (page.grace, page.multiplier, page.buffer, page.scale):
        assert w.maximumWidth() == FIELD_WIDTH


# ------------------------------------------------------------------ 자동 백업 (U3)


def test_auto_backup_on_exit_only_with_valid_license(controller, paths, tmp_path, key, license_service):
    assert controller.auto_backup_on_exit() is None  # 라이선스 없음 → 실제 데이터 미사용
    license_service.install(_license_file(tmp_path, key, license_service))
    result = controller.auto_backup_on_exit()
    assert result.path.parent == paths.backups / "auto" and result.path.exists()


def test_auto_backup_notice_shown_in_banner_and_settings(ctx, tmp_path):
    from rentdesk.services.auto_backup import NOTICE_KEY

    prefs = LocalPreferences(tmp_path / "preferences.json")
    prefs.set(NOTICE_KEY, "지난번 종료 때 자동 백업을 만들지 못했습니다.")
    ctx.preferences = prefs
    window = MainWindow(ctx)
    assert "자동 백업을 만들지 못했습니다" in window.backup_banner_label.text()
    page = window.page_by_key["settings"]
    assert page.auto_enabled.isChecked() and page.auto_enabled.isEnabled()
    assert not page.auto_copy.isEnabled()  # 기본 백업 폴더를 고르기 전
    page.set_backup_dir(tmp_path)
    assert page.auto_copy.isEnabled()
    page.auto_copy.setChecked(True)
    page.auto_enabled.setChecked(False)
    assert prefs.get("auto_backup_enabled") is False and prefs.get("auto_backup_copy_to_backup_dir") is True
    assert not page.auto_copy.isEnabled()


def test_auto_backup_disabled_in_demo(conn, clock, ui, tmp_path):
    window = MainWindow(AppContext(conn, clock, is_demo=True,
                                   preferences=LocalPreferences(tmp_path / "p.json")))
    page = window.page_by_key["settings"]
    assert not page.auto_enabled.isEnabled()
    assert "예제 데이터 모드" in page.auto_note.text()
