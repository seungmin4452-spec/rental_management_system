"""M4.6 R6: 견적서·대여확인서·반납확인서 내용, 출력 가능 상태, 문서 설정(약관·유효기간·사업자 정보)."""

from __future__ import annotations

import pytest

from rentdesk.domain.documents import (
    CHECKED,
    DEFAULT_RENTAL_TERMS,
    UNCHECKED,
    DocumentKind,
    plain_text,
)
from rentdesk.domain.errors import InvalidStateError, ValidationError
from rentdesk.services.customers import CustomerInput
from rentdesk.services.kits import KitInput, KitPick
from rentdesk.services.reservations import ReservationInput
from rentdesk.services.returns import ReturnItemInput
from tests.support import kst

Q, RENT, RET = DocumentKind.QUOTE, DocumentKind.RENTAL, DocumentKind.RETURN
START, DUE = kst(10, 20, 10), kst(10, 23, 10)


@pytest.fixture
def setup(svc):
    svc.settings.update_business_info(name="테스트 렌탈", phone="02-123-4567",
                                      address="서울시 중구 1", registration_no="123-45-67890")
    cam = svc.add_asset("CAM-1", rate=30_000, deposit=100_000, name="미러리스 카메라")
    lens = svc.add_asset("LENS-1", rate=10_000, deposit=50_000, category="렌즈", name="표준 줌 렌즈")
    tripod = svc.add_asset("TRI-1", rate=5_000, deposit=0, category="삼각대", name="삼각대")
    customer = svc.customers.create_customer(CustomerInput(
        name="홍길동", phone="010-1111-2222", email="secret@example.com", note="VIP 고객 비밀 메모",
    ))
    kit = svc.kits.create_kit(KitInput(name="카메라 세트", asset_ids=[cam.id, lens.id],
                                       checklist=["배터리 2개", "충전기"], discount_won=5_000))
    return svc, cam, lens, tripod, customer, kit


def _book(setup, *, confirm=True):
    svc, cam, lens, tripod, customer, kit = setup
    data = ReservationInput(customer_id=customer.id, start_at=START, due_at=DUE,
                            asset_ids=[cam.id, lens.id, tripod.id], discount_won=3_000,
                            notes="내부 메모: 단골", kits=[KitPick(kit.id, (cam.id, lens.id))])
    return (svc.reservations.create_confirmed if confirm else svc.reservations.create_draft)(data)


def _text(svc, order_id, kind):
    return plain_text(svc.documents.build(order_id, kind))


def test_available_documents_follow_order_status(setup):
    svc, cam, *_ = setup
    draft = _book(setup, confirm=False)
    assert svc.documents.available(draft.id) == [Q]
    svc.reservations.confirm(draft.id)
    assert svc.documents.available(draft.id) == [Q, RENT]
    svc.checkout(draft.id, START)
    assert svc.documents.available(draft.id) == [RENT]
    svc.return_asset(draft.id, cam.id, kst(10, 22, 10))
    assert svc.documents.available(draft.id) == [RENT, RET]
    with pytest.raises(InvalidStateError):
        svc.documents.quote(draft.id)


def test_canceled_order_has_no_documents(setup):
    svc = setup[0]
    order = _book(setup)
    svc.reservations.cancel(order.id)
    assert svc.documents.available(order.id) == []
    with pytest.raises(InvalidStateError, match="대여확인서"):
        svc.documents.rental_confirmation(order.id)


def test_quote_content(setup):
    svc = setup[0]
    order = _book(setup, confirm=False)
    doc = svc.documents.quote(order.id)
    text = plain_text(doc)
    assert doc.title == "견적서" and doc.order_no == order.order_no
    assert "고객 홍길동 귀하" in text and "010-1111-2222" in text
    assert "견적 유효기간 2026-10-26까지 (견적일로부터 7일)" in text
    # 3일 × (30,000 + 10,000 + 5,000) = 135,000 − 할인 3,000 − 세트 5,000
    amounts = {a.label: a.value for a in doc.amounts}
    assert amounts["기본 대여료"] == "135,000원"
    assert amounts["할인"] == "-3,000원"
    assert amounts["세트 할인: 카메라 세트 (5,000원)"] == "-5,000원"
    assert amounts["대여료 합계 (3일)"] == "127,000원"
    assert amounts["보증금"] == "150,000원"
    assert "아직 예약이 확정되지 않았습니다" in text
    assert "세트 '카메라 세트' 구성품: 배터리 2개, 충전기" in text
    assert "보증금은 반납 후" in text
    assert doc.terms == "" and doc.signatures == ()


