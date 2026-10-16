"""M4.6 R1·R2 화면: 카테고리 관리 창, 장비 등록 창의 "기타", 예약 마법사 근거 문구."""

from __future__ import annotations

from rentdesk.ui.dialogs.asset_dialog import AssetDialog
from rentdesk.ui.dialogs.category_dialog import CategoryDialog
from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard
from tests.support import kst


def _select(dlg: CategoryDialog, name: str) -> None:
    for row in range(dlg.table.rowCount()):
        if dlg.table.item(row, 0).text().startswith(name):
            dlg.table.selectRow(row)
            return
    raise AssertionError(name)


def test_category_dialog_lists_system_last_and_locks_it(ctx, svc, ui):
    svc.assets.create_category("텐트")
    dlg = CategoryDialog(ctx)
    names = [dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())]
    assert names == ["텐트", "기타 (기본)"]
    _select(dlg, "기타")
    assert dlg.name.isReadOnly() and not dlg.archive_btn.isEnabled()


def test_category_dialog_saves_override_and_bolds_it(ctx, svc, ui):
    tent = svc.assets.create_category("텐트")
    dlg = CategoryDialog(ctx)
    _select(dlg, "텐트")
    assert dlg.buffer.use_default.isChecked() and not dlg.buffer.spin.isEnabled()
    assert "1시간" in dlg.buffer.use_default.text()  # 가게 기본값 안내
    dlg.buffer.use_default.setChecked(False)
    dlg.buffer.spin.setValue(720)
    dlg.name.setText("텐트·타프")
    assert dlg.save()
    saved = svc.assets.list_categories()[0]
    assert (saved.id, saved.name, saved.policy.turnaround_buffer_minutes) == (tent.id, "텐트·타프", 720)
    assert saved.policy.grace_minutes is None  # 체크한 항목은 가게 기본값 사용
    row = 0
    assert dlg.table.item(row, 1).text() == "12시간" and dlg.table.item(row, 1).font().bold()
    assert not dlg.table.item(row, 2).font().bold()
    assert dlg.changed


def test_category_dialog_shows_field_error(ctx, svc, ui):
    svc.assets.create_category("텐트")
    svc.assets.create_category("캠핑")
    dlg = CategoryDialog(ctx)
    _select(dlg, "텐트")
    dlg.name.setText("캠핑")
    assert not dlg.save()
    assert "이미 존재하는 카테고리" in ui.texts("warn")[-1]


def test_category_dialog_add_and_archive(ctx, svc, ui):
    dlg = CategoryDialog(ctx)
    dlg.new_name.setText("유모차")
    dlg.add_category()
    assert [c.name for c in svc.assets.list_categories()] == ["유모차", "기타"]
    _select(dlg, "유모차")
    dlg.toggle_archived()
    assert [c.name for c in svc.assets.list_categories()] == ["기타"]
    dlg.show_archived.setChecked(True)
    _select(dlg, "유모차")
    assert dlg.archive_btn.text() == "다시 사용"


def test_asset_dialog_lists_system_category_last(ctx, svc):
    svc.assets.create_category("텐트")
    dlg = AssetDialog(ctx)
    items = [dlg.category.itemText(i) for i in range(dlg.category.count())]
    assert items == ["텐트", "기타"]


def test_wizard_shows_category_basis_only_when_overridden(ctx, svc):
    cam = svc.add_asset("CAM-1", category="카메라")
    k = svc.add_customer()
    wz = ReservationWizard(ctx, customer_id=k.id)
    wz.start.set_value(kst(10, 20, 10))
    wz.due.set_value(kst(10, 21, 10))
    assert wz.go_next()
    wz.set_selected([cam.id])
    assert wz.go_next()
    assert "준비" not in wz.quote_label.text()  # 카테고리 설정이 없으면 지금처럼

    tent = svc.assets.create_category("텐트")
    svc.assets.update_category_policy(tent.id, turnaround_buffer_minutes=720, grace_minutes=None,
                                      late_multiplier="1.5")
    t = svc.add_asset("TENT-1", category="텐트")
    wz.go_prev()
    wz.set_selected([cam.id, t.id])
    assert wz.go_next()
    text = wz.quote_label.text()
    assert "텐트: 준비 12시간(카테고리 설정) · 연체 1일당 1.5배(카테고리 설정)" in text
    assert "카메라: 준비 1시간(가게 기본값)" in text
