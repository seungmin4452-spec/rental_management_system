"""PyInstaller로 패키징된 뒤에도 동작하는 읽기 전용 리소스 경로 조회."""

from __future__ import annotations

import sys
from pathlib import Path


def get_resources_dir() -> Path:
    """실행 환경(소스 실행 vs PyInstaller 번들)에 맞는 읽기 전용 리소스 루트를 반환한다."""
    bundled_base = getattr(sys, "_MEIPASS", None)
    if bundled_base:
        return Path(bundled_base) / "rentdesk" / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def get_sample_data_dir() -> Path:
    return get_resources_dir() / "sample_data"


def get_app_icon_path() -> Path:
    """창·작업 표시줄 아이콘(256px PNG). 원본은 `scripts/make_icons.py`로 생성."""
    return get_resources_dir() / "icons" / "app.png"
