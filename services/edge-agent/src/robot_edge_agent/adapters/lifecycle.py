"""硬件适配器生命周期状态机。"""

from collections.abc import Mapping
from enum import Enum

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport
from robot_edge_agent.adapters.protocol import HardwareAdapter


class AdapterState(str, Enum):
    """适配器受控生命周期状态。"""

    CLOSED = "closed"
    OPEN = "open"
    CONFIGURED = "configured"
    RUNNING = "running"
    FAILED = "failed"


class InvalidAdapterTransition(RuntimeError):
    """调用顺序不符合生命周期约束。"""


class AdapterValidationError(ValueError):
    """设备或配置未通过适配器校验。"""

    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("；".join(issues))


class AdapterController:
    """统一执行适配器生命周期并阻止非法状态转换。"""

    def __init__(self, adapter: HardwareAdapter) -> None:
        self.adapter = adapter
        self.state = AdapterState.CLOSED
        self.device: DeviceDescriptor | None = None

    def open(self, device: DeviceDescriptor) -> None:
        """校验当前状态并打开设备。"""

        self._require(AdapterState.CLOSED)
        try:
            self.adapter.open(device)
        except Exception:
            self.state = AdapterState.FAILED
            raise
        self.device = device
        self.state = AdapterState.OPEN

    def configure(self, config: Mapping[str, object]) -> None:
        """校验配置并应用到已打开设备。"""

        self._require(AdapterState.OPEN, AdapterState.CONFIGURED)
        if self.device is None:
            raise InvalidAdapterTransition("当前没有已打开设备")
        issues = self.adapter.validate(self.device, config)
        if issues:
            raise AdapterValidationError(issues)
        try:
            self.adapter.configure(config)
        except Exception:
            self.state = AdapterState.FAILED
            raise
        self.state = AdapterState.CONFIGURED

    def start(self) -> None:
        """启动已经配置的适配器。"""

        self._require(AdapterState.CONFIGURED)
        try:
            self.adapter.start()
        except Exception:
            self.state = AdapterState.FAILED
            raise
        self.state = AdapterState.RUNNING

    def health(self) -> HealthReport:
        """读取运行中适配器的健康状态。"""

        self._require(AdapterState.RUNNING)
        return self.adapter.health()

    def stop(self) -> None:
        """停止运行并保留已打开设备和配置。"""

        self._require(AdapterState.RUNNING)
        try:
            self.adapter.stop()
        except Exception:
            self.state = AdapterState.FAILED
            raise
        self.state = AdapterState.CONFIGURED

    def close(self) -> None:
        """关闭适配器；运行中会先执行stop。"""

        if self.state is AdapterState.CLOSED:
            return
        if self.state is AdapterState.RUNNING:
            self.stop()
        try:
            self.adapter.close()
        finally:
            self.device = None
            self.state = AdapterState.CLOSED

    def _require(self, *allowed: AdapterState) -> None:
        if self.state not in allowed:
            expected = ", ".join(item.value for item in allowed)
            raise InvalidAdapterTransition(
                f"当前状态{self.state.value}不允许该操作，期望状态：{expected}"
            )
