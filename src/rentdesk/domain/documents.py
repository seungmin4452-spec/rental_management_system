"""출력 문서(대여확인서·반납확인서·견적서) 모델과 HTML 변환 (MULTI_INDUSTRY_PLAN R6, 구 U8).

서비스가 예약·정산 데이터로 `Document`를 채우고, 이 모듈의 `render_html`이 Qt 리치 텍스트
(`QTextDocument`)가 읽을 수 있는 HTML로 바꾼다. PDF 저장·인쇄는 화면 계층(`ui/document_output.py`)이 한다.
DB·Qt 접근이 없는 순수 함수만 둔다. 사용자 입력 문자열은 모두 HTML 이스케이프한다.

개인정보: 문서에는 고객 이름과 연락처만 넣는다(이메일·고객 메모는 넣지 않는다).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.timeutil import format_seoul

TERMS_MAX = 5000
QUOTE_VALID_DAYS_DEFAULT = 7
QUOTE_VALID_DAYS_MAX = 365

# 기본 대여 약관(업종 공통 초안). 판매자 확인 필요 — 설정 화면에 "법적 효력 보장 안 함" 안내를 함께 둔다.
DEFAULT_RENTAL_TERMS = """\
1. 대여 기간: 대여 기간은 이 확인서에 적힌 대여 시작부터 반납 예정 일시까지입니다. 연장하려면 반납 예정 일시 전에 가게와 협의해야 합니다.
2. 연체: 반납 예정 일시가 지나면 가게가 정한 기준에 따라 연체료를 청구합니다.
3. 파손·분실: 대여 기간 중 장비나 구성품이 파손·분실되면 수리비 또는 같은 제품 구입 비용을 기준으로 배상해야 합니다.
4. 세척: 사용 후 심한 오염이 있으면 세척 비용을 청구할 수 있습니다.
5. 구성품: 출고 때 함께 확인한 구성품이 반납 때 빠져 있으면 그 구성품 비용을 청구합니다.
6. 보증금: 보증금은 반납 후 장비 상태를 확인한 뒤 돌려드립니다. 연체료·파손 등 추가 비용이 있으면 보증금에서 뺀 나머지를 돌려드립니다.
7. 사용: 장비는 용도에 맞게 사용하며 다른 사람에게 다시 빌려주지 않습니다.
8. 위 내용과 다르게 정한 사항은 가게와 고객이 따로 합의한 내용을 따릅니다."""

TERMS_NOTICE = (
    "기본 약관은 참고용 예시이며 법적 효력을 보장하지 않습니다. "
    "가게 운영 방식에 맞게 고쳐 쓰고, 필요하면 전문가의 검토를 받으세요."
)

CHECKED = "■"  # KS X 1001 기호(한글 글꼴에 있음)
UNCHECKED = "□"


class DocumentKind(StrEnum):
    RENTAL = "RENTAL"  # 대여확인서
    RETURN = "RETURN"  # 반납확인서
    QUOTE = "QUOTE"  # 견적서


DOCUMENT_TITLES = {
    DocumentKind.RENTAL: "대여확인서",
    DocumentKind.RETURN: "반납확인서",
    DocumentKind.QUOTE: "견적서",
}


@dataclass(frozen=True)
class DocBusiness:
    name: str = ""
    phone: str = ""
    address: str = ""
    registration_no: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.name or self.phone or self.address or self.registration_no)


@dataclass(frozen=True)
class DocTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    title: str = ""
    right_cols: tuple[int, ...] = ()  # 금액 칸(오른쪽 정렬)


@dataclass(frozen=True)
class DocAmount:
    label: str
    value: str
    strong: bool = False


@dataclass(frozen=True)
class DocChecklist:
    """세트 구성품 체크리스트. checks가 None이면 손으로 표시할 빈 칸(□)."""

    title: str
    labels: tuple[str, ...]
    checks: tuple[bool, ...] | None = None


@dataclass(frozen=True)
class Document:
    kind: DocumentKind
    business: DocBusiness
    order_no: str
    issued_at: datetime
    info: tuple[tuple[str, str], ...]  # (항목, 값) — 고객·기간 등
    tables: tuple[DocTable, ...] = ()
    amounts: tuple[DocAmount, ...] = ()
    amounts_title: str = "금액"
    checklists: tuple[DocChecklist, ...] = ()
    checklist_title: str = "세트 구성품 확인"
    notes: tuple[str, ...] = ()  # 안내 문구(유효기간·보증금 등)
    memo: tuple[tuple[str, str], ...] = ()  # (제목, 내용) — 출고 메모 등 사용자 입력
    terms: str = ""  # 비면 약관 칸 없음
    signatures: tuple[str, ...] = ()  # 서명란 제목(예: "고객", "사업자")

    @property
    def title(self) -> str:
        return DOCUMENT_TITLES[self.kind]

    @property
    def file_stem(self) -> str:
        """저장 파일 기본 이름: "대여확인서_R20261019-001"."""
        return f"{self.title}_{self.order_no}"


# --------------------------------------------------------------------------- 설정값 검증


def clean_terms(text: object) -> str:
    """약관 문구 검증. 앞뒤 공백·줄 끝 공백을 정리한다. 비우면 약관 칸 없이 출력한다."""
    if not isinstance(text, str):
        raise ValidationError("rental_terms", "약관 문구가 올바르지 않습니다.")
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    clean = "\n".join(lines).strip()
    if len(clean) > TERMS_MAX:
        raise ValidationError("rental_terms", f"약관 문구는 {TERMS_MAX:,}자 이하로 입력해주세요.")
    return clean


def validate_quote_valid_days(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("quote_valid_days", "견적 유효기간은 일 단위 정수여야 합니다.")
    if not 1 <= value <= QUOTE_VALID_DAYS_MAX:
        raise ValidationError("quote_valid_days", f"견적 유효기간은 1일 이상 {QUOTE_VALID_DAYS_MAX}일 이하여야 합니다.")
    return value


def checklist_marks(labels: tuple[str, ...], checks: tuple[bool, ...] | None) -> list[str]:
    """"■ 배터리" / "□ 충전기". 확인 결과가 없으면 모두 빈 칸."""
    marks = checks if checks is not None and len(checks) == len(labels) else (False,) * len(labels)
    return [f"{CHECKED if ok else UNCHECKED} {label}" for label, ok in zip(labels, marks, strict=True)]


# --------------------------------------------------------------------------- HTML


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _multiline(text: str) -> str:
    return "<br>".join(_e(line) for line in text.split("\n"))


def _cell_text(text: str) -> str:
    """표 칸: 첫 줄은 그대로, 다음 줄(계산 근거·사유)은 작고 흐리게."""
    first, *rest = text.split("\n")
    out = _e(first)
    for line in rest:
        out += f'<br><span style="font-size:8pt; color:#555555;">{_e(line)}</span>'
    return out


_BORDER = "border-collapse:collapse;"
_CELL = 'style="border:1px solid #888888; padding:3px;"'
_RIGHT = ' align="right"'
_HEAD = 'style="border:1px solid #888888; padding:3px; background-color:#eeeeee; font-weight:bold;"'


def _header(doc: Document) -> str:
    biz = doc.business
    right = []
    if biz.name:
        right.append(f'<span style="font-size:12pt; font-weight:bold;">{_e(biz.name)}</span>')
    if biz.registration_no:
        right.append(f"사업자등록번호 {_e(biz.registration_no)}")
    if biz.address:
        right.append(_e(biz.address))
    if biz.phone:
        right.append(f"전화 {_e(biz.phone)}")
    return (
        '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td valign="bottom"><span style="font-size:20pt; font-weight:bold;">{_e(doc.title)}</span></td>'
        f'<td align="right" valign="bottom">{"<br>".join(right)}</td>'
        "</tr></table><hr>"
    )


def _info(doc: Document) -> str:
    rows = [("문서 번호", doc.order_no), ("발행 일시", format_seoul(doc.issued_at)), *doc.info]
    cells = []
    for i in range(0, len(rows), 2):
        pair = rows[i:i + 2]
        tds = "".join(
            f"<td {_HEAD} width=\"16%\">{_e(k)}</td><td {_CELL} width=\"34%\">{_e(v)}</td>" for k, v in pair
        )
        if len(pair) == 1:
            tds += f"<td {_CELL}></td><td {_CELL}></td>"
        cells.append(f"<tr>{tds}</tr>")
    return f'<table width="100%" cellspacing="0" style="{_BORDER}">{"".join(cells)}</table>'


def _table(table: DocTable) -> str:
    out = []
    if table.title:
        out.append(f"<h3>{_e(table.title)}</h3>")
    head = "".join(f"<td {_HEAD}>{_e(h)}</td>" for h in table.headers)
    body = []
    for row in table.rows:
        tds = "".join(
            f"<td {_CELL}{_RIGHT if c in table.right_cols else ''}>{_cell_text(v)}</td>"
            for c, v in enumerate(row)
        )
        body.append(f"<tr>{tds}</tr>")
    if not table.rows:
        body.append(f'<tr><td {_CELL} colspan="{len(table.headers)}">없음</td></tr>')
    out.append(f'<table width="100%" cellspacing="0" style="{_BORDER}"><tr>{head}</tr>{"".join(body)}</table>')
    return "".join(out)


def _amounts(doc: Document, left: str) -> str:
    rows = []
    for a in doc.amounts:
        style = ' style="font-weight:bold;"' if a.strong else ""
        rows.append(
            f"<tr><td {_HEAD}>{_e(a.label)}</td>"
            f'<td {_CELL} align="right"><span{style}>{_e(a.value)}</span></td></tr>'
        )
    inner = f'<table width="100%" cellspacing="0" style="{_BORDER}">{"".join(rows)}</table>'
    # 금액 표는 오른쪽 칸, 체크리스트·메모는 왼쪽 칸(A4 1장에 들어가도록).
    # 오른쪽 정렬 표(float)는 Qt에서 테두리가 용지 밖으로 밀리므로 바깥 표로 나눈다.
    return ('<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:6px;"><tr>'
            f'<td width="50%" valign="top" style="padding-right:10px;">{left}</td>'
            f'<td width="50%" valign="top"><h3>{_e(doc.amounts_title)}</h3>{inner}</td></tr></table>')


def _checklists(doc: Document) -> str:
    parts = [f"<h3>{_e(doc.checklist_title)}</h3>"]
    for cl in doc.checklists:
        marks = "&nbsp;&nbsp; ".join(_e(m) for m in checklist_marks(cl.labels, cl.checks))
        parts.append(f"<p><b>{_e(cl.title)}</b><br>{marks or '구성품 없음'}</p>")
    return "".join(parts)


def _side(doc: Document) -> str:
    """금액 표 옆(왼쪽)에 놓는 체크리스트·메모·안내."""
    parts = []
    if doc.checklists:
        parts.append(_checklists(doc))
    for title, text in doc.memo:
        parts.append(f"<h3>{_e(title)}</h3><p>{_multiline(text)}</p>")
    if doc.notes:
        parts.append("<p>" + "<br>".join(f"※ {_e(n)}" for n in doc.notes) + "</p>")
    return "".join(parts)


def _signatures(doc: Document) -> str:
    tds = "".join(
        f'<td width="{100 // len(doc.signatures)}%" style="padding:8px;">'
        f"<b>{_e(name)}</b>&nbsp;&nbsp;성명 ________________&nbsp;&nbsp;(서명)</td>"
        for name in doc.signatures
    )
    return f'<table width="100%" cellspacing="0" style="margin-top:12px;"><tr>{tds}</tr></table>'


def render_html(doc: Document) -> str:
    """`QTextDocument.setHtml`용 HTML(A4 세로 기준). 글꼴은 출력 쪽에서 기본 글꼴로 정한다."""
    parts = [
        "<html><head><meta charset=\"utf-8\">"
        "<style>h3 { font-size:10.5pt; margin-top:8px; margin-bottom:3px; } p { margin-top:2px; }</style>"
        "</head><body style=\"font-size:9.5pt;\">",
        _header(doc),
        _info(doc),
    ]
    parts.extend(_table(t) for t in doc.tables)
    if doc.amounts:
        parts.append(_amounts(doc, _side(doc)))
    else:
        parts.append(_side(doc))
    if doc.terms:
        parts.append(f'<h3>대여 약관</h3><p style="font-size:8.5pt;">{_multiline(doc.terms)}</p>')
    if doc.signatures:
        parts.append("<p>위 내용을 확인했습니다.</p>")
        parts.append(_signatures(doc))
    parts.append(
        f'<p align="right" style="font-size:8pt; color:#666666;">출력 {_e(format_seoul(doc.issued_at))}</p>'
        "</body></html>"
    )
    return "".join(parts)


def plain_text(doc: Document) -> str:
    """테스트·검색용: 문서에 들어가는 글자를 한 문자열로(HTML 태그 없이)."""
    parts = [doc.title, doc.business.name, doc.business.phone, doc.business.address,
             doc.business.registration_no, doc.order_no]
    parts += [f"{k} {v}" for k, v in doc.info]
    for t in doc.tables:
        parts.append(t.title)
        parts.extend(" ".join(r) for r in t.rows)
    parts += [f"{a.label} {a.value}" for a in doc.amounts]
    for cl in doc.checklists:
        parts.append(cl.title)
        parts.extend(checklist_marks(cl.labels, cl.checks))
    parts += [f"{t} {v}" for t, v in doc.memo]
    parts += list(doc.notes)
    parts.append(doc.terms)
    parts += list(doc.signatures)
    return "\n".join(p for p in parts if p)
