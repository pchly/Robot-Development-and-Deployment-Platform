"""硬件适配器SDK生命周期测试。"""

import pytest

from robot_edge_agent.adapters import AdapterController, AdapterState
from robot_edge_agent.adapters.lifecycle import (
    AdapterValidationError,
    InvalidAdapterTransition,
)
from robot_edge_agent.adapters.mock_camera import MockCameraAdapter


def test_mock_camera_completes_lifecycle() -> None:
    adapter = MockCameraAdapter()
    controller = AdapterController(adapter)
    device = adapter.probe()[0]

    controller.open(device)
    controller.configure({"width": 640, "height": 480, "fps": 15})
    controller.start()

    assert controller.state is AdapterState.RUNNING
    assert controller.health().healthy is True
    assert controller.health().metrics["fps"] == 15.0

    controller.stop()
    assert controller.state is AdapterState.CONFIGURED
    controller.close()
    assert controller.state is AdapterState.CLOSED
    assert controller.device is None


def test_controller_rejects_invalid_transition() -> None:
    controller = AdapterController(MockCameraAdapter())

    with pytest.raises(InvalidAdapterTransition, match="期望状态：configured"):
        controller.start()


def test_controller_rejects_invalid_configuration() -> None:
    adapter = MockCameraAdapter()
    controller = AdapterController(adapter)
    controller.open(adapter.probe()[0])

    with pytest.raises(AdapterValidationError) as captured:
        controller.configure({"width": 640, "height": 480, "fps": 0})

    assert captured.value.issues == ("fps必须是正整数",)
    assert controller.state is AdapterState.OPEN


def test_close_stops_running_adapter() -> None:
    adapter = MockCameraAdapter()
    controller = AdapterController(adapter)
    controller.open(adapter.probe()[0])
    controller.configure({"width": 320, "height": 240, "fps": 10})
    controller.start()

    controller.close()

    assert controller.state is AdapterState.CLOSED
    assert adapter.health().healthy is False


def test_controller_marks_open_failure() -> None:
    adapter = MockCameraAdapter()
    controller = AdapterController(adapter)
    unknown_device = adapter.probe()[0]
    object.__setattr__(unknown_device, "device_id", "unknown")

    with pytest.raises(ValueError, match="未知模拟相机"):
        controller.open(unknown_device)

    assert controller.state is AdapterState.FAILED
