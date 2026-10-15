"""단일 프로그램 인스턴스 실행 잠금 (PRD 8.1).

Qt `QLockFile`을 사용해 macOS/Windows 공통으로 동작한다. 비정상 종료로 남은 잠금 파일은
Qt가 프로세스 생존 여부로 판단해 자동 회수한다(stale lock).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self._lock = QLockFile(str(path))
        self._lock.setStaleLockTime(0)  # 시간 기준이 아닌 프로세스 생존 여부로만 판단

    def acquire(self) -> bool:
        """다른 실행 중인 인스턴스가 잠금을 갖고 있으면 False."""
        return self._lock.tryLock(0)

    def release(self) -> None:
        if self._lock.isLocked():
            self._lock.unlock()
