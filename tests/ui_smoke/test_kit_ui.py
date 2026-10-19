"""M4.6 R4 화면: 세트 탭·세트 창, 예약 마법사 세트 추가·대체, 출고·반납 체크리스트, 예약 상세."""

from __future__ import annotations

from rentdesk.domain.kits import KitDiscountKind
from rentdesk.domain.models import OrderStatus
from rentdesk.services.kits import KitInput, KitPick
from rentdesk.ui.dialogs.checkout_dialog import CheckoutDialog
from rentdesk.ui.dialogs.kit_dialog import KitDialog
from rentdesk.ui.dialogs.kit_pick_dialog import DROP, KitPickDialog
from rentdesk.ui.dialogs.order_detail import OrderDetailDialog
from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard
from rentdesk.ui.dialogs.return_dialog import ReturnDialog
from rentdesk.ui.pages.assets import AssetsPage
from tests.support import kst
from tests.ui_smoke.test_calendar_ui import FakeNav

START, DUE = kst(10, 20, 10), kst(10, 21, 10)


def _gear(svc):
    return {
        "CAM-1": svc.add_asset("CAM-1", rate=30_000, category="카메라"),
        "CAM-2": svc.add_asset("CAM-2", rate=30_000, category="카메라"),
        "LENS-1": svc.add_asset("LENS-1", rate=10_000, category="렌즈", name="렌즈"),
    }


def _kit(svc, gear, **kwargs):
    data = dict(name="카메라 세트", asset_ids=[gear["CAM-1"].id, gear["LENS-1"].id],
                checklist=["배터리", "충전기"], discount_won=5_000)
    data.update(kwargs)
    return svc.kits.create_kit(KitInput(**data))


def _book(svc, kit, ids=None):
    from rentdesk.services.reservations import ReservationInput

    ids = list(kit.asset_ids) if ids is None else ids
    return svc.reservations.create_confirmed(ReservationInput(
        customer_id=svc.add_customer().id, start_at=START, due_at=DUE, asset_ids=ids,
        kits=[KitPick(kit.id, ids)],
    ))


# --------------------------------------------------------------------------- 세트 탭·세트 창


def test_assets_page_has_kit_tab_with_warning(ctx, svc):
    gear = _gear(svc)
    _kit(svc, gear)
    page = AssetsPage(ctx, FakeNav())
    assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == ["장비 목록", "세트"]
    panel = page.kits
    assert panel.table.rowCount() == 1 and panel.table.item(0, 0).text() == "카메라 세트"
    assert panel.table.item(0, 1).text() == "CAM-1, LENS-1"
    assert panel.table.item(0, 4).text() == "40,000원"
    svc.assets.retire_asset(gear["LENS-1"].id)
    page.refresh()
    assert panel.table.item(0, 5).text() == "주의" and "LENS-1" in panel.warnings.text()


def test_kit_dialog_validation_then_create_percent(ctx, svc, ui):
    gear = _gear(svc)
    dlg = KitDialog(ctx)
    dlg.name.setText("렌즈 세트")
    assert not dlg.save()
    assert "1개 이상" in dlg.member_errors.text()
    dlg.add_asset(gear["CAM-1"].id)
    dlg.add_asset(gear["CAM-1"].id)
    assert "이미" in dlg.member_errors.text()
    dlg.add_asset(gear["LENS-1"].id)
    dlg.discount_kind.setCurrentIndex(dlg.discount_kind.findData(KitDiscountKind.PERCENT))
    dlg.discount_percent.setValue(10)
    assert "40,000원" in dlg.preview.text() and "4,000원" in dlg.preview.text()
    dlg.checklist.setPlainText("배터리\n배터리")
    assert not dlg.save()
    assert "두 번" in dlg.checklist_errors.text()
    dlg.checklist.setPlainText("배터리 2개\n\n가방")
    assert dlg.save()
    kit = dlg.result_kit
    assert kit.discount.kind == KitDiscountKind.PERCENT and str(kit.discount.percent) == "10"
    assert kit.checklist == ("배터리 2개", "가방")


def test_kit_dialog_edit_and_remove_member(ctx, svc):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    dlg = KitDialog(ctx, kit)
    assert dlg.discount_won.value() == 5_000 and dlg.checklist.toPlainText() == "배터리\n충전기"
    dlg.members.select_key(gear["LENS-1"].id)
    dlg.remove_selected()
    assert dlg.save()
    assert svc.kits.get_kit(kit.id).asset_ids == (gear["CAM-1"].id,)


def test_kits_panel_archive(ctx, svc, ui):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    page = AssetsPage(ctx, FakeNav())
    page.kits.table.select_key(kit.id)
    page.kits.toggle_archived()
    assert svc.kits.get_kit(kit.id).is_archived
    assert page.kits.table.rowCount() == 0


# --------------------------------------------------------------------------- 예약 마법사


