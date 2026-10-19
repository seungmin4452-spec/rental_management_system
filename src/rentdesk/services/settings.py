"""운영 설정(유예시간·연체 배수·반납 후 준비 시간)·요금 규칙과 출력물용 사업자 정보·문서 문구 조회/변경 (PRD S-09).

운영 설정 변경값은 이후 확정되는 예약에만 스냅샷으로 적용되며 기존 약정에 소급되지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from rentdesk.domain.documents import (
    DEFAULT_RENTAL_TERMS,
    QUOTE_VALID_DAYS_DEFAULT,
    clean_terms,
    validate_quote_valid_days,
)
from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import Asset, RentalPolicy
from rentdesk.domain.policy import effective_policy, effective_rate_rule
from rentdesk.domain.pricing import parse_multiplier
from rentdesk.domain.rates import RateRule, rate_rule_from_data, rate_rule_to_json, validate_rate_rule
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import Clock, clean_optional, current_time, default_clock

MAX_MINUTES = 7 * 24 * 60


@dataclass(frozen=True)
class BusinessInfo:
    """출력물(엑셀·확인서) 머리말용 사업자 정보. 고객사별 하드코딩 없이 사용자가 입력한다."""

    name: str = ""
    phone: str = ""
    address: str = ""  # R6: 확인서·견적서에만 표시
    registration_no: str = ""


@dataclass(frozen=True)
class DocumentSettings:
    """확인서·견적서 문구(R6). 약관을 한 번도 저장하지 않았으면 기본 약관을 쓴다."""

    rental_terms: str = DEFAULT_RENTAL_TERMS
    quote_valid_days: int = QUOTE_VALID_DAYS_DEFAULT

    @property
    def is_default_terms(self) -> bool:
        return self.rental_terms == DEFAULT_RENTAL_TERMS


def _setting_or(conn: sqlite3.Connection, key: str, default: object) -> object:
    """R6에서 추가한 설정 키는 마이그레이션 없이 '없으면 기본값'으로 읽는다."""
    try:
        return repo.get_setting(conn, key)
    except KeyError:
        return default


def read_policy(conn: sqlite3.Connection) -> RentalPolicy:
    return RentalPolicy(
        grace_minutes=int(repo.get_setting(conn, "grace_minutes")),
        late_multiplier=Decimal(str(repo.get_setting(conn, "late_multiplier"))),
        turnaround_buffer_minutes=int(repo.get_setting(conn, "turnaround_buffer_minutes")),
    )


def policies_for_assets(conn: sqlite3.Connection, assets: Iterable[Asset]) -> dict[int, RentalPolicy]:
    """장비별 실제 적용 운영 설정(가게 기본값 → 카테고리 설정, MULTI_INDUSTRY_PLAN R2)."""
    assets = list(assets)
    shop = read_policy(conn)
    categories = repo.categories_by_id(conn, (a.category_id for a in assets))
    return {
        a.id: effective_policy(shop, categories[a.category_id].policy if a.category_id in categories else None)
        for a in assets
    }


def read_rate_rule(conn: sqlite3.Connection) -> RateRule:
    """가게 기본 요금 규칙(M4.6 R3)."""
    return rate_rule_from_data(repo.get_setting(conn, "rate_rule"))


def rate_rules_for_assets(conn: sqlite3.Connection, assets: Iterable[Asset]) -> dict[int, RateRule]:
    """장비별 실제 적용 요금 규칙(가게 기본값 → 카테고리 규칙)."""
    assets = list(assets)
    shop = read_rate_rule(conn)
    categories = repo.categories_by_id(conn, (a.category_id for a in assets))
    return {
        a.id: effective_rate_rule(shop, categories[a.category_id].rate_rule if a.category_id in categories else None)
        for a in assets
    }


def read_business_info(conn: sqlite3.Connection) -> BusinessInfo:
    return BusinessInfo(
        name=str(repo.get_setting(conn, "business_name") or ""),
        phone=str(repo.get_setting(conn, "business_phone") or ""),
        address=str(_setting_or(conn, "business_address", "") or ""),
        registration_no=str(_setting_or(conn, "business_registration_no", "") or ""),
    )


def read_document_settings(conn: sqlite3.Connection) -> DocumentSettings:
    terms = _setting_or(conn, "rental_terms", None)
    days = _setting_or(conn, "quote_valid_days", QUOTE_VALID_DAYS_DEFAULT)
    return DocumentSettings(
        rental_terms=DEFAULT_RENTAL_TERMS if terms is None else str(terms),
        quote_valid_days=days if isinstance(days, int) and not isinstance(days, bool)
        else QUOTE_VALID_DAYS_DEFAULT,
    )


def validate_multiplier(value: str | Decimal) -> Decimal:
    multiplier = parse_multiplier(value)
    if multiplier > 100:
        raise ValidationError("late_multiplier", "연체 1일당 일 대여료의 배수는 100배 이하여야 합니다.")
    return multiplier


def validate_minutes(field: str, label: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, f"{label}은(는) 분 단위 정수여야 합니다.")
    if not 0 <= value <= MAX_MINUTES:
        raise ValidationError(field, f"{label}은(는) 0분 이상 {MAX_MINUTES}분 이하여야 합니다.")
    return value


class SettingsService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def get_policy(self) -> RentalPolicy:
        return read_policy(self._conn)

    def update_policy(
        self,
        *,
        grace_minutes: int,
        late_multiplier: str | Decimal,
        turnaround_buffer_minutes: int,
    ) -> RentalPolicy:
        grace = validate_minutes("grace_minutes", "연체료 없이 기다려 주는 시간", grace_minutes)
        buffer = validate_minutes("turnaround_buffer_minutes", "반납 후 준비 시간", turnaround_buffer_minutes)
        multiplier = validate_multiplier(late_multiplier)
        now = current_time(self._clock)
        with transaction(self._conn):
            repo.set_setting(self._conn, "grace_minutes", grace)
            repo.set_setting(self._conn, "late_multiplier", str(multiplier))
            repo.set_setting(self._conn, "turnaround_buffer_minutes", buffer)
            repo.insert_audit(
                self._conn, entity_type="settings", entity_id=None, action="UPDATE_POLICY",
                description=f"grace={grace}, multiplier={multiplier}, buffer={buffer}", now=now,
            )
        return self.get_policy()

    def get_rate_rule(self) -> RateRule:
        return read_rate_rule(self._conn)

    def update_rate_rule(self, rule: RateRule) -> RateRule:
        """가게 기본 요금 규칙 변경. 이후 확정되는 예약에만 적용(기존 예약은 품목 스냅샷 유지)."""
        clean = validate_rate_rule(rule)
        now = current_time(self._clock)
        with transaction(self._conn):
            repo.set_setting(self._conn, "rate_rule", json.loads(rate_rule_to_json(clean)))
            repo.insert_audit(
                self._conn, entity_type="settings", entity_id=None, action="UPDATE_RATE_RULE",
                description=rate_rule_to_json(clean)[:500], now=now,
            )
        return self.get_rate_rule()

    def get_business_info(self) -> BusinessInfo:
        return read_business_info(self._conn)

    def update_business_info(
        self, *, name: str, phone: str, address: str | None = None, registration_no: str | None = None,
    ) -> BusinessInfo:
        """주소·사업자등록번호를 넘기지 않으면(None) 기존 값을 유지한다."""
        clean_name = clean_optional(name, "business_name", "상호", 100) or ""
        clean_phone = clean_optional(phone, "business_phone", "사업자 전화번호", 30) or ""
        clean_address = (None if address is None
                         else clean_optional(address, "business_address", "주소", 200) or "")
        clean_reg = (None if registration_no is None
                     else clean_optional(registration_no, "business_registration_no", "사업자등록번호", 20) or "")
        now = current_time(self._clock)
        with transaction(self._conn):
            repo.set_setting(self._conn, "business_name", clean_name)
            repo.set_setting(self._conn, "business_phone", clean_phone)
            if clean_address is not None:
                repo.set_setting(self._conn, "business_address", clean_address)
            if clean_reg is not None:
                repo.set_setting(self._conn, "business_registration_no", clean_reg)
            repo.insert_audit(
                self._conn, entity_type="settings", entity_id=None, action="UPDATE_BUSINESS",
                description=None, now=now,
            )
        return read_business_info(self._conn)

    def get_document_settings(self) -> DocumentSettings:
        return read_document_settings(self._conn)

    def update_document_settings(self, *, rental_terms: str, quote_valid_days: int) -> DocumentSettings:
        """약관 문구(비우면 약관 칸 없이 출력)와 견적 유효기간. 이미 출력한 문서에는 영향이 없다."""
        terms = clean_terms(rental_terms)
        days = validate_quote_valid_days(quote_valid_days)
        now = current_time(self._clock)
        with transaction(self._conn):
            repo.set_setting(self._conn, "rental_terms", terms)
            repo.set_setting(self._conn, "quote_valid_days", days)
            repo.insert_audit(
                self._conn, entity_type="settings", entity_id=None, action="UPDATE_DOCUMENTS",
                description=f"terms={len(terms)}자, quote_valid_days={days}", now=now,
            )
        return self.get_document_settings()
