"""不向宿主机引入Librealsense的RealSense控制面适配器。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport

_INTEL_VENDOR_ID = "8086"


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip("\x00\r\n ")
    except OSError:
        return None


class RealSenseAdapter:
    """通过Linux USB sysfs发现相机；数据面由独立OCI Runtime管理。"""

    adapter_id = "hardware.camera.realsense"

    def __init__(
        self,
        *,
        usb_root: Path = Path("/sys/bus/usb/devices"),
        device_root: Path = Path("/dev/bus/usb"),
    ) -> None:
        self._usb_root = usb_root
        self._device_root = device_root
        self._device: DeviceDescriptor | None = None
        self._config: dict[str, object] = {}
        self._ready = False

    def probe(self) -> list[DeviceDescriptor]:
        """只读发现RealSense USB设备，不导入SDK或启动视频流。"""

        try:
            candidates = list(self._usb_root.iterdir())
        except OSError:
            return []
        devices = [
            descriptor
            for candidate in candidates
            if (descriptor := self._descriptor(candidate)) is not None
        ]
        return sorted(devices, key=lambda item: (item.name.casefold(), item.device_id))

    def _descriptor(self, path: Path) -> DeviceDescriptor | None:
        vendor_id = (_read_text(path / "idVendor") or "").lower()
        product_id = (_read_text(path / "idProduct") or "").lower()
        product = _read_text(path / "product") or ""
        if vendor_id != _INTEL_VENDOR_ID or "realsense" not in product.casefold():
            return None
        serial = _read_text(path / "serial")
        identity = serial or path.name
        safe_identity = re.sub(r"[^A-Za-z0-9._-]+", "-", identity).strip("-").lower()
        bus_number = _read_text(path / "busnum")
        device_number = _read_text(path / "devnum")
        device_nodes: tuple[str, ...] = ()
        if (
            bus_number
            and device_number
            and bus_number.isdigit()
            and device_number.isdigit()
        ):
            node = self._device_root / f"{int(bus_number):03d}" / f"{int(device_number):03d}"
            if node.exists():
                device_nodes = (str(node),)
        metadata = {
            key: value
            for key, value in {
                "profile_id": "realsense.d415" if product_id == "0ad3" else None,
                "transport_serial": serial,
                "serial_source": "usb_descriptor" if serial else None,
                "speed_mbps": _read_text(path / "speed"),
                "usb_version": _read_text(path / "version"),
                "sysfs_path": str(path),
                "data_plane": "oci_runtime",
            }.items()
            if value is not None
        }
        return DeviceDescriptor(
            device_id=f"realsense-usb-{safe_identity}",
            name=product or "Intel RealSense Camera",
            transport="usb",
            manufacturer=_read_text(path / "manufacturer") or "Intel",
            model=product or None,
            serial=serial,
            vendor_id=vendor_id,
            product_id=product_id,
            device_nodes=device_nodes,
            capabilities=(
                "image.rgb8.v1",
                "image.depth16.v1",
                "camera.intrinsics.v1",
            ),
            metadata=MappingProxyType(metadata),
        )

    def validate(self, device: DeviceDescriptor, config: Mapping[str, object]) -> list[str]:
        issues: list[str] = []
        if device.vendor_id != _INTEL_VENDOR_ID or not device.device_id.startswith(
            "realsense-usb-"
        ):
            issues.append("设备不属于RealSense适配包")
        for name in ("width", "height", "fps"):
            value = config.get(name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                issues.append(f"{name}必须是正整数")
        return issues

    def open(self, device: DeviceDescriptor) -> None:
        issues = self.validate(device, {})
        if issues:
            raise ValueError("；".join(issues))
        self._device = device

    def configure(self, config: Mapping[str, object]) -> None:
        if self._device is None:
            raise RuntimeError("RealSense设备尚未打开")
        issues = self.validate(self._device, config)
        if issues:
            raise ValueError("；".join(issues))
        self._config = dict(config)

    def start(self) -> None:
        if self._device is None:
            raise RuntimeError("RealSense设备尚未打开")
        self._ready = True

    def health(self) -> HealthReport:
        return HealthReport(
            healthy=self._ready,
            message=(
                "控制面适配器已就绪；数据流由OCI Runtime管理"
                if self._ready
                else "控制面适配器未启动"
            ),
            metrics=MappingProxyType({"runtime_managed": 1.0}),
        )

    def stop(self) -> None:
        self._ready = False

    def close(self) -> None:
        self.stop()
        self._device = None
        self._config = {}
