"""Intel RealSense RGB-D相机适配器。"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport


class RealSenseDependencyError(RuntimeError):
    """当前运行环境没有可用的Librealsense Python绑定。"""


class RealSenseAdapter:
    """通过可替换的pyrealsense2后端管理RealSense设备。"""

    adapter_id = "hardware.camera.realsense"

    def __init__(self, sdk: Any | None = None) -> None:
        self._injected_sdk = sdk
        self._loaded_sdk: Any | None = None
        self._device: DeviceDescriptor | None = None
        self._config: dict[str, object] = {}
        self._pipeline: Any | None = None
        self._running = False
        self._frames_received = 0

    def _sdk(self) -> Any:
        if self._injected_sdk is not None:
            return self._injected_sdk
        if self._loaded_sdk is not None:
            return self._loaded_sdk
        try:
            self._loaded_sdk = importlib.import_module("pyrealsense2")
        except ImportError as error:
            raise RealSenseDependencyError(
                "未安装pyrealsense2；请先在Jetson安装并验证Librealsense SDK"
            ) from error
        return self._loaded_sdk

    def _read_info(self, device: Any, name: str) -> str | None:
        sdk = self._sdk()
        info = getattr(sdk.camera_info, name, None)
        if info is None:
            return None
        try:
            if hasattr(device, "supports") and not device.supports(info):
                return None
            value = device.get_info(info)
        except RuntimeError:
            return None
        return str(value).strip() or None

    def probe(self) -> list[DeviceDescriptor]:
        """通过SDK只读枚举RealSense设备，不启动视频流。"""

        devices: list[DeviceDescriptor] = []
        for device in self._sdk().context().query_devices():
            serial = self._read_info(device, "serial_number")
            name = self._read_info(device, "name") or "RealSense Camera"
            product_id = self._read_info(device, "product_id")
            identity = serial or product_id or name
            safe_identity = re.sub(r"[^A-Za-z0-9._-]+", "-", identity).strip("-").lower()
            metadata = {
                key: value
                for key, value in {
                    "sdk_serial": serial,
                    "asic_serial": self._read_info(device, "asic_serial_number"),
                    "firmware_update_id": self._read_info(device, "firmware_update_id"),
                    "physical_port": self._read_info(device, "physical_port"),
                    "firmware_version": self._read_info(device, "firmware_version"),
                    "product_id": product_id,
                    "product_line": self._read_info(device, "product_line"),
                    "usb_type": self._read_info(device, "usb_type_descriptor"),
                }.items()
                if value is not None
            }
            devices.append(
                DeviceDescriptor(
                    device_id=f"realsense-{safe_identity}",
                    name=name,
                    transport="usb",
                    manufacturer="RealSense",
                    model=name,
                    serial=serial,
                    vendor_id="8086",
                    product_id=product_id,
                    capabilities=(
                        "image.rgb8.v1",
                        "image.depth16.v1",
                        "camera.intrinsics.v1",
                    ),
                    metadata=MappingProxyType(metadata),
                )
            )
        return devices

    def validate(self, device: DeviceDescriptor, config: Mapping[str, object]) -> list[str]:
        """校验通用RGB-D流参数，具体Profile在启动时由SDK再次解析。"""

        issues: list[str] = []
        if not device.device_id.startswith("realsense-") or not device.serial:
            issues.append("设备不是具有序列号的RealSense相机")
        for name in ("width", "height", "fps"):
            value = config.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                issues.append(f"{name}必须是正整数")
        enable_color = config.get("enable_color", True)
        enable_depth = config.get("enable_depth", True)
        if not isinstance(enable_color, bool) or not isinstance(enable_depth, bool):
            issues.append("enable_color和enable_depth必须是布尔值")
        elif not enable_color and not enable_depth:
            issues.append("彩色流和深度流不能同时关闭")
        return issues

    def open(self, device: DeviceDescriptor) -> None:
        if not device.device_id.startswith("realsense-") or not device.serial:
            raise ValueError("无法打开未知RealSense设备")
        self._sdk()
        self._device = device

    def configure(self, config: Mapping[str, object]) -> None:
        if self._device is None:
            raise RuntimeError("RealSense设备尚未打开")
        self._config = dict(config)

    def start(self) -> None:
        if self._device is None or not self._config:
            raise RuntimeError("RealSense设备尚未打开或配置")
        sdk = self._sdk()
        pipeline = sdk.pipeline()
        pipeline_config = sdk.config()
        pipeline_config.enable_device(self._device.serial)
        width = int(self._config["width"])
        height = int(self._config["height"])
        fps = int(self._config["fps"])
        if self._config.get("enable_color", True):
            pipeline_config.enable_stream(
                sdk.stream.color,
                width,
                height,
                sdk.format.rgb8,
                fps,
            )
        if self._config.get("enable_depth", True):
            pipeline_config.enable_stream(
                sdk.stream.depth,
                width,
                height,
                sdk.format.z16,
                fps,
            )
        pipeline.start(pipeline_config)
        self._pipeline = pipeline
        self._running = True
        self._frames_received = 0

    def health(self) -> HealthReport:
        if not self._running or self._pipeline is None:
            return HealthReport(healthy=False, message="RealSense视频流未运行")
        if self._pipeline.poll_for_frames():
            self._frames_received += 1
        return HealthReport(
            healthy=True,
            message="RealSense视频流运行中",
            metrics=MappingProxyType(
                {
                    "configured_fps": float(self._config["fps"]),
                    "frames_received": float(self._frames_received),
                }
            ),
        )

    def stop(self) -> None:
        if self._running and self._pipeline is not None:
            self._pipeline.stop()
        self._running = False

    def close(self) -> None:
        self.stop()
        self._pipeline = None
        self._device = None
        self._config = {}
        self._frames_received = 0