def test_quote_valid_days_setting(setup):
    svc = setup[0]
    svc.settings.update_document_settings(rental_terms="약관", quote_valid_days=30)
    order = _book(setup)
    text = _text(svc, order.id, Q)
    assert "2026-11-18까지 (견적일로부터 30일)" in text
    assert "아직 예약이 확정되지 않았습니다" not in text


def test_rental_confirmation_before_checkout_has_blank_checklist_and_terms(setup):
    svc = setup[0]
    order = _book(setup)
    doc = svc.documents.rental_confirmation(order.id)
    text = plain_text(doc)
    assert doc.terms == DEFAULT_RENTAL_TERMS and doc.signatures == ("고객", "사업자")
    assert f"{UNCHECKED} 배터리 2개" in text and CHECKED not in text
    assert "출고 때 표시" in doc.checklist_title
    rows = {r[0]: r for r in doc.tables[0].rows}
    assert rows["CAM-1"][1] == "미러리스 카메라"
    assert rows["CAM-1"][3].startswith("90,000원\n일 대여료 30,000원 × 3일")
    assert rows["LENS-1"][5] == "카메라 세트" and rows["TRI-1"][5] == ""


def test_rental_confirmation_after_checkout_uses_charges_and_checks(setup):
    svc, cam, *_ = setup
    order = _book(setup)
    svc.checkout(order.id, START, note="바디 하단 스크래치", rental_receipt_won=127_000,
                 deposit_in_won=150_000, kit_checks={order.kits[0].id: (True, False)})
    svc.clock.now = kst(10, 22, 9)
    svc.extensions.extend(order.id, kst(10, 24, 10))
    doc = svc.documents.rental_confirmation(order.id)
    text = plain_text(doc)
    amounts = {a.label: a.value for a in doc.amounts}
    assert amounts["기본 대여료"] == "135,000원"
    assert amounts["예약 할인"] == "-3,000원"
    assert amounts["세트 할인: 카메라 세트 (5,000원)"] == "-5,000원"
    assert amounts["연장 요금"] == "45,000원"
    assert amounts["대여료 합계"] == "172,000원"
    assert amounts["받은 대여료"] == "127,000원" and amounts["받은 보증금"] == "150,000원"
    assert f"{CHECKED} 배터리 2개" in text and f"{UNCHECKED} 충전기" in text
    assert "바디 하단 스크래치" in text
    assert "반납 예정 2026-10-24 10:00" in text
    assert "처음 반납 예정 2026-10-23 10:00 (연장 1회)" in text
    assert "출고 일시 2026-10-20 10:00" in text


def test_return_confirmation_content(setup):
    svc, cam, lens, tripod, *_ = setup
    order = _book(setup)
    svc.checkout(order.id, START, rental_receipt_won=127_000, deposit_in_won=150_000,
                 kit_checks={order.kits[0].id: (True, True)})
    svc.clock.now = kst(10, 23, 15)
    items = {i.asset_id: i for i in order.items}
    svc.returns.return_items(order.id, [
        ReturnItemInput(items[cam.id].id, returned_at=kst(10, 23, 15), damage_fee_won=20_000,
                        extra_charge_reason="CLEANING", extra_charge_memo="모래 오염", note="렌즈캡 헐거움"),
        ReturnItemInput(items[lens.id].id, returned_at=kst(10, 23, 9)),
    ], kit_checks={order.kits[0].id: (True, False)})
    doc = svc.documents.return_confirmation(order.id)
    text = plain_text(doc)
    rows = {r[0]: r for r in doc.tables[0].rows}
    assert rows["CAM-1"][2] == "2026-10-23 15:00" and rows["CAM-1"][3] == "정상"
    assert rows["CAM-1"][4] == "30,000원"  # 5시간 늦음 → 1일 × 30,000
    assert rows["CAM-1"][5] == "20,000원\n세척: 모래 오염"
    assert rows["CAM-1"][6] == "렌즈캡 헐거움"
    assert rows["LENS-1"][4] == "" and rows["TRI-1"][2] == "대여 중"
    assert "아직 반납하지 않은 장비: TRI-1" in text
    assert f"{CHECKED} 배터리 2개" in text and f"{UNCHECKED} 충전기" in text
    amounts = {a.label: a.value for a in doc.amounts}
    # 청구: 127,000 + 연체 30,000 + 추가 비용 20,000
    assert amounts["청구 합계"] == "177,000원"
    assert amounts["미수금"] == "50,000원"
    assert amounts["돌려줄 보증금"] == "150,000원"
    assert amounts["정산"] == "미정산"
    charge_kinds = [r[0] for r in doc.tables[1].rows]
    assert "연체료" in charge_kinds and "추가 비용(세척)" in charge_kinds


