"""엑셀 일괄 가져오기: 장비·고객 (USABILITY_PLAN U10).

흐름: ① 양식 파일 저장 → ② 파일 선택 → ③ 미리보기(행별 등록 가능/오류 이유) →
④ 오류 없는 행만 등록하거나 취소 → ⑤ 가져오기 전 백업 1개를 만든 뒤 한 트랜잭션으로 등록.

- 칸 값 해석은 `domain.import_cells`, 길이·필수 검사는 기존 장비/고객 서비스와 같은 규칙을 쓴다.
- 관리번호는 대소문자를 무시하고 DB·파일 안 중복을 검사한다. 비우면 AST-000001 형태로 자동 부여.
- 없는 카테고리는 사용자가 고르면 새로 만들고, 고르지 않으면 그 행은 오류로 둔다.
- 고객은 이름·구분·전화번호·이메일·메모만 받는다. 주민등록번호·카드번호 같은 열은 읽지 않는다.
- 미리보기와 등록 사이에 DB가 바뀌어 결과가 달라지면(다른 창에서 같은 관리번호 등록 등) 등록을 거부하고
  다시 미리보기를 하게 한다.
"""

from __future__ import annotations

import logging
import sqlite3
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from rentdesk.domain.errors import DomainError, InvalidStateError, ValidationError
from rentdesk.domain.import_cells import (
    cell_customer_type,
    cell_phone,
    cell_text,
    cell_won,
    is_blank,
    normalize_header,
)
from rentdesk.domain.models import SYSTEM_CATEGORY_NAME, CustomerType
from rentdesk.domain.timeutil import to_seoul
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.assets import AUTO_CODE_PREFIX, AssetInput
from rentdesk.services.auto_backup import prune
from rentdesk.services.backup import BackupError, create_backup
from rentdesk.services.common import (
    Clock,
    clean_optional,
    clean_required,
    current_time,
    default_clock,
)
from rentdesk.services.customers import CustomerInput
from rentdesk.services.reports import save_workbook

log = logging.getLogger("rentdesk.bulk_import")

MAX_ROWS = 5000
MAX_FILE_BYTES = 20 * 1024 * 1024
HEADER_SEARCH_ROWS = 10
BACKUP_PREFIX = "RentalDesk_before_import_"
BACKUP_KEEP = 10

# 개인정보 보호: 이런 머리글의 열은 값을 읽지 않는다(PRD — 민감정보 수집 금지).
SENSITIVE_WORDS = ("주민", "외국인등록", "여권", "운전면허", "면허번호", "신분증", "카드번호", "계좌", "비밀번호")


class ImportKind(StrEnum):
    ASSETS = "assets"
    CUSTOMERS = "customers"


KIND_LABELS = {ImportKind.ASSETS: "장비", ImportKind.CUSTOMERS: "고객"}


@dataclass(frozen=True)
class ImportColumn:
    key: str
    header: str
    required: bool = False
    aliases: tuple[str, ...] = ()
    example: object = None
    help: str = ""
    text_format: bool = False  # 엑셀이 숫자로 바꾸지 않도록 '텍스트' 서식

    @property
    def template_header(self) -> str:
        return f"{self.header}(필수)" if self.required else self.header


