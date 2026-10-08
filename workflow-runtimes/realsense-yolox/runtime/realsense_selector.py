"""Resolve an Edge-discovered RealSense identity to the SDK serial used by librealsense."""

from __future__ import annotations

from typing import Any


def _read_info(device: Any, field: Any) -> str:
    if field is None:
        return ""
    try:
        if hasattr(device, "supports") and not device.supports(field):
            return ""
        return str(device.get_info(field)).strip()
    except (RuntimeError, TypeError, ValueError):
        return ""


def resolve_sdk_serial(sdk: Any, selector: str) -> str:
    """Accept SDK, ASIC or firmware-update identity and return the SDK serial."""

    camera_info = sdk.camera_info
    serial_field = camera_info.serial_number
    alias_fields = (
        serial_field,
        getattr(camera_info, "asic_serial_number", None),
        getattr(camera_info, "firmware_update_id", None),
    )
    candidates: list[tuple[str, set[str]]] = []
    for device in sdk.context().query_devices():
        sdk_serial = _read_info(device, serial_field)
        if not sdk_serial:
            continue
        aliases = {value for field in alias_fields if (value := _read_info(device, field))}
        candidates.append((sdk_serial, aliases))

    normalized_selector = selector.strip()
    if not normalized_selector:
        if len(candidates) == 1:
            return candidates[0][0]
        if not candidates:
            raise RuntimeError("未发现可用RealSense设备")
        raise RuntimeError("发现多个RealSense设备，必须指定目标设备")

    matches = [sdk_serial for sdk_serial, aliases in candidates if normalized_selector in aliases]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(f"目标RealSense不可用：{normalized_selector}")
    raise RuntimeError(f"RealSense设备标识不唯一：{normalized_selector}")
