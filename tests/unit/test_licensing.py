"""오프라인 Ed25519 라이선스 (PRD 11.1, T-22, CP-08의 OS 독립 부분)."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nacl.signing import SigningKey, VerifyKey

from rentdesk.license_public_key import PUBLIC_KEY_HEX
from rentdesk.platform.device_id import FakeDeviceIdProvider
from rentdesk.services.licensing import (
    LicenseError,
    LicenseService,
    LicenseStatus,
    canonical_payload,
    format_machine_code,
    is_machine_code,
    issue_license,
    machine_code_from_device_id,
    verify_license,
)

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]


class BrokenDeviceProvider:
    def get_device_id(self) -> str:
        raise RuntimeError("no id")


@pytest.fixture
def key() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture
def device() -> FakeDeviceIdProvider:
    return FakeDeviceIdProvider("MAC-UUID-1234")


@pytest.fixture
def code(device) -> str:
    return machine_code_from_device_id(device.get_device_id())


def _service(tmp_path, key, device, clock=lambda: NOW) -> LicenseService:
    return LicenseService(tmp_path / "license", device,
                          public_key_hex=key.verify_key.encode().hex(), clock=clock)


def _issue(key, code, **kw) -> str:
    return issue_license(key, machine_code=code, customer_label="OO렌탈", issued_at=NOW, **kw)


def test_embedded_public_key_is_valid_ed25519():
    assert len(bytes(VerifyKey(bytes.fromhex(PUBLIC_KEY_HEX)))) == 32


def test_machine_code_is_stable_hashed_and_formatted(device, code):
    assert code == machine_code_from_device_id(device.get_device_id())
    assert is_machine_code(code) and len(code) == 20
    assert "MAC-UUID-1234" not in code and device.get_device_id() not in code
    pretty = format_machine_code(code)
    assert pretty.count("-") == 4 and is_machine_code(pretty.lower())
    other = machine_code_from_device_id(FakeDeviceIdProvider("WINDOWS-GUID").get_device_id())
    assert other != code


def test_valid_license(key, code):
    result = verify_license(_issue(key, code), public_key=key.verify_key, machine_code=code, now=NOW)
    assert result.valid and result.info.customer_label == "OO렌탈"
    assert result.info.expires_at is None


def test_t22_tampered_payload_is_rejected(key, code):
    doc = json.loads(_issue(key, code))
    doc["license"]["edition"] = "enterprise"
    result = verify_license(json.dumps(doc), public_key=key.verify_key, machine_code=code, now=NOW)
    assert result.status == LicenseStatus.BAD_SIGNATURE


def test_t22_machine_code_swapped_in_payload_is_rejected(key, code):
    """다른 기기 코드로 내용만 바꿔치기 → 서명 불일치."""
    other = machine_code_from_device_id("other")
    doc = json.loads(_issue(key, code))
    doc["license"]["machine_code"] = other
    result = verify_license(json.dumps(doc), public_key=key.verify_key, machine_code=other, now=NOW)
    assert result.status == LicenseStatus.BAD_SIGNATURE


def test_t22_signature_tampered(key, code):
    doc = json.loads(_issue(key, code))
    sig = bytearray(base64.b64decode(doc["signature"]))
    sig[0] ^= 1
    doc["signature"] = base64.b64encode(bytes(sig)).decode()
    assert verify_license(json.dumps(doc), public_key=key.verify_key, machine_code=code,
                          now=NOW).status == LicenseStatus.BAD_SIGNATURE
    doc["signature"] = base64.b64encode(b"short").decode()
    assert verify_license(json.dumps(doc), public_key=key.verify_key, machine_code=code,
                          now=NOW).status == LicenseStatus.BAD_SIGNATURE


def test_t22_signed_by_other_key(key, code):
    forged = _issue(SigningKey.generate(), code)
    assert verify_license(forged, public_key=key.verify_key, machine_code=code,
                          now=NOW).status == LicenseStatus.BAD_SIGNATURE


def test_t22_other_device(key, code):
    other = machine_code_from_device_id(FakeDeviceIdProvider("WINDOWS-GUID").get_device_id())
    result = verify_license(_issue(key, code), public_key=key.verify_key, machine_code=other, now=NOW)
    assert result.status == LicenseStatus.WRONG_DEVICE
    assert "사용권 이전" in result.message


def test_expired_license(key, code):
    text = _issue(key, code, expires_at=NOW + timedelta(days=1))
    assert verify_license(text, public_key=key.verify_key, machine_code=code, now=NOW).valid
    assert verify_license(text, public_key=key.verify_key, machine_code=code,
                          now=NOW + timedelta(days=1)).status == LicenseStatus.EXPIRED


def test_wrong_product_even_if_signed(key, code):
    payload = json.loads(_issue(key, code))["license"]
    payload["product"] = "OtherApp"
    sig = key.sign(canonical_payload(payload)).signature
    text = json.dumps({"license": payload, "signature": base64.b64encode(sig).decode()})
    assert verify_license(text, public_key=key.verify_key, machine_code=code,
                          now=NOW).status == LicenseStatus.WRONG_PRODUCT


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"license": {}, "signature": "@@"}',
                                  '{"license": {"format_version": 2}, "signature": ""}'])
def test_malformed(key, code, text):
    assert verify_license(text, public_key=key.verify_key, machine_code=code,
                          now=NOW).status == LicenseStatus.MALFORMED


def test_issue_validates_inputs(key, code):
    with pytest.raises(ValueError):
        issue_license(key, machine_code="short", customer_label="x", issued_at=NOW)
    with pytest.raises(ValueError):
        issue_license(key, machine_code=code, customer_label="  ", issued_at=NOW)


# ------------------------------------------------------------------ LicenseService


def test_service_missing_then_install(tmp_path, key, device, code):
    service = _service(tmp_path, key, device)
    assert service.check().status == LicenseStatus.MISSING
    src = tmp_path / "다운로드" / "RentDeskLite_license.json"
    src.parent.mkdir()
    src.write_text(_issue(key, code), encoding="utf-8")
    assert service.install(src).valid
    assert service.license_path.exists()
    assert service.check().valid


def test_service_install_rejects_invalid_and_keeps_existing(tmp_path, key, device, code):
    service = _service(tmp_path, key, device)
    good = tmp_path / "good.json"
    good.write_text(_issue(key, code), encoding="utf-8")
    service.install(good)
    before = service.license_path.read_bytes()

    other_pc = tmp_path / "other.json"
    other_pc.write_text(_issue(key, machine_code_from_device_id("other")), encoding="utf-8")
    with pytest.raises(LicenseError) as info:
        service.install(other_pc)
    assert info.value.status == LicenseStatus.WRONG_DEVICE
    assert service.license_path.read_bytes() == before

    with pytest.raises(LicenseError):
        service.install(tmp_path / "없는 파일.json")
    big = tmp_path / "big.json"
    big.write_bytes(b"{" + b" " * 20_000 + b"}")
    with pytest.raises(LicenseError):
        service.install(big)
    assert service.check().valid


def test_service_detects_tampered_installed_file(tmp_path, key, device, code):
    service = _service(tmp_path, key, device)
    service.license_path.parent.mkdir(parents=True)
    doc = json.loads(_issue(key, code))
    doc["license"]["customer_label"] = "다른 회사"
    service.license_path.write_text(json.dumps(doc), encoding="utf-8")
    assert service.check().status == LicenseStatus.BAD_SIGNATURE
    service.license_path.write_bytes(b"\xff\xfe broken")
    assert service.check().status == LicenseStatus.MALFORMED


def test_license_copied_to_another_os_is_not_valid(tmp_path, key, device, code):
    """Mac에서 받은 라이선스 파일을 Windows PC에 복사해도 활성화되지 않는다(CP-08, CROSS_PLATFORM I)."""
    text = _issue(key, code)
    windows = _service(tmp_path / "win", key, FakeDeviceIdProvider("WINDOWS-MACHINE-GUID"))
    windows.license_path.parent.mkdir(parents=True)
    windows.license_path.write_text(text, encoding="utf-8")
    assert windows.check().status == LicenseStatus.WRONG_DEVICE
    # 판매자가 Windows 기기 코드로 재발급하면 유효
    reissued = tmp_path / "reissued.json"
    reissued.write_text(_issue(key, windows.machine_code()), encoding="utf-8")
    assert windows.install(reissued).valid


def test_device_unavailable(tmp_path, key):
    service = _service(tmp_path, key, BrokenDeviceProvider())
    assert service.machine_code() is None
    assert service.check().status in (LicenseStatus.MISSING, LicenseStatus.DEVICE_UNAVAILABLE)
    src = tmp_path / "l.json"
    src.write_text(_issue(key, "A" * 20), encoding="utf-8")
    with pytest.raises(LicenseError) as info:
        service.install(src)
    assert info.value.status == LicenseStatus.DEVICE_UNAVAILABLE


def test_default_public_key_used_by_service(tmp_path, device):
    service = LicenseService(tmp_path, device, clock=lambda: NOW)
    assert service.check().status == LicenseStatus.MISSING


# ------------------------------------------------------------------ 판매자 스크립트


def _script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "scripts" / "generate_license.py"), *args],
                          capture_output=True, text=True, encoding="utf-8")


def test_seller_script_keygen_issue_verify(tmp_path, code):
    key_path = tmp_path / "seller keys" / "signing_key.json"
    assert _script("keygen", "--out", str(key_path)).returncode == 0
    if sys.platform != "win32":
        assert key_path.stat().st_mode & 0o077 == 0  # 소유자만 읽기
    assert _script("keygen", "--out", str(key_path)).returncode == 2  # 덮어쓰지 않음

    out = tmp_path / "고객" / "license.json"
    issued = _script("issue", "--key", str(key_path), "--machine-code", format_machine_code(code),
                     "--customer", "OO렌탈", "--out", str(out))
    assert issued.returncode == 0, issued.stderr
    ledger = (key_path.parent / "issued_licenses.csv").read_text(encoding="utf-8")
    assert "OO렌탈" in ledger and format_machine_code(code) in ledger

    ok = _script("verify", "--file", str(out), "--machine-code", code, "--key", str(key_path))
    assert ok.returncode == 0 and "VALID" in ok.stdout
    wrong = _script("verify", "--file", str(out), "--machine-code", "B" * 20, "--key", str(key_path))
    assert wrong.returncode == 1 and "WRONG_DEVICE" in wrong.stdout
    # 앱 내장 공개키와 다른 키로 서명했으므로 앱 기준으로는 무효
    assert _script("verify", "--file", str(out), "--machine-code", code).returncode == 1
    # 개인키가 라이선스 파일에 들어가지 않음
    private_hex = json.loads(key_path.read_text())["private_key_hex"]
    assert private_hex not in out.read_text(encoding="utf-8")


def test_seller_script_refuses_key_inside_repo(tmp_path):
    inside = REPO / "keys_should_not_be_here" / "k.json"
    result = _script("keygen", "--out", str(inside))
    assert result.returncode == 2
    assert not inside.exists() and not inside.parent.exists()