ASSET_COLUMNS: tuple[ImportColumn, ...] = (
    ImportColumn("asset_code", "관리번호", aliases=("장비번호", "자산번호", "자산코드"),
                 example="CAM-001", text_format=True,
                 help="장비마다 다른 번호(최대 30자, 대소문자 구분 안 함). 비우면 AST-000001처럼 자동으로 붙습니다."),
    ImportColumn("name", "장비명", required=True, aliases=("이름", "품명"),
                 example="미러리스 카메라", help="최대 100자"),
    ImportColumn("category", "카테고리", aliases=("분류", "종류"),
                 example="카메라",
                 help="비우면 '기타'로 넣습니다. 없는 카테고리는 가져올 때 새로 만들지, '기타'로 넣을지 고를 수 있습니다(최대 50자)."),
    ImportColumn("brand", "브랜드", aliases=("제조사",), example="소니", help="선택"),
    ImportColumn("model", "모델명", aliases=("모델",), example="A7M4", text_format=True, help="선택"),
    ImportColumn("serial_no", "시리얼 번호", aliases=("시리얼", "일련번호", "S/N"), example="SN0001",
                 text_format=True, help="선택"),
    ImportColumn("daily_rate_won", "일 대여료", required=True, aliases=("일대여료", "대여료", "1일 대여료"),
                 example=50000, help="원 단위 숫자(예: 50000 또는 50,000원). 소수·음수는 안 됩니다."),
    ImportColumn("deposit_won", "보증금", example=300000, help="원 단위 숫자. 비우면 0원."),
    ImportColumn("location", "보관 위치", aliases=("위치",), example="A-1 선반", help="선택"),
    ImportColumn("note", "메모", aliases=("비고",), example="배터리 2개 포함", help="선택(최대 1000자)"),
)

CUSTOMER_COLUMNS: tuple[ImportColumn, ...] = (
    ImportColumn("name", "이름", required=True, aliases=("고객명", "업체명", "고객명/업체명", "성명", "상호"),
                 example="홍길동", help="개인은 이름, 사업자는 업체명(최대 100자)"),
    ImportColumn("customer_type", "구분", aliases=("고객 구분", "고객구분"), example="개인",
                 help="'개인' 또는 '사업자'. 비우면 개인."),
    ImportColumn("phone", "전화번호", aliases=("연락처", "휴대폰", "휴대전화", "전화"), example="010-1234-5678",
                 text_format=True, help="선택. 이름과 전화번호가 이미 등록된 고객과 같으면 중복으로 보고 등록하지 않습니다."),
    ImportColumn("email", "이메일", aliases=("메일", "e-mail", "email"), example="hong@example.com", help="선택"),
    ImportColumn("note", "메모", aliases=("비고",), example="촬영 동호회", help="선택(최대 1000자)"),
)

COLUMNS = {ImportKind.ASSETS: ASSET_COLUMNS, ImportKind.CUSTOMERS: CUSTOMER_COLUMNS}


def template_name(kind: ImportKind) -> str:
    return f"{KIND_LABELS[kind]}_가져오기_양식.xlsx"


# --------------------------------------------------------------------------- 결과 모델


@dataclass(frozen=True)
class ImportRow:
    """미리보기 1행. `row_no`는 엑셀에서 보이는 행 번호."""

    row_no: int
    cells: dict[str, object]
    errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    asset: AssetInput | None = None
    customer: CustomerInput | None = None
    new_category: str | None = None  # 아직 없는 카테고리(새로 만들기를 고르지 않으면 "기타"로 등록)

    def problems(self, create_categories: bool) -> tuple[str, ...]:
        return self.errors

    def notes_for(self, create_categories: bool) -> tuple[str, ...]:
        """안내 문구. 없는 카테고리를 만들지 않으면 "기타"로 넣는다고 알려준다(M4.6 R1)."""
        if self.new_category and not create_categories and not self.errors:
            return (f"'{self.new_category}' 카테고리가 없어 '{SYSTEM_CATEGORY_NAME}'로 넣습니다.", *self.notes)
        return self.notes

    def category_for(self, create_categories: bool) -> str | None:
        """실제로 등록될 카테고리 이름."""
        if self.asset is None:
            return None
        if self.new_category and not create_categories:
            return SYSTEM_CATEGORY_NAME
        return self.asset.category_name

    def is_ok(self, create_categories: bool) -> bool:
        return not self.problems(create_categories)

    def label(self) -> str:
        """미리보기 표의 대표 값(장비명 또는 고객명)."""
        return cell_text(self.cells.get("name")) or ""


