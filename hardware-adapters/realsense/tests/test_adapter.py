"""RealSense独立插件契约测试。"""

from pathlib import Path

from robot_edge_agent.adapters import AdapterController, AdapterState, HardwareAdapter

from robot_hardware_realsense import RealSenseAdapter


def _write(device: Path, name: str, value: str) -> None:
    (device / name).write_text(value, encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    usb_root = tmp_path / "sys" / "usb"
    device_root = tmp_path / "dev" / "bus" / "usb"
    camera = usb_root / "2-3.2"
    node_directory = device_root / "002"
    camera.mkdir(parents=True)
    node_directory.mkdir(parents=True)
    (node_directory / "005").touch()
    for name, value in {
        "idVendor": "8086\n",
        "idProduct": "0ad3\n",
        "manufacturer": "Intel(R) RealSense(TM)\n",
        "product": "Intel(R) RealSense(TM) Depth Camera 415\n",
        "serial": "123423024767\n",
        "busnum": "2\n",
        "devnum": "5\n",
        "speed": "5000\n",
        "version": "3.20\n",
    }.items():
        _write(camera, name, value)
    return usb_root, device_root


def test_adapter_discovers_d415_without_vendor_sdk(tmp_path: Path) -> None:
    usb_root, device_root = _fixture(tmp_path)

    device = RealSenseAdapter(usb_root=usb_root, device_root=device_root).probe()[0]

    assert device.device_id == "realsense-usb-123423024767"
    assert device.vendor_id == "8086"
    assert device.product_id == "0ad3"
    assert device.serial == "123423024767"
    assert device.metadata["profile_id"] == "realsense.d415"
    assert device.metadata["speed_mbps"] == "5000"
    assert device.device_nodes == (str(device_root / "002" / "005"),)


def test_adapter_satisfies_protocol_and_control_lifecycle(tmp_path: Path) -> None:
    usb_root, device_root = _fixture(tmp_path)
    adapter = RealSenseAdapter(usb_root=usb_root, device_root=device_root)
    assert isinstance(adapter, HardwareAdapter)
    controller = AdapterController(adapter)
    device = adapter.probe()[0]

    controller.open(device)
    controller.configure({"width": 640, "height": 480, "fps": 30})
    controller.start()

    assert controller.state is AdapterState.RUNNING
    assert controller.health().healthy is True
    assert controller.health().metrics["runtime_managed"] == 1.0
    controller.close()
    assert controller.state is AdapterState.CLOSED


def test_adapter_ignores_unrelated_intel_usb_device(tmp_path: Path) -> None:
    usb_root = tmp_path / "usb"
    device = usb_root / "1-1"
    device.mkdir(parents=True)
    _write(device, "idVendor", "8086")
    _write(device, "idProduct", "1234")
    _write(device, "product", "Intel USB Device")

    assert RealSenseAdapter(usb_root=usb_root).probe() == []
