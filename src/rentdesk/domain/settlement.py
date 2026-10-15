"""정산 계산·검증 순수 함수 (PRD 7.4, S-07).

```text
net_rental_receipts = sum(RENTAL_RECEIPT) - sum(RENTAL_REFUND)
net_deposit_applied = sum(DEPOSIT_APPLIED) - sum(DEPOSIT_APPLICATION_REVERSAL)
charge_total        = sum(charge_lines.amount_won)      # 할인은 음수
amount_due          = charge_total - net_rental_receipts - net_deposit_applied
deposit_held        = sum(DEPOSIT_IN) - sum(DEPOSIT_OUT) - net_deposit_applied
```

대여료(청구·수금)와 보증금(수령·반환·충당)을 서로 다른 합계로 유지해 혼동하지 않는다.
금액은 모두 KRW 정수이며 DB 접근이 없다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import ChargeKind, MoneyKind
from rentdesk.domain.pricing import validate_won

# 역거래 종류 → 상쇄 대상 원거래 종류
REVERSAL_OF: dict[MoneyKind, MoneyKind] = {
    MoneyKind.RENTAL_REFUND: MoneyKind.RENTAL_RECEIPT,
    MoneyKind.DEPOSIT_OUT: MoneyKind.DEPOSIT_IN,
    MoneyKind.DEPOSIT_APPLICATION_REVERSAL: MoneyKind.DEPOSIT_APPLIED,
}

# 실제 돈이 오가지 않는 내부 대체 거래(결제수단 기록 불가)
INTERNAL_KINDS = frozenset({MoneyKind.DEPOSIT_APPLIED, MoneyKind.DEPOSIT_APPLICATION_REVERSAL})

MONEY_KIND_LABELS = {
    MoneyKind.RENTAL_RECEIPT: "대여료 수금",
    MoneyKind.RENTAL_REFUND: "대여료 환급",
    MoneyKind.DEPOSIT_IN: "보증금 수령",
    MoneyKind.DEPOSIT_OUT: "보증금 반환",
    MoneyKind.DEPOSIT_APPLIED: "보증금 대금충당",
    MoneyKind.DEPOSIT_APPLICATION_REVERSAL: "보증금 충당 취소",
}

_MONEY_FIELD = {
    MoneyKind.RENTAL_RECEIPT: "rental_receipts_won",
    MoneyKind.RENTAL_REFUND: "rental_refunds_won",
    MoneyKind.DEPOSIT_IN: "deposit_in_won",
    MoneyKind.DEPOSIT_OUT: "deposit_out_won",
    MoneyKind.DEPOSIT_APPLIED: "deposit_applied_won",
    MoneyKind.DEPOSIT_APPLICATION_REVERSAL: "deposit_application_reversed_won",
}

_CHARGE_FIELD = {
    ChargeKind.RENTAL: "rental_charges_won",
    ChargeKind.DISCOUNT: "discount_charges_won",
    ChargeKind.LATE: "late_charges_won",
    ChargeKind.DAMAGE: "damage_charges_won",
    ChargeKind.ADJUSTMENT: "adjustment_charges_won",
}


@dataclass(frozen=True)
class SettlementSummary:
    """주문 1건의 정산 현황. 원거래 합계만 저장하고 파생값은 프로퍼티로 계산한다."""

    rental_charges_won: int = 0
    discount_charges_won: int = 0  # 음수(할인)
    late_charges_won: int = 0
    damage_charges_won: int = 0
    adjustment_charges_won: int = 0
    rental_receipts_won: int = 0
    rental_refunds_won: int = 0
    deposit_in_won: int = 0
    deposit_out_won: int = 0
    deposit_applied_won: int = 0
    deposit_application_reversed_won: int = 0

    @property
    def charge_total_won(self) -> int:
        return (
            self.rental_charges_won + self.discount_charges_won + self.late_charges_won
            + self.damage_charges_won + self.adjustment_charges_won
        )

    @property
    def net_rental_receipts_won(self) -> int:
        return self.rental_receipts_won - self.rental_refunds_won

    @property
    def net_deposit_applied_won(self) -> int:
        return self.deposit_applied_won - self.deposit_application_reversed_won

    @property
    def amount_due_won(self) -> int:
        """> 0: 미수, = 0: 청구액 정산됨, < 0: 초과수금(환급 필요)."""
        return self.charge_total_won - self.net_rental_receipts_won - self.net_deposit_applied_won

    @property
    def deposit_held_won(self) -> int:
        """고객에게 아직 환급 또는 대금 충당해야 할 보증금."""
        return self.deposit_in_won - self.deposit_out_won - self.net_deposit_applied_won

    @property
    def is_settled(self) -> bool:
        return self.amount_due_won == 0 and self.deposit_held_won == 0


def summarize(
    charges: Iterable[tuple[ChargeKind, int]],
    money: Iterable[tuple[MoneyKind, int]],
) -> SettlementSummary:
    totals: dict[str, int] = {}
    for kind, amount in charges:
        name = _CHARGE_FIELD[ChargeKind(kind)]
        totals[name] = totals.get(name, 0) + amount
    for kind, amount in money:
        name = _MONEY_FIELD[MoneyKind(kind)]
        totals[name] = totals.get(name, 0) + amount
    return SettlementSummary(**totals)


def apply_money(summary: SettlementSummary, kind: MoneyKind, amount_won: int) -> SettlementSummary:
    """거래 1건을 반영한 결과(저장 전 미리보기용). 검증은 `validate_money_entry`."""
    name = _MONEY_FIELD[kind]
    return replace(summary, **{name: getattr(summary, name) + amount_won})


@dataclass(frozen=True)
class RelatedEntry:
    """역거래가 참조하는 이전 원거래와 그 원거래에 이미 연결된 역거래 합계."""

    kind: MoneyKind
    amount_won: int
    already_reversed_won: int


def validate_money_entry(
    summary: SettlementSummary,
    kind: MoneyKind,
    amount_won: object,
    *,
    related: RelatedEntry | None = None,
) -> int:
    """금전 원장 입력 검증. 통과하면 금액(int)을 반환, 위반 시 `ValidationError`.

    - 모든 금액은 원 단위 양의 정수(음수 효과는 거래 종류로 표현).
    - 대여료 환급 ≤ 누적 실수령 대여료.
    - 보증금 반환 ≤ 보증금 보유잔액.
    - 보증금 충당 ≤ min(보증금 보유잔액, 미수금).
    - 충당 취소 ≤ 누적 충당액.
    - 역거래가 원거래를 참조하면 종류가 대응해야 하고, 원거래의 미상쇄 잔액을 넘을 수 없다.
    """
    amount = validate_won("amount_won", amount_won, label="금액")
    if amount == 0:
        raise ValidationError("amount_won", "금액은 1원 이상이어야 합니다.")

    if kind == MoneyKind.RENTAL_REFUND and amount > summary.net_rental_receipts_won:
        raise ValidationError(
            "amount_won",
            f"환급액이 지금까지 받은 대여료({summary.net_rental_receipts_won:,}원)를 초과합니다.",
        )
    if kind == MoneyKind.DEPOSIT_OUT and amount > summary.deposit_held_won:
        raise ValidationError(
            "amount_won",
            f"반환액이 보증금 보유잔액({summary.deposit_held_won:,}원)을 초과합니다.",
        )
    if kind == MoneyKind.DEPOSIT_APPLIED:
        due = max(0, summary.amount_due_won)
        if due == 0:
            raise ValidationError("amount_won", "충당할 미수금이 없습니다.")
        limit = min(summary.deposit_held_won, due)
        if amount > limit:
            raise ValidationError(
                "amount_won",
                f"충당액은 보증금 보유잔액({summary.deposit_held_won:,}원)과 "
                f"미수금({due:,}원) 중 작은 금액 이하여야 합니다.",
            )
    if (
        kind == MoneyKind.DEPOSIT_APPLICATION_REVERSAL
        and amount > summary.net_deposit_applied_won
    ):
        raise ValidationError(
            "amount_won",
            f"충당 취소액이 누적 충당액({summary.net_deposit_applied_won:,}원)을 초과합니다.",
        )

    if related is not None:
        expected = REVERSAL_OF.get(kind)
        if expected is None:
            raise ValidationError(
                "related_entry_id", f"{MONEY_KIND_LABELS[kind]}은(는) 이전 거래를 참조할 수 없습니다."
            )
        if related.kind != expected:
            raise ValidationError(
                "related_entry_id",
                f"{MONEY_KIND_LABELS[kind]}은(는) {MONEY_KIND_LABELS[expected]} 거래만 정정할 수 있습니다.",
            )
        remaining = related.amount_won - related.already_reversed_won
        if amount > remaining:
            raise ValidationError(
                "amount_won", f"정정 금액이 원거래의 남은 금액({remaining:,}원)을 초과합니다."
            )
    return amount
