"""硬件适配包脚手架测试。"""

import json
from argparse import ArgumentTypeError
from pathlib import Path

import pytest
from robot_platform_api.catalog import load_catalog

from tools.scaffold_hardware_adapter import (
    Port,
    ScaffoldConfig,
    parse_port,
    scaffold,
)


def sample_config(project_root: Path) -> ScaffoldConfig:
    return ScaffoldConfig(
        project_root=project_root,
        slug="demo-lidar",
        adapter_id="hardware.lidar.demo",
        class_name="DemoLidar",
        display_name="Demo LiDAR",
        vendor="Demo Robotics",
        category="lidar.3d",
        transport="usb",
        vendor_ids=("1234",),
        outputs=(Port(name="points", data_type="pointcloud.xyz.v1"),),
    )


def test_scaffold_generates_independent_definition_only_package(tmp_path: Path) -> None:
    target = scaffold(sample_config(tmp_path))

    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["adapter_id"] == "hardware.lidar.demo"
    assert manifest["readiness"] == "definition_only"
    assert manifest["outputs"] == [
        {"name": "points", "data_type": "pointcloud.xyz.v1"}
    ]
    assert (target / "src" / "robot_hardware_demo_lidar" / "adapter.py").is_file()
    assert (target / "src" / "robot_hardware_demo_lidar" / "simulator.py").is_file()
    assert (target / "tests" / "test_adapter_contract.py").is_file()

    snapshot = load_catalog(tmp_path)
    generated = next(
        item for item in snapshot.hardware if item.hardware_id == "hardware.lidar.demo"
    )
    assert generated.readiness == "definition_only"
    assert generated.outputs[0].data_type == "pointcloud.xyz.v1"

    for source in (target / "src").rglob("*.py"):
        compile(source.read_text(encoding="utf-8"), str(source), "exec")


def test_scaffold_never_overwrites_existing_package(tmp_path: Path) -> None:
    config = sample_config(tmp_path)
    scaffold(config)

    with pytest.raises(FileExistsError, match="已经存在"):
        scaffold(config)


def test_usb_package_requires_valid_vendor_id(tmp_path: Path) -> None:
    config = sample_config(tmp_path)
    invalid = ScaffoldConfig(
        **{
            **config.__dict__,
            "vendor_ids": ("xyz",),
        }
    )

    with pytest.raises(ValueError, match="4位十六进制"):
        scaffold(invalid)


@pytest.mark.parametrize(
    "value",
    ["missing-separator", "Color:image.rgb8.v1", 'color:image.rgb8.v1"'],
)
def test_output_port_rejects_unsafe_or_invalid_values(value: str) -> None:
    with pytest.raises(ArgumentTypeError, match="name:data_type"):
        parse_port(value)
