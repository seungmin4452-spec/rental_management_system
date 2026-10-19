"""M4.6 R3 화면: 설정 > 요금 계산 방식, 카테고리 관리 창의 요금 계산 탭, 예약 마법사·연장 근거 문구."""

from __future__ import annotations

from decimal import Decimal

from rentdesk.domain.rates import DEFAULT_RATE_RULE, DayCountMethod, RateRule
from rentdesk.ui.dialogs.category_dialog import CategoryDialog
from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard
from rentdesk.ui.pages.settings import SettingsPage
from rentdesk.ui.rate_rule_editor import RateRuleEditor
from tests.support import kst


def test_editor_round_trips_rule_and_previews(ui):
    editor = RateRuleEditor()
    rule = RateRule(day_count=DayCountMethod.CALENDAR, weekend_enabled=True, weekend_start_weekday=5,
                    weekend_start_minute=9 * 60 + 30, weekly_enabled=True, weekly_multiplier=Decimal("4.5"))
    editor.set_rule(rule)
    assert editor.rule() == rule
    assert not editor.return_grace.isEnabled()  # 날짜 기준에서는 반납 여유 시간 없음
    assert editor.weekly_multiplier.isEnabled() and not editor.monthly_multiplier.isEnabled()
    text = editor.preview.text()
    assert "10일: 75,000원 — 주 단가 적용: 1주 + 3일" in text
    assert "1박2일" in text


def test_settings_page_saves_shop_rule(ctx, svc, ui):
    page = SettingsPage(ctx, None)
    assert page.rate_editor.rule() == DEFAULT_RATE_RULE
    page.rate_editor.weekly_on.setChecked(True)
    assert page.save_rate_rule()
    assert svc.settings.get_rate_rule() == RateRule(weekly_enabled=True)
    assert "7일 = 일 요금×5" in ui.texts("confirm")[-1]


def test_settings_page_shows_rule_field_error(ctx, svc, ui):
    page = SettingsPage(ctx, None)
    page.rate_editor.weekend_on.setChecked(True)
    page.rate_editor.weekend_end_day.setCurrentIndex(4)  # 금 12:00 ~ 금 12:00
    assert not page.save_rate_rule()
    label = page.rate_editor.errors._labels["weekend_end_weekday"]
    assert "달라야" in label.text()
    assert svc.settings.get_rate_rule() == DEFAULT_RATE_RULE


def test_category_dialog_saves_rate_rule(ctx, svc, ui):
    camping = svc.assets.create_category("캠핑")
    dlg = CategoryDialog(ctx)
    dlg.table.selectRow(0)
    assert dlg.rate_use_default.isChecked() and not dlg.rate_editor.isEnabled()
    assert dlg.table.item(0, 4).text() == "24시간"
    dlg.rate_use_default.setChecked(False)
    dlg.rate_editor.day_count.setCurrentIndex(dlg.rate_editor.day_count.findData("CALENDAR"))
    dlg.rate_editor.weekend_on.setChecked(True)
    assert dlg.save()
    saved = svc.assets.list_categories()[0]
    assert saved.id == camping.id
    assert saved.rate_rule == RateRule(day_count=DayCountMethod.CALENDAR, weekend_enabled=True)
    assert dlg.table.item(0, 4).text() == "날짜 기준 · 주말" and dlg.table.item(0, 4).font().bold()
    dlg.table.selectRow(0)
    dlg.rate_use_default.setChecked(True)
    assert dlg.save()
    assert svc.assets.list_categories()[0].rate_rule is None


def test_category_dialog_rate_error_switches_tab(ctx, svc, ui):
    svc.assets.create_category("캠핑")
    dlg = CategoryDialog(ctx)
    dlg.table.selectRow(0)
    dlg.rate_use_default.setChecked(False)
    dlg.rate_editor.weekend_on.setChecked(True)
    dlg.rate_editor.weekend_end_day.setCurrentIndex(4)
    assert not dlg.save()
    assert dlg.tabs.currentIndex() == 1


def test_wizard_shows_long_rate_basis(ctx, svc):
    svc.settings.update_rate_rule(RateRule(weekly_enabled=True))
    cam = svc.add_asset("CAM-1", rate=10_000)
    wz = ReservationWizard(ctx, customer_id=svc.add_customer().id)
    wz.start.set_value(kst(10, 20, 10))
    wz.due.set_value(kst(10, 30, 10))
    assert "10일 (24시간 단위 올림" in wz.days_label.text()
    assert wz.go_next()
    wz.set_selected([cam.id])
    assert wz.go_next()
    text = wz.quote_label.text()
    assert "기본 대여료 80,000원" in text and "CAM-1: 주 단가 적용: 1주 + 3일" in text


def test_wizard_days_label_follows_calendar_rule(ctx, svc):
    svc.settings.update_rate_rule(RateRule(day_count=DayCountMethod.CALENDAR))
    wz = ReservationWizard(ctx, customer_id=svc.add_customer().id)
    wz.start.set_value(kst(10, 20, 14))
    wz.due.set_value(kst(10, 21, 12))
    assert wz.days_label.text().startswith("1일 (날짜 기준")


def test_extension_dialog_explains_waived_charge(ctx, svc, ui):
    from rentdesk.ui.dialogs.extension_dialog import ExtensionDialog

    svc.settings.update_rate_rule(RateRule(weekly_enabled=True))
    cam = svc.add_asset("CAM-1", rate=10_000)
    order = svc.book(svc.add_customer().id, [cam.id], kst(10, 19, 10), kst(10, 24, 10))
    svc.checkout(order.id, kst(10, 19, 10))
    dlg = ExtensionDialog(ctx, order.id)
    dlg.new_due.set_value(kst(10, 25, 10))
    assert "주·월·주말 단가가 적용되어 추가 요금이 없습니다" in dlg.preview.text()
