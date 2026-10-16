"""CustomerService: 고객 생성/수정/검색/보관 (PRD P0-02, S-03).

주민등록번호·카드번호·신분증 등은 수집하지 않는다. 전화번호는 고유키가 아니다.
감사 로그에는 고객 연락처를 남기지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rentdesk.domain.errors import InvalidStateError, NotFoundError, ValidationError
from rentdesk.domain.models import Customer, CustomerType
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import (
    Clock,
    clean_optional,
    clean_required,
    current_time,
    default_clock,
)


@dataclass(frozen=True)
class CustomerInput:
    name: str
    customer_type: CustomerType = CustomerType.INDIVIDUAL
    phone: str | None = None
    email: str | None = None
    note: str | None = None


def _clean(data: CustomerInput) -> CustomerInput:
    try:
        customer_type = CustomerType(data.customer_type)
    except ValueError:
        raise ValidationError("customer_type", "고객 구분이 올바르지 않습니다.") from None
    return CustomerInput(
        name=clean_required(data.name, "name", "고객명/업체명", 100),
        customer_type=customer_type,
        phone=clean_optional(data.phone, "phone", "전화번호", 30),
        email=clean_optional(data.email, "email", "이메일", 100),
        note=clean_optional(data.note, "note", "메모", 1000),
    )


class CustomerService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def get_customer(self, customer_id: int) -> Customer:
        customer = repo.get_customer(self._conn, customer_id)
        if customer is None:
            raise NotFoundError("고객을 찾을 수 없습니다.")
        return customer

    def search_customers(self, query: str = "", *, include_archived: bool = False) -> list[Customer]:
        return repo.search_customers(
            self._conn, query=query.strip(), include_archived=include_archived
        )

    def create_customer(self, data: CustomerInput) -> Customer:
        clean = _clean(data)
        now = current_time(self._clock)
        with transaction(self._conn):
            customer_id = repo.insert_customer(
                self._conn,
                name=clean.name,
                customer_type=clean.customer_type,
                phone=clean.phone,
                email=clean.email,
                note=clean.note,
                created_at=now,
            )
            repo.insert_audit(
                self._conn, entity_type="customer", entity_id=customer_id, action="CREATE",
                description=None, now=now,
            )
        return self.get_customer(customer_id)

    def update_customer(self, customer_id: int, data: CustomerInput) -> Customer:
        clean = _clean(data)
        now = current_time(self._clock)
        with transaction(self._conn):
            if repo.get_customer(self._conn, customer_id) is None:
                raise NotFoundError("고객을 찾을 수 없습니다.")
            repo.update_customer(
                self._conn,
                customer_id,
                name=clean.name,
                customer_type=clean.customer_type,
                phone=clean.phone,
                email=clean.email,
                note=clean.note,
            )
            repo.insert_audit(
                self._conn, entity_type="customer", entity_id=customer_id, action="UPDATE",
                description=None, now=now,
            )
        return self.get_customer(customer_id)

    def set_archived(self, customer_id: int, archived: bool) -> Customer:
        """보관된 고객은 목록·신규 예약에서 제외되지만 기존 예약 이력은 유지된다."""
        now = current_time(self._clock)
        with transaction(self._conn):
            if repo.get_customer(self._conn, customer_id) is None:
                raise NotFoundError("고객을 찾을 수 없습니다.")
            repo.set_customer_archived(self._conn, customer_id, archived)
            repo.insert_audit(
                self._conn, entity_type="customer", entity_id=customer_id,
                action="ARCHIVE" if archived else "UNARCHIVE", description=None, now=now,
            )
        return self.get_customer(customer_id)

    def delete_customer(self, customer_id: int) -> None:
        """예약 이력이 없는 고객만 물리 삭제. 이력이 있으면 보관 처리를 안내."""
        now = current_time(self._clock)
        with transaction(self._conn):
            if repo.get_customer(self._conn, customer_id) is None:
                raise NotFoundError("고객을 찾을 수 없습니다.")
            if repo.customer_has_orders(self._conn, customer_id):
                raise InvalidStateError(
                    "예약 이력이 있는 고객은 삭제할 수 없습니다. 거래 중지(숨김)를 이용해주세요."
                )
            repo.delete_customer(self._conn, customer_id)
            repo.insert_audit(
                self._conn, entity_type="customer", entity_id=customer_id, action="DELETE",
                description=None, now=now,
            )
