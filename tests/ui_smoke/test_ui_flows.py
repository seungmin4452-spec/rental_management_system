"""화면 → 서비스 연결 흐름 (S-02~S-09). 대화상자는 띄우지 않고 submit 메서드를 직접 호출한다."""

from datetime import date
from decimal import Decimal

from openpyxl import load_workbook

from rentdesk.domain.models import ItemStatus, MoneyKind, OrderStatus, ReturnCondition
from rentdesk.ui.dialogs.asset_dialog import AssetDetailDialog, AssetDialog
from rentdesk.ui.dialogs.block_dialog import BlockDialog
from rentdesk.ui.dialogs.checkout_dialog import CheckoutDialog
from rentdesk.ui.dialogs.customer_dialog import CustomerDialog
from rentdesk.ui.dialogs.order_detail import OrderDetailDialog
from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard
from rentdesk.ui.dialogs.return_dialog import ReturnDialog
from rentdesk.ui.dialogs.settlement_dialog import SettlementDialog
from rentdesk.ui.main_window import MainWindow
from tests.support import kst


# ---------------------------------------------------------------- S-02 / S-03


def test_asset_dialog_field_error_then_create(ctx, svc, ui):
    dlg = AssetDialog(ctx)
    dlg.code.setText("CAM-0001")
    dlg.category.setCurrentText("카메라")
    assert not dlg.submit()
    assert not dlg.errors._labels["name"].isHidden()  # 필드 옆 한국어 오류
    assert "장비명" in dlg.errors._labels["name"].text()
    assert svc.count("assets") == 0

    dlg.name.setText("미러리스 카메라 A")
    dlg.rate.setValue(30_000)
    dlg.deposit.setValue(100_000)
    assert dlg.submit()
    assert dlg.result_asset.asset_code == "CAM-0001"

    dup = AssetDialog(ctx)
    dup.code.setText("cam-0001")
    dup.name.setText("다른 장비")
    dup.category.setCurrentText("카메라")
    assert not dup.submit()
    assert any("이미 등록된 장비번호" in t for t in ui.texts("warn"))  # T-01 화면 경로


def test_asset_edit_keeps_code_read_only(ctx, svc):
    asset = svc.add_asset("CAM-1")
    dlg = AssetDialog(ctx, asset)
    assert dlg.code.isReadOnly()
    dlg.rate.setValue(45_000)
    assert dlg.submit()
    assert svc.assets.get_asset(asset.id).daily_rate_won == 45_000


def test_retire_with_confirmed_reservation_shows_order(ctx, svc, ui):
    asset = svc.add_asset("CAM-1")
    order = svc.book(svc.add_customer().id, [asset.id], kst(10, 20, 10), kst(10, 21, 10))
    window = MainWindow(ctx)
    page = window.page_by_key["assets"]
    page.table.select_key(asset.id)
    page.toggle_retire()
    assert any(order.order_no in t for t in ui.texts("warn"))
    window.close()


def test_customer_dialog_create_and_privacy_notice(ctx, svc):
    dlg = CustomerDialog(ctx)
    dlg.name.setText("   ")
    assert not dlg.submit()
    dlg.name.setText("예제 영상제작소")
    dlg.kind.setCurrentIndex(1)
    assert dlg.submit()
    assert dlg.result_customer.customer_type == "BUSINESS"


# ---------------------------------------------------------------- S-04


def _wizard_to_step3(ctx, customer_id, asset_ids, start, due):
    wz = ReservationWizard(ctx, customer_id=customer_id)
    wz.start.set_value(start)
    wz.due.set_value(due)
    assert wz.go_next()
    wz.set_selected(asset_ids)
    assert wz.go_next()
    return wz


def test_reservation_wizard_requires_customer(ctx, svc):
    wz = ReservationWizard(ctx)
    assert not wz.go_next()
    assert wz.stack.currentIndex() == 0
    assert "고객" in wz.errors1._labels["customer_id"].text()


