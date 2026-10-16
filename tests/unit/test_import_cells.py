"""엑셀 가져오기 칸 값 해석 (USABILITY_PLAN U10)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.import_cells import (
    cell_customer_type,
    cell_phone,
    cell_text,
    cell_won,
    normalize_header,
)
from rentdesk.domain.models import CustomerType


@pytest.mark.parametrize(
    ("value", "expected"),
    [(50000, 50000), (50000.0, 50000), ("50000", 50000), ("50,000", 50000), ("50,000원", 50000),
     (" 50 000 원 ", 50000), ("0", 0), (0, 0), ("30000.0", 30000)],
)
def test_won_accepts_common_shapes(value, expected):
    assert cell_won(value, "f", "일 대여료") == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [(-1, "0 이상"), ("-1,000", "0 이상"), (1.5, "소수"), ("오만원", "숫자로"), ("1.5", "숫자로"),
     (True, "숫자로"), (None, "입력해주세요"), ("  ", "입력해주세요")],
)
def test_won_rejects(value, message):
    with pytest.raises(ValidationError) as err:
        cell_won(value, "f", "일 대여료")
    assert message in err.value.message


def test_won_blank_default():
    assert cell_won(None, "f", "보증금", blank_as=0) == 0
    assert cell_won("", "f", "보증금", blank_as=0) == 0


def test_text_from_numbers_and_dates():
    assert cell_text(12345) == "12345"
    assert cell_text(12345.0) == "12345"
    assert cell_text(1.5) == "1.5"
    assert cell_text("  A7M4 ") == "A7M4"
    assert cell_text("   ") is None
    assert cell_text(None) is None
    assert cell_text(date(2026, 1, 2)) == "2026-01-02"
    assert cell_text(datetime(2026, 1, 2)) == "2026-01-02"


def test_phone_restores_leading_zero():
    assert cell_phone(1012345678) == ("01012345678", True)
    assert cell_phone(1012345678.0) == ("01012345678", True)
    assert cell_phone("010-1234-5678") == ("010-1234-5678", False)
    assert cell_phone(None) == (None, False)


def test_customer_type_words():
    assert cell_customer_type(None) == CustomerType.INDIVIDUAL
    assert cell_customer_type("개인") == CustomerType.INDIVIDUAL
    assert cell_customer_type(" 사업자 ") == CustomerType.BUSINESS
    assert cell_customer_type("법인") == CustomerType.BUSINESS
    assert cell_customer_type("BUSINESS") == CustomerType.BUSINESS
    with pytest.raises(ValidationError):
        cell_customer_type("단체")


def test_normalize_header():
    assert normalize_header(" 장비명(필수) ") == "장비명"
    assert normalize_header("일 대여료 *") == "일대여료"
    assert normalize_header(None) == ""
