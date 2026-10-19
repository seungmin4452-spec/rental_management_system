"""M4.6 R5: 반납 추가 비용 사유·메모 (domain.extra_charges)."""

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.extra_charges import (
    DESCRIPTION_MAX,
    EXTRA_CHARGE_REASON_LABELS,
    MEMO_MAX,
    ExtraChargeReason,
    clean_memo,
    describe,
    missing_part_memo,
    parse_reason,
    reason_label,
    reason_of,
)

R = ExtraChargeReason


def test_labels_cover_all_reasons_in_order():
    assert list(EXTRA_CHARGE_REASON_LABELS) == list(R)
    assert list(EXTRA_CHARGE_REASON_LABELS.values()) == ["파손", "분실", "세척", "구성품 누락", "기타"]


@pytest.mark.parametrize("reason", list(R))
def test_describe_and_read_back_every_reason(reason):
    text = describe("CAM-1", reason, "메모 (괄호)")
    assert text.startswith(f"CAM-1 추가 비용({EXTRA_CHARGE_REASON_LABELS[reason]})")
    assert reason_of(text) == reason
    assert reason_label(text) == f"추가 비용({EXTRA_CHARGE_REASON_LABELS[reason]})"


def test_describe_without_memo_and_truncates():
    assert describe("CAM-1", R.CLEANING, None) == "CAM-1 추가 비용(세척)"
    long = describe("CAM-1", R.OTHER, "가" * 600)
    assert len(long) == DESCRIPTION_MAX and reason_of(long) == R.OTHER


def test_legacy_damage_line_and_unknown():
    assert reason_of("CAM-1 파손비") == R.DAMAGE
    assert reason_label("CAM-1 파손비") == "추가 비용(파손)"
    assert reason_of(None) is None and reason_of("직접 입력한 내용") is None
    assert reason_label("직접 입력한 내용") == "추가 비용"


def test_parse_reason():
    assert parse_reason("CLEANING") == R.CLEANING
    assert parse_reason(R.LOSS) == R.LOSS
    with pytest.raises(ValidationError) as exc:
        parse_reason("WASH")
    assert exc.value.field == "extra_charge_reason"


def test_clean_memo_rules():
    assert clean_memo(R.CLEANING, "  흙   묻음 ", 10_000) == "흙 묻음"
    assert clean_memo(R.CLEANING, "", 10_000) is None
    assert clean_memo(R.CLEANING, "흙", 0) is None  # 금액 없으면 메모 버림
    assert clean_memo(R.OTHER, "", 0) is None  # 금액 없으면 '기타'도 메모 불필요
    with pytest.raises(ValidationError, match="기타"):
        clean_memo(R.OTHER, "   ", 5_000)
    with pytest.raises(ValidationError, match=f"{MEMO_MAX}자"):
        clean_memo(R.DAMAGE, "가" * (MEMO_MAX + 1), 5_000)
    with pytest.raises(ValidationError):
        clean_memo(R.DAMAGE, 123, 5_000)


def test_missing_part_memo():
    assert missing_part_memo([("카메라 세트", ["배터리", "충전기"])]) == "카메라 세트: 배터리, 충전기"
    assert missing_part_memo([("A", ["x"]), ("B", []), ("C", ["y"])]) == "A: x / C: y"
    assert missing_part_memo([("A", [])]) is None
    assert len(missing_part_memo([("세트", ["가" * 300])])) == MEMO_MAX