def test_reservation_wizard_confirm_flow(ctx, svc, ui):
    a1, a2 = svc.add_asset("CAM-1"), svc.add_asset("CAM-2")
    k = svc.add_customer("홍길동")
    wz = _wizard_to_step3(ctx, k.id, [a1.id, a2.id], kst(10, 20, 10), kst(10, 21, 11))
    wz.discount.setValue(10_000)
    assert "110,000원" in wz.quote_label.text()  # 2대 × 30,000 × 2일 - 10,000
    assert "200,000원" in wz.quote_label.text()  # 보증금
    assert wz.save(confirm=True)
    order = wz.result_order
    assert order.status == OrderStatus.CONFIRMED
    assert order.discount_won == 10_000
    assert any(order.order_no in t for t in ui.texts("info"))


def test_reservation_wizard_shows_unavailable_with_reason(ctx, svc):
    a1 = svc.add_asset("CAM-1")
    k = svc.add_customer()
    existing = svc.book(k.id, [a1.id], kst(10, 20, 10), kst(10, 21, 10))
    wz = ReservationWizard(ctx, customer_id=k.id)
    wz.start.set_value(kst(10, 20, 15))
    wz.due.set_value(kst(10, 21, 15))
    wz.go_next()
    item = wz.asset_list.item(0)
    assert "사용 불가" in item.text()
    assert existing.order_no in item.toolTip()  # US-02: 충돌 예약번호 표시


def test_reservation_wizard_conflict_on_save_returns_to_step2(ctx, svc, ui):
    """화면 사전 검사 이후 다른 확정이 생겨도 저장 직전 재검사에서 거부 (T-02)."""
    a1 = svc.add_asset("CAM-1")
    k = svc.add_customer()
    wz = _wizard_to_step3(ctx, k.id, [a1.id], kst(10, 20, 10), kst(10, 21, 10))
    other = svc.book(svc.add_customer("다른고객").id, [a1.id], kst(10, 20, 11), kst(10, 20, 13))
    assert not wz.save(confirm=True)
    assert wz.stack.currentIndex() == 1
    assert any(other.order_no in t for t in ui.texts("warn"))
    assert len(svc.reservations.list_orders(statuses=[OrderStatus.CONFIRMED])) == 1


def test_reservation_wizard_draft_then_edit_and_confirm(ctx, svc):
    a1, a2 = svc.add_asset("CAM-1"), svc.add_asset("CAM-2")
    k = svc.add_customer()
    wz = _wizard_to_step3(ctx, k.id, [a1.id], kst(10, 20, 10), kst(10, 21, 10))
    assert wz.save(confirm=False)
    draft = wz.result_order
    assert draft.status == OrderStatus.DRAFT

    edit = ReservationWizard(ctx, draft)
    assert edit.customer.currentData() == k.id
    assert edit.go_next()
    edit.set_selected([a1.id, a2.id])
    assert edit.go_next()
    assert edit.save(confirm=True)
    after = svc.reservations.get_order(draft.id)
    assert after.status == OrderStatus.CONFIRMED
    assert {i.asset_id for i in after.items} == {a1.id, a2.id}


def test_order_detail_cancel_needs_confirmation(ctx, svc, ui):
    a = svc.add_asset("CAM-1")
    order = svc.book(svc.add_customer().id, [a.id], kst(10, 20, 10), kst(10, 21, 10))
    dlg = OrderDetailDialog(ctx, order.id)
    assert dlg.checkout_btn.isEnabled() and not dlg.return_btn.isEnabled()
    ui.answer = False
    assert not dlg.cancel_order()
    assert svc.reservations.get_order(order.id).status == OrderStatus.CONFIRMED
    ui.answer = True
    assert dlg.cancel_order()
    assert svc.reservations.get_order(order.id).status == OrderStatus.CANCELED
    assert not dlg.cancel_btn.isEnabled()


# ---------------------------------------------------------------- S-05 / S-06 / S-07


