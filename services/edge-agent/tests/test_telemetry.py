"""系统信息与遥测采集测试。"""

from pathlib import Path

from robot_edge_agent.telemetry import (
    collect_max_temperature,
    collect_static_metadata,
    collect_system_metrics,
    parse_meminfo,
)


def test_parse_meminfo_converts_kilobytes_to_bytes() -> None:
    result = parse_meminfo("MemTotal: 1024 kB\nMemAvailable: 256 kB\nInvalid\n")

    assert result == {
        "MemTotal": 1024 * 1024,
        "MemAvailable": 256 * 1024,
    }


def test_collect_max_temperature_ignores_invalid_values(tmp_path: Path) -> None:
    first_zone = tmp_path / "thermal_zone0"
    second_zone = tmp_path / "thermal_zone1"
    invalid_zone = tmp_path / "thermal_zone2"
    first_zone.mkdir()
    second_zone.mkdir()
    invalid_zone.mkdir()
    (first_zone / "temp").write_text("47500", encoding="utf-8")
    (second_zone / "temp").write_text("51", encoding="utf-8")
    (invalid_zone / "temp").write_text("invalid", encoding="utf-8")

    assert collect_max_temperature(tmp_path) == 51.0


def test_collect_static_metadata_reads_jetson_files(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    l4t_path = tmp_path / "nv_tegra_release"
    os_release_path = tmp_path / "os-release"
    model_path.write_text("NVIDIA Jetson AGX Orin\x00", encoding="utf-8")
    l4t_path.write_text("# R36, REVISION: 5.2\nsecond line", encoding="utf-8")
    os_release_path.write_text('PRETTY_NAME="Ubuntu 22.04.5 LTS"\n', encoding="utf-8")

    metadata = collect_static_metadata(
        model_path=model_path,
        l4t_path=l4t_path,
        os_release_path=os_release_path,
    )

    assert metadata["jetson_model"] == "NVIDIA Jetson AGX Orin"
    assert metadata["l4t_release"] == "# R36, REVISION: 5.2"
    assert metadata["os_pretty_name"] == "Ubuntu 22.04.5 LTS"


def test_collect_system_metrics_reads_linux_files(tmp_path: Path) -> None:
    meminfo_path = tmp_path / "meminfo"
    uptime_path = tmp_path / "uptime"
    data_path = tmp_path / "data"
    thermal_root = tmp_path / "thermal"
    data_path.mkdir()
    thermal_root.mkdir()
    meminfo_path.write_text("MemTotal: 2048 kB\nMemAvailable: 1024 kB\n", encoding="utf-8")
    uptime_path.write_text("42.5 12.0", encoding="utf-8")

    metrics = collect_system_metrics(
        meminfo_path=meminfo_path,
        uptime_path=uptime_path,
        data_path=data_path,
        thermal_root=thermal_root,
    )

    assert metrics["memory_total_bytes"] == 2048 * 1024
    assert metrics["memory_available_bytes"] == 1024 * 1024
    assert metrics["uptime_s"] == 42.5
    assert metrics["root_disk_total_bytes"] > 0
    assert metrics["data_disk_free_bytes"] > 0
