"""ReservationService: 예약 임시저장/확정/수정/취소 (PRD P0-04, 6.1, 7.1, 7.2).

- DRAFT는 가용성을 점유하지 않는다. CONFIRMED부터 점유.
- 확정·확정 예약 수정은 `BEGIN IMMEDIATE` 트랜잭션 안에서 가용성을 다시 검사한 뒤 저장한다.
  화면의 사전 검사(AvailabilityService)만 신뢰하지 않는다.
- 확정 시 장비 단가/보증금, 유예시간/연체 배수/반납 후 준비 시간, 요금 규칙을 품목별 스냅샷으로 저장한다.
  운영 설정·요금 규칙은 장비 카테고리 설정이 있으면 그 값, 없으면 가게 기본값(MULTI_INDUSTRY_PLAN R2·R3).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from rentdesk.domain.availability import validate_reservation_window
from rentdesk.domain.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from rentdesk.domain.models import (
    PRE_CHECKOUT_STATUSES,
    Asset,
    ChargeKind,
    ItemStatus,
    LifecycleStatus,
    OrderStatus,
    RentalItem,
    RentalOrder,
    RentalPolicy,
    category_sort_key,
)
from rentdesk.domain.policy import policy_basis
from rentdesk.domain.pricing import OrderQuote, PricedItem, quote_order, rental_days, validate_won
from rentdesk.domain.timeutil import ensure_aware, to_seoul, to_utc
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.availability import describe_conflict, find_conflicts
from rentdesk.services.common import Clock, clean_optional, current_time, default_clock
from rentdesk.domain.rates import DayCountMethod, RateRule
from rentdesk.services.settings import policies_for_assets, rate_rules_for_assets, read_policy, read_rate_rule

MAX_ITEMS_PER_ORDER = 30
CANCEL_FEE_DESCRIPTION = "예약 취소 수수료"


@dataclass(frozen=True)
class ReservationInput:
    customer_id: int
    start_at: datetime
    due_at: datetime
    asset_ids: Sequence[int]
    discount_won: int = 0
    notes: str | None = None


@dataclass(frozen=True)
class _Clean:
    customer_id: int
    start_at: datetime
    due_at: datetime
    asset_ids: tuple[int, ...]
    discount_won: int
    notes: str | None


def _clean_input(data: ReservationInput) -> _Clean:
    for field, value in (("start_at", data.start_at), ("due_at", data.due_at)):
        if not isinstance(value, datetime):
            raise ValidationError(field, "일시를 입력해주세요.")
        try:
            ensure_aware(value)
        except ValueError:
            raise ValidationError(field, "일시에 시간대 정보가 없습니다.") from None
    asset_ids = tuple(data.asset_ids)
    if not asset_ids:
        raise ValidationError("asset_ids", "예약할 장비를 1개 이상 선택해주세요.")
    if len(asset_ids) > MAX_ITEMS_PER_ORDER:
        raise ValidationError(
            "asset_ids", f"한 예약에는 장비를 최대 {MAX_ITEMS_PER_ORDER}개까지 담을 수 있습니다."
        )
    if any(isinstance(a, bool) or not isinstance(a, int) for a in asset_ids):
        raise ValidationError("asset_ids", "장비 선택 값이 올바르지 않습니다.")
    if len(set(asset_ids)) != len(asset_ids):
        raise ValidationError("asset_ids", "같은 장비를 한 예약에 두 번 담을 수 없습니다.")
    return _Clean(
        customer_id=data.customer_id,
        start_at=to_utc(data.start_at),
        due_at=to_utc(data.due_at),
        asset_ids=asset_ids,
        discount_won=validate_won("discount_won", data.discount_won, label="할인 금액"),
        notes=clean_optional(data.notes, "notes", "메모", 2000),
    )


def quote_items(
    items: Sequence[RentalItem], start_at: datetime, due_at: datetime, discount_won: int
) -> OrderQuote:
    return quote_order(
        [PricedItem(i.daily_rate_snapshot_won, i.deposit_snapshot_won, i.rate_rule_snapshot) for i in items],
        start_at, due_at, discount_won,
    )


@dataclass(frozen=True)
class OrderRow:
    order: RentalOrder
    customer_name: str
    item_count: int
    expected_total_rental_fee_won: int
    expected_deposit_won: int
    out_count: int
    returned_count: int

    @property
    def progress_label(self) -> str:
        """출고/반납 여부 표시."""
        if self.order.status in PRE_CHECKOUT_STATUSES:
            return "출고 전"
        if self.order.status == OrderStatus.CANCELED:
            return "-"
        if self.out_count == 0:
            return "전체 반납"
        if self.returned_count == 0:
            return "출고됨"
        return f"부분 반납 ({self.returned_count}/{self.item_count})"


def order_row(order: RentalOrder, customer_name: str) -> OrderRow:
    items = [i for i in order.items if i.status != ItemStatus.CANCELED]
    if order.status == OrderStatus.CANCELED:
        items = list(order.items)
    quote = quote_items(items, order.start_at, order.due_at, order.discount_won)
    return OrderRow(
        order=order,
        customer_name=customer_name,
        item_count=len(items),
        expected_total_rental_fee_won=quote.expected_total_rental_fee_won,
        expected_deposit_won=quote.expected_deposit_won,
        out_count=sum(1 for i in items if i.status == ItemStatus.OUT),
        returned_count=sum(1 for i in items if i.status == ItemStatus.RETURNED),
    )


class ReservationService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    # ------------------------------------------------------------------ queries

    def get_order(self, order_id: int) -> RentalOrder:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        return order

    def list_orders(
        self,
        *,
        statuses: Sequence[OrderStatus] | None = None,
        customer_id: int | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        query: str = "",
    ) -> list[RentalOrder]:
        return repo.list_orders(
            self._conn, statuses=statuses, customer_id=customer_id,
            period_start=period_start, period_end=period_end, query=query.strip(),
        )

    def list_order_rows(
        self,
        *,
        statuses: Sequence[OrderStatus] | None = None,
        customer_id: int | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        query: str = "",
    ) -> list[OrderRow]:
        """예약 목록(S-04) 행: 고객명·장비 수·예상 대여료·출고/반납 진행 상황."""
        orders = self.list_orders(
            statuses=statuses, customer_id=customer_id, period_start=period_start,
            period_end=period_end, query=query,
        )
        names = repo.customer_names(self._conn, (o.customer_id for o in orders))
        return [order_row(o, names.get(o.customer_id, "")) for o in orders]

    def customer_name(self, order: RentalOrder) -> str:
        return repo.customer_names(self._conn, [order.customer_id]).get(order.customer_id, "")

    def quote(self, order: RentalOrder) -> OrderQuote:
        """주문 스냅샷 기준 예상 대여료/보증금. 취소 품목은 제외."""
        items = [i for i in order.items if i.status != ItemStatus.CANCELED]
        return quote_items(items, order.start_at, order.due_at, order.discount_won)

    def preview_quote(
        self,
        asset_ids: Sequence[int],
        start_at: datetime,
        due_at: datetime,
        discount_won: int = 0,
        *,
        order_id: int | None = None,
    ) -> OrderQuote:
        """예약 작성 3단계 화면용 예상 금액.

        신규/임시저장 예약은 현재 장비 단가 기준(확정 시 다시 스냅샷).
        `order_id`가 확정 예약이면 기존 품목은 스냅샷 단가, 새로 추가한 품목만 현재 단가.
        """
        assets = repo.get_assets(self._conn, list(asset_ids))
        if len(assets) != len(set(asset_ids)):
            raise ValidationError("asset_ids", "존재하지 않는 장비가 포함되어 있습니다.")
        snapshots: dict[int, RentalItem] = {}
        if order_id is not None:
            order = self.get_order(order_id)
            if order.status == OrderStatus.CONFIRMED:
                snapshots = {i.asset_id: i for i in order.items}
        rules = rate_rules_for_assets(self._conn, assets.values())
        priced = [
            PricedItem(
                snapshots[a.id].daily_rate_snapshot_won, snapshots[a.id].deposit_snapshot_won,
                snapshots[a.id].rate_rule_snapshot,
            )
            if a.id in snapshots else PricedItem(a.daily_rate_won, a.deposit_won, rules[a.id])
            for a in (assets[asset_id] for asset_id in dict.fromkeys(asset_ids))
        ]
        return quote_order(priced, to_utc(start_at), to_utc(due_at), discount_won)

    def policy_basis(self, asset_ids: Sequence[int]) -> list[str]:
        """예약 마법사 근거 문구(R2·R3): 장비 카테고리별 준비 시간·연체 기준·요금 규칙과 출처.

        담긴 장비의 카테고리 중 하나라도 카테고리 설정이 있을 때만 보여준다(단순한 가게는 빈 목록).
        """
        assets = repo.get_assets(self._conn, list(asset_ids))
        categories = repo.categories_by_id(self._conn, (a.category_id for a in assets.values()))
        used = sorted(categories.values(), key=category_sort_key)
        if not any(c.policy.overridden or c.rate_rule is not None for c in used):
            return []
        shop = read_policy(self._conn)
        return [policy_basis(c.name, c.policy, shop, c.rate_rule) for c in used]

    def period_days_text(self, start_at: datetime, due_at: datetime) -> str:
        """예약 마법사 1단계 대여일수 안내(가게 기본 요금 규칙 기준)."""
        rule = read_rate_rule(self._conn)
        days = rental_days(to_utc(start_at), to_utc(due_at), rule)
        if rule.day_count == DayCountMethod.CALENDAR:
            text = f"{days}일 (날짜 기준, 1박2일 = 1일)"
        elif rule.return_grace_minutes:
            text = f"{days}일 (24시간 단위, {rule.return_grace_minutes}분 이내 초과는 같은 날)"
        else:
            text = f"{days}일 (24시간 단위 올림, 최소 1일)"
        if any(c.rate_rule is not None for c in repo.list_categories(self._conn)):
            text += " · 카테고리마다 계산 방식이 다를 수 있음"
        return text

    def price_basis(
        self,
        asset_ids: Sequence[int],
        start_at: datetime,
        due_at: datetime,
        *,
        order_id: int | None = None,
    ) -> list[str]:
        """예약 마법사 요금 근거(R3): 주·월·주말 단가가 적용된 장비만 "CAM-001: 주 단가 적용: 1주 + 3일"."""
        quote = self.preview_quote(asset_ids, start_at, due_at, order_id=order_id)
        assets = repo.get_assets(self._conn, list(asset_ids))
        return [
            f"{assets[asset_id].asset_code}: {fee.basis}"
            for asset_id, fee in zip(dict.fromkeys(asset_ids), quote.item_fees, strict=True)
            if fee.uses_long_rate
        ]

    # ------------------------------------------------------------------ commands

    def create_draft(self, data: ReservationInput) -> RentalOrder:
        return self._create(data, confirm=False)

    def create_confirmed(self, data: ReservationInput) -> RentalOrder:
        """임시저장 없이 바로 확정(같은 트랜잭션)."""
        return self._create(data, confirm=True)

    def confirm(self, order_id: int) -> RentalOrder:
        now = current_time(self._clock)
        with transaction(self._conn):
            self._confirm_in_txn(order_id, now)
        return self.get_order(order_id)

    def update_order(
        self, order_id: int, data: ReservationInput, *, confirm: bool = False
    ) -> RentalOrder:
        """출고 전(DRAFT/CONFIRMED) 예약의 고객·기간·장비·할인·메모 수정.

        CONFIRMED: 기존 품목은 스냅샷 단가를 유지하고, 추가 품목만 현재 단가로 스냅샷.
        수정 결과를 같은 트랜잭션 안에서 충돌 재검사하며, 충돌 시 전체 롤백.
        `confirm=True`이면 DRAFT 수정과 확정을 한 트랜잭션으로 처리한다(확정 실패 시 수정도 롤백).
        """
        clean = _clean_input(data)
        now = current_time(self._clock)
        with transaction(self._conn):
            order = repo.get_order(self._conn, order_id)
            if order is None:
                raise NotFoundError("예약을 찾을 수 없습니다.")
            if order.status not in PRE_CHECKOUT_STATUSES:
                raise InvalidStateError(self._not_editable_message(order.status))
            self._check_customer(clean.customer_id, allow_archived=clean.customer_id == order.customer_id)
            self._check_assets(clean.asset_ids)
            start_changed = clean.start_at != order.start_at
            validate_reservation_window(
                clean.start_at, clean.due_at, now,
                require_future_start=order.status == OrderStatus.CONFIRMED and start_changed,
            )

            assets = repo.get_assets(self._conn, list(clean.asset_ids))
            policies = policies_for_assets(self._conn, assets.values())
            rules = rate_rules_for_assets(self._conn, assets.values())
            existing = {i.asset_id: i for i in order.items}
            for asset_id, item in existing.items():
                if asset_id not in clean.asset_ids:
                    repo.delete_item(self._conn, item.id)
            for asset_id in clean.asset_ids:
                if asset_id not in existing:
                    self._insert_item(order_id, assets[asset_id], policies[asset_id], rules[asset_id])
            repo.update_order_header(
                self._conn, order_id, customer_id=clean.customer_id,
                start_at=clean.start_at, due_at=clean.due_at,
                discount_won=clean.discount_won, notes=clean.notes, now=now,
            )
            items = repo.get_items(self._conn, order_id)
            quote_items(items, clean.start_at, clean.due_at, clean.discount_won)

            if order.status == OrderStatus.CONFIRMED:
                self._raise_if_conflicts(
                    clean.asset_ids, clean.start_at, clean.due_at,
                    {i.asset_id: i.turnaround_buffer_snapshot_minutes for i in items}, now, order_id,
                )
            repo.insert_audit(
                self._conn, entity_type="rental_order", entity_id=order_id, action="UPDATE",
                description=order.order_no, now=now,
            )
            if confirm:
                if order.status != OrderStatus.DRAFT:
                    raise InvalidStateError("임시저장 상태의 예약만 확정할 수 있습니다.")
                self._confirm_in_txn(order_id, now)
        return self.get_order(order_id)

    def cancel(self, order_id: int, *, cancel_fee_won: object = 0) -> RentalOrder:
        """출고 전 예약만 취소. 품목은 CANCELED.

        cancel_fee_won > 0이면 취소 수수료를 청구 원장에 '조정'(설명 "예약 취소 수수료")으로 남긴다.
        받은 선입금·보증금의 환급은 정산 화면의 '정산 마무리'로 처리한다(USABILITY_PLAN U5).
        """
        fee = validate_won("cancel_fee_won", cancel_fee_won, label="취소 수수료")
        now = current_time(self._clock)
        with transaction(self._conn):
            order = repo.get_order(self._conn, order_id)
            if order is None:
                raise NotFoundError("예약을 찾을 수 없습니다.")
            if order.status not in PRE_CHECKOUT_STATUSES:
                if order.status == OrderStatus.IN_RENTAL:
                    raise InvalidStateError(
                        "이미 출고된 예약은 취소할 수 없습니다. 반납 처리를 이용해주세요."
                    )
                if order.status == OrderStatus.COMPLETED:
                    raise InvalidStateError("반납 완료된 예약은 취소할 수 없습니다.")
                raise InvalidStateError("이미 취소된 예약입니다.")
            repo.set_items_status_for_order(
                self._conn, order_id, ItemStatus.RESERVED, ItemStatus.CANCELED
            )
            repo.set_order_status(self._conn, order_id, OrderStatus.CANCELED, now=now)
            if fee:
                repo.insert_charge(
                    self._conn, order_id=order_id, rental_item_id=None, kind=ChargeKind.ADJUSTMENT,
                    amount_won=fee, description=CANCEL_FEE_DESCRIPTION, now=now,
                )
            repo.insert_audit(
                self._conn, entity_type="rental_order", entity_id=order_id, action="CANCEL",
                description=f"{order.order_no} 수수료 {fee}" if fee else order.order_no, now=now,
            )
        return self.get_order(order_id)

    # ------------------------------------------------------------------ internals

    def _create(self, data: ReservationInput, *, confirm: bool) -> RentalOrder:
        clean = _clean_input(data)
        now = current_time(self._clock)
        with transaction(self._conn):
            self._check_customer(clean.customer_id, allow_archived=False)
            assets = self._check_assets(clean.asset_ids)
            validate_reservation_window(
                clean.start_at, clean.due_at, now, require_future_start=confirm
            )
            rules = rate_rules_for_assets(self._conn, assets.values())
            quote_order(
                [PricedItem(a.daily_rate_won, a.deposit_won, rules[a.id]) for a in assets.values()],
                clean.start_at, clean.due_at, clean.discount_won,
            )
            shop = read_policy(self._conn)
            policies = policies_for_assets(self._conn, assets.values())
            order_id = repo.insert_order(
                self._conn,
                order_no=self._next_order_no(now),
                customer_id=clean.customer_id,
                status=OrderStatus.DRAFT,
                start_at=clean.start_at,
                due_at=clean.due_at,
                buffer_minutes=shop.turnaround_buffer_minutes,  # 호환용(계산은 품목 스냅샷)
                discount_won=clean.discount_won,
                notes=clean.notes,
                now=now,
            )
            for asset_id in clean.asset_ids:
                self._insert_item(order_id, assets[asset_id], policies[asset_id], rules[asset_id])
            repo.insert_audit(
                self._conn, entity_type="rental_order", entity_id=order_id, action="CREATE_DRAFT",
                description=None, now=now,
            )
            if confirm:
                self._confirm_in_txn(order_id, now)
        return self.get_order(order_id)

    def _confirm_in_txn(self, order_id: int, now: datetime) -> None:
        order = repo.get_order(self._conn, order_id)
        if order is None:
            raise NotFoundError("예약을 찾을 수 없습니다.")
        if order.status != OrderStatus.DRAFT:
            raise InvalidStateError("임시저장 상태의 예약만 확정할 수 있습니다.")
        self._check_customer(order.customer_id, allow_archived=False)
        asset_ids = tuple(i.asset_id for i in order.items)
        assets = self._check_assets(asset_ids)
        validate_reservation_window(order.start_at, order.due_at, now, require_future_start=True)

        # 확정 시점의 단가/보증금/운영 설정/요금 규칙(카테고리 설정 → 가게 기본값)을 품목별 스냅샷으로 고정
        policies = policies_for_assets(self._conn, assets.values())
        rules = rate_rules_for_assets(self._conn, assets.values())
        for item in order.items:
            asset, policy = assets[item.asset_id], policies[item.asset_id]
            repo.update_item_snapshot(
                self._conn, item.id,
                daily_rate_won=asset.daily_rate_won, deposit_won=asset.deposit_won,
                late_multiplier=policy.late_multiplier, grace_minutes=policy.grace_minutes,
                buffer_minutes=policy.turnaround_buffer_minutes, rate_rule=rules[item.asset_id],
            )
        quote_items(
            repo.get_items(self._conn, order_id), order.start_at, order.due_at, order.discount_won
        )
        self._raise_if_conflicts(
            asset_ids, order.start_at, order.due_at,
            {a: p.turnaround_buffer_minutes for a, p in policies.items()}, now, order_id,
        )
        repo.set_order_status(
            self._conn, order_id, OrderStatus.CONFIRMED, now=now,
            buffer_minutes=read_policy(self._conn).turnaround_buffer_minutes,  # 호환용
        )
        repo.insert_audit(
            self._conn, entity_type="rental_order", entity_id=order_id, action="CONFIRM",
            description=order.order_no, now=now,
        )

    def _insert_item(self, order_id: int, asset: Asset, policy: RentalPolicy, rule: RateRule) -> None:
        repo.insert_item(
            self._conn, order_id=order_id, asset_id=asset.id,
            daily_rate_won=asset.daily_rate_won, deposit_won=asset.deposit_won,
            late_multiplier=policy.late_multiplier, grace_minutes=policy.grace_minutes,
            buffer_minutes=policy.turnaround_buffer_minutes, rate_rule=rule,
        )

    def _raise_if_conflicts(
        self,
        asset_ids: Sequence[int],
        start_at: datetime,
        due_at: datetime,
        buffer_minutes: Mapping[int, int],
        now: datetime,
        order_id: int,
    ) -> None:
        conflicts = find_conflicts(
            self._conn, asset_ids, start_at, due_at, buffer_minutes, now,
            exclude_order_id=order_id,
        )
        if conflicts:
            lines = "\n".join(f"- {describe_conflict(c)}" for c in conflicts)
            raise ConflictError(
                f"선택한 장비가 다른 일정과 겹쳐 예약을 확정할 수 없습니다.\n{lines}", conflicts
            )

    def _check_customer(self, customer_id: int, *, allow_archived: bool) -> None:
        customer = repo.get_customer(self._conn, customer_id)
        if customer is None:
            raise ValidationError("customer_id", "고객을 선택해주세요.")
        if customer.is_archived and not allow_archived:
            raise ValidationError("customer_id", "거래 중지(숨김)된 고객으로는 예약할 수 없습니다.")

    def _check_assets(self, asset_ids: Sequence[int]) -> dict[int, Asset]:
        assets = repo.get_assets(self._conn, list(asset_ids))
        missing = [a for a in asset_ids if a not in assets]
        if missing:
            raise ValidationError("asset_ids", "존재하지 않는 장비가 포함되어 있습니다.")
        retired = [a.asset_code for a in assets.values() if a.lifecycle_status != LifecycleStatus.ACTIVE]
        if retired:
            raise ValidationError(
                "asset_ids", f"사용 중지된 장비는 예약할 수 없습니다: {', '.join(sorted(retired))}"
            )
        return assets

    def _next_order_no(self, now: datetime) -> str:
        """`R-YYYYMMDD-0001`. 날짜는 사용자 기준(서울) 생성일. 트랜잭션 안에서만 호출."""
        yyyymmdd = to_seoul(now).strftime("%Y%m%d")
        seq = repo.max_order_seq_for_date(self._conn, yyyymmdd) + 1
        return f"R-{yyyymmdd}-{seq:04d}"

    @staticmethod
    def _not_editable_message(status: OrderStatus) -> str:
        if status == OrderStatus.IN_RENTAL:
            return "출고된 예약은 기간이나 장비를 수정할 수 없습니다."
        if status == OrderStatus.COMPLETED:
            return "반납 완료된 예약은 수정할 수 없습니다."
        return "취소된 예약은 수정할 수 없습니다."
