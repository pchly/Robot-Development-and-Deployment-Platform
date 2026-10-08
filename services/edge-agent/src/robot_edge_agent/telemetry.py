"""不依赖第三方库的边缘节点系统信息与基础遥测采集。"""

import os
import platform
import shutil
from contextlib import suppress
from pathlib import Path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip("\x00\r\n ")
    except OSError:
        return None


def parse_meminfo(content: str) -> dict[str, int]:
    """解析Linux `/proc/meminfo`，统一转换为字节。"""

    result: dict[str, int] = {}
    for line in content.splitlines():
        name, separator, raw_value = line.partition(":")
        if not separator:
            continue
        parts = raw_value.strip().split()
        if not parts or not parts[0].isdigit():
            continue
        value = int(parts[0])
        if len(parts) > 1 and parts[1].lower() == "kb":
            value *= 1024
        result[name] = value
    return result


def collect_max_temperature(thermal_root: Path = Path("/sys/class/thermal")) -> float | None:
    """读取可用热区并返回最高温度，单位为摄氏度。"""

    temperatures: list[float] = []
    for temperature_path in thermal_root.glob("thermal_zone*/temp"):
        raw_value = _read_text(temperature_path)
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        temperature_c = value / 1000 if abs(value) >= 1000 else value
        if -50 <= temperature_c <= 250:
            temperatures.append(temperature_c)
    return max(temperatures) if temperatures else None


def collect_static_metadata(
    *,
    model_path: Path = Path("/proc/device-tree/model"),
    l4t_path: Path = Path("/etc/nv_tegra_release"),
    os_release_path: Path = Path("/etc/os-release"),
) -> dict[str, object]:
    """采集注册时使用的稳定系统信息。"""

    metadata: dict[str, object] = {
        "hostname": platform.node(),
        "operating_system": platform.system(),
        "machine": platform.machine(),
        "kernel_release": platform.release(),
        "python_version": platform.python_version(),
    }
    model = _read_text(model_path)
    if model:
        metadata["jetson_model"] = model
    l4t_release = _read_text(l4t_path)
    if l4t_release:
        metadata["l4t_release"] = l4t_release.splitlines()[0]
    os_release = _read_text(os_release_path)
    if os_release:
        for line in os_release.splitlines():
            if line.startswith("PRETTY_NAME="):
                metadata["os_pretty_name"] = line.partition("=")[2].strip('"')
                break
    return metadata


def collect_system_metrics(
    *,
    meminfo_path: Path = Path("/proc/meminfo"),
    uptime_path: Path = Path("/proc/uptime"),
    data_path: Path = Path("/data"),
    thermal_root: Path = Path("/sys/class/thermal"),
) -> dict[str, object]:
    """采集心跳携带的轻量系统指标。"""

    metrics: dict[str, object] = {}
    meminfo_content = _read_text(meminfo_path)
    if meminfo_content:
        meminfo = parse_meminfo(meminfo_content)
        metrics["memory_total_bytes"] = meminfo.get("MemTotal", 0)
        metrics["memory_available_bytes"] = meminfo.get("MemAvailable", 0)

    uptime_content = _read_text(uptime_path)
    if uptime_content:
        with suppress(ValueError, IndexError):
            metrics["uptime_s"] = float(uptime_content.split()[0])

    if hasattr(os, "getloadavg"):
        metrics["load_average_1m"] = os.getloadavg()[0]

    root_usage = shutil.disk_usage(Path("/"))
    metrics["root_disk_total_bytes"] = root_usage.total
    metrics["root_disk_free_bytes"] = root_usage.free

    if data_path.exists():
        data_usage = shutil.disk_usage(data_path)
        metrics["data_disk_total_bytes"] = data_usage.total
        metrics["data_disk_free_bytes"] = data_usage.free

    max_temperature_c = collect_max_temperature(thermal_root)
    if max_temperature_c is not None:
        metrics["max_temperature_c"] = max_temperature_c
    return metrics
