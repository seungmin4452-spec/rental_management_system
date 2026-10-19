"""M4.6 R6: 문서 모델·HTML 변환·약관/유효기간 검증 (순수 함수)."""

from __future__ import annotations

import pytest

from rentdesk.domain.documents import (
    CHECKED,
    DEFAULT_RENTAL_TERMS,
    TERMS_MAX,
    UNCHECKED,
    DocAmount,
    DocBusiness,
    DocChecklist,
    Document,
    DocumentKind,
    DocTable,
    checklist_marks,
    clean_terms,
    plain_text,
    render_html,
    validate_quote_valid_days,
)
from rentdesk.domain.errors import ValidationError
from rentdesk.domain.extra_charges import short_text
from tests.support import kst


def _doc(**kwargs) -> Document:
    base = dict(
        kind=DocumentKind.RENTAL, business=DocBusiness(name="가게"), order_no="R-1",
        issued_at=kst(10, 19, 9), info=(("고객", "홍길동"),),
    )
    base.update(kwargs)
    return Document(**base)


def test_default_terms_cover_required_topics():
    for word in ("파손", "분실", "세척", "구성품", "연체", "보증금"):
        assert word in DEFAULT_RENTAL_TERMS
    assert clean_terms(DEFAULT_RENTAL_TERMS) == DEFAULT_RENTAL_TERMS


def test_clean_terms_trims_and_normalizes_newlines():
    assert clean_terms("  1. 가  \r\n2. 나\t \r\n\n") == "1. 가\n2. 나"
    assert clean_terms("   ") == ""


@pytest.mark.parametrize("value", [None, 3, "가" * (TERMS_MAX + 1)])
def test_clean_terms_rejects_invalid(value):
    with pytest.raises(ValidationError) as exc:
        clean_terms(value)
    assert exc.value.field == "rental_terms"


@pytest.mark.parametrize("value, ok", [(1, True), (365, True), (0, False), (366, False), (True, False),
                                       ("7", False), (7.0, False)])
def test_quote_valid_days(value, ok):
    if ok:
        assert validate_quote_valid_days(value) == value
    else:
        with pytest.raises(ValidationError):
            validate_quote_valid_days(value)


def test_checklist_marks():
    assert checklist_marks(("배터리", "충전기"), (True, False)) == [f"{CHECKED} 배터리", f"{UNCHECKED} 충전기"]
    assert checklist_marks(("배터리",), None) == [f"{UNCHECKED} 배터리"]
    assert checklist_marks(("배터리", "충전기"), (True,)) == [f"{UNCHECKED} 배터리", f"{UNCHECKED} 충전기"]


def test_titles_and_file_stem():
    assert _doc().title == "대여확인서"
    assert _doc(kind=DocumentKind.RETURN).title == "반납확인서"
    assert _doc(kind=DocumentKind.QUOTE).file_stem == "견적서_R-1"


def test_render_escapes_user_text():
    doc = _doc(
        business=DocBusiness(name="<b>가게</b>", address="서울 & 부산"),
        info=(("고객", '<script>"x"</script>'),),
        tables=(DocTable(("장비",), (("<i>CAM</i>\n근거<br>",),)),),
        memo=(("출고 메모", "<img src=x>"),),
        terms="1. <u>약관</u>",
    )
    html = render_html(doc)
    for raw in ("<b>가게</b>", "<script>", "<i>CAM</i>", "<img src=x>", "<u>약관</u>", "근거<br>"):
        assert raw not in html
    assert "&lt;script&gt;" in html and "서울 &amp; 부산" in html


def test_render_sections_follow_content():
    full = render_html(_doc(
        amounts=(DocAmount("대여료 합계", "10,000원", strong=True),),
        checklists=(DocChecklist("카메라 세트", ("배터리",), (True,)),),
        terms="1. 약관", signatures=("고객", "사업자"), notes=("안내",),
    ))
    assert "대여 약관" in full and "(서명)" in full and "카메라 세트" in full and "※ 안내" in full
    assert f"{CHECKED} 배터리" in full and "10,000원" in full
    bare = render_html(_doc())
    assert "대여 약관" not in bare and "(서명)" not in bare
    assert "R-1" in bare and "2026-10-19 09:00" in bare


def test_empty_table_shows_none_row():
    assert "없음" in render_html(_doc(tables=(DocTable(("장비",), ()),)))


def test_plain_text_contains_all_parts():
    text = plain_text(_doc(
        tables=(DocTable(("장비",), (("CAM-1",),), title="대여 장비"),),
        amounts=(DocAmount("보증금", "1원"),),
        checklists=(DocChecklist("세트", ("충전기",)),), terms="약관 본문",
    ))
    for word in ("대여확인서", "가게", "R-1", "고객 홍길동", "CAM-1", "보증금 1원", f"{UNCHECKED} 충전기", "약관 본문"):
        assert word in text


@pytest.mark.parametrize("desc, expected", [
    ("CAM-1 추가 비용(세척): 얼룩 심함", "세척: 얼룩 심함"),
    ("CAM-1 추가 비용(분실)", "분실"),
    ("CAM-1 파손비", "파손"),
    ("알 수 없는 줄", "알 수 없는 줄"),
    (None, ""),
])
def test_extra_charge_short_text(desc, expected):
    assert short_text(desc) == expected