def test_kit_pick_dialog_suggests_substitute(ctx, svc):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    svc.book(svc.add_customer("다른 고객").id, [gear["CAM-1"].id], START, DUE)
    dlg = KitPickDialog(ctx, START, DUE)
    assert "CAM-1" in dlg.notice.text()
    assert dlg.choices[0].currentData() == gear["CAM-2"].id  # 같은 카테고리 대체가 기본
    assert dlg.accept_pick()
    assert dlg.result_pick == KitPick(kit.id, [gear["CAM-2"].id, gear["LENS-1"].id])


def test_kit_pick_dialog_rejects_all_dropped(ctx, svc):
    gear = _gear(svc)
    _kit(svc, gear)
    dlg = KitPickDialog(ctx, START, DUE)
    for combo in dlg.choices:
        combo.setCurrentIndex(combo.findText(DROP))
    assert not dlg.accept_pick() and "담을 장비가 없습니다" in dlg.errors.text()


def test_wizard_books_kit_with_discount(ctx, svc, ui):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    customer = svc.add_customer()
    wz = ReservationWizard(ctx, customer_id=customer.id, start_at=START, due_at=DUE)
    assert wz.go_next()
    wz.apply_kit_pick(KitPick(kit.id, list(kit.asset_ids)), kit.name)
    assert "세트 1개" in wz.selected_label.text()
    kit_col = 7
    names = {wz.asset_table.item(r, 1).text(): wz.asset_table.item(r, kit_col).text()
             for r in range(wz.asset_table.rowCount())}
    assert names["CAM-1"] == "카메라 세트" and names["CAM-2"] == ""
    assert wz.go_next()
    assert "세트 할인(카메라 세트, 5,000원)" in wz.quote_label.text()
    assert "35,000원" in wz.quote_label.text()  # 40,000 − 5,000
    assert wz.save(confirm=True)
    order = wz.result_order
    assert order.kits[0].name == "카메라 세트"
    assert all(i.order_kit_id == order.kits[0].id for i in order.items)


def test_wizard_unchecking_kit_asset_removes_it_from_kit(ctx, svc):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    wz = ReservationWizard(ctx, customer_id=svc.add_customer().id, start_at=START, due_at=DUE)
    wz.go_next()
    wz.apply_kit_pick(KitPick(kit.id, list(kit.asset_ids)), kit.name)
    wz.set_selected([gear["CAM-1"].id])
    assert wz._kit_picks() == [KitPick(kit.id, [gear["CAM-1"].id])]
    wz.remove_kit(kit.id)
    assert wz._kit_picks() == [] and wz._selected == set()


def test_wizard_edit_loads_existing_kit(ctx, svc):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    order = _book(svc, kit)
    wz = ReservationWizard(ctx, order)
    assert wz._kit_picks() == [KitPick(kit.id, list(kit.asset_ids))]
    assert wz.kit_list.count() == 1 and "카메라 세트" in wz.kit_list.item(0).text()
    wz.go_next()
    wz.go_next()
    assert wz.save(confirm=False)
    assert svc.reservations.get_order(order.id).kits[0].id == order.kits[0].id


# --------------------------------------------------------------------------- 출고·반납·상세


def test_checkout_dialog_kit_checklist_warns_but_checks_out(ctx, svc, ui):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    order = _book(svc, kit)
    svc.clock.now = START
    dlg = CheckoutDialog(ctx, order.id)
    assert not dlg.kit_checks.isHidden()
    assert "세트 할인 5,000원" in dlg.amounts.text()
    boxes = dlg.kit_checks.boxes[order.kits[0].id]
    boxes[0].setChecked(True)
    assert "충전기" in dlg.kit_checks.warning.text()
    assert dlg.submit()
    assert any("확인하지 않은 구성품" in t and "충전기" in t for t in ui.texts("confirm"))
    after = svc.reservations.get_order(order.id)
    assert after.status == OrderStatus.IN_RENTAL and after.kits[0].checkout_missing == ["충전기"]


def test_checkout_dialog_without_kits_hides_checklist(ctx, svc):
    asset = svc.add_asset("CAM-9")
    order = svc.book(svc.add_customer().id, [asset.id], START, DUE)
    assert CheckoutDialog(ctx, order.id).kit_checks.isHidden()


def test_return_dialog_saves_kit_checks_and_detail_shows_them(ctx, svc, ui):
    gear = _gear(svc)
    kit = _kit(svc, gear)
    order = _book(svc, kit)
    svc.checkout(order.id, START, kit_checks={order.kits[0].id: [True, True]})
    svc.clock.now = kst(10, 21, 9)
    dlg = ReturnDialog(ctx, order.id)
    dlg.kit_checks.boxes[order.kits[0].id][1].setChecked(True)
    for row in dlg.rows:
        row.check.setChecked(True)
    assert dlg.submit()
    after = svc.reservations.get_order(order.id)
    assert after.kits[0].return_missing == ["배터리"]
    detail = OrderDetailDialog(ctx, order.id)
    assert "출고 구성품 모두 확인" in detail.header.text()
    assert "반납 구성품 <b>누락: 배터리</b>" in detail.header.text()
    assert detail.items.item(0, 7).text() == "카메라 세트"
