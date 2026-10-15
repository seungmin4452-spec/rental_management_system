"""기기별 로컬 환경설정(JSON). 절대경로처럼 다른 PC/OS로 옮기면 안 되는 값만 둔다.

DB(`app_settings`)는 백업으로 다른 OS에 복원되므로 기기 경로를 넣지 않는다(CROSS_PLATFORM F).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class LocalPreferences:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str, default: object = None) -> object:
        return self._load().get(key, default)

    def set(self, key: str, value: object) -> None:
        data = self._load()
        data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".prefs-", suffix=".json", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def backup_dir(self) -> Path | None:
        """사용자가 정한 기본 백업 폴더. 없어졌거나(USB 분리 등) 잘못된 값이면 None."""
        value = self.get("backup_dir")
        if isinstance(value, str) and value and Path(value).is_dir():
            return Path(value)
        return None

    def set_backup_dir(self, path: Path | None) -> None:
        self.set("backup_dir", str(path) if path is not None else None)