def _confirmed(svc, *codes):
    assets = [svc.add_asset(c) for c in codes]
    order = svc.book(svc.add_customer("고객A").id, [a.id for a in assets],
                     kst(10, 19, 10), kst(10, 20, 10))
    return order, assets


def test_checkout_dialog_blocks_early_then_checks_out_once(ctx, svc, ui):
    order, _ = _confirmed(svc, "CAM-1", "LEN-1")
    dlg = CheckoutDialog(ctx, order.id)
    assert not dlg.submit_btn.isEnabled()  # 09:00 < 예약 시작 10:00
    assert "예약 시작 시각" in dlg.blockers.text()

    svc.clock.now = kst(10, 19, 10, 5)
    dlg = CheckoutDialog(ctx, order.id)
    assert dlg.submit_btn.isEnabled()
    assert "60,000원" in dlg.amounts.text() and "200,000원" in dlg.amounts.text()
    dlg.deposit.setValue(200_000)
    dlg.note.setPlainText("외관 양호")
    assert dlg.submit()
    after = svc.reservations.get_order(order.id)
    assert after.status == OrderStatus.IN_RENTAL
    assert svc.settlements.summary(order.id).deposit_held_won == 200_000

    again = CheckoutDialog(ctx, order.id)  # T-07: 재출고 시도
    assert not again.submit_btn.isEnabled()
    assert not again.submit()
    assert svc.count("charge_lines") == 2


def test_return_dialog_partial_return_with_late_fee(ctx, svc, ui):
    order, (cam, lens) = _confirmed(svc, "CAM-1", "LEN-1")
    svc.checkout(order.id, kst(10, 19, 10))
    svc.clock.now = kst(10, 20, 11, 30)
    dlg = ReturnDialog(ctx, order.id)
    assert len(dlg.rows) == 2
    row = next(r for r in dlg.rows if r.asset_code == "CAM-1")
    row.check.setChecked(True)
    assert row.late_fee_won == 30_000  # 1시간 30분 지연 - 60분 유예 → 1일
    assert "연체 1일" in row.late.text()
    assert dlg.submit()
    after = svc.reservations.get_order(order.id)
    assert after.status == OrderStatus.IN_RENTAL  # T-08 화면 경로
    statuses = {i.asset_code: i.status for i in after.items}
    assert statuses == {"CAM-1": ItemStatus.RETURNED, "LEN-1": ItemStatus.OUT}

    again = ReturnDialog(ctx, order.id)  # 반납된 장비는 목록에서 제외(T-12)
    assert [r.asset_code for r in again.rows] == ["LEN-1"]


def test_return_dialog_last_item_warns_unsettled_and_affected(ctx, svc, ui):
    order, (cam,) = _confirmed(svc, "CAM-1")
    later = svc.book(svc.add_customer("후속").id, [cam.id], kst(10, 22, 10), kst(10, 23, 10))
    svc.checkout(order.id, kst(10, 19, 10))
    svc.clock.now = kst(10, 20, 9)
    dlg = ReturnDialog(ctx, order.id)
    dlg.rows[0].check.setChecked(True)
    dlg.rows[0].condition.setCurrentIndex(dlg.rows[0].condition.findData(
        ReturnCondition.MAINTENANCE_REQUIRED))
    assert dlg.submit()
    assert dlg.result.completed
    assert any("미정산" in t for t in ui.texts("info"))  # T-09: 완료와 정산은 별개
    assert any(later.order_no in t for t in ui.texts("warn"))  # US-06 영향 예약 경고