def test_documents_contain_only_name_and_phone(setup):
    svc, cam, lens, tripod, *_ = setup
    order = _book(setup)
    svc.checkout(order.id, START)
    for asset in (cam, lens, tripod):
        svc.return_asset(order.id, asset.id, kst(10, 23, 9))
    draft = _book(setup, confirm=False)
    texts = [_text(svc, order.id, RENT), _text(svc, order.id, RET), _text(svc, draft.id, Q)]
    for text in texts:
        assert "홍길동" in text and "010-1111-2222" in text
        assert "secret@example.com" not in text
        assert "VIP" not in text and "내부 메모" not in text


def test_documents_do_not_write_to_db(setup):
    svc = setup[0]
    order = _book(setup)
    before = svc.count("audit_events"), svc.count("charge_lines")
    svc.documents.rental_confirmation(order.id)
    svc.documents.quote(order.id)
    assert (svc.count("audit_events"), svc.count("charge_lines")) == before


# --------------------------------------------------------------------------- 설정


def test_document_settings_default_and_update(svc):
    d = svc.settings.get_document_settings()
    assert d.rental_terms == DEFAULT_RENTAL_TERMS and d.quote_valid_days == 7 and d.is_default_terms
    saved = svc.settings.update_document_settings(rental_terms="  1. 우리 가게 약관  \n", quote_valid_days=14)
    assert saved.rental_terms == "1. 우리 가게 약관" and saved.quote_valid_days == 14
    assert not saved.is_default_terms
    assert svc.settings.get_document_settings() == saved


def test_empty_terms_print_without_terms_section(setup):
    svc = setup[0]
    svc.settings.update_document_settings(rental_terms="", quote_valid_days=7)
    order = _book(setup)
    assert svc.documents.rental_confirmation(order.id).terms == ""


def test_changed_terms_appear_in_next_document(setup):
    svc = setup[0]
    order = _book(setup)
    svc.settings.update_document_settings(rental_terms="1. 새 약관 문구", quote_valid_days=7)
    assert svc.documents.rental_confirmation(order.id).terms == "1. 새 약관 문구"


@pytest.mark.parametrize("terms, days, field", [
    ("가" * 5001, 7, "rental_terms"),
    ("약관", 0, "quote_valid_days"),
    (None, 7, "rental_terms"),
])
def test_document_settings_validation_keeps_old_values(svc, terms, days, field):
    with pytest.raises(ValidationError) as exc:
        svc.settings.update_document_settings(rental_terms=terms, quote_valid_days=days)
    assert exc.value.field == field
    assert svc.settings.get_document_settings().rental_terms == DEFAULT_RENTAL_TERMS


def test_business_info_address_and_registration(svc):
    b = svc.settings.update_business_info(name="가게", phone="02-1", address=" 서울 ", registration_no="1-2-3")
    assert (b.address, b.registration_no) == ("서울", "1-2-3")
    # 주소·등록번호를 넘기지 않으면 기존 값 유지(예제 데이터 등 예전 호출 호환)
    b = svc.settings.update_business_info(name="가게2", phone="")
    assert (b.name, b.address, b.registration_no) == ("가게2", "서울", "1-2-3")
    with pytest.raises(ValidationError) as exc:
        svc.settings.update_business_info(name="가게", phone="", address="가" * 201)
    assert exc.value.field == "business_address"


def test_document_settings_survive_backup_restore_format(svc, conn):
    """설정은 app_settings에 JSON으로 저장되어 백업 파일(DB 사본)에 함께 들어간다."""
    svc.settings.update_document_settings(rental_terms="약관 A", quote_valid_days=3)
    rows = dict(conn.execute(
        "SELECT key, value_json FROM app_settings WHERE key IN ('rental_terms', 'quote_valid_days')"
    ).fetchall())
    assert rows == {"rental_terms": '"약관 A"', "quote_valid_days": "3"}
