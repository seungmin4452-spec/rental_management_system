"""정산 공식·검증 순수 함수 (PRD 7.4, T-15~T-17)."""

from __future__ import annotations

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import ChargeKind as C
from rentdesk.domain.models import MoneyKind as M
from rentdesk.domain.pricing import rental_charge_won
from rentdesk.domain.settlement import (
    RelatedEntry,
    SettlementSummary,
    apply_money,
    summarize,
    validate_money_entry,
)
from tests.support import kst


def _summary(charges=(), money=()) -> SettlementSummary:
    return summarize(charges, money)


class TestSummarize:
    def test_empty_is_settled(self):
        s = _summary()
        assert s.amount_due_won == 0 and s.deposit_held_won == 0 and s.is_settled

    def test_prd_formula(self):
        s = _summary(
            [(C.RENTAL, 60_000), (C.RENTAL, 40_000), (C.DISCOUNT, -10_000), (C.LATE, 30_000),
             (C.DAMAGE, 5_000)],
            [(M.RENTAL_RECEIPT, 50_000), (M.RENTAL_REFUND, 5_000), (M.DEPOSIT_IN, 100_000),
             (M.DEPOSIT_APPLIED, 20_000), (M.DEPOSIT_APPLICATION_REVERSAL, 5_000),
             (M.DEPOSIT_OUT, 10_000)],
        )
        assert s.charge_total_won == 125_000
        assert s.net_rental_receipts_won == 45_000
        assert s.net_deposit_applied_won == 15_000
        assert s.amount_due_won == 125_000 - 45_000 - 15_000
        assert s.deposit_held_won == 100_000 - 10_000 - 15_000
        assert not s.is_settled

    def test_overpaid_is_negative_due(self):
        s = _summary([(C.RENTAL, 30_000)], [(M.RENTAL_RECEIPT, 40_000)])
        assert s.amount_due_won == -10_000

    def test_apply_money_is_preview_only(self):
        s = _summary([(C.RENTAL, 30_000)])
        after = apply_money(s, M.RENTAL_RECEIPT, 30_000)
        assert after.amount_due_won == 0
        assert s.amount_due_won == 30_000


class TestT15DepositFlow:
    def test_receive_apply_return_leaves_zero_held(self):
        s = _summary([(C.RENTAL, 30_000)])
        for kind, amount in (
            (M.DEPOSIT_IN, 100_000), (M.DEPOSIT_APPLIED, 30_000), (M.DEPOSIT_OUT, 70_000)
        ):
            validate_money_entry(s, kind, amount)
            s = apply_money(s, kind, amount)
        assert s.deposit_held_won == 0
        assert s.net_deposit_applied_won == 30_000
        assert s.amount_due_won == 0
        assert s.is_settled


class TestValidation:
    @pytest.mark.parametrize("amount", [0, -1, 1.5, "100", True, None])
    def test_amount_must_be_positive_int(self, amount):
        with pytest.raises(ValidationError) as exc:
            validate_money_entry(_summary(), M.RENTAL_RECEIPT, amount)
        assert exc.value.field == "amount_won"

    def test_t16_deposit_out_over_held_rejected(self):
        s = _summary(money=[(M.DEPOSIT_IN, 100_000)])
        validate_money_entry(s, M.DEPOSIT_OUT, 100_000)
        with pytest.raises(ValidationError) as exc:
            validate_money_entry(s, M.DEPOSIT_OUT, 100_001)
        assert "보유잔액" in exc.value.message

    def test_deposit_out_counts_applied(self):
        s = _summary([(C.RENTAL, 30_000)], [(M.DEPOSIT_IN, 100_000), (M.DEPOSIT_APPLIED, 30_000)])
        with pytest.raises(ValidationError):
            validate_money_entry(s, M.DEPOSIT_OUT, 70_001)

    def test_t17_refund_over_receipts_rejected(self):
        s = _summary([(C.RENTAL, 30_000)], [(M.RENTAL_RECEIPT, 30_000), (M.RENTAL_REFUND, 10_000)])
        validate_money_entry(s, M.RENTAL_REFUND, 20_000)
        with pytest.raises(ValidationError):
            validate_money_entry(s, M.RENTAL_REFUND, 20_001)

    def test_apply_limited_by_due_and_held(self):
        s = _summary([(C.RENTAL, 30_000)], [(M.DEPOSIT_IN, 100_000)])
        validate_money_entry(s, M.DEPOSIT_APPLIED, 30_000)
        with pytest.raises(ValidationError):  # 미수금 초과
            validate_money_entry(s, M.DEPOSIT_APPLIED, 30_001)
        s2 = _summary([(C.RENTAL, 300_000)], [(M.DEPOSIT_IN, 100_000)])
        with pytest.raises(ValidationError):  # 보유잔액 초과
            validate_money_entry(s2, M.DEPOSIT_APPLIED, 100_001)

    def test_apply_without_receivable_rejected(self):
        s = _summary([(C.RENTAL, 30_000)], [(M.RENTAL_RECEIPT, 30_000), (M.DEPOSIT_IN, 100_000)])
        with pytest.raises(ValidationError) as exc:
            validate_money_entry(s, M.DEPOSIT_APPLIED, 1)
        assert "미수금" in exc.value.message

    def test_application_reversal_limited(self):
        s = _summary([(C.RENTAL, 30_000)], [(M.DEPOSIT_IN, 100_000), (M.DEPOSIT_APPLIED, 30_000)])
        validate_money_entry(s, M.DEPOSIT_APPLICATION_REVERSAL, 30_000)
        with pytest.raises(ValidationError):
            validate_money_entry(s, M.DEPOSIT_APPLICATION_REVERSAL, 30_001)


class TestReversalLink:
    def test_counterpart_kind_required(self):
        s = _summary(money=[(M.RENTAL_RECEIPT, 10_000), (M.DEPOSIT_IN, 50_000)])
        with pytest.raises(ValidationError) as exc:
            validate_money_entry(
                s, M.RENTAL_REFUND, 1_000, related=RelatedEntry(M.DEPOSIT_IN, 50_000, 0)
            )
        assert exc.value.field == "related_entry_id"

    def test_original_kinds_cannot_reference(self):
        with pytest.raises(ValidationError):
            validate_money_entry(
                _summary(), M.RENTAL_RECEIPT, 1_000,
                related=RelatedEntry(M.RENTAL_RECEIPT, 1_000, 0),
            )

    def test_cannot_exceed_remaining_of_original(self):
        s = _summary(money=[(M.RENTAL_RECEIPT, 10_000), (M.RENTAL_RECEIPT, 50_000)])
        related = RelatedEntry(M.RENTAL_RECEIPT, 10_000, already_reversed_won=4_000)
        validate_money_entry(s, M.RENTAL_REFUND, 6_000, related=related)
        with pytest.raises(ValidationError):
            validate_money_entry(s, M.RENTAL_REFUND, 6_001, related=related)


def test_rental_charge_uses_rounded_up_days():
    # T-23과 동일 규칙: 25시간 → 2일
    assert rental_charge_won(30_000, kst(10, 20, 10), kst(10, 21, 11)) == 60_000
    assert rental_charge_won(30_000, kst(10, 20, 10), kst(10, 20, 11)) == 30_000
    assert rental_charge_won(0, kst(10, 20, 10), kst(10, 22, 10)) == 0
