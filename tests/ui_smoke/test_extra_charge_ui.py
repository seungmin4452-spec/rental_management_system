"""M4.6 R5 화면: 반납 창 추가 비용(사유·내용), 정산 창 '추가 비용(사유)' 표기."""

from __future__ import annotations

from rentdesk.domain.extra_charges import ExtraChargeReason
from rentdesk.domain.models import ChargeKind
from rentdesk.services.kits import KitInput, KitPick
from rentdesk.services.reservations import ReservationInput
from rentdesk.ui.dialogs.return_dialog import ReturnDialog
from rentdesk.ui.dialogs.settlement_dialog import SettlementDialog
from tests.support import kst

R = ExtraChargeReason
START, DUE = kst(10, 20, 10), kst(10, 21, 10)


def _out(svc, with_kit=False):
    cam = svc.add_asset("CAM-1", rate=30_000)
    lens = svc.add_asset("LENS-1", rate=10_000, category="렌즈", name="렌즈")
    kits = []
    if with_kit:
        kit = svc.kits.create_kit(KitInput(name="카메라 세트", asset_ids=[cam.id, lens.id],
                                           checklist=["배터리", "충전기"]))
        kits = [KitPick(kit.id, [cam.id, lens.id])]
    order = svc.reservations.create_confirmed(ReservationInput(
        customer_id=svc.add_customer().id, start_at=START, due_at=DUE,
        asset_ids=[cam.id, lens.id], kits=kits,
    ))
    checks = {order.kits[0].id: [True, True]} if with_kit else {}
    svc.checkout(order.id, START, kit_checks=checks)
    svc.clock.now = kst(10, 21, 9)
    return order


def _row(dlg, code):
    return next(r for r in dlg.rows if r.asset_code == code)


def test_return_dialog_extra_charge_reason_and_settlement_label(ctx, svc, ui):
    order = _out(svc)
    dlg = ReturnDialog(ctx, order.id)
    row = _row(dlg, "CAM-1")
    assert [row.reason.itemText(i) for i in range(row.reason.count())] == [
        "파손", "분실", "세척", "구성품 누락", "기타"]
    assert not row.reason.isEnabled() and not row.extra_memo.isEnabled()  # 금액 0이면 잠김
    row.check.setChecked(True)
    row.damage.setValue(10_000)
    assert row.reason.isEnabled() and row.extra_memo.isEnabled()
    row.reason.setCurrentIndex(row.reason.findData(R.CLEANING))
    row.extra_memo.setText("모래 세척")
    assert "추가 비용 10,000원" in dlg.total.text()
    assert dlg.submit()
    assert any("추가 비용(세척) 10,000원" in t for t in ui.texts("confirm"))

    settle = SettlementDialog(ctx, order.id)
    kinds = [settle.charges.item(r, 1).text() for r in range(settle.charges.rowCount())]
    assert "추가 비용(세척)" in kinds and "파손비" not in kinds
    descs = [settle.charges.item(r, 3).text() for r in range(settle.charges.rowCount())]
    assert "CAM-1 추가 비용(세척): 모래 세척" in descs


def test_return_dialog_other_without_memo_is_rejected(ctx, svc, ui):
    order = _out(svc)
    dlg = ReturnDialog(ctx, order.id)
    row = _row(dlg, "CAM-1")
    row.check.setChecked(True)
    row.damage.setValue(5_000)
    row.reason.setCurrentIndex(row.reason.findData(R.OTHER))
    assert not dlg.submit()
    assert not [c for c in svc.settlements.get_settlement(order.id).charges if c.kind == ChargeKind.DAMAGE]


def test_return_dialog_missing_part_prefills_kit_items(ctx, svc, ui):
    order = _out(svc, with_kit=True)
    dlg = ReturnDialog(ctx, order.id)
    dlg.kit_checks.boxes[order.kits[0].id][0].setChecked(True)  # 충전기 빠짐
    row = _row(dlg, "CAM-1")
    row.check.setChecked(True)
    row.damage.setValue(8_000)
    row.reason.setCurrentIndex(row.reason.findData(R.MISSING_PART))
    assert row.extra_memo.text() == "카메라 세트: 충전기"
    assert dlg.submit()
    assert any("사유 '구성품 누락'" in t for t in ui.texts("confirm"))
    lines = [c.description for c in svc.settlements.get_settlement(order.id).charges
             if c.kind == ChargeKind.DAMAGE]
    assert lines == ["CAM-1 추가 비용(구성품 누락): 카메라 세트: 충전기"]


def test_return_dialog_missing_part_keeps_typed_memo(ctx, svc, ui):
    order = _out(svc, with_kit=True)
    dlg = ReturnDialog(ctx, order.id)
    row = _row(dlg, "LENS-1")
    row.damage.setValue(1_000)
    row.extra_memo.setText("렌즈 캡")
    row.reason.setCurrentIndex(row.reason.findData(R.MISSING_PART))
    assert row.extra_memo.text() == "렌즈 캡"