def test_settlement_dialog_rejects_over_refund_and_records(ctx, svc, ui):
    order, _ = _confirmed(svc, "CAM-1")
    svc.checkout(order.id, kst(10, 19, 10), deposit_in_won=100_000)
    dlg = SettlementDialog(ctx, order.id)
    assert "30,000원" in dlg.figures["amount_due"].text()

    dlg.kind.setCurrentIndex(dlg.kind.findData(MoneyKind.DEPOSIT_OUT))
    dlg.amount.setValue(150_000)  # 보유 100,000원 초과 (T-16)
    assert "초과" in dlg.errors._labels["amount_won"].text()
    assert not dlg.submit()
    assert svc.count("money_entries") == 1

    dlg.kind.setCurrentIndex(dlg.kind.findData(MoneyKind.DEPOSIT_APPLIED))
    assert not dlg.method.isEnabled()  # 내부 대체 거래는 결제수단 없음
    dlg.amount.setValue(30_000)
    assert "미수금 0원" in dlg.preview.text()
    ui.answer = False
    assert not dlg.submit()
    ui.answer = True
    assert dlg.submit()
    s = svc.settlements.summary(order.id)
    assert (s.amount_due_won, s.deposit_held_won) == (0, 70_000)
    assert "70,000원" in dlg.figures["deposit_held"].text()


def test_settlement_dialog_disabled_before_checkout(ctx, svc):
    order, _ = _confirmed(svc, "CAM-1")
    dlg = SettlementDialog(ctx, order.id)
    assert not dlg.record_btn.isEnabled()


# ---------------------------------------------------------------- S-08


def test_block_dialog_warns_affected_and_respects_cancel(ctx, svc, ui):
    order, (cam,) = _confirmed(svc, "CAM-1")
    dlg = BlockDialog(ctx, asset_id=cam.id)
    dlg.reason.setText("렌즈 점검")
    ui.answer = False
    assert not dlg.submit()
    assert any(order.order_no in t for t in ui.texts("confirm"))
    assert svc.count("maintenance_blocks") == 0
    ui.answer = True
    assert dlg.submit()
    assert svc.count("maintenance_blocks") == 1
    detail = AssetDetailDialog(ctx, cam.id)
    assert "정비 중" in detail.header.text()
    assert detail.blocks.rowCount() == 1


def test_block_dialog_requires_reason(ctx, svc):
    cam = svc.add_asset("CAM-1")
    dlg = BlockDialog(ctx, asset_id=cam.id)
    assert not dlg.submit()
    assert "사유" in dlg.errors._labels["reason"].text()


# ---------------------------------------------------------------- S-09


def test_reports_page_export_and_overwrite_confirm(ctx, svc, ui, tmp_path):
    svc.add_asset("CAM-1")
    window = MainWindow(ctx)
    page = window.page_by_key["reports"]
    target = tmp_path / "한글 경로" / "장비 목록.xlsx"
    target.parent.mkdir()
    assert page.export_to("assets", target) == target
    assert load_workbook(target)["장비 목록"]["A1"].value == "장비 목록"

    ui.answer = False
    assert page.export_to("assets", target) is None
    ui.answer = True
    page.export_to("assets", target)
    assert any("덮어쓸까요" in t for t in ui.texts("confirm"))
    for kind in ("reservations", "schedule", "overdue", "settlements"):
        assert page.export_to(kind, tmp_path / f"{kind}.xlsx") is not None
    window.close()


def test_reports_page_invalid_period_message(ctx, ui, tmp_path):
    window = MainWindow(ctx)
    page = window.page_by_key["reports"]
    page.res_from.set_value(date(2026, 10, 31))
    page.res_to.set_value(date(2026, 10, 1))
    assert page.export_to("reservations", tmp_path / "x.xlsx") is None
    assert any("종료일" in t for t in ui.texts("warn"))
    window.close()


def test_settings_page_policy_and_business(ctx, svc, ui):
    window = MainWindow(ctx)
    page = window.page_by_key["settings"]
    page.grace.setValue(30)
    page.multiplier.setValue(1.5)
    page.buffer.setValue(0)
    assert page.save_policy()
    p = svc.settings.get_policy()
    assert (p.grace_minutes, p.late_multiplier, p.turnaround_buffer_minutes) == (30, Decimal("1.5"), 0)
    page.biz_name.setText("예제 렌탈샵")
    assert page.save_business()
    assert svc.settings.get_business_info().name == "예제 렌탈샵"
    window.close()
