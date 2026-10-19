"""PyInstaller로 패키징된 뒤에도 동작하는 읽기 전용 리소스 경로 조회."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


def get_resources_dir() -> Path:
    """실행 환경(소스 실행 vs PyInstaller 번들)에 맞는 읽기 전용 리소스 루트를 반환한다."""
    bundled_base = getattr(sys, "_MEIPASS", None)
    if bundled_base:
        return Path(bundled_base) / "rentdesk" / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def get_sample_data_dir() -> Path:
    return get_resources_dir() / "sample_data"


def get_starter_dir() -> Path:
    """시작 도우미(M4.6 R7)의 장비 종류별 추천 카테고리 데이터(`*.json`)."""
    return get_resources_dir() / "starter"


def get_app_icon_path() -> Path:
    """창·작업 표시줄 아이콘(256px PNG). 원본은 `scripts/make_icons.py`로 생성."""
    return get_resources_dir() / "icons" / "app.png"


@dataclass(frozen=True)
class SupportInfo:
    """판매자 문의 안내(빌드마다 `resources/support/support.json`에서 바꾼다). 고객 개인정보는 넣지 않는다."""
    contact: str
    manual_url: str = ""


DEFAULT_SUPPORT = SupportInfo(contact="프로그램 사용 중 궁금한 점은 구매한 곳(판매자)에 문의해주세요.")


def get_support_info() -> SupportInfo:
    try:
        data = json.loads((get_resources_dir() / "support" / "support.json").read_text(encoding="utf-8"))
        contact = str(data.get("contact") or "").strip()
        url = str(data.get("manual_url") or "").strip()
    except (OSError, ValueError, AttributeError):
        return DEFAULT_SUPPORT
    if not contact:
        return DEFAULT_SUPPORT
    return SupportInfo(contact=contact, manual_url=url if url.startswith("https://") else "")


def get_third_party_notices_path() -> Path:
    """오픈소스 라이선스 고지문. `scripts/make_notices.py`로 생성한다(K6)."""
    return get_resources_dir() / "legal" / "THIRD_PARTY_NOTICES.txt"
