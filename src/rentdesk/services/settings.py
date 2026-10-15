"""운영 설정(유예시간·연체 배수·전환 버퍼) 조회/변경 (PRD S-09).

변경값은 이후 확정되는 예약에만 스냅샷으로 적용되며 기존 약정에 소급되지 않는다.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.models import RentalPolicy
from rentdesk.domain.pricing import parse_multiplier
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.services.common import Clock, current_time, default_clock

MAX_MINUTES = 7 * 24 * 60


def read_policy(conn: sqlite3.Connection) -> RentalPolicy:
    return RentalPolicy(
        grace_minutes=int(repo.get_setting(conn, "grace_minutes")),
        late_multiplier=Decimal(str(repo.get_setting(conn, "late_multiplier"))),
        turnaround_buffer_minutes=int(repo.get_setting(conn, "turnaround_buffer_minutes")),
    )


def _validate_minutes(field: str, label: str, value: object) -> int:
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
        grace = _validate_minutes("grace_minutes", "유예시간", grace_minutes)
        buffer = _validate_minutes("turnaround_buffer_minutes", "전환 버퍼", turnaround_buffer_minutes)
        multiplier = parse_multiplier(late_multiplier)
        if multiplier > 100:
            raise ValidationError("late_multiplier", "연체 배수는 100 이하여야 합니다.")
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
