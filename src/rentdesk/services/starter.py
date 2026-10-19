"""StarterService: 시작 도우미(M4.6 R7) — 장비 종류를 고르면 추천 카테고리·카테고리 설정을 추가한다.

- 추천값은 `resources/starter/*.json`(코드 분기 없음). 형식 검증은 `domain.starter`.
- 고른 종류의 카테고리 중 **없는 이름만** 한 트랜잭션으로 추가한다. 기존 카테고리는 건드리지 않는다.
- 첫 실행(장비·고객·예약이 하나도 없음)에 한 번 권한다. 적용하거나 건너뛰면 다시 묻지 않는다
  (`app_settings.starter_offered`, 마이그레이션 없이 "없으면 아직 안 물어봄"). 메뉴에서 언제든 다시 열 수 있다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.starter import (
    StarterKind,
    StarterPlan,
    parse_starter_kind,
    plan_starter,
    sort_kinds,
)
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import transaction
from rentdesk.platform.resources import get_starter_dir
from rentdesk.services.assets import _clean_category_policy, _clean_rate_rule
from rentdesk.services.common import Clock, current_time, default_clock

OFFERED_KEY = "starter_offered"
MAX_FILE_BYTES = 64 * 1024


def load_starter_kinds(directory: Path | None = None) -> list[StarterKind]:
    """데이터 폴더의 `*.json`을 모두 읽어 검증한다. 하나라도 잘못되면 오류(동봉 데이터 실수를 숨기지 않음)."""
    folder = get_starter_dir() if directory is None else directory
    kinds = []
    for path in sorted(folder.glob("*.json")):
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValidationError("starter", f"시작 도우미 데이터({path.name})가 너무 큽니다.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValidationError("starter", f"시작 도우미 데이터({path.name})를 읽을 수 없습니다.") from None
        kind = parse_starter_kind(data, path.name)
        for category in kind.categories:  # 카테고리 관리 창과 같은 검증
            _clean_category_policy(
                category.policy.turnaround_buffer_minutes, category.policy.grace_minutes,
                category.policy.late_multiplier,
            )
            _clean_rate_rule(category.rate_rule)
        kinds.append(kind)
    return sort_kinds(kinds)


@dataclass(frozen=True)
class StarterResult:
    added: tuple[str, ...]
    skipped: tuple[str, ...]  # 이미 있어서 건너뛴 이름(기존 값 유지)

    @property
    def message(self) -> str:
        parts = []
        if self.added:
            parts.append(f"카테고리 {len(self.added)}개를 추가했습니다: {', '.join(self.added)}")
        else:
            parts.append("새로 추가할 카테고리가 없습니다.")
        if self.skipped:
            parts.append(f"이미 있는 카테고리 {len(self.skipped)}개는 그대로 두었습니다: {', '.join(self.skipped)}")
        return "\n".join(parts)


class StarterService:
    def __init__(
        self, conn: sqlite3.Connection, clock: Clock = default_clock, *, data_dir: Path | None = None
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._data_dir = data_dir
        self._kinds: list[StarterKind] | None = None

    def kinds(self) -> list[StarterKind]:
        if self._kinds is None:
            self._kinds = load_starter_kinds(self._data_dir)
        return list(self._kinds)

    # ------------------------------------------------------------------ 첫 실행

    def is_empty_database(self) -> bool:
        """장비·고객·예약이 하나도 없는 DB(카테고리만 있는 것은 빈 DB로 본다)."""
        for table in ("assets", "customers", "rental_orders"):
            if self._conn.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone()[0]:  # noqa: S608
                return False
        return True

    def was_offered(self) -> bool:
        try:
            return bool(repo.get_setting(self._conn, OFFERED_KEY))
        except KeyError:
            return False

    def should_offer(self) -> bool:
        """첫 실행에 도우미를 띄울지: 아직 안 물어봤고 DB가 비어 있을 때."""
        return not self.was_offered() and self.is_empty_database()

    def mark_offered(self) -> None:
        """건너뛰기: 다음 실행부터 묻지 않는다."""
        with transaction(self._conn):
            repo.set_setting(self._conn, OFFERED_KEY, True)

    # ------------------------------------------------------------------ 적용

    def plan(self, keys: Iterable[str]) -> StarterPlan:
        existing = [c.name for c in repo.list_categories(self._conn, include_archived=True)]
        return plan_starter(self.kinds(), keys, existing)

    def apply(self, keys: Iterable[str]) -> StarterResult:
        """고른 종류의 카테고리를 추가한다(한 트랜잭션). 아무것도 안 골라도 '물어봄'으로 기록한다."""
        keys = list(keys)
        if any(not isinstance(k, str) for k in keys):
            raise ValidationError("starter", "장비 종류 값이 올바르지 않습니다.")
        kinds = self.kinds()
        now = current_time(self._clock)
        with transaction(self._conn):
            # 저장 직전에 다시 계획(다른 창에서 카테고리를 만들었어도 기존 값은 덮어쓰지 않음)
            existing = [c.name for c in repo.list_categories(self._conn, include_archived=True)]
            plan = plan_starter(kinds, keys, existing)
            for item in plan.add:
                category = item.category
                category_id = repo.insert_category(self._conn, category.name)
                if category.policy.overridden:
                    repo.update_category_policy(self._conn, category_id, category.policy)
                if category.rate_rule is not None:
                    repo.update_category_rate_rule(self._conn, category_id, category.rate_rule)
                repo.insert_audit(
                    self._conn, entity_type="category", entity_id=category_id, action="STARTER_ADD",
                    description=f"시작 도우미: {item.kind_label} — {category.name}", now=now,
                )
            repo.set_setting(self._conn, OFFERED_KEY, True)
        return StarterResult(
            added=tuple(i.category.name for i in plan.add), skipped=plan.skipped
        )
