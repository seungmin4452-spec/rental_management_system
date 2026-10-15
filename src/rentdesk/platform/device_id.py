"""기기 식별자 조회 추상화.

- 원본 하드웨어 식별자를 그대로 저장/전송하지 않고 해시된 불가역 문자열만 사용한다.
- Windows 전용 API나 macOS 전용 명령은 해당 OS 분기에서만 호출한다.
- 테스트는 FakeDeviceIdProvider로 OS 호출 없이 동작을 검증한다.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from typing import Protocol


class DeviceIdProvider(Protocol):
    def get_device_id(self) -> str:
        """현재 기기를 식별하는 불가역 해시 문자열(hex)을 반환한다."""
        ...


def _hash_identifier(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


class MacOSDeviceIdProvider:
    """macOS: IOPlatformExpertDevice의 IOPlatformUUID를 해시한다."""

    def get_device_id(self) -> str:
        if sys.platform != "darwin":
            raise RuntimeError("MacOSDeviceIdProvider는 macOS에서만 사용할 수 있습니다.")
        try:
            output = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"macOS 기기 식별자를 조회할 수 없습니다: {exc}") from exc

        for line in output.splitlines():
            if "IOPlatformUUID" in line:
                uuid_value = line.split('"')[-2]
                return _hash_identifier(uuid_value)
        raise RuntimeError("IOPlatformUUID를 찾을 수 없습니다.")


class WindowsDeviceIdProvider:
    """Windows: 레지스트리의 MachineGuid를 해시한다."""

    def get_device_id(self) -> str:
        if sys.platform != "win32":
            raise RuntimeError("WindowsDeviceIdProvider는 Windows에서만 사용할 수 있습니다.")
        import winreg  # Windows 전용 모듈, 다른 OS에서 import하지 않음

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        except OSError as exc:
            raise RuntimeError(f"Windows 기기 식별자를 조회할 수 없습니다: {exc}") from exc
        return _hash_identifier(machine_guid)


class FakeDeviceIdProvider:
    """테스트 전용: 고정 문자열을 해시하여 반환."""

    def __init__(self, fixed_raw_id: str = "TEST-DEVICE-0001") -> None:
        self._fixed_raw_id = fixed_raw_id

    def get_device_id(self) -> str:
        return _hash_identifier(self._fixed_raw_id)


def get_default_provider() -> DeviceIdProvider:
    if sys.platform == "darwin":
        return MacOSDeviceIdProvider()
    if sys.platform == "win32":
        return WindowsDeviceIdProvider()
    raise RuntimeError(f"지원하지 않는 운영체제입니다: {sys.platform}")
