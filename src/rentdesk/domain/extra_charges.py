"""반납 추가 비용 (MULTI_INDUSTRY_PLAN R5).

예전 "파손비"를 파손·분실·세척·구성품 누락·기타 사유를 고를 수 있는 "추가 비용"으로 넓힌다.
DB 값(`charge_lines.kind = 'DAMAGE'`)과 코드 이름은 그대로 두고(U1 방식), 사유와 메모는
청구 줄 설명(`description`)에 "CAM-1 추가 비용(세척): 메모" 형식으로 저장한다.
예전 "CAM-1 파손비" 줄은 사유 '파손'으로 읽는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from rentdesk.domain.errors import ValidationError

EXTRA_CHARGE_LABEL = "추가 비용"
MEMO_MAX = 200
DESCRIPTION_MAX = 500  # charge_lines.description 제약


class ExtraChargeReason(StrEnum):
    DAMAGE = "DAMAGE"
    LOSS = "LOSS"
    CLEANING = "CLEANING"
    MISSING_PART = "MISSING_PART"
    OTHER = "OTHER"


EXTRA_CHARGE_REASON_LABELS = {
    ExtraChargeReason.DAMAGE: "파손",
    ExtraChargeReason.LOSS: "분실",
    ExtraChargeReason.CLEANING: "세척",
    ExtraChargeReason.MISSING_PART: "구성품 누락",
    ExtraChargeReason.OTHER: "기타",
}
_BY_LABEL = {label: reason for reason, label in EXTRA_CHARGE_REASON_LABELS.items()}
_PATTERN = re.compile(r" 추가 비용\(([^)]+)\)")
_LEGACY = " 파손비"


def parse_reason(value: object) -> ExtraChargeReason:
    try:
        return ExtraChargeReason(value)
    except ValueError:
        raise ValidationError("extra_charge_reason", "추가 비용 사유가 올바르지 않습니다.") from None


def clean_memo(reason: ExtraChargeReason, memo: object, amount_won: int) -> str | None:
    """메모 검증. 금액이 0이면 메모는 버린다. 사유가 '기타'면 메모가 필요하다."""
    if memo is not None and not isinstance(memo, str):
        raise ValidationError("extra_charge_memo", "추가 비용 메모가 올바르지 않습니다.")
    text = " ".join((memo or "").split())
    if amount_won <= 0:
        return None
    if len(text) > MEMO_MAX:
        raise ValidationError("extra_charge_memo", f"추가 비용 메모는 {MEMO_MAX}자 이하로 입력해주세요.")
    if reason == ExtraChargeReason.OTHER and not text:
        raise ValidationError("extra_charge_memo", "추가 비용 사유가 '기타'이면 내용을 입력해주세요.")
    return text or None


def describe(asset_code: str, reason: ExtraChargeReason, memo: str | None) -> str:
    """청구 줄 설명: "CAM-1 추가 비용(세척): 메모"(500자 제한 안으로 자름)."""
    text = f"{asset_code} {EXTRA_CHARGE_LABEL}({EXTRA_CHARGE_REASON_LABELS[reason]})"
    if memo:
        text += f": {memo}"
    return text[:DESCRIPTION_MAX]


def reason_of(description: str | None) -> ExtraChargeReason | None:
    """청구 줄 설명에서 사유를 읽는다. 예전 "파손비" 줄은 파손, 알 수 없으면 None."""
    if not description:
        return None
    match = _PATTERN.search(description)
    if match:
        return _BY_LABEL.get(match.group(1))
    if description.endswith(_LEGACY):
        return ExtraChargeReason.DAMAGE
    return None


def reason_label(description: str | None) -> str:
    """정산 화면 '종류' 칸: "추가 비용(세척)", 사유를 모르면 "추가 비용"."""
    reason = reason_of(description)
    if reason is None:
        return EXTRA_CHARGE_LABEL
    return f"{EXTRA_CHARGE_LABEL}({EXTRA_CHARGE_REASON_LABELS[reason]})"


def missing_part_memo(kits: Iterable[tuple[str, Iterable[str]]]) -> str | None:
    """구성품 누락 사유의 기본 메모: "카메라 세트: 배터리, 충전기"."""
    parts = [f"{name}: {', '.join(labels)}" for name, labels in kits if labels]
    text = " / ".join(parts)
    return text[:MEMO_MAX] or None


def short_text(description: str | None) -> str:
    """확인서 칸용 짧은 설명: "CAM-1 추가 비용(세척): 얼룩" → "세척: 얼룩", 예전 "CAM-1 파손비" → "파손"."""
    reason = reason_of(description)
    if reason is None:
        return description or ""
    label = EXTRA_CHARGE_REASON_LABELS[reason]
    _, sep, memo = (description or "").partition(": ")
    return f"{label}: {memo}" if sep and memo else label
