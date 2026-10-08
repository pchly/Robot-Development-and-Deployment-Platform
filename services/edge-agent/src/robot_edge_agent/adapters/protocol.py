"""硬件适配器必须实现的生命周期协议。"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport


@runtime_checkable
class HardwareAdapter(Protocol):
    """硬件族适配包的最小运行接口。"""

    adapter_id: str

    def probe(self) -> list[DeviceDescriptor]:
        """只读发现可用设备，不占用硬件。"""

    def validate(self, device: DeviceDescriptor, config: Mapping[str, object]) -> list[str]:
        """返回配置与环境问题；空列表表示可继续。"""

    def open(self, device: DeviceDescriptor) -> None:
        """打开指定设备。"""

    def configure(self, config: Mapping[str, object]) -> None:
        """应用已经校验的配置。"""

    def start(self) -> None:
        """开始产生数据或接受命令。"""

    def health(self) -> HealthReport:
        """返回当前健康状态和轻量指标。"""

    def stop(self) -> None:
        """停止数据流或命令处理。"""

    def close(self) -> None:
        """释放设备资源。"""
