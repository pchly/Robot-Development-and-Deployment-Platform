"""独立硬件适配包加载与故障隔离测试。"""

from pathlib import Path

import pytest

from robot_edge_agent.adapters import DeviceDescriptor, HealthReport
from robot_edge_agent.adapters.plugins import (
    AdapterPluginRegistry,
    DuplicateAdapterIdError,
    load_adapter_plugins,
)


class FakeEntryPoint:
    def __init__(self, name: str, value: str, target: object) -> None:
        self.name = name
        self.value = value
        self._target = target

    def load(self) -> object:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class DemoAdapter:
    adapter_id = "hardware.sensor.demo"

    def probe(self) -> list[DeviceDescriptor]:
        return [
            DeviceDescriptor(
                device_id="demo-sensor-01",
                name="Demo Sensor",
                transport="can",
                manufacturer="Demo",
                serial="CAN-01",
                capabilities=("sensor.value.v1",),
            )
        ]

    def validate(self, device: DeviceDescriptor, config: object) -> list[str]:
        return []

    def open(self, device: DeviceDescriptor) -> None:
        pass

    def configure(self, config: object) -> None:
        pass

    def start(self) -> None:
        pass

    def health(self) -> HealthReport:
        return HealthReport(healthy=True, message="ok")

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_plugin_load_failure_is_isolated() -> None:
    registry = load_adapter_plugins(
        entry_points=[
            FakeEntryPoint("broken", "broken:Adapter", ImportError("SDK缺失")),
            FakeEntryPoint("demo", "demo:Adapter", DemoAdapter),
        ]
    )

    assert registry.capabilities == ("hardware.sensor.demo",)
    assert len(registry.failures) == 1
    assert "SDK缺失" in registry.failures[0].message
    assert registry.discover_devices()[0]["transport"] == "can"
    assert registry.discover_devices()[0]["metadata"]["adapter_id"] == "hardware.sensor.demo"


def test_duplicate_adapter_id_is_rejected() -> None:
    with pytest.raises(DuplicateAdapterIdError, match="adapter_id重复"):
        load_adapter_plugins(
            entry_points=[
                FakeEntryPoint("demo-a", "a:Adapter", DemoAdapter),
                FakeEntryPoint("demo-b", "b:Adapter", DemoAdapter),
            ]
        )


def test_probe_failure_does_not_hide_other_plugins() -> None:
    class FailingAdapter(DemoAdapter):
        adapter_id = "hardware.sensor.failing"

        def probe(self) -> list[DeviceDescriptor]:
            raise RuntimeError("设备暂不可用")

    registry = AdapterPluginRegistry(adapters=(FailingAdapter(), DemoAdapter()))

    devices = registry.discover_devices()

    assert [item["device_id"] for item in devices] == ["demo-sensor-01"]


def test_managed_plugin_directory_discovers_installed_entry_point(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    distribution = plugin_root / "demo_hardware-0.1.0.dist-info"
    distribution.mkdir(parents=True)
    (distribution / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo-hardware\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (distribution / "entry_points.txt").write_text(
        "[robot_platform.hardware_adapters]\ndemo = managed_demo_plugin:ManagedAdapter\n",
        encoding="utf-8",
    )
    (plugin_root / "managed_demo_plugin.py").write_text(
        "from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport\n"
        "class ManagedAdapter:\n"
        "    adapter_id = 'hardware.sensor.managed'\n"
        "    def probe(self): return []\n"
        "    def validate(self, device, config): return []\n"
        "    def open(self, device): pass\n"
        "    def configure(self, config): pass\n"
        "    def start(self): pass\n"
        "    def health(self): return HealthReport(healthy=True, message='ok')\n"
        "    def stop(self): pass\n"
        "    def close(self): pass\n",
        encoding="utf-8",
    )

    registry = load_adapter_plugins(plugin_paths=[plugin_root])

    assert registry.capabilities == ("hardware.sensor.managed",)
    assert registry.failures == ()
