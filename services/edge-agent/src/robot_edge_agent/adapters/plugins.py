"""从受管目录加载独立硬件适配包。"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path
from typing import Any, Protocol

from robot_edge_agent.adapters.protocol import HardwareAdapter

LOGGER = logging.getLogger(__name__)
ENTRY_POINT_GROUP = "robot_platform.hardware_adapters"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_ALLOWED_TRANSPORTS = {"usb", "can", "serial", "ethernet", "simulation"}


class AdapterEntryPoint(Protocol):
    """测试与importlib.metadata共同满足的最小入口协议。"""

    name: str
    value: str

    def load(self) -> Any: ...


class DuplicateAdapterIdError(RuntimeError):
    """两个已安装插件声明了相同adapter_id。"""


@dataclass(frozen=True)
class PluginLoadFailure:
    """被隔离的单个插件加载失败。"""

    entry_point: str
    message: str


@dataclass(frozen=True)
class AdapterPluginRegistry:
    """一次Agent启动期间固定的适配器插件快照。"""

    adapters: tuple[HardwareAdapter, ...] = ()
    failures: tuple[PluginLoadFailure, ...] = ()

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(adapter.adapter_id for adapter in self.adapters))

    def discover_devices(self) -> list[dict[str, object]]:
        """隔离各插件probe故障并转换为Platform API设备结构。"""

        devices: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for adapter in self.adapters:
            try:
                descriptors = adapter.probe()
            except Exception as error:
                LOGGER.warning("硬件插件probe失败：%s：%s", adapter.adapter_id, error)
                continue
            for descriptor in descriptors:
                if (
                    not _SAFE_ID.fullmatch(descriptor.device_id)
                    or descriptor.transport not in _ALLOWED_TRANSPORTS
                    or descriptor.device_id in seen_ids
                ):
                    LOGGER.warning(
                        "硬件插件返回了无效或重复设备：adapter=%s device=%s transport=%s",
                        adapter.adapter_id,
                        descriptor.device_id,
                        descriptor.transport,
                    )
                    continue
                seen_ids.add(descriptor.device_id)
                metadata = dict(descriptor.metadata)
                metadata["adapter_id"] = adapter.adapter_id
                if descriptor.model:
                    metadata.setdefault("model", descriptor.model)
                if descriptor.capabilities:
                    metadata.setdefault("capabilities", ",".join(descriptor.capabilities))
                devices.append(
                    {
                        "device_id": descriptor.device_id,
                        "transport": descriptor.transport,
                        "name": descriptor.name,
                        "manufacturer": descriptor.manufacturer,
                        "vendor_id": descriptor.vendor_id,
                        "product_id": descriptor.product_id,
                        "serial": descriptor.serial,
                        "device_nodes": list(descriptor.device_nodes),
                        "metadata": metadata,
                    }
                )
        return sorted(
            devices,
            key=lambda item: (str(item["name"]).casefold(), str(item["device_id"])),
        )


def _managed_entry_points(plugin_paths: Sequence[Path]) -> list[AdapterEntryPoint]:
    entries: list[AdapterEntryPoint] = []
    resolved_paths = [str(path.resolve()) for path in plugin_paths if path.is_dir()]
    for path in resolved_paths:
        if path not in sys.path:
            sys.path.append(path)
    for distribution in distributions(path=resolved_paths):
        entries.extend(
            entry for entry in distribution.entry_points if entry.group == ENTRY_POINT_GROUP
        )
    return entries


def load_adapter_plugins(
    *,
    plugin_paths: Sequence[Path] = (),
    entry_points: Iterable[AdapterEntryPoint] | None = None,
) -> AdapterPluginRegistry:
    """加载插件；普通加载故障隔离，adapter_id冲突立即拒绝。"""

    selected_entries = (
        list(entry_points) if entry_points is not None else _managed_entry_points(plugin_paths)
    )
    adapters: dict[str, HardwareAdapter] = {}
    origins: dict[str, str] = {}
    failures: list[PluginLoadFailure] = []
    for entry in sorted(selected_entries, key=lambda item: (item.name, item.value)):
        origin = f"{entry.name}={entry.value}"
        try:
            factory = entry.load()
            if not callable(factory):
                raise TypeError("entry point目标必须是可调用适配器工厂")
            adapter = factory()
            if not isinstance(adapter, HardwareAdapter):
                raise TypeError("插件没有实现HardwareAdapter协议")
            if not _SAFE_ID.fullmatch(adapter.adapter_id) or not adapter.adapter_id.startswith(
                "hardware."
            ):
                raise ValueError("adapter_id格式不合法")
            if adapter.adapter_id in adapters:
                raise DuplicateAdapterIdError(
                    f"adapter_id重复：{adapter.adapter_id}"
                    f"（{origins[adapter.adapter_id]} 与 {origin}）"
                )
            adapters[adapter.adapter_id] = adapter
            origins[adapter.adapter_id] = origin
        except DuplicateAdapterIdError:
            raise
        except Exception as error:
            failures.append(PluginLoadFailure(entry_point=origin, message=str(error)))
            LOGGER.warning("硬件插件加载失败并已隔离：%s：%s", origin, error)
    return AdapterPluginRegistry(
        adapters=tuple(adapters.values()),
        failures=tuple(failures),
    )
