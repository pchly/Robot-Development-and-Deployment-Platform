"""RealSense transport-to-SDK identity resolution tests."""

from types import SimpleNamespace

import pytest

from realsense_selector import resolve_sdk_serial


class FakeDevice:
    def __init__(self, **values: str) -> None:
        self.values = values

    def supports(self, field: str) -> bool:
        return field in self.values

    def get_info(self, field: str) -> str:
        return self.values[field]


class FakeContext:
    def __init__(self, devices: list[FakeDevice]) -> None:
        self.devices = devices

    def query_devices(self) -> list[FakeDevice]:
        return self.devices


def fake_sdk(*devices: FakeDevice) -> SimpleNamespace:
    fields = SimpleNamespace(
        serial_number="sdk_serial",
        asic_serial_number="asic_serial",
        firmware_update_id="firmware_update_id",
    )
    return SimpleNamespace(camera_info=fields, context=lambda: FakeContext(list(devices)))


def test_resolves_usb_transport_serial_to_sdk_serial() -> None:
    sdk = fake_sdk(
        FakeDevice(
            sdk_serial="125322060182",
            asic_serial="123423024767",
            firmware_update_id="123423024767",
        )
    )

    assert resolve_sdk_serial(sdk, "123423024767") == "125322060182"
    assert resolve_sdk_serial(sdk, "125322060182") == "125322060182"


def test_empty_selector_auto_selects_only_device() -> None:
    sdk = fake_sdk(FakeDevice(sdk_serial="SDK-ONE"))

    assert resolve_sdk_serial(sdk, "") == "SDK-ONE"


def test_empty_selector_rejects_multiple_devices() -> None:
    sdk = fake_sdk(
        FakeDevice(sdk_serial="SDK-ONE"),
        FakeDevice(sdk_serial="SDK-TWO"),
    )

    with pytest.raises(RuntimeError, match="多个RealSense"):
        resolve_sdk_serial(sdk, "")


def test_unknown_selector_is_rejected() -> None:
    sdk = fake_sdk(FakeDevice(sdk_serial="SDK-ONE", asic_serial="ASIC-ONE"))

    with pytest.raises(RuntimeError, match="目标RealSense不可用"):
        resolve_sdk_serial(sdk, "missing")
