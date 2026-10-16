"""종료 시 자동 백업 (USABILITY_PLAN U3).

- 실제 데이터만, 하루 1개(서울 날짜 기준 같은 이름 → 같은 날 다시 종료하면 최신으로 교체).
- 앱 데이터 폴더 `backups/auto/`에 최근 `KEEP`개만 남긴다.
- 선택 시 사용자가 고른 기본 백업 폴더(USB 등)에도 복사한다. 폴더가 없으면 건너뛰고 다음 실행 때 알린다.
- 자동 백업은 '마지막 백업'(7일 백업 알림 기준)을 갱신하지 않는다. 같은 PC 안의 사본만으로는
  PC 고장·분실에 대비할 수 없으므로 외부 보관 백업을 계속 권한다.
- 실패해도 예외를 밖으로 던지지 않는다(종료를 막지 않음). 결과는 기기별 설정에 남겨 다음 실행 때 보여준다.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rentdesk.domain.timeutil import parse_utc_str, to_seoul, to_utc_str
from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services.backup import create_backup

log = logging.getLogger("rentdesk.auto_backup")

KEEP = 14
PREFIX = "RentDeskLite_auto_"
ENABLED_KEY = "auto_backup_enabled"
COPY_KEY = "auto_backup_copy_to_backup_dir"
LAST_KEY = "auto_backup_last_utc"
NOTICE_KEY = "auto_backup_notice"


@dataclass(frozen=True)
class AutoBackupResult:
    path: Path | None
    copied_to: Path | None = None
    removed: tuple[Path, ...] = ()
    notice: str | None = None  # 다음 실행 때 사용자에게 보여줄 문제


def auto_backup_name(now: datetime) -> str:
    return f"{PREFIX}{to_seoul(now).date().isoformat()}.zip"


def prune(folder: Path, keep: int = KEEP, prefix: str = PREFIX) -> tuple[Path, ...]:
    """자동 백업 파일 중 오래된 것부터 지워 `keep`개만 남긴다(이름에 날짜가 있어 이름순 = 날짜순)."""
    files = sorted(p for p in folder.glob(f"{prefix}*.zip") if p.is_file())
    removed = []
    for old in files[: max(0, len(files) - keep)]:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            log.warning("could not remove old auto backup")
    return tuple(removed)


def run_auto_backup(conn, folder: Path, now: datetime, *, keep: int = KEEP,
                    copy_dir: Path | None = None, copy_requested: bool = False) -> AutoBackupResult:
    """자동 백업을 만든다. 실패하면 path=None과 notice를 돌려준다(예외를 던지지 않음)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        result = create_backup(conn, folder / auto_backup_name(now), now=now, overwrite=True)
    except Exception:  # noqa: BLE001 - 종료를 막지 않기 위해 모든 오류를 기록만 한다
        log.exception("auto backup failed")
        return AutoBackupResult(
            path=None,
            notice="지난번 종료 때 자동 백업을 만들지 못했습니다. 설정 화면에서 직접 백업 파일을 만들어주세요.",
        )
    removed = prune(folder, keep)
    copied = None
    notice = None
    if copy_requested:
        if copy_dir is not None and copy_dir.is_dir():
            try:
                copied = Path(shutil.copy2(result.path, copy_dir / result.path.name))
            except OSError:
                log.warning("auto backup copy to backup folder failed")
                notice = "자동 백업을 기본 백업 폴더로 복사하지 못했습니다. 폴더(USB 등)가 연결돼 있는지 확인해주세요."
        else:
            notice = "기본 백업 폴더(USB 등)를 찾을 수 없어 자동 백업을 복사하지 못했습니다. 연결 상태를 확인해주세요."
    return AutoBackupResult(path=result.path, copied_to=copied, removed=removed, notice=notice)


class AutoBackupSettings:
    """기기별 설정(preferences.json)에 저장하는 자동 백업 선택값과 마지막 결과."""

    def __init__(self, prefs: LocalPreferences) -> None:
        self.prefs = prefs

    @property
    def enabled(self) -> bool:
        return self.prefs.get(ENABLED_KEY, True) is not False

    def set_enabled(self, value: bool) -> None:
        self.prefs.set(ENABLED_KEY, bool(value))

    @property
    def copy_to_backup_dir(self) -> bool:
        return self.prefs.get(COPY_KEY, False) is True

    def set_copy_to_backup_dir(self, value: bool) -> None:
        self.prefs.set(COPY_KEY, bool(value))

    @property
    def notice(self) -> str | None:
        value = self.prefs.get(NOTICE_KEY)
        return value if isinstance(value, str) and value else None

    def clear_notice(self) -> None:
        if self.notice is not None:
            self.prefs.set(NOTICE_KEY, None)

    def last_at(self) -> datetime | None:
        value = self.prefs.get(LAST_KEY)
        try:
            return parse_utc_str(value) if isinstance(value, str) else None
        except ValueError:
            return None

    def record(self, result: AutoBackupResult, now: datetime) -> None:
        if result.path is not None:
            self.prefs.set(LAST_KEY, to_utc_str(now))
        self.prefs.set(NOTICE_KEY, result.notice)

    def run(self, conn, folder: Path, now: datetime) -> AutoBackupResult | None:
        """설정이 켜져 있으면 자동 백업을 만들고 결과를 기록한다. 꺼져 있으면 None."""
        if not self.enabled:
            return None
        raw_dir = self.prefs.get("backup_dir")
        copy_dir = Path(raw_dir) if isinstance(raw_dir, str) and raw_dir else None
        result = run_auto_backup(conn, folder, now, copy_dir=copy_dir,
                                 copy_requested=self.copy_to_backup_dir)
        try:
            self.record(result, now)
        except OSError:
            log.warning("could not record auto backup result")
        return result
