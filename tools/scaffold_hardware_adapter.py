"""生成独立硬件适配包的工程骨架。"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
CLASS_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")
PORT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DATA_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
VENDOR_ID_PATTERN = re.compile(r"^[0-9A-Fa-f]{4}$")


@dataclass(frozen=True)
class Port:
    """脚手架输出端口。"""

    name: str
    data_type: str


@dataclass(frozen=True)
class ScaffoldConfig:
    """经过校验的脚手架参数。"""

    project_root: Path
    slug: str
    adapter_id: str
    class_name: str
    display_name: str
    vendor: str
    category: str
    transport: str
    vendor_ids: tuple[str, ...]
    outputs: tuple[Port, ...]

    @property
    def module_name(self) -> str:
        return f"robot_hardware_{self.slug.replace('-', '_')}"

    @property
    def target(self) -> Path:
        return self.project_root / "hardware-adapters" / self.slug


def parse_port(value: str) -> Port:
    """解析name:data_type形式的端口参数。"""

    name, separator, data_type = value.partition(":")
    if (
        not separator
        or not PORT_PATTERN.fullmatch(name)
        or not DATA_TYPE_PATTERN.fullmatch(data_type.strip())
    ):
        raise argparse.ArgumentTypeError(
            "端口必须使用name:data_type格式，例如color:image.rgb8.v1"
        )
    return Port(name=name, data_type=data_type.strip())


def validate_config(config: ScaffoldConfig) -> None:
    """在写入文件前完成路径和标识符校验。"""

    if not SLUG_PATTERN.fullmatch(config.slug):
        raise ValueError("slug必须是小写kebab-case")
    if not ID_PATTERN.fullmatch(config.adapter_id):
        raise ValueError("adapter-id格式不合法")
    if not CLASS_PATTERN.fullmatch(config.class_name):
        raise ValueError("class-name必须是PascalCase")
    if not config.adapter_id.startswith("hardware."):
        raise ValueError("adapter-id必须以hardware.开头")
    if not ID_PATTERN.fullmatch(config.category):
        raise ValueError("category格式不合法")
    for field_name, value in (
        ("name", config.display_name),
        ("vendor", config.vendor),
    ):
        if not value.strip() or any(character in value for character in ('"', "\\", "\r", "\n")):
            raise ValueError(f"{field_name}包含不支持的字符")
    if config.transport == "usb" and not config.vendor_ids:
        raise ValueError("USB适配包至少需要一个--vendor-id")
    invalid_vendor_ids = [
        vendor_id
        for vendor_id in config.vendor_ids
        if not VENDOR_ID_PATTERN.fullmatch(vendor_id)
    ]
    if invalid_vendor_ids:
        raise ValueError("vendor-id必须是4位十六进制数")
    if not config.outputs:
        raise ValueError("至少需要一个--output")
    output_names = [port.name for port in config.outputs]
    if len(set(output_names)) != len(output_names):
        raise ValueError("输出端口名称不能重复")

    root = config.project_root.resolve()
    target = config.target.resolve()
    if target.parent != (root / "hardware-adapters").resolve():
        raise ValueError("目标目录必须位于项目hardware-adapters目录下")
    if target.exists():
        raise FileExistsError(f"目标适配包已经存在：{target}")


def render_manifest(config: ScaffoldConfig) -> str:
    """生成Catalog和发布流程共同读取的Manifest。"""

    payload = {
        "schema_version": "1.0",
        "adapter_id": config.adapter_id,
        "version": "0.1.0",
        "name": config.display_name,
        "vendor": config.vendor,
        "category": config.category,
        "readiness": "definition_only",
        "discovery": {
            "transport": config.transport,
            "vendor_ids": [item.lower() for item in config.vendor_ids],
            "product_name_contains": [],
        },
        "host_requirements": {
            "operating_system": "TODO",
            "architectures": ["aarch64", "x86_64"],
            "container_runtime": "docker",
        },
        "runtime": {
            "isolation": "oci",
            "image": f"robot-platform/{config.slug}-runtime:0.1.0",
            "network": "none",
            "devices": [],
            "device_cgroup_rules": [],
        },
        "outputs": [
            {"name": port.name, "data_type": port.data_type}
            for port in config.outputs
        ],
        "profiles": [f"profiles/{config.slug}.json"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_profile(config: ScaffoldConfig) -> str:
    payload = {
        "profile_id": f"{config.slug}.default",
        "model": config.display_name,
        "discovery": {
            "transport": config.transport,
            "vendor_ids": [item.lower() for item in config.vendor_ids],
            "product_ids": [],
        },
        "validation": {
            "status": "not_started",
            "hardware_in_loop": False,
        },
        "defaults": {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_pyproject(config: ScaffoldConfig) -> str:
    return f'''[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "robot-hardware-{config.slug}"
version = "0.1.0"
description = "{config.display_name}硬件适配包"
requires-python = ">=3.10,<3.15"
dependencies = ["robot-edge-agent>=0.4.1,<1"]

[project.optional-dependencies]
dev = ["pytest>=8.3,<9", "ruff>=0.11,<1"]

[project.entry-points."robot_platform.hardware_adapters"]
{config.slug.replace('-', '_')} = "{config.module_name}.adapter:{config.class_name}Adapter"

[tool.hatch.build.targets.wheel]
packages = ["src/{config.module_name}"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", "../../services/edge-agent/src"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
'''


def render_adapter(config: ScaffoldConfig) -> str:
    return f'''"""{config.display_name}真实硬件适配器骨架。"""

from collections.abc import Mapping

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport


class {config.class_name}Adapter:
    """实现前保持definition_only，不得用于生产部署。"""

    adapter_id = "{config.adapter_id}"

    def probe(self) -> list[DeviceDescriptor]:
        """TODO：只读枚举设备，不得在probe阶段占用硬件。"""
        return []

    def validate(self, device: DeviceDescriptor, config: Mapping[str, object]) -> list[str]:
        """TODO：返回完整配置问题，不产生硬件动作。"""
        return ["真实硬件适配器尚未实现"]

    def open(self, device: DeviceDescriptor) -> None:
        raise NotImplementedError("TODO：打开指定设备")

    def configure(self, config: Mapping[str, object]) -> None:
        raise NotImplementedError("TODO：应用已经校验的配置")

    def start(self) -> None:
        raise NotImplementedError("TODO：启动数据流")

    def health(self) -> HealthReport:
        return HealthReport(healthy=False, message="真实硬件适配器尚未实现")

    def stop(self) -> None:
        """TODO：停止数据流；重复调用必须安全。"""

    def close(self) -> None:
        """TODO：释放资源；重复调用必须安全。"""
'''


def render_simulator(config: ScaffoldConfig) -> str:
    capabilities = ", ".join(repr(port.data_type) for port in config.outputs)
    return f'''"""{config.display_name}确定性模拟器。"""

from collections.abc import Mapping
from types import MappingProxyType

from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport


class {config.class_name}Simulator:
    adapter_id = "{config.adapter_id}"

    def __init__(self) -> None:
        self._opened = False
        self._configured = False
        self._running = False

    def probe(self) -> list[DeviceDescriptor]:
        return [
            DeviceDescriptor(
                device_id="{config.slug}-sim-01",
                name="{config.display_name} Simulator",
                transport="simulation",
                manufacturer="{config.vendor}",
                model="sim-v1",
                serial="SIM-0001",
                capabilities=({capabilities},),
                metadata=MappingProxyType({{"transport": "simulation"}}),
            )
        ]

    def validate(self, device: DeviceDescriptor, config: Mapping[str, object]) -> list[str]:
        if device.device_id != "{config.slug}-sim-01":
            return ["设备不属于当前适配器"]
        return []

    def open(self, device: DeviceDescriptor) -> None:
        if device.device_id != "{config.slug}-sim-01":
            raise ValueError("未知模拟设备")
        self._opened = True

    def configure(self, config: Mapping[str, object]) -> None:
        if not self._opened:
            raise RuntimeError("设备尚未打开")
        self._configured = True

    def start(self) -> None:
        if not self._configured:
            raise RuntimeError("设备尚未配置")
        self._running = True

    def health(self) -> HealthReport:
        return HealthReport(
            healthy=self._running,
            message="模拟器运行中" if self._running else "模拟器未运行",
            metrics=MappingProxyType({{"ticks": 1.0 if self._running else 0.0}}),
        )

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        self._configured = False
        self._opened = False
'''


def render_contract_test(config: ScaffoldConfig) -> str:
    return f'''"""生成包的生命周期契约测试。"""

from robot_edge_agent.adapters import AdapterController, AdapterState, HardwareAdapter

from {config.module_name}.adapter import {config.class_name}Adapter
from {config.module_name}.simulator import {config.class_name}Simulator


def test_real_adapter_implements_protocol_shape() -> None:
    assert isinstance({config.class_name}Adapter(), HardwareAdapter)


def test_simulator_completes_lifecycle() -> None:
    adapter = {config.class_name}Simulator()
    controller = AdapterController(adapter)
    device = adapter.probe()[0]

    controller.open(device)
    controller.configure({{"sample_rate": 10}})
    controller.start()

    assert controller.state is AdapterState.RUNNING
    assert controller.health().healthy is True
    controller.stop()
    controller.close()
    assert controller.state is AdapterState.CLOSED
'''


def render_readme(config: ScaffoldConfig) -> str:
    return f'''# {config.display_name}适配包

状态：`definition_only`。当前只生成工程骨架和模拟器，不能访问真实硬件或进入一键部署配方。

## 实现顺序

1. 完成`src/{config.module_name}/adapter.py`中的只读发现和生命周期；
2. 补充`profiles/{config.slug}.json`的型号、USB PID和默认参数；
3. 为异常、重复关闭和热插拔补充测试；
4. 构建隔离Runtime并固定镜像版本与摘要；
5. 完成硬件在环和稳定性验收；
6. 最后将Manifest的`readiness`改为`validated`并创建工作流Runtime配方。

## 本地契约测试

从本目录使用Edge Agent开发环境运行：

```powershell
..\\..\\services\\edge-agent\\.venv\\Scripts\\python -m pytest -p no:cacheprovider
```
'''


def scaffold(config: ScaffoldConfig) -> Path:
    """以不存在的目标目录为边界创建完整骨架。"""

    validate_config(config)
    package_dir = config.target
    module_dir = package_dir / "src" / config.module_name
    files = {
        package_dir / "manifest.json": render_manifest(config),
        package_dir / "profiles" / f"{config.slug}.json": render_profile(config),
        package_dir / "pyproject.toml": render_pyproject(config),
        package_dir / "README.md": render_readme(config),
        module_dir / "__init__.py": (
            f'"""{config.display_name}硬件适配包。"""\n\n'
            f"from {config.module_name}.adapter import {config.class_name}Adapter\n\n"
            f'__all__ = ["{config.class_name}Adapter"]\n'
        ),
        module_dir / "adapter.py": render_adapter(config),
        module_dir / "simulator.py": render_simulator(config),
        package_dir / "tests" / "test_adapter_contract.py": render_contract_test(config),
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return package_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--slug", required=True)
    parser.add_argument("--adapter-id", required=True)
    parser.add_argument("--class-name", required=True)
    parser.add_argument("--name", dest="display_name", required=True)
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--transport", choices=("usb", "can", "serial", "ethernet"), required=True)
    parser.add_argument("--vendor-id", action="append", default=[])
    parser.add_argument("--output", action="append", type=parse_port, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = ScaffoldConfig(
        project_root=args.project_root,
        slug=args.slug,
        adapter_id=args.adapter_id,
        class_name=args.class_name,
        display_name=args.display_name,
        vendor=args.vendor,
        category=args.category,
        transport=args.transport,
        vendor_ids=tuple(args.vendor_id),
        outputs=tuple(args.output),
    )
    try:
        target = scaffold(config)
    except (ValueError, FileExistsError) as error:
        raise SystemExit(f"错误：{error}") from error
    print(f"硬件适配包骨架已生成：{target}")
    print("当前状态：definition_only；完成实机验证前请勿改为validated。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
