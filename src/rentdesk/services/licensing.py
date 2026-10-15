"""오프라인 라이선스 (PRD 11.1, CROSS_PLATFORM F, T-22).

- 판매자가 고객의 `기기 코드`를 받아 Ed25519 개인키로 서명한 라이선스 파일을 발급한다
  (`scripts/generate_license.py`, 판매자 PC에서만 실행). 앱에는 공개키만 들어 있다.
- 기기 코드는 OS별 `DeviceIdProvider`의 해시를 제품 이름과 함께 다시 해시한 20자 문자열이다.
  원본 하드웨어 식별자는 화면·파일·로그 어디에도 남지 않는다.
- 검사 실패 시 이유를 알려주고 예제 데이터 모드만 허용한다. 실제 데이터는 삭제·변경하지 않는다.
- 오프라인 서명 파일은 완전한 복제 방지책이 아니며 기본적인 구매 확인 용도다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.signing import SigningKey, VerifyKey

from rentdesk import PRODUCT
from rentdesk.domain.errors import DomainError
from rentdesk.domain.timeutil import parse_utc_str, to_utc_str
from rentdesk.license_public_key import PUBLIC_KEY_HEX
from rentdesk.platform.device_id import DeviceIdProvider
from rentdesk.services.common import Clock, current_time, default_clock

LICENSE_FORMAT_VERSION = 1
LICENSE_FILE_NAME = "license.json"
MAX_LICENSE_BYTES = 16 * 1024
MACHINE_CODE_LENGTH = 20
EDITIONS = ("standard",)
_CODE_CHARS = re.compile(r"^[A-Z2-7]{20}$")


class LicenseStatus(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    MALFORMED = "MALFORMED"
    BAD_SIGNATURE = "BAD_SIGNATURE"
    WRONG_PRODUCT = "WRONG_PRODUCT"
    WRONG_DEVICE = "WRONG_DEVICE"
    EXPIRED = "EXPIRED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"


STATUS_MESSAGES = {
    LicenseStatus.VALID: "정품 라이선스가 확인되었습니다.",
    LicenseStatus.MISSING: "등록된 라이선스가 없습니다. 판매자에게 받은 라이선스 파일을 불러와주세요.",
    LicenseStatus.MALFORMED: "라이선스 파일 형식이 올바르지 않습니다. 판매자에게 받은 파일이 맞는지 확인해주세요.",
    LicenseStatus.BAD_SIGNATURE: "라이선스 파일이 변경되었거나 정식 발급 파일이 아닙니다.",
    LicenseStatus.WRONG_PRODUCT: "RentDesk Lite용 라이선스 파일이 아닙니다.",
    LicenseStatus.WRONG_DEVICE: (
        "이 라이선스는 다른 PC용으로 발급되었습니다. PC·운영체제를 바꾸었다면 아래 기기 코드를 "
        "판매자에게 보내 사용권 이전(재발급)을 요청해주세요."
    ),
    LicenseStatus.EXPIRED: "라이선스 사용 기간이 끝났습니다. 판매자에게 문의해주세요.",
    LicenseStatus.DEVICE_UNAVAILABLE: "이 PC의 기기 코드를 확인할 수 없습니다. 판매자에게 문의해주세요.",
}


class LicenseError(DomainError):
    """라이선스 파일 등록 실패. `status`로 실패 이유를 구분한다."""

    def __init__(self, status: LicenseStatus, message: str | None = None) -> None:
        super().__init__(message or STATUS_MESSAGES[status])
        self.status = status


@dataclass(frozen=True)
class LicenseInfo:
    license_id: str
    product: str
    edition: str
    customer_label: str
    machine_code: str
    issued_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class LicenseCheck:
    status: LicenseStatus
    info: LicenseInfo | None = None

    @property
    def valid(self) -> bool:
        return self.status == LicenseStatus.VALID

    @property
    def message(self) -> str:
        return STATUS_MESSAGES[self.status]


def normalize_machine_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


def format_machine_code(code: str) -> str:
    """사람이 받아 적기 쉬운 `XXXX-XXXX-XXXX-XXXX-XXXX` 형태."""
    raw = normalize_machine_code(code)
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def is_machine_code(code: str) -> bool:
    return bool(_CODE_CHARS.match(normalize_machine_code(code)))


def machine_code_from_device_id(device_id: str) -> str:
    """OS 어댑터의 기기 해시 → 제품 전용 20자 기기 코드(base32, 100비트)."""
    digest = hashlib.sha256(f"{PRODUCT}:machine:v1:{device_id}".encode()).digest()
    return base64.b32encode(digest).decode("ascii")[:MACHINE_CODE_LENGTH]


def canonical_payload(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def issue_license(
    signing_key: SigningKey,
    *,
    machine_code: str,
    customer_label: str,
    issued_at: datetime,
    edition: str = "standard",
    expires_at: datetime | None = None,
    license_id: str | None = None,
) -> str:
    """서명된 라이선스 파일 내용(JSON 문자열). 판매자 발급 스크립트와 테스트에서만 쓴다."""
    code = normalize_machine_code(machine_code)
    if not _CODE_CHARS.match(code):
        raise ValueError("기기 코드 형식이 올바르지 않습니다.")
    if edition not in EDITIONS:
        raise ValueError(f"지원하지 않는 에디션: {edition}")
    label = customer_label.strip()
    if not label or len(label) > 100:
        raise ValueError("고객 표시명은 1~100자여야 합니다.")
    payload = {
        "format_version": LICENSE_FORMAT_VERSION,
        "license_id": license_id or str(uuid.uuid4()),
        "product": PRODUCT,
        "edition": edition,
        "customer_label": label,
        "machine_code": code,
        "issued_at_utc": to_utc_str(issued_at),
        "expires_at_utc": to_utc_str(expires_at) if expires_at else None,
    }
    signature = signing_key.sign(canonical_payload(payload)).signature
    return json.dumps(
        {"license": payload, "signature": base64.b64encode(signature).decode("ascii")},
        ensure_ascii=False, indent=2,
    )


def _parse_info(payload: dict) -> LicenseInfo:
    fields = ("license_id", "product", "edition", "customer_label", "machine_code", "issued_at_utc")
    if not all(isinstance(payload.get(f), str) for f in fields):
        raise ValueError("fields")
    expires = payload.get("expires_at_utc")
    if expires is not None and not isinstance(expires, str):
        raise ValueError("expires")
    return LicenseInfo(
        license_id=payload["license_id"],
        product=payload["product"],
        edition=payload["edition"],
        customer_label=payload["customer_label"],
        machine_code=payload["machine_code"],
        issued_at=parse_utc_str(payload["issued_at_utc"]),
        expires_at=parse_utc_str(expires) if expires else None,
    )


def verify_license(text: str, *, public_key: VerifyKey, machine_code: str, now: datetime) -> LicenseCheck:
    """서명 → 제품 → 기기 → 기간 순으로 검사한다. 문자열 비교만으로 인증하지 않는다(PRD 11.1)."""
    try:
        doc = json.loads(text)
        payload = doc["license"]
        signature = base64.b64decode(doc["signature"], validate=True)
        if not isinstance(payload, dict) or payload.get("format_version") != LICENSE_FORMAT_VERSION:
            return LicenseCheck(LicenseStatus.MALFORMED)
    except (ValueError, KeyError, TypeError):
        return LicenseCheck(LicenseStatus.MALFORMED)
    try:
        public_key.verify(canonical_payload(payload), signature)
    except (BadSignatureError, CryptoError, ValueError, TypeError):
        return LicenseCheck(LicenseStatus.BAD_SIGNATURE)
    try:
        info = _parse_info(payload)
    except (ValueError, TypeError):
        return LicenseCheck(LicenseStatus.MALFORMED)
    if info.product != PRODUCT:
        return LicenseCheck(LicenseStatus.WRONG_PRODUCT, info)
    if normalize_machine_code(info.machine_code) != normalize_machine_code(machine_code):
        return LicenseCheck(LicenseStatus.WRONG_DEVICE, info)
    if info.expires_at is not None and now >= info.expires_at:
        return LicenseCheck(LicenseStatus.EXPIRED, info)
    return LicenseCheck(LicenseStatus.VALID, info)


class LicenseService:
    """설치된 라이선스(`<앱 데이터>/license/license.json`) 확인과 새 파일 등록."""

    def __init__(
        self,
        license_dir: Path,
        device_provider: DeviceIdProvider,
        *,
        public_key_hex: str = PUBLIC_KEY_HEX,
        clock: Clock = default_clock,
    ) -> None:
        self._dir = license_dir
        self._device = device_provider
        self._public_key = VerifyKey(bytes.fromhex(public_key_hex))
        self._clock = clock
        self._machine_code: str | None = None

    @property
    def license_path(self) -> Path:
        return self._dir / LICENSE_FILE_NAME

    def machine_code(self) -> str | None:
        """화면 표시용 기기 코드. OS가 식별자를 주지 않으면 None."""
        if self._machine_code is None:
            try:
                self._machine_code = machine_code_from_device_id(self._device.get_device_id())
            except (RuntimeError, OSError):
                return None
        return self._machine_code

    def _verify_text(self, text: str) -> LicenseCheck:
        code = self.machine_code()
        if code is None:
            return LicenseCheck(LicenseStatus.DEVICE_UNAVAILABLE)
        return verify_license(text, public_key=self._public_key, machine_code=code,
                              now=current_time(self._clock))

    def check(self) -> LicenseCheck:
        try:
            raw = self.license_path.read_bytes()
        except FileNotFoundError:
            return LicenseCheck(LicenseStatus.MISSING)
        except OSError:
            return LicenseCheck(LicenseStatus.MALFORMED)
        if len(raw) > MAX_LICENSE_BYTES:
            return LicenseCheck(LicenseStatus.MALFORMED)
        try:
            return self._verify_text(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return LicenseCheck(LicenseStatus.MALFORMED)

    def install(self, source: Path | str) -> LicenseCheck:
        """선택한 파일을 검사해 유효할 때만 등록한다. 실패하면 기존 라이선스를 그대로 둔다."""
        path = Path(source)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise LicenseError(LicenseStatus.MALFORMED, "라이선스 파일을 읽을 수 없습니다.") from exc
        if len(raw) > MAX_LICENSE_BYTES:
            raise LicenseError(LicenseStatus.MALFORMED)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LicenseError(LicenseStatus.MALFORMED) from exc
        result = self._verify_text(text)
        if not result.valid:
            raise LicenseError(result.status)
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".license-", suffix=".json", dir=str(self._dir))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            os.replace(tmp, self.license_path)
        except OSError as exc:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise LicenseError(LicenseStatus.MALFORMED, "라이선스 파일을 저장하지 못했습니다.") from exc
        return result
