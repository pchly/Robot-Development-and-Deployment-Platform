"""Edge节点本机制品的只读盘点。"""

import hashlib
import json
import subprocess
from collections.abc import Callable, Iterable
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

from robot_edge_agent.calibration import load_calibration_profile

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
DEFAULT_PLUGIN_ROOTS = (Path("/data/robot-platform/edge-agent/plugins"),)
DEFAULT_RUNTIME_PACKAGE_ROOTS = (Path("/data/robot-platform/edge-agent/runtime-packages"),)
DEFAULT_CALIBRATION_ROOTS = (Path("/data/robot-platform/edge-agent/calibrations"),)


def collect_python_artifacts() -> list[dict[str, object]]:
    """只上报声明硬件适配器入口的Python发行包。"""

    found: list[dict[str, object]] = []
    for distribution in distributions():
        name = distribution.metadata.get("Name", "")
        if not name.lower().replace("_", "-").startswith("robot-"):
            continue
        if not any(
            entry.group == "robot_platform.hardware_adapters" for entry in distribution.entry_points
        ):
            continue
        found.append(
            {
                "kind": "edge_plugin",
                "locator": name,
                "version": distribution.version,
            }
        )
    return found


def collect_managed_plugin_artifacts(
    roots: Iterable[Path] = DEFAULT_PLUGIN_ROOTS,
) -> list[dict[str, object]]:
    """从受管激活指针盘点插件，并交叉校验发布标记与包元数据。"""

    found: list[dict[str, object]] = []
    for root in roots:
        active_root = root / "active"
        release_root = (root / "releases").resolve()
        if not active_root.is_dir():
            continue
        for pointer in sorted(active_root.glob("*.json")):
            try:
                payload = json.loads(pointer.read_text(encoding="utf-8"))
                release_path = payload["release_path"]
                if not isinstance(release_path, str):
                    continue
                release = (root / release_path).resolve()
                release.relative_to(release_root)
                marker = json.loads((release / ".artifact.json").read_text(encoding="utf-8"))
                contract = {
                    "artifact_id": payload["artifact_id"],
                    "version": payload["version"],
                    "digest": payload["digest"],
                }
                if marker != contract or not release.is_dir():
                    continue
                installed = list(distributions(path=[str(release)]))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

            for distribution in installed:
                name = str(distribution.metadata.get("Name", ""))
                if (
                    not name.lower().replace("_", "-").startswith("robot-")
                    or distribution.version != contract["version"]
                ):
                    continue
                found.append(
                    {
                        "kind": "edge_plugin",
                        "locator": name,
                        "version": distribution.version,
                        "digest": contract["digest"],
                        "artifact_id": contract["artifact_id"],
                        "install_path": str(release),
                        "active": True,
                    }
                )
    return found


def collect_managed_runtime_packages(
    roots: Iterable[Path] = DEFAULT_RUNTIME_PACKAGE_ROOTS,
) -> list[dict[str, object]]:
    """盘点由Agent以版本目录和激活指针管理的Python Runtime Package。"""

    found: list[dict[str, object]] = []
    for root in roots:
        active_root = root / "active"
        release_root = (root / "releases").resolve()
        if not active_root.is_dir():
            continue
        for pointer in sorted(active_root.glob("*.json")):
            try:
                payload = json.loads(pointer.read_text(encoding="utf-8"))
                release_path = payload["release_path"]
                if not isinstance(release_path, str):
                    continue
                release = (root / release_path).resolve()
                release.relative_to(release_root)
                marker = json.loads((release / ".artifact.json").read_text(encoding="utf-8"))
                contract = {
                    "artifact_id": payload["artifact_id"],
                    "version": payload["version"],
                    "digest": payload["digest"],
                }
                if marker != contract or not release.is_dir():
                    continue
                installed = list(distributions(path=[str(release)]))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

            for distribution in installed:
                name = str(distribution.metadata.get("Name", ""))
                if not name or distribution.version != contract["version"]:
                    continue
                found.append(
                    {
                        "kind": "runtime_package",
                        "locator": name,
                        "version": distribution.version,
                        "digest": contract["digest"],
                        "artifact_id": contract["artifact_id"],
                        "install_path": str(release),
                        "active": True,
                    }
                )
    return found