@dataclass(frozen=True)
class ImportPreview:
    kind: ImportKind
    file_name: str
    sheet_name: str
    rows: tuple[ImportRow, ...]
    new_categories: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()  # 파일 전체 안내(무시한 열, 건너뛴 예시 행 등)

    def ok_rows(self, create_categories: bool) -> list[ImportRow]:
        return [r for r in self.rows if r.is_ok(create_categories)]

    def error_rows(self, create_categories: bool) -> list[ImportRow]:
        return [r for r in self.rows if not r.is_ok(create_categories)]


@dataclass(frozen=True)
class ImportResult:
    kind: ImportKind
    created: int
    skipped: int
    created_categories: tuple[str, ...] = ()
    backup_path: Path | None = None
    created_ids: tuple[int, ...] = field(default=(), repr=False)


# --------------------------------------------------------------------------- 파일 읽기


@dataclass(frozen=True)
class _RawSheet:
    sheet_name: str
    rows: tuple[tuple[int, dict[str, object]], ...]
    notices: tuple[str, ...]


def _header_lookup(columns: Sequence[ImportColumn]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for col in columns:
        for name in (col.header, *col.aliases):
            lookup[normalize_header(name).casefold()] = col.key
    return lookup


def _is_sensitive(header: str) -> bool:
    return any(word in header for word in SENSITIVE_WORDS)


def _same_as_example(cells: dict[str, object], columns: Sequence[ImportColumn]) -> bool:
    return all(cell_text(cells.get(c.key)) == cell_text(c.example) for c in columns)


def _choose_sheet(workbook, kind: ImportKind):
    wanted = KIND_LABELS[kind]
    for ws in workbook.worksheets:
        if normalize_header(ws.title) == wanted:
            return ws
    for ws in workbook.worksheets:
        if normalize_header(ws.title) != "설명":
            return ws
    return workbook.worksheets[0]


def _open_workbook(path: Path):
    if not path.is_file():
        raise DomainError("파일을 찾을 수 없습니다. 다시 선택해주세요.")
    suffix = path.suffix.lower()
    if suffix == ".xls":
        raise DomainError(
            "예전 엑셀 형식(.xls)은 읽을 수 없습니다. 엑셀에서 '다른 이름으로 저장' → "
            "'Excel 통합 문서(*.xlsx)'로 저장한 뒤 다시 선택해주세요."
        )
    if suffix not in (".xlsx", ".xlsm"):
        raise DomainError("엑셀 파일(.xlsx)만 가져올 수 있습니다.")
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise DomainError("파일이 너무 큽니다(20MB 이하). 장비·고객 목록만 남겨 다시 저장해주세요.")
        return load_workbook(path, read_only=True, data_only=True)
    except DomainError:
        raise
    except PermissionError as exc:
        raise DomainError("파일을 열 수 없습니다. 권한을 확인하거나 파일을 다른 곳에 복사해 다시 선택해주세요.") from exc
    except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise DomainError("엑셀 파일을 읽지 못했습니다. 파일이 손상됐거나 암호가 걸려 있는지 확인해주세요.") from exc


def read_sheet(path: Path | str, kind: ImportKind) -> _RawSheet:
    """엑셀 파일에서 머리글을 찾아 행별 원래 값(열 key → 칸 값)을 읽는다."""
    path = Path(path)
    columns = COLUMNS[kind]
    lookup = _header_lookup(columns)
    required = {c.key for c in columns if c.required}
    workbook = _open_workbook(path)
    try:
        ws = _choose_sheet(workbook, kind)
        if hasattr(ws, "reset_dimensions"):
            ws.reset_dimensions()  # 다른 프로그램이 저장한 파일의 잘못된 범위 정보 무시
        rows_iter = ws.iter_rows(values_only=True)
        header_map: dict[int, str] = {}
        ignored: list[str] = []
        sensitive: list[str] = []
        header_row_no = 0
        for row_no, values in enumerate(rows_iter, start=1):
            if row_no > HEADER_SEARCH_ROWS:
                break
            mapped: dict[int, str] = {}
            for idx, value in enumerate(values or ()):
                key = lookup.get(normalize_header(value).casefold())
                if key is not None and key not in mapped.values():
                    mapped[idx] = key
            if required <= set(mapped.values()):
                header_map, header_row_no = mapped, row_no
                for idx, value in enumerate(values):
                    header = normalize_header(value)
                    if idx in mapped or not header:
                        continue
                    (sensitive if _is_sensitive(header) else ignored).append(str(value).strip())
                break
        if not header_map:
            names = ", ".join(c.header for c in columns if c.required)
            raise DomainError(
                f"{KIND_LABELS[kind]} 머리글({names})을 찾을 수 없습니다. "
                f"[양식 파일 저장]으로 받은 {KIND_LABELS[kind]} 양식에 입력했는지 확인해주세요."
            )
        data: list[tuple[int, dict[str, object]]] = []
        example_skipped = False
        for row_no, values in enumerate(rows_iter, start=header_row_no + 1):
            values = values or ()
            cells = {key: values[idx] if idx < len(values) else None for idx, key in header_map.items()}
            if all(is_blank(v) for v in cells.values()):
                continue
            if not data and not example_skipped and _same_as_example(cells, columns):
                example_skipped = True
                continue
            if len(data) >= MAX_ROWS:
                raise DomainError(f"한 번에 {MAX_ROWS:,}행까지 가져올 수 있습니다. 파일을 나눠서 가져와주세요.")
            data.append((row_no, cells))
        title = ws.title
    finally:
        workbook.close()

    notices: list[str] = []
    if example_skipped:
        notices.append("양식의 예시 행과 똑같은 행은 가져오지 않았습니다.")
    if sensitive:
        notices.append(f"개인정보 보호를 위해 이 열은 읽지 않았습니다: {', '.join(sensitive)}")
    if ignored:
        notices.append(f"양식에 없는 열은 가져오지 않습니다: {', '.join(ignored)}")
    return _RawSheet(sheet_name=title, rows=tuple(data), notices=tuple(notices))


# --------------------------------------------------------------------------- 행 검사


class _Collector:
    """한 행의 여러 칸 오류를 모아 한꺼번에 보여준다."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def take(self, fn: Callable[[], object], default: object = None) -> object:
        try:
            return fn()
        except ValidationError as exc:
            self.errors.append(exc.message)
            return default


def _phone_key(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


class _AssetChecker:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.db_codes = {c.casefold() for c in repo.all_asset_codes(conn)}
        self.categories = {
            c.name.casefold(): c for c in repo.list_categories(conn, include_archived=True)
        }
        self.system_name = repo.get_system_category(conn).name
        self.file_codes: dict[str, int] = {}
        self.new_categories: dict[str, str] = {}  # casefold → 파일에서 처음 쓴 표기

    def check(self, row_no: int, cells: dict[str, object]) -> ImportRow:
        c = _Collector()
        text = lambda key: cell_text(cells.get(key))  # noqa: E731
        code = c.take(lambda: clean_optional(text("asset_code"), "asset_code", "관리번호", 30))
        name = c.take(lambda: clean_required(text("name"), "name", "장비명", 100))
        category = c.take(lambda: clean_optional(text("category"), "category", "카테고리", 50))
        brand = c.take(lambda: clean_optional(text("brand"), "brand", "브랜드", 100))
        model = c.take(lambda: clean_optional(text("model"), "model", "모델명", 100))
        serial = c.take(lambda: clean_optional(text("serial_no"), "serial_no", "시리얼 번호", 100))
        location = c.take(lambda: clean_optional(text("location"), "location", "보관 위치", 100))
        note = c.take(lambda: clean_optional(text("note"), "note", "메모", 1000))
        rate = c.take(lambda: cell_won(cells.get("daily_rate_won"), "daily_rate_won", "일 대여료"))
        deposit = c.take(lambda: cell_won(cells.get("deposit_won"), "deposit_won", "보증금", blank_as=0))

        if isinstance(code, str):
            folded = code.casefold()
            if folded in self.db_codes:
                c.errors.append(f"이미 등록된 관리번호입니다: {code}")
            elif folded in self.file_codes:
                c.errors.append(f"관리번호가 파일의 {self.file_codes[folded]}행과 겹칩니다: {code}")
            else:
                self.file_codes[folded] = row_no

        new_category = None
        notes: list[str] = []
        if category is None and not any("카테고리" in e for e in c.errors):
            category = self.system_name
            notes.append(f"카테고리가 비어 있어 '{category}'로 넣습니다.")
        if isinstance(category, str):
            existing = self.categories.get(category.casefold())
            if existing is None:
                new_category = self.new_categories.setdefault(category.casefold(), category)
                category = new_category
            elif existing.is_archived:
                c.errors.append(f"숨김 처리된 카테고리입니다: {existing.name}")
            else:
                category = existing.name

        asset = None
        if not c.errors:
            asset = AssetInput(
                name=name, category_name=category, daily_rate_won=rate, deposit_won=deposit,
                asset_code=code, brand=brand, model=model, serial_no=serial,
                location=location, note=note,
            )
        return ImportRow(row_no=row_no, cells=cells, errors=tuple(c.errors), notes=tuple(notes),
                         asset=asset, new_category=new_category)


class _CustomerChecker:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.db_keys = {
            (name.casefold(), _phone_key(phone)) for name, phone in repo.customer_name_phones(conn)
            if _phone_key(phone)
        }
        self.file_keys: dict[tuple[str, str], int] = {}

    def check(self, row_no: int, cells: dict[str, object]) -> ImportRow:
        c = _Collector()
        notes: list[str] = []
        text = lambda key: cell_text(cells.get(key))  # noqa: E731
        name = c.take(lambda: clean_required(text("name"), "name", "이름", 100))
        ctype = c.take(lambda: cell_customer_type(cells.get("customer_type")), CustomerType.INDIVIDUAL)
        phone_raw, zero_added = c.take(lambda: cell_phone(cells.get("phone")), (None, False))
        phone = c.take(lambda: clean_optional(phone_raw, "phone", "전화번호", 30))
        email = c.take(lambda: clean_optional(text("email"), "email", "이메일", 100))
        note = c.take(lambda: clean_optional(text("note"), "note", "메모", 1000))
        if zero_added:
            notes.append("전화번호가 숫자로 저장돼 있어 앞에 0을 붙였습니다.")

        if isinstance(name, str) and isinstance(phone, str) and _phone_key(phone):
            key = (name.casefold(), _phone_key(phone))
            if key in self.db_keys:
                c.errors.append("이름과 전화번호가 같은 고객이 이미 등록돼 있습니다.")
            elif key in self.file_keys:
                c.errors.append(f"이름과 전화번호가 파일의 {self.file_keys[key]}행과 같습니다.")
            else:
                self.file_keys[key] = row_no

        customer = None
        if not c.errors:
            customer = CustomerInput(name=name, customer_type=ctype, phone=phone, email=email, note=note)
        return ImportRow(row_no=row_no, cells=cells, errors=tuple(c.errors), notes=tuple(notes),
                         customer=customer)


def _evaluate(conn: sqlite3.Connection, kind: ImportKind,
              rows: Sequence[tuple[int, dict[str, object]]]) -> tuple[tuple[ImportRow, ...], tuple[str, ...]]:
    if kind == ImportKind.ASSETS:
        checker = _AssetChecker(conn)
        result = tuple(checker.check(n, cells) for n, cells in rows)
        new_categories = tuple(
            name for name in checker.new_categories.values()
            if any(r.new_category == name and not r.errors for r in result)
        )
        return result, new_categories
    checker = _CustomerChecker(conn)
    return tuple(checker.check(n, cells) for n, cells in rows), ()


def _signature(rows: Sequence[ImportRow]) -> tuple:
    return tuple((r.row_no, r.errors, r.new_category, r.asset, r.customer) for r in rows)


# --------------------------------------------------------------------------- 서비스


class ImportService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def save_template(self, kind: ImportKind, path: Path | str, *, overwrite: bool = False) -> Path:
        return save_workbook(build_template(kind), path, overwrite=overwrite)

    def preview(self, kind: ImportKind, path: Path | str) -> ImportPreview:
        raw = read_sheet(path, kind)
        rows, new_categories = _evaluate(self._conn, kind, raw.rows)
        notices = list(raw.notices)
        if not rows:
            notices.insert(0, "가져올 행이 없습니다. 머리글 아래에 내용을 입력했는지 확인해주세요.")
        return ImportPreview(
            kind=kind, file_name=Path(path).name, sheet_name=raw.sheet_name, rows=rows,
            new_categories=new_categories, notices=tuple(notices),
        )

    def run_import(self, preview: ImportPreview, *, create_categories: bool,
                   backup_dir: Path | None) -> ImportResult:
        """오류 없는 행만 한 트랜잭션으로 등록한다. `backup_dir`가 있으면 먼저 백업을 만든다."""
        ok_rows = preview.ok_rows(create_categories)
        if not ok_rows:
            raise ValidationError("rows", "등록할 수 있는 행이 없습니다. 오류 이유를 확인해 파일을 고친 뒤 다시 선택해주세요.")
        now = current_time(self._clock)
        backup_path = self._backup(backup_dir, now) if backup_dir is not None else None

        created_ids: list[int] = []
        created_categories: list[str] = []
        with transaction(self._conn):
            raw = [(r.row_no, r.cells) for r in preview.rows]
            fresh, _ = _evaluate(self._conn, preview.kind, raw)
            if _signature(fresh) != _signature(preview.rows):
                raise InvalidStateError(
                    "미리보기 이후 데이터가 바뀌었습니다(같은 관리번호·고객이 새로 등록되는 등). "
                    "파일을 다시 선택해 미리보기를 확인해주세요."
                )
            if preview.kind == ImportKind.ASSETS:
                created_ids = self._insert_assets(ok_rows, now, created_categories, create_categories)
            else:
                created_ids = self._insert_customers(ok_rows, now)
        return ImportResult(
            kind=preview.kind, created=len(created_ids), skipped=len(preview.rows) - len(ok_rows),
            created_categories=tuple(created_categories), backup_path=backup_path,
            created_ids=tuple(created_ids),
        )

    def _backup(self, folder: Path, now: datetime) -> Path:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{BACKUP_PREFIX}{to_seoul(now):%Y-%m-%d_%H%M%S}.zip"
            result = create_backup(self._conn, folder / name, now=now, overwrite=True)
        except (BackupError, OSError) as exc:
            log.warning("pre-import backup failed")
            raise DomainError(
                "가져오기 전에 백업을 만들지 못해 가져오기를 멈췄습니다. 남은 저장 공간을 확인해주세요."
            ) from exc
        prune(folder, BACKUP_KEEP, BACKUP_PREFIX)
        return result.path

    def _insert_assets(self, rows: Sequence[ImportRow], now: datetime,
                       created_categories: list[str], create_categories: bool) -> list[int]:
        category_ids = {c.name.casefold(): c.id for c in repo.list_categories(self._conn)}
        system_id = repo.get_system_category(self._conn).id
        used = {c.casefold() for c in repo.all_asset_codes(self._conn)}
        used |= {r.asset.asset_code.casefold() for r in rows if r.asset.asset_code}
        next_no = repo.max_auto_asset_number(self._conn) + 1
        ids = []
        for row in rows:
            data = row.asset
            code = data.asset_code
            if code is None:
                while f"{AUTO_CODE_PREFIX}{next_no:06d}".casefold() in used:
                    next_no += 1
                code = f"{AUTO_CODE_PREFIX}{next_no:06d}"
                used.add(code.casefold())
            folded = data.category_name.casefold()
            if folded not in category_ids and not create_categories:
                folded = SYSTEM_CATEGORY_NAME.casefold()
                category_ids.setdefault(folded, system_id)
            if folded not in category_ids:
                category_ids[folded] = repo.insert_category(self._conn, data.category_name)
                created_categories.append(data.category_name)
                repo.insert_audit(self._conn, entity_type="category", entity_id=category_ids[folded],
                                  action="CREATE", description="엑셀 가져오기", now=now)
            asset_id = repo.insert_asset(
                self._conn, asset_code=code, name=data.name, category_id=category_ids[folded],
                brand=data.brand, model=data.model, serial_no=data.serial_no, location=data.location,
                daily_rate_won=data.daily_rate_won, deposit_won=data.deposit_won, note=data.note,
                created_at=now,
            )
            repo.insert_audit(self._conn, entity_type="asset", entity_id=asset_id, action="CREATE",
                              description=f"{code} (엑셀 가져오기)", now=now)
            ids.append(asset_id)
        return ids

    def _insert_customers(self, rows: Sequence[ImportRow], now: datetime) -> list[int]:
        ids = []
        for row in rows:
            data = row.customer
            customer_id = repo.insert_customer(
                self._conn, name=data.name, customer_type=data.customer_type, phone=data.phone,
                email=data.email, note=data.note, created_at=now,
            )
            # 감사 로그에 고객 연락처를 남기지 않는다.
            repo.insert_audit(self._conn, entity_type="customer", entity_id=customer_id, action="CREATE",
                              description="엑셀 가져오기", now=now)
            ids.append(customer_id)
        return ids


# --------------------------------------------------------------------------- 양식 파일


def build_template(kind: ImportKind) -> Workbook:
    columns = COLUMNS[kind]
    label = KIND_LABELS[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = label
    ws.append([c.template_header for c in columns])
    ws.append([c.example for c in columns])
    required_fill = PatternFill("solid", fgColor="FCE4D6")
    fill = PatternFill("solid", fgColor="DDE6F0")
    for idx, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.font = Font(bold=True)
        cell.fill = required_fill if col.required else fill
        cell.alignment = Alignment(horizontal="center")
        ws.cell(row=2, column=idx).font = Font(color="888888", italic=True)
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = max(14, len(col.template_header) * 2 + 4)
        if col.text_format:
            ws.column_dimensions[letter].number_format = "@"
            ws.cell(row=2, column=idx).number_format = "@"
    ws.freeze_panes = "A2"

    guide = wb.create_sheet("설명")
    guide.append([f"{label} 가져오기 양식 사용법"])
    guide.cell(row=1, column=1).font = Font(size=14, bold=True)
    for line in (
        f"1. '{label}' 시트의 1행(머리글)은 그대로 두고 2행부터 한 줄에 {label} 하나씩 입력합니다.",
        "2. 2행의 회색 예시는 지우거나 덮어쓰세요. 예시와 똑같은 행은 가져오지 않습니다.",
        "3. (필수) 열은 꼭 채워야 합니다. 오류가 있는 행은 미리보기에서 이유를 보여주고, 오류 없는 행만 등록할 수 있습니다.",
        "4. 가져오기 직전에 현재 데이터를 자동으로 백업합니다.",
    ):
        guide.append([line])
    if kind == ImportKind.CUSTOMERS:
        guide.append(["5. 주민등록번호·카드번호·계좌번호 같은 개인정보는 입력하지 마세요. 그런 열은 읽지 않습니다."])
    guide.append([])
    guide.append(["열", "필수", "설명"])
    header_row = guide.max_row
    for idx in range(1, 4):
        guide.cell(row=header_row, column=idx).font = Font(bold=True)
        guide.cell(row=header_row, column=idx).fill = fill
    for col in columns:
        guide.append([col.header, "필수" if col.required else "", col.help])
    guide.column_dimensions["A"].width = 16
    guide.column_dimensions["B"].width = 8
    guide.column_dimensions["C"].width = 90
    wb.active = 0
    return wb
