"""엑셀 가져오기의 칸 값 해석 (USABILITY_PLAN U10). DB·Qt와 무관한 순수 함수.

엑셀 칸에는 같은 뜻이 여러 모양으로 들어온다(30000 / 30000.0 / "30,000" / "30,000원").
여기서는 칸 하나를 서비스 입력값(문자열·원 단위 정수)으로 바꾸기만 하고, 길이·필수 여부 같은
업무 검증은 기존 서비스 검증(`AssetService`/`CustomerService`)을 그대로 쓴다.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import CustomerType

_WON_TEXT = re.compile(r"^-?\d+$")

CUSTOMER_TYPE_WORDS = {
    "개인": CustomerType.INDIVIDUAL,
    "individual": CustomerType.INDIVIDUAL,
    "사업자": CustomerType.BUSINESS,
    "법인": CustomerType.BUSINESS,
    "업체": CustomerType.BUSINESS,
    "business": CustomerType.BUSINESS,
}


def normalize_header(value: object) -> str:
    """머리글 비교용: 공백·별표·'(필수)'·'(선택)'을 없앤다."""
    if value is None:
        return ""
    text = str(value)
    for mark in ("(필수)", "(선택)", "*"):
        text = text.replace(mark, "")
    return "".join(text.split())


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def cell_text(value: object) -> str | None:
    """문자 칸. 숫자로 저장된 시리얼·모델명(12345, 12345.0)도 글자로 받는다. 빈 칸은 None."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M") if value.time() != time() else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def cell_phone(value: object) -> tuple[str | None, bool]:
    """전화번호 칸. 엑셀이 숫자로 바꿔 앞의 0이 사라진 경우(1012345678) 0을 붙인다.

    돌려주는 bool은 0을 붙였는지 여부(미리보기에서 알려준다).
    """
    if isinstance(value, bool):
        raise ValidationError("phone", "전화번호 값이 올바르지 않습니다.")
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        digits = str(int(value))
        if int(value) > 0 and not digits.startswith("0"):
            return "0" + digits, True
        return digits, False
    return cell_text(value), False


def cell_won(value: object, field: str, label: str, *, blank_as: int | None = None) -> int:
    """금액 칸 → 원 단위 정수. '30,000원'·'30000.0'은 받고, 소수·음수·글자는 거부한다."""
    if is_blank(value):
        if blank_as is not None:
            return blank_as
        raise ValidationError(field, f"{label}을(를) 입력해주세요.")
    if isinstance(value, bool):
        raise ValidationError(field, f"{label}은(는) 숫자로 입력해주세요.")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValidationError(field, f"{label}은(는) 원 단위 정수로 입력해주세요(소수 불가).")
        number = int(value)
    elif isinstance(value, int):
        number = value
    else:
        text = str(value).strip().replace(",", "").replace(" ", "")
        if text.endswith("원"):
            text = text[:-1]
        if text.endswith(".0"):
            text = text[:-2]
        if not _WON_TEXT.match(text):
            raise ValidationError(field, f"{label}은(는) 숫자로 입력해주세요: {str(value).strip()[:20]}")
        number = int(text)
    if number < 0:
        raise ValidationError(field, f"{label}은(는) 0 이상이어야 합니다.")
    return number


def cell_customer_type(value: object) -> CustomerType:
    """'개인'/'사업자'(빈 칸은 개인). 법인·업체도 사업자로 받는다."""
    if is_blank(value):
        return CustomerType.INDIVIDUAL
    word = str(value).strip()
    found = CUSTOMER_TYPE_WORDS.get(word.casefold())
    if found is None:
        raise ValidationError("customer_type", f"구분은 '개인' 또는 '사업자'로 입력해주세요: {word[:20]}")
    return found
