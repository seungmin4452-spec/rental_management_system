"""전역 검색: 장비번호·고객명·예약번호 (PRD 5 공통 UI 원칙 '상단 전역 검색')."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rentdesk.domain.display import format_dt
from rentdesk.domain.models import ORDER_STATUS_LABELS, OrderStatus
from rentdesk.domain.timeutil import parse_utc_str
from rentdesk.persistence import repositories as repo

MAX_RESULTS_PER_KIND = 20


class ResultKind(StrEnum):
    ASSET = "ASSET"
    CUSTOMER = "CUSTOMER"
    ORDER = "ORDER"


RESULT_KIND_LABELS = {
    ResultKind.ASSET: "장비",
    ResultKind.CUSTOMER: "고객",
    ResultKind.ORDER: "예약",
}


@dataclass(frozen=True)
class SearchResult:
    kind: ResultKind
    entity_id: int
    title: str
    detail: str


class SearchService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def search(self, query: str) -> list[SearchResult]:
        """보관/은퇴 항목도 포함해 찾는다(이력 확인 목적). 연락처는 결과에 노출하지 않는다."""
        q = query.strip()
        if not q:
            return []
        results: list[SearchResult] = []
        for a in repo.search_assets(self._conn, query=q, include_retired=True)[:MAX_RESULTS_PER_KIND]:
            retired = " (사용 중지)" if a.lifecycle_status == "RETIRED" else ""
            results.append(SearchResult(
                ResultKind.ASSET, a.id, f"{a.asset_code} {a.name}{retired}", a.category_name
            ))
        for c in repo.search_customers(self._conn, query=q, include_archived=True)[
            :MAX_RESULTS_PER_KIND
        ]:
            archived = " (숨김)" if c.is_archived else ""
            kind = "사업자" if c.customer_type == "BUSINESS" else "개인"
            results.append(SearchResult(ResultKind.CUSTOMER, c.id, f"{c.name}{archived}", kind))
        for r in repo.search_orders_by_no(self._conn, q, MAX_RESULTS_PER_KIND):
            start: datetime = parse_utc_str(r["start_at_utc"])
            results.append(SearchResult(
                ResultKind.ORDER, r["order_id"], f"{r['order_no']} {r['customer_name']}",
                f"{ORDER_STATUS_LABELS[OrderStatus(r['status'])]} · {format_dt(start)}",
            ))
        return results
