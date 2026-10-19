"""작은 사용성 수정: 휠로 값 바뀜 방지, 다크 모드 문서 미리보기, 정산 창 '자세히' 접기."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from rentdesk.services.reservations import ReservationInput
from rentdesk.ui import theme
from rentdesk.ui.dialogs.document_dialog import DocumentDialog
from rentdesk.ui.dialogs.settlement_dialog import SettlementDialog
from rentdesk.ui.widgets import DateEdit
from tests.support import kst


def _wheel(widget: QWidget) -> None:
    pos = QPointF(widget.rect().center())
    event = QWheelEvent(pos, QPointF(widget.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def _order(svc):
    cam = svc.add_asset("CAM-1", rate=30_000)
    return svc.reservations.create_confirmed(ReservationInput(
        customer_id=svc.add_customer().id, start_at=kst(10, 20, 10), due_at=kst(10, 21, 10),
        asset_ids=[cam.id],
    ))


def test_wheel_does_not_change_unfocused_inputs(qapp):
    theme.apply_theme(qapp, dark=False)
    host = QWidget()
    lay = QVBoxLayout(host)
    text = QLineEdit()  # 포커스는 다른 칸에 둔다
    lay.addWidget(text)
    spin, combo, day = QSpinBox(), QComboBox(), DateEdit()
    spin.setValue(5)
    combo.addItems(["가", "나", "다"])
    for w in (spin, combo, day):
        lay.addWidget(w)
    host.show()
    text.setFocus()
    qapp.processEvents()
    before_day = day.date()

    for w in (spin, combo, day):
        assert w.focusPolicy() == Qt.FocusPolicy.StrongFocus  # 휠만으로 포커스를 가져가지 않음
        _wheel(w)
    assert spin.value() == 5
    assert combo.currentIndex() == 0
    assert day.date() == before_day

    host.activateWindow()
    spin.setFocus()
    qapp.processEvents()
    if spin.hasFocus():  # 창 없는 플랫폼에서 포커스를 못 받으면 이 부분은 건너뛴다
        _wheel(spin)
        assert spin.value() == 6  # 선택한 칸은 휠로 바뀐다
    host.close()


def test_document_preview_is_paper_white_in_dark_mode(qapp, ctx, svc):
    theme.apply_theme(qapp, dark=True)
    try:
        dlg = DocumentDialog(ctx.documents.quote(_order(svc).id))
        dlg.show()
        qapp.processEvents()
        palette = dlg.preview.viewport().palette()
        assert palette.color(palette.ColorRole.Base).name() == "#ffffff"
        assert palette.color(palette.ColorRole.Text).name() == "#111111"
        dlg.close()
    finally:
        theme.apply_theme(qapp, dark=False)


def test_settlement_details_toggle_keeps_width_and_top_layout(qapp, ctx, svc):
    dlg = SettlementDialog(ctx, _order(svc).id)  # 출고 전이라 상세가 펼쳐진 채로 열린다
    dlg.show()
    qapp.processEvents()
    width = dlg.width()

    dlg._show_details(False)
    qapp.processEvents()
    collapsed = (dlg.width(), dlg.findChild(type(dlg.figures["amount_due"].parent())).geometry())
    dlg._show_details(True)
    qapp.processEvents()
    dlg._show_details(False)
    qapp.processEvents()

    assert dlg.width() == width
    assert (dlg.width(), dlg.findChild(type(dlg.figures["amount_due"].parent())).geometry()) == collapsed
    assert dlg.details.isHidden() and "자세히 보기" in dlg.details_btn.text()
    dlg.close()
