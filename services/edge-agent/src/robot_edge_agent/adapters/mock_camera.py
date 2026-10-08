"""用于端到端框架验证的模拟相机适配器。"""

from collections.abc import Mapping
from types import MappingProxyType

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport


class MockCameraAdapter:
    """不访问真实硬件的确定性相机适配器。"""

    adapter_id = "hardware.camera.mock"

    def __init__(self) -> None:
        self._opened = False
        self._running = False
        self._config: dict[str, object] = {}

    def probe(self) -> list[DeviceDescriptor]:
        return [
            DeviceDescriptor(
                device_id="mock-camera-01",
                name="模拟RGB相机",
                transport="simulation",
                manufacturer="Robot Platform",
                model="mock-v1",
                serial="SIM-0001",
                capabilities=("image.rgb8.v1",),
                metadata=MappingProxyType({"transport": "simulation"}),
            )
        ]

    def validate(self, device: DeviceDescriptor, config: Mapping[str, object]) -> list[str]:
        issues: list[str] = []
        if device.device_id != "mock-camera-01":
            issues.append("设备不属于模拟相机适配器")
        for name in ("width", "height", "fps"):
            value = config.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                issues.append(f"{name}必须是正整数")
        return issues

    def open(self, device: DeviceDescriptor) -> None:
        if device.device_id != "mock-camera-01":
            raise ValueError("未知模拟相机")
        self._opened = True

    def configure(self, config: Mapping[str, object]) -> None:
        if not self._opened:
            raise RuntimeError("模拟相机尚未打开")
        self._config = dict(config)

    def start(self) -> None:
        if not self._config:
            raise RuntimeError("模拟相机尚未配置")
        self._running = True

    def health(self) -> HealthReport:
        fps = float(self._config.get("fps", 0)) if self._running else 0.0
        return HealthReport(
            healthy=self._running,
            message="模拟视频流运行中" if self._running else "模拟视频流未运行",
            metrics=MappingProxyType({"fps": fps, "frames_dropped": 0.0}),
        )

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        self._opened = False
        self._config = {}