def collect_managed_calibration_profiles(
    roots: Iterable[Path] = DEFAULT_CALIBRATION_ROOTS,
) -> list[dict[str, object]]:
    """盘点由Agent按版本安装并通过激活指针选择的标定配置。"""

    found: list[dict[str, object]] = []
    for root in roots:
        active_root = root / "active"
        release_root = (root / "releases").resolve()
        if not active_root.is_dir():
            continue
        for pointer in sorted(active_root.glob("*.json")):
            try:
                payload = json.loads(pointer.read_text(encoding="utf-8"))
                release_path = payload["release_path"]
                if not isinstance(release_path, str):
                    continue
                release = (root / release_path).resolve()
                release.relative_to(release_root)
                marker = json.loads((release / ".artifact.json").read_text(encoding="utf-8"))
                profile_path = release / "profile.json"
                digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
                contract = {
                    "artifact_id": payload["artifact_id"],
                    "version": payload["version"],
                    "digest": payload["digest"],
                    "locator": payload["locator"],
                }
                if (
                    marker != contract
                    or not release.is_dir()
                    or contract["digest"] != f"sha256:{digest}"
                ):
                    continue
                profile = load_calibration_profile(profile_path)
                if (
                    profile.calibration_id != contract["locator"]
                    or profile.version != contract["version"]
                ):
                    continue
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            found.append(
                {
                    "kind": "calibration_profile",
                    "locator": contract["locator"],
                    "version": contract["version"],
                    "digest": contract["digest"],
                    "artifact_id": contract["artifact_id"],
                    "install_path": str(release),
                    "active": True,
                }
            )
    return found


def collect_container_images(
    *,
    run: CommandRunner = subprocess.run,
) -> list[dict[str, object]]:
    """通过Docker只读命令获取本地镜像标签和摘要。"""

    try:
        result = run(
            [
                "docker",
                "image",
                "ls",
                "--digests",
                "--no-trunc",
                "--format",
                "{{json .}}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    images: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        repository = str(item.get("Repository", ""))
        tag = str(item.get("Tag", ""))
        if not repository or repository == "<none>" or not tag or tag == "<none>":
            continue
        digest = str(item.get("Digest", ""))
        image_id = str(item.get("ID", ""))
        images.append(
            {
                "kind": "container_image",
                "locator": f"{repository}:{tag}",
                "version": tag,
                "digest": digest if digest and digest != "<none>" else image_id or None,
            }
        )
    return images


def collect_file_artifacts(
    roots: Iterable[Path] = (Path("/data/robot-platform/cache"),),
) -> list[dict[str, object]]:
    """盘点缓存目录中的模型和TensorRT Engine，并固定内容摘要。"""

    found: list[dict[str, object]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for pattern, kind in (("*.engine", "tensorrt_engine"), ("*.onnx", "model")):
            for path in sorted(root.rglob(pattern)):
                try:
                    size = path.stat().st_size
                    digest = hashlib.sha256()
                    with path.open("rb") as artifact_file:
                        for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                            digest.update(chunk)
                except OSError:
                    continue
                found.append(
                    {
                        "kind": kind,
                        "locator": str(path),
                        "size_bytes": size,
                        "digest": f"sha256:{digest.hexdigest()}",
                    }
                )
    return found


def collect_installed_artifacts(
    *,
    run: CommandRunner = subprocess.run,
    cache_roots: Iterable[Path] = (Path("/data/robot-platform/cache"),),
    plugin_roots: Iterable[Path] = DEFAULT_PLUGIN_ROOTS,
    runtime_package_roots: Iterable[Path] = DEFAULT_RUNTIME_PACKAGE_ROOTS,
    calibration_roots: Iterable[Path] = DEFAULT_CALIBRATION_ROOTS,
) -> list[dict[str, Any]]:
    """汇总本机插件、容器镜像和模型文件，限制注册载荷大小。"""

    artifacts = [
        *collect_python_artifacts(),
        *collect_managed_plugin_artifacts(plugin_roots),
        *collect_managed_runtime_packages(runtime_package_roots),
        *collect_managed_calibration_profiles(calibration_roots),
        *collect_container_images(run=run),
        *collect_file_artifacts(cache_roots),
    ]
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for artifact in artifacts:
        key = (str(artifact["kind"]), str(artifact["locator"]).casefold())
        unique[key] = artifact
    return list(unique.values())[:1024]
