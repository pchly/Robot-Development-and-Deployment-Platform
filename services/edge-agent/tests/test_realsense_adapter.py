"""RealSense适配器的后端契约测试。"""

import importlib

import pytest

from robot_edge_agent.adapters import AdapterController, AdapterState
from robot_edge_agent.adapters.realsense import RealSenseAdapter, RealSenseDependencyError


class FakeCameraInfo:
    serial_number = "serial_number"
    asic_serial_number = "asic_serial_number"
    firmware_update_id = "firmware_update_id"
    physical_port = "physical_port"
    name = "name"
    product_id = "product_id"
    firmware_version = "firmware_version"
    product_line = "product_line"
    usb_type_descriptor = "usb_type_descriptor"


class FakeDevice:
    values = {
        "serial_number": "123423024767",
        "asic_serial_number": "125322060182",
        "firmware_update_id": "123423024767",
        "physical_port": "/sys/devices/platform/usb/2-3.2",
        "name": "Intel RealSense D415",
        "product_id": "0AD3",
        "firmware_version": "5.16.0.1",
        "product_line": "D400",
        "usb_type_descriptor": "3.2",
    }

    def supports(self, info: str) -> bool:
        return info in self.values

    def get_info(self, info: str) -> str:
        return self.values[info]


class FakeContext:
    def query_devices(self) -> list[FakeDevice]:
        return [FakeDevice()]


class FakeConfig:
    def __init__(self) -> None:
        self.serial = ""
        self.streams: list[tuple[object, ...]] = []

    def enable_device(self, serial: str) -> None:
        self.serial = serial

    def enable_stream(self, *arguments: object) -> None:
        self.streams.append(arguments)


class FakePipeline:
    def __init__(self) -> None:
        self.started_with: FakeConfig | None = None
        self.stopped = False

    def start(self, config: FakeConfig) -> None:
        self.started_with = config

    def poll_for_frames(self) -> bool:
        return True

    def stop(self) -> None:
        self.stopped = True


class FakeSDK:
    camera_info = FakeCameraInfo()
    stream = type("Stream", (), {"color": "color", "depth": "depth"})()
    format = type("Format", (), {"rgb8": "rgb8", "z16": "z16"})()

    def __init__(self) -> None:
        self.last_pipeline: FakePipeline | None = None

    def context(self) -> FakeContext:
        return FakeContext()

    def config(self) -> FakeConfig:
        return FakeConfig()

    def pipeline(self) -> FakePipeline:
        self.last_pipeline = FakePipeline()
        return self.last_pipeline


def test_probe_maps_sdk_information_to_stable_descriptor() -> None:
    device = RealSenseAdapter(FakeSDK()).probe()[0]

    assert device.device_id == "realsense-123423024767"
    assert device.name == "Intel RealSense D415"
    assert device.serial == "123423024767"
    assert device.metadata["sdk_serial"] == "123423024767"
    assert device.metadata["asic_serial"] == "125322060182"
    assert device.metadata["firmware_update_id"] == "123423024767"
    assert device.metadata["product_id"] == "0AD3"
    assert device.metadata["usb_type"] == "3.2"


def test_realsense_completes_adapter_lifecycle() -> None:
    sdk = FakeSDK()
    adapter = RealSenseAdapter(sdk)
    controller = AdapterController(adapter)
    device = adapter.probe()[0]

    controller.open(device)
    controller.configure(
        {"width": 640, "height": 480, "fps": 30, "enable_color": True, "enable_depth": True}
    )
    controller.start()

    assert controller.state is AdapterState.RUNNING
    assert controller.health().metrics["frames_received"] == 1.0
    assert sdk.last_pipeline is not None
    assert sdk.last_pipeline.started_with is not None
    assert sdk.last_pipeline.started_with.serial == "123423024767"
    assert len(sdk.last_pipeline.started_with.streams) == 2

    controller.close()
    assert sdk.last_pipeline.stopped is True
    assert controller.state is AdapterState.CLOSED


def test_validate_rejects_invalid_stream_configuration() -> None:
    adapter = RealSenseAdapter(FakeSDK())
    device = adapter.probe()[0]

    issues = adapter.validate(
        device,
        {"width": 640, "height": 480, "fps": 0, "enable_color": False, "enable_depth": False},
    )

    assert "fps必须是正整数" in issues
    assert "彩色流和深度流不能同时关闭" in issues


def test_missing_python_binding_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_module(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", missing_module)

    with pytest.raises(RealSenseDependencyError, match="pyrealsense2"):
        RealSenseAdapter().probe()
