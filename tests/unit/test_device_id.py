import hashlib

from rentdesk.platform.device_id import FakeDeviceIdProvider


def test_fake_device_id_is_deterministic_and_irreversible():
    provider = FakeDeviceIdProvider("TEST-DEVICE-0001")

    device_id = provider.get_device_id()

    assert device_id == hashlib.sha256(b"TEST-DEVICE-0001").hexdigest()
    assert "TEST-DEVICE-0001" not in device_id


def test_fake_device_id_differs_for_different_raw_ids():
    provider_a = FakeDeviceIdProvider("DEVICE-A")
    provider_b = FakeDeviceIdProvider("DEVICE-B")

    assert provider_a.get_device_id() != provider_b.get_device_id()
