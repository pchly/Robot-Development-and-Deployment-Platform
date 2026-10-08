"""USB设备只读发现测试。"""

from pathlib import Path

from robot_edge_agent.adapters import AdapterPluginRegistry, DeviceDescriptor, HealthReport
from robot_edge_agent.devices import discover_edge_devices, discover_usb_devices


def _write(device: Path, name: str, value: str) -> None:
    (device / name).write_text(value, encoding="utf-8")


def test_discover_usb_devices_reads_identity_and_node(tmp_path: Path) -> None:
    usb_root = tmp_path / "sys" / "usb"
    device_root = tmp_path / "dev" / "bus" / "usb"
    realsense = usb_root / "1-2"
    node_directory = device_root / "001"
    realsense.mkdir(parents=True)
    node_directory.mkdir(parents=True)
    (node_directory / "005").touch()
    _write(realsense, "idVendor", "8086\n")
    _write(realsense, "idProduct", "0b5c\n")
    _write(realsense, "manufacturer", "Intel RealSense\n")
    _write(realsense, "product", "Intel RealSense D455\n")
    _write(realsense, "serial", "123456789\n")
    _write(realsense, "busnum", "1\n")
    _write(realsense, "devnum", "5\n")
    _write(realsense, "speed", "5000\n")
    _write(realsense, "version", " 3.20\n")

    devices = discover_usb_devices(usb_root=usb_root, device_root=device_root)

    assert len(devices) == 1
    assert devices[0]["device_id"].startswith("usb-8086-0b5c-")
    assert devices[0]["name"] == "Intel RealSense D455"
    assert devices[0]["serial"] == "123456789"
    assert devices[0]["metadata"]["transport_serial"] == "123456789"
    assert devices[0]["metadata"]["serial_source"] == "usb_descriptor"
    assert devices[0]["device_nodes"] == [str(node_directory / "005")]
    assert devices[0]["metadata"]["speed_mbps"] == "5000"


def test_discover_usb_devices_ignores_entries_without_usb_ids(tmp_path: Path) -> None:
    usb_root = tmp_path / "usb"
    (usb_root / "usb1").mkdir(parents=True)

    assert discover_usb_devices(usb_root=usb_root, device_root=tmp_path / "dev") == []


def test_discover_usb_devices_handles_missing_root(tmp_path: Path) -> None:
    assert discover_usb_devices(usb_root=tmp_path / "missing") == []


def test_discover_edge_devices_merges_kernel_and_plugin_inventory() -> None:
    class SerialAdapter:
        adapter_id = "hardware.sensor.serial_demo"

        def probe(self) -> list[DeviceDescriptor]:
            return [
                DeviceDescriptor(
                    device_id="serial-demo-01",
                    name="Serial Demo",
                    transport="serial",
                    serial="SERIAL-01",
                )
            ]

        def validate(self, device: object, config: object) -> list[str]:
            return []

        def open(self, device: object) -> None:
            pass

        def configure(self, config: object) -> None:
            pass

        def start(self) -> None:
            pass

        def health(self) -> HealthReport:
            return HealthReport(healthy=True, message="ok")

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    registry = AdapterPluginRegistry(adapters=(SerialAdapter(),))
    usb_device = {
        "device_id": "usb-1234-5678-test",
        "transport": "usb",
        "name": "USB Demo",
    }

    devices = discover_edge_devices(registry, usb_discoverer=lambda: [usb_device])

    assert [item["device_id"] for item in devices] == [
        "serial-demo-01",
        "usb-1234-5678-test",
    ]


def test_plugin_enriches_same_physical_usb_device_without_duplicate() -> None:
    class CameraAdapter:
        adapter_id = "hardware.camera.demo"

        def probe(self) -> list[DeviceDescriptor]:
            return [
                DeviceDescriptor(
                    device_id="camera-demo-serial-01",
                    name="Domain Camera",
                    transport="usb",
                    manufacturer="Camera Vendor",
                    model="Camera Model",
                    serial="SERIAL-01",
                    vendor_id="1234",
                    product_id="5678",
                    capabilities=("image.rgb8.v1",),
                    metadata={"profile_id": "camera.demo"},
                )
            ]

        def validate(self, device: object, config: object) -> list[str]:
            return []

        def open(self, device: object) -> None:
            pass

        def configure(self, config: object) -> None:
            pass

        def start(self) -> None:
            pass

        def health(self) -> HealthReport:
            return HealthReport(healthy=True, message="ok")

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    kernel_device = {
        "device_id": "usb-1234-5678-stable",
        "transport": "usb",
        "name": "Generic USB Camera",
        "manufacturer": "Generic Vendor",
        "vendor_id": "1234",
        "product_id": "5678",
        "serial": "SERIAL-01",
        "device_nodes": ["/dev/bus/usb/001/005"],
        "metadata": {"speed_mbps": "5000"},
    }

    devices = discover_edge_devices(
        AdapterPluginRegistry(adapters=(CameraAdapter(),)),
        usb_discoverer=lambda: [kernel_device],
    )

    assert len(devices) == 1
    assert devices[0]["device_id"] == "usb-1234-5678-stable"
    assert devices[0]["name"] == "Domain Camera"
    assert devices[0]["metadata"]["speed_mbps"] == "5000"
    assert devices[0]["metadata"]["adapter_id"] == "hardware.camera.demo"
    assert devices[0]["metadata"]["plugin_device_id"] == "camera-demo-serial-01"
