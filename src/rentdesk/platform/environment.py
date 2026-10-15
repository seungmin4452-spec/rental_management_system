"""OS/아키텍처 조회. 비즈니스 로직에서 직접 sys.platform 분기 대신 이 모듈을 통해서만 조회한다."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvironmentInfo:
    os_name: str  # "macOS" | "Windows" | "Linux" | "Unknown"
    machine: str  # e.g. "arm64", "x86_64", "AMD64"
    os_version: str
    python_version: str


def get_environment_info() -> EnvironmentInfo:
    if sys.platform == "darwin":
        os_name = "macOS"
    elif sys.platform == "win32":
        os_name = "Windows"
    elif sys.platform.startswith("linux"):
        os_name = "Linux"
    else:
        os_name = "Unknown"

    return EnvironmentInfo(
        os_name=os_name,
        machine=platform.machine(),
        os_version=platform.platform(),
        python_version=platform.python_version(),
    )


def is_supported_target() -> bool:
    """P0 공식 지원 대상: macOS arm64, Windows x64(AMD64)."""
    info = get_environment_info()
    if info.os_name == "macOS":
        return info.machine == "arm64"
    if info.os_name == "Windows":
        return info.machine in ("AMD64", "x86_64")
    return False
