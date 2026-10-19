"""M4.6 R6 화면: PDF 생성(한글·글꼴 포함·A4 1장), 미리보기 저장, 예약 상세 문서 메뉴, 출고·반납 후 확인서, 설정."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rentdesk.domain.documents import DEFAULT_RENTAL_TERMS, DocumentKind
from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services.customers import CustomerInput
from rentdesk.services.kits import KitInput, KitPick
from rentdesk.services.reservations import ReservationInput
from rentdesk.ui import document_output
from rentdesk.ui.dialogs.checkout_dialog import CheckoutDialog
from rentdesk.ui.dialogs.document_dialog import DocumentDialog
from rentdesk.ui.dialogs.order_detail import OrderDetailDialog
from rentdesk.ui.dialogs.return_dialog import ReturnDialog
from rentdesk.ui.pages.settings import SettingsPage
from tests.support import kst

START, DUE = kst(10, 20, 10), kst(10, 23, 10)
ROOT = Path(__file__).resolve().parents[2]


def _order(svc, *, confirm=True):
    svc.settings.update_business_info(name="테스트 렌탈", phone="02-123-4567")
    cam = svc.add_asset("CAM-1", name="미러리스 카메라")
    lens = svc.add_asset("LENS-1", rate=10_000, category="렌즈", name="렌즈")
    kit = svc.kits.create_kit(KitInput(name="카메라 세트", asset_ids=[cam.id, lens.id],
                                       checklist=["배터리", "충전기"]))
    customer = svc.customers.create_customer(CustomerInput(name="홍길동", phone="010-1111-2222"))
    data = ReservationInput(customer_id=customer.id, start_at=START, due_at=DUE,
                            asset_ids=[cam.id, lens.id], kits=[KitPick(kit.id, (cam.id, lens.id))])
    create = svc.reservations.create_confirmed if confirm else svc.reservations.create_draft
    return create(data)


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    """실제 앱과 같은 기본 플랫폼으로 PDF를 만들어 쪽수·글자를 읽는다(pdf_probe.py 참고)."""
    pytest.importorskip("PySide6.QtPdf")
    out = tmp_path_factory.mktemp("pdf")
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
    proc = subprocess.run([sys.executable, str(Path(__file__).with_name("pdf_probe.py")), str(out)],
                          capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    if proc.returncode != 0 and "qt.qpa" in proc.stderr:
        pytest.skip("화면(디스플레이) 없는 환경이라 기본 플랫폼으로 PDF를 확인할 수 없음")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_pdf_has_korean_text_embedded_font_and_fits_one_a4(probe):
    rental = probe["rental"]
    assert rental["pages"] == 1
    assert rental["font_embedded"]  # 글꼴을 PDF에 넣어 다른 PC에서도 한글이 보이게
    assert rental["to_unicode"]  # 글자 복사·검색 가능
    compact = "".join(rental["text"].split())
    for word in ("대여확인서", "홍길동", "010-1111-2222", "테스트렌탈", "CAM-1", "미러리스카메라",
                 "카메라세트", "배터리", "스크래치있음", "대여약관", "서명"):
        assert word in compact, word


@pytest.mark.parametrize("name, title", [("quote", "견적서"), ("return", "반납확인서")])
def test_other_documents_render_to_one_page(probe, name, title):
    assert probe[name]["pages"] == 1
    assert title in "".join(probe[name]["text"].split())


def test_pdf_bytes_in_test_platform(qapp, svc):
    """offscreen에서도 PDF 자체는 만들어진다(A4, PDF 머리글)."""
    order = _order(svc)
    data = document_output.pdf_bytes(svc.documents.quote(order.id))
    assert data.startswith(b"%PDF") and b"/MediaBox [0 0 595" in data


def test_save_pdf_adds_extension_and_reports(qapp, ctx, svc, ui, tmp_path):
    order = _order(svc)
    prefs = LocalPreferences(tmp_path / "prefs.json")
    dlg = DocumentDialog(ctx.documents.quote(order.id), preferences=prefs)
    assert "견적서" in dlg.preview.toPlainText()
    saved = dlg.save_to(tmp_path / "견적서_1")
    assert saved == tmp_path / "견적서_1.pdf"
    assert saved.read_bytes().startswith(b"%PDF")
    assert not (tmp_path / "견적서_1.pdf.tmp").exists()
    assert prefs.get("document_dir") == str(tmp_path)
    assert any("PDF로 저장" in t for t in ui.texts("info"))


def test_save_pdf_failure_warns_without_crash(qapp, ctx, svc, ui, tmp_path):
    order = _order(svc)
    dlg = DocumentDialog(ctx.documents.quote(order.id))
    assert dlg.save_to(tmp_path / "없는 폴더" / "a.pdf") is None
    assert any("저장하지 못했습니다" in t for t in ui.texts("warn"))


def test_order_detail_document_menu_follows_status(qapp, ctx, svc, ui, opened_documents):
    order = _order(svc, confirm=False)
    dlg = OrderDetailDialog(ctx, order.id)
    enabled = {k: a.isEnabled() for k, a in dlg.doc_actions.items()}
    assert enabled == {DocumentKind.QUOTE: True, DocumentKind.RENTAL: False, DocumentKind.RETURN: False}
    dlg.open_document(DocumentKind.QUOTE)
    assert opened_documents[-1].document.title == "견적서"
    dlg.confirm()
    assert dlg.doc_actions[DocumentKind.RENTAL].isEnabled()
    # 메뉴가 꺼진 문서를 직접 부르면 안내만 하고 창은 열지 않는다
    count = len(opened_documents)
    dlg.open_document(DocumentKind.RETURN)
    assert len(opened_documents) == count
    assert any("반납확인서" in t for t in ui.texts("warn"))


def test_checkout_opens_rental_confirmation(qapp, ctx, svc, ui, opened_documents, tmp_path):
    ctx.preferences = LocalPreferences(tmp_path / "prefs.json")
    order = _order(svc)
    svc.clock.now = START
    dlg = CheckoutDialog(ctx, order.id)
    assert dlg.open_doc.isChecked()
    assert dlg.submit()
    assert [d.document.title for d in opened_documents] == ["대여확인서"]
    assert ctx.preferences.get("open_rental_doc_after_checkout") is True


def test_checkout_without_document_remembers_choice(qapp, ctx, svc, ui, opened_documents, tmp_path):
    ctx.preferences = LocalPreferences(tmp_path / "prefs.json")
    order = _order(svc)
    svc.clock.now = START
    dlg = CheckoutDialog(ctx, order.id)
    dlg.open_doc.setChecked(False)
    assert dlg.submit()
    assert opened_documents == []
    order2 = svc.book(order.customer_id, [svc.add_asset("CAM-9").id], kst(10, 25, 10), kst(10, 26, 10))
    assert not CheckoutDialog(ctx, order2.id).open_doc.isChecked()


def test_return_opens_return_confirmation(qapp, ctx, svc, ui, opened_documents):
    order = _order(svc)
    svc.checkout(order.id, START)
    svc.clock.now = kst(10, 23, 9)
    dlg = ReturnDialog(ctx, order.id)
    dlg.rows[0].check.setChecked(True)
    assert dlg.submit()
    assert [d.document.title for d in opened_documents] == ["반납확인서"]


def test_settings_page_saves_terms_and_business(qapp, ctx, ui):
    page = SettingsPage(ctx, nav=None)
    assert page.terms.toPlainText() == DEFAULT_RENTAL_TERMS
    page.terms.setPlainText("1. 우리 약관")
    page.quote_days.setValue(10)
    assert page.save_documents()
    d = ctx.settings.get_document_settings()
    assert (d.rental_terms, d.quote_valid_days) == ("1. 우리 약관", 10)
    page.reset_terms()
    assert page.terms.toPlainText() == DEFAULT_RENTAL_TERMS
    assert ctx.settings.get_document_settings().rental_terms == "1. 우리 약관"  # 저장 전에는 그대로

    page.biz_name.setText("가게")
    page.biz_reg_no.setText("123-45-67890")
    page.biz_address.setText("서울시")
    assert page.save_business()
    b = ctx.settings.get_business_info()
    assert (b.registration_no, b.address) == ("123-45-67890", "서울시")


def test_settings_page_terms_too_long_shows_error(qapp, ctx, ui):
    page = SettingsPage(ctx, nav=None)
    page.terms.setPlainText("가" * 5001)
    assert "5,001" in page.terms_count.text()
    assert not page.save_documents()
    assert ctx.settings.get_document_settings().rental_terms == DEFAULT_RENTAL_TERMS
