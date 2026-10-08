"""硬件适配器使用的稳定数据对象。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    """适配器只读发现的设备描述。"""

    device_id: str
    name: str
    transport: str = "unknown"
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    vendor_id: str | None = None
    product_id: str | None = None
    device_nodes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class HealthReport:
    """适配器运行健康状态。"""

    healthy: bool
    message: str
    metrics: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
