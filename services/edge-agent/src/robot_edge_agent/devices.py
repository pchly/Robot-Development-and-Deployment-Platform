"""端侧设备的只读发现。"""

import hashlib
from collections.abc import Callable
from pathlib import Path

from robot_edge_agent.adapters.plugins import AdapterPluginRegistry


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip("\x00\r\n ")
    except OSError:
        return None


def _device_id(vendor_id: str, product_id: str, stable_identity: str) -> str:
    digest = hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:12]
    return f"usb-{vendor_id}-{product_id}-{digest}"


def discover_usb_devices(
    *,
    usb_root: Path = Path("/sys/bus/usb/devices"),
    device_root: Path = Path("/dev/bus/usb"),
) -> list[dict[str, object]]:
    """从sysfs读取USB设备，不打开设备节点，也不执行供应商程序。"""

    devices: list[dict[str, object]] = []
    try:
        candidates = list(usb_root.iterdir())
    except OSError:
        return devices

    for device_path in candidates:
        vendor_id = _read_text(device_path / "idVendor")
        product_id = _read_text(device_path / "idProduct")
        if not vendor_id or not product_id:
            continue

        vendor_id = vendor_id.lower()
        product_id = product_id.lower()
        serial = _read_text(device_path / "serial")
        product = _read_text(device_path / "product")
        manufacturer = _read_text(device_path / "manufacturer")
        bus_number = _read_text(device_path / "busnum")
        device_number = _read_text(device_path / "devnum")
        speed = _read_text(device_path / "speed")
        usb_version = _read_text(device_path / "version")

        device_nodes: list[str] = []
        if bus_number and device_number and bus_number.isdigit() and device_number.isdigit():
            node = device_root / f"{int(bus_number):03d}" / f"{int(device_number):03d}"
            if node.exists():
                device_nodes.append(str(node))

        metadata = {"sysfs_path": str(device_path)}
        if serial:
            metadata["transport_serial"] = serial
            metadata["serial_source"] = "usb_descriptor"
        if speed:
            metadata["speed_mbps"] = speed
        if usb_version:
            metadata["usb_version"] = usb_version

        stable_identity = serial or device_path.name
        devices.append(
            {
                "device_id": _device_id(vendor_id, product_id, stable_identity),
                "transport": "usb",
                "name": product or f"USB {vendor_id}:{product_id}",
                "manufacturer": manufacturer,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "serial": serial,
                "device_nodes": device_nodes,
                "metadata": metadata,
            }
        )

    return sorted(devices, key=lambda item: (str(item["name"]).casefold(), str(item["device_id"])))


def discover_edge_devices(
    adapter_registry: AdapterPluginRegistry,
    *,
    usb_discoverer: Callable[[], list[dict[str, object]]] = discover_usb_devices,
) -> list[dict[str, object]]:
    """合并内核级USB盘点与独立适配器插件发现结果。"""

    devices: dict[str, dict[str, object]] = {}
    usb_identities: dict[tuple[str, str, str, str], str] = {}
    for device in usb_discoverer():
        device_id = str(device.get("device_id", ""))
        if device_id and device_id not in devices:
            devices[device_id] = device
            identity = _physical_identity(device)
            if identity is not None:
                usb_identities[identity] = device_id

    for plugin_device in adapter_registry.discover_devices():
        plugin_id = str(plugin_device.get("device_id", ""))
        if not plugin_id:
            continue
        identity = _physical_identity(plugin_device)
        existing_id = usb_identities.get(identity) if identity is not None else None
        if existing_id is None:
            devices.setdefault(plugin_id, plugin_device)
            continue
        devices[existing_id] = _merge_device(devices[existing_id], plugin_device)

    return sorted(
        devices.values(),
        key=lambda item: (str(item["name"]).casefold(), str(item["device_id"])),
    )


def _physical_identity(device: dict[str, object]) -> tuple[str, str, str, str] | None:
    transport = str(device.get("transport") or "").lower()
    vendor_id = str(device.get("vendor_id") or "").lower()
    product_id = str(device.get("product_id") or "").lower()
    serial = str(device.get("serial") or "")
    if not all((transport, vendor_id, product_id, serial)):
        return None
    return transport, vendor_id, product_id, serial


def _merge_device(
    kernel_device: dict[str, object],
    plugin_device: dict[str, object],
) -> dict[str, object]:
    """保留稳定内核ID，并使用插件提供的领域信息增强记录。"""

    merged = {**kernel_device, **plugin_device, "device_id": kernel_device["device_id"]}
    merged["device_nodes"] = sorted(
        {
            *[str(item) for item in kernel_device.get("device_nodes", [])],
            *[str(item) for item in plugin_device.get("device_nodes", [])],
        }
    )
    merged["metadata"] = {
        **dict(kernel_device.get("metadata", {})),
        **dict(plugin_device.get("metadata", {})),
        "plugin_device_id": str(plugin_device["device_id"]),
    }
    return merged
