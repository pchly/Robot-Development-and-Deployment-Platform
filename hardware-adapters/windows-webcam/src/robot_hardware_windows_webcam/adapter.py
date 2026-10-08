"""Discover Windows cameras without coupling the control plane to OpenCV."""

from __future__ import annotations

import platform
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport

CaptureFactory = Callable[[int], Any]


def _opencv_capture(index: int) -> Any:
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("Windows摄像头插件需要目标环境提供OpenCV") from error
    backends = (
        getattr(cv2, "CAP_MSMF", None),
        getattr(cv2, "CAP_DSHOW", None),
        getattr(cv2, "CAP_ANY", 0),
    )
    attempted: set[int] = set()
    last_capture: Any | None = None
    for backend in backends:
        if not isinstance(backend, int) or backend in attempted:
            continue
        attempted.add(backend)
        capture = cv2.VideoCapture(index, backend)
        if capture.isOpened():
            return capture
        capture.release()
        last_capture = capture
    return last_capture or cv2.VideoCapture(index)


class WindowsWebcamAdapter:
    """Enumerate and lifecycle-check local Windows webcam indices."""

    adapter_id = "hardware.camera.windows-webcam"

    def __init__(
        self,
        *,
        capture_factory: CaptureFactory = _opencv_capture,
        system_name: str | None = None,
        maximum_devices: int = 4,
    ) -> None:
        self._capture_factory = capture_factory
        self._system_name = system_name or platform.system()
        self._maximum_devices = maximum_devices
        self._capture: Any | None = None
        self._device: DeviceDescriptor | None = None
        self._config: dict[str, object] = {}
        self._running = False
        self._discovered_devices: tuple[DeviceDescriptor, ...] = ()

    def probe(self) -> list[DeviceDescriptor]:
        if self._system_name.casefold() != "windows":
            return []
        # OpenCV没有只枚举、不打开视频流的跨后端接口。成功发现后缓存进程期
        # 设备清单，避免心跳探测与正在运行的Runtime争抢同一摄像头。
        if self._discovered_devices:
            return list(self._discovered_devices)
        devices: list[DeviceDescriptor] = []
        for index in range(self._maximum_devices):
            capture = None
            try:
                capture = self._capture_factory(index)
                if not capture.isOpened():
                    continue
                try:
                    backend_name = capture.getBackendName()
                except (AttributeError, TypeError, RuntimeError):
                    backend_name = "OpenCV"
                devices.append(
                    DeviceDescriptor(
                        device_id=f"windows-camera-{index}",
                        name=f"Windows Camera {index}",
                        transport="usb",
                        manufacturer="Windows Media Capture",
                        model="opencv-video-capture",
                        serial=f"CAMERA-{index}",
                        capabilities=("image.rgb8.v1",),
                        metadata=MappingProxyType(
                            {
                                "runtime_selector": str(index),
                                "camera_index": str(index),
                                "capture_backend": backend_name,
                            }
                        ),
                    )
                )
            except Exception:
                continue
            finally:
                if capture is not None:
                    capture.release()
        if devices:
            self._discovered_devices = tuple(devices)
        return list(devices)

    def validate(self, device: DeviceDescriptor, config: Mapping[str, object]) -> list[str]:
        issues: list[str] = []
        if not device.device_id.startswith("windows-camera-"):
            issues.append("设备不属于Windows摄像头适配器")
        for name in ("width", "height", "fps"):
            value = config.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                issues.append(f"{name}必须是正整数")
        return issues

    def open(self, device: DeviceDescriptor) -> None:
        if self._system_name.casefold() != "windows":
            raise RuntimeError("Windows摄像头适配器只能在Windows节点运行")
        selector = device.metadata.get("runtime_selector")
        if not isinstance(selector, str) or not selector.isdigit():
            raise ValueError("Windows摄像头缺少合法索引")
        capture = self._capture_factory(int(selector))
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"无法打开Windows摄像头 {selector}")
        self._capture = capture
        self._device = device

    def configure(self, config: Mapping[str, object]) -> None:
        if self._capture is None:
            raise RuntimeError("Windows摄像头尚未打开")
        self._config = dict(config)

    def start(self) -> None:
        if self._capture is None or not self._config:
            raise RuntimeError("Windows摄像头尚未打开或配置")
        self._running = True

    def health(self) -> HealthReport:
        return HealthReport(
            healthy=self._running and self._capture is not None,
            message="Windows摄像头已就绪" if self._running else "Windows摄像头未运行",
            metrics=MappingProxyType(
                {"fps": float(self._config.get("fps", 0)), "frames_dropped": 0.0}
            ),
        )

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None
        self._device = None
        self._config = {}
        self._running = False
