"""Edge插件的摘要校验、版本化安装、原子激活与回滚。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Callable
from importlib.metadata import distributions
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from robot_edge_agent.calibration import CalibrationProfileInstaller
from robot_edge_agent.container_installation import CommandRunner, ContainerImageInstaller
from robot_edge_agent.file_installation import (
    ManagedFileInstaller,
    ManagedFileRollbackError,
)

ENTRY_POINT_GROUP = "robot_platform.hardware_adapters"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_FILES = 10_000


class ArtifactClient(Protocol):
    def list_artifact_installations(self, agent_id: str) -> list[dict[str, Any]]: ...

    def report_artifact_installation_status(
        self,
        operation_id: str,
        status: str,
        *,
        message: str = "",
    ) -> dict[str, Any]: ...

    def download_artifact(self, download_path: str, *, max_bytes: int) -> bytes: ...

    def download_artifact_to_file(
        self,
        download_path: str,
        destination: Path,
        *,
        max_bytes: int,
        expected_digest: str,
    ) -> int: ...


class PluginInstallationError(RuntimeError):
    """安装在激活前失败。"""


class PluginRollbackError(PluginInstallationError):
    """激活后发生故障，旧指针已恢复。"""


def _safe_name(value: str, field: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise PluginInstallationError(f"{field}格式不合法")
    return value


def _expected_download_path(artifact: dict[str, Any]) -> str:
    """Return the only Platform API path allowed by the resolved artifact plan."""

    artifact_id = _safe_name(str(artifact.get("artifact_id", "")), "artifact_id")
    variant_id = artifact.get("selected_variant_id")
    if variant_id is None:
        return f"/api/v1/artifacts/{artifact_id}/content"
    safe_variant_id = _safe_name(str(variant_id), "selected_variant_id")
    return f"/api/v1/artifacts/{artifact_id}/content?variant_id={safe_variant_id}"


def active_plugin_paths(plugin_root: Path) -> list[Path]:
    """读取原子指针；存在受管版本时忽略旧的平铺插件目录。"""

    active_root = plugin_root / "active"
    release_root = (plugin_root / "releases").resolve()
    paths: list[Path] = []
    if active_root.is_dir():
        for pointer in sorted(active_root.glob("*.json")):
            try:
                payload = json.loads(pointer.read_text(encoding="utf-8"))
                relative = payload["release_path"]
                if not isinstance(relative, str):
                    continue
                candidate = (plugin_root / relative).resolve()
                candidate.relative_to(release_root)
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                continue
            if candidate.is_dir():
                paths.append(candidate)
    return paths or ([plugin_root] if plugin_root.is_dir() else [])


class PluginInstaller:
    """只安装Platform API计划中摘要固定的Python wheel。"""

    def __init__(
        self,
        plugin_root: Path,
        *,
        activation_validator: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._root = plugin_root.resolve()
        self._activation_validator = activation_validator or self._validate_activation

    @staticmethod
    def _validate_activation(plugin_root: Path, release: Path) -> None:
        if release.resolve() not in [path.resolve() for path in active_plugin_paths(plugin_root)]:
            raise PluginInstallationError("原子激活指针校验失败")

    def _validate_plan(self, plan: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        operation_id = _safe_name(str(plan.get("operation_id", "")), "operation_id")
        artifact = plan.get("artifact")
        if not isinstance(artifact, dict) or artifact.get("kind") != "edge_plugin":
            raise PluginInstallationError("安装计划不是Edge插件")
        _safe_name(str(artifact.get("artifact_id", "")), "artifact_id")
        version = _safe_name(str(artifact.get("version", "")), "version")
        distribution = artifact.get("distribution")
        if not isinstance(distribution, dict):
            raise PluginInstallationError("安装计划缺少可信分发信息")
        expected_path = _expected_download_path(artifact)
        if (
            distribution.get("transport") != "platform_api"
            or distribution.get("download_path") != expected_path
            or distribution.get("digest") != artifact.get("digest")
        ):
            raise PluginInstallationError("分发信息与制品契约不一致")
        size = distribution.get("size_bytes")
        if not isinstance(size, int) or size <= 0 or size > _MAX_ARCHIVE_BYTES:
            raise PluginInstallationError("制品大小不合法")
        return operation_id, artifact, distribution | {"version": version}

    @staticmethod
    def _extract_wheel(content: bytes, destination: Path) -> None:
        try:
            archive = zipfile.ZipFile(BytesIO(content))
        except zipfile.BadZipFile as error:
            raise PluginInstallationError("下载内容不是合法wheel") from error
        with archive:
            members = archive.infolist()
            if len(members) > _MAX_ARCHIVE_FILES:
                raise PluginInstallationError("wheel文件数量超过限制")
            if sum(item.file_size for item in members) > _MAX_ARCHIVE_BYTES:
                raise PluginInstallationError("wheel解压后大小超过限制")
            for item in members:
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PluginInstallationError("wheel不允许包含符号链接")
                target = (destination / item.filename).resolve()
                try:
                    target.relative_to(destination.resolve())
                except ValueError as error:
                    raise PluginInstallationError("wheel包含越界路径") from error
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

    @staticmethod
    def _validate_distribution(path: Path, locator: str, version: str) -> None:
        installed = list(distributions(path=[str(path)]))
        normalized = locator.casefold().replace("_", "-")
        matches = [
            item
            for item in installed
            if str(item.metadata.get("Name", "")).casefold().replace("_", "-") == normalized
            and item.version == version
        ]
        if len(matches) != 1:
            raise PluginInstallationError("wheel包名或版本与Catalog不一致")
        entries = [entry for entry in matches[0].entry_points if entry.group == ENTRY_POINT_GROUP]
        if not entries:
            raise PluginInstallationError("wheel没有声明硬件适配器入口")

    def install(
        self,
        plan: dict[str, Any],
        content: bytes,
        *,
        progress: Callable[[str, str], None],
    ) -> None:
        operation_id, artifact, distribution = self._validate_plan(plan)
        artifact_id = str(artifact["artifact_id"])
        expected_digest = str(distribution["digest"])
        progress("verifying", "正在复核下载大小、SHA-256与wheel结构")
        if len(content) != distribution["size_bytes"]:
            raise PluginInstallationError("下载大小与Catalog不一致")
        actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if actual_digest != expected_digest:
            raise PluginInstallationError("下载内容SHA-256与Catalog不一致")

        staging_root = self._root / ".staging"
        stage = staging_root / operation_id
        releases_root = self._root / "releases"
        active_root = self._root / "active"
        rollback_root = self._root / ".rollback"
        release_name = f"{distribution['version']}-{actual_digest[7:19]}"
        release = releases_root / artifact_id / release_name
        pointer = active_root / f"{artifact_id}.json"
        pointer_tmp = active_root / f".{artifact_id}.{operation_id}.tmp"
        rollback = rollback_root / f"{operation_id}.pointer"
        for candidate in (stage, release, pointer, pointer_tmp, rollback):
            candidate.resolve().relative_to(self._root)

        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True, mode=0o750)
        switched = False
        previous_pointer = pointer.read_bytes() if pointer.is_file() else None
        try:
            self._extract_wheel(content, stage)
            self._validate_distribution(
                stage,
                str(artifact["locator"]),
                str(distribution["version"]),
            )
            marker = {
                "artifact_id": artifact_id,
                "version": distribution["version"],
                "digest": actual_digest,
            }
            (stage / ".artifact.json").write_text(
                json.dumps(marker, sort_keys=True), encoding="utf-8"
            )
            progress("installing", "正在写入版本目录并原子切换激活指针")
            release.parent.mkdir(parents=True, exist_ok=True)
            if release.exists():
                existing_marker = release / ".artifact.json"
                if (
                    not existing_marker.is_file()
                    or json.loads(existing_marker.read_text(encoding="utf-8")) != marker
                ):
                    raise PluginInstallationError("目标版本目录存在但内容契约不一致")
                shutil.rmtree(stage)
            else:
                os.replace(stage, release)
            active_root.mkdir(parents=True, exist_ok=True)
            rollback_root.mkdir(parents=True, exist_ok=True)
            rollback.write_bytes(previous_pointer or b"")
            pointer_payload = {
                **marker,
                "operation_id": operation_id,
                "release_path": release.relative_to(self._root).as_posix(),
            }
            pointer_tmp.write_text(json.dumps(pointer_payload, sort_keys=True), encoding="utf-8")
            os.replace(pointer_tmp, pointer)
            switched = True
            self._activation_validator(self._root, release)
        except Exception as error:
            if pointer_tmp.exists():
                pointer_tmp.unlink()
            if stage.exists():
                shutil.rmtree(stage)
            if switched:
                if previous_pointer is None:
                    pointer.unlink(missing_ok=True)
                else:
                    restore = active_root / f".{artifact_id}.rollback.tmp"
                    restore.write_bytes(previous_pointer)
                    os.replace(restore, pointer)
                rollback.unlink(missing_ok=True)
                raise PluginRollbackError(str(error)) from error
            rollback.unlink(missing_ok=True)
            if isinstance(error, PluginInstallationError):
                raise
            raise PluginInstallationError(str(error)) from error

    def confirm_activation(
        self,
        plan: dict[str, Any],
        *,
        health_check: Callable[[dict[str, Any]], tuple[bool, str]],
    ) -> str:
        """重启后确认指针、发布标记与适配器探测结果，失败则恢复旧指针。"""

        operation_id, artifact, distribution = self._validate_plan(plan)
        artifact_id = str(artifact["artifact_id"])
        pointer = self._root / "active" / f"{artifact_id}.json"
        rollback = self._root / ".rollback" / f"{operation_id}.pointer"
        release_root = (self._root / "releases").resolve()
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            release = (self._root / str(payload["release_path"])).resolve()
            release.relative_to(release_root)
            marker = json.loads((release / ".artifact.json").read_text(encoding="utf-8"))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PluginInstallationError("重启后无法验证当前激活指针") from error
        expected_marker = {
            "artifact_id": artifact_id,
            "version": distribution["version"],
            "digest": distribution["digest"],
        }
        if (
            payload.get("operation_id") != operation_id
            or marker != expected_marker
            or not release.is_dir()
        ):
            raise PluginInstallationError("重启后激活指针与安装事务不一致")
        try:
            healthy, message = health_check(plan)
        except Exception as error:
            healthy, message = False, f"插件健康检查异常：{error}"
        if healthy:
            rollback.unlink(missing_ok=True)
            return message
        if not rollback.is_file():
            raise PluginInstallationError(f"{message}；缺少可用回滚记录")
        previous_pointer = rollback.read_bytes()
        if previous_pointer:
            restore = pointer.parent / f".{artifact_id}.{operation_id}.health-rollback.tmp"
            restore.write_bytes(previous_pointer)
            os.replace(restore, pointer)
        else:
            pointer.unlink(missing_ok=True)
        rollback.unlink(missing_ok=True)
        raise PluginRollbackError(message)


class RuntimePackageInstaller(PluginInstaller):
    """Install a digest-pinned Python runtime package without loading it in Agent."""

    def _validate_plan(
        self,
        plan: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        operation_id = _safe_name(str(plan.get("operation_id", "")), "operation_id")
        artifact = plan.get("artifact")
        if not isinstance(artifact, dict) or artifact.get("kind") != "runtime_package":
            raise PluginInstallationError("安装计划不是Runtime Package")
        _safe_name(str(artifact.get("artifact_id", "")), "artifact_id")
        version = _safe_name(str(artifact.get("version", "")), "version")
        distribution = artifact.get("distribution")
        if not isinstance(distribution, dict):
            raise PluginInstallationError("安装计划缺少可信分发信息")
        if (
            distribution.get("transport") != "platform_api"
            or distribution.get("download_path") != _expected_download_path(artifact)
            or distribution.get("digest") != artifact.get("digest")
        ):
            raise PluginInstallationError("分发信息与Runtime Package契约不一致")
        size = distribution.get("size_bytes")
        if not isinstance(size, int) or size <= 0 or size > _MAX_ARCHIVE_BYTES:
            raise PluginInstallationError("Runtime Package大小不合法")
        return operation_id, artifact, distribution | {"version": version}

    @staticmethod
    def _validate_distribution(path: Path, locator: str, version: str) -> None:
        installed = list(distributions(path=[str(path)]))
        package_name = locator.split("==", maxsplit=1)[0]
        normalized = package_name.casefold().replace("_", "-")
        matches = [
            item
            for item in installed
            if str(item.metadata.get("Name", "")).casefold().replace("_", "-") == normalized
            and item.version == version
        ]
        if len(matches) != 1:
            raise PluginInstallationError("Runtime wheel包名或版本与Catalog不一致")


class ArtifactInstallationManager:
    """拉取平台计划并串行执行；一次激活后请求Agent重启。"""

    def __init__(
        self,
        plugin_root: Path,
        *,
        runtime_package_root: Path | None = None,
        calibration_root: Path | None = None,
        health_check: Callable[[dict[str, Any]], tuple[bool, str]] | None = None,
        container_run: CommandRunner | None = None,
        managed_file_roots: tuple[Path, ...] = (Path("/data/robot-platform/cache/tensorrt"),),
    ) -> None:
        self._installer = PluginInstaller(plugin_root)
        self._runtime_package_installer = RuntimePackageInstaller(
            runtime_package_root or plugin_root.resolve().parent / "runtime-packages"
        )
        self._calibration_installer = CalibrationProfileInstaller(
            calibration_root or plugin_root.resolve().parent / "calibrations"
        )
        self._staging_root = plugin_root.resolve().parent / "staging"
        self._container_installer = ContainerImageInstaller(
            **({"run": container_run} if container_run is not None else {})
        )
        self._file_installer = ManagedFileInstaller(managed_file_roots)
        self._health_check = health_check or (lambda _plan: (True, "插件重启后健康确认通过"))

    def reconcile(self, client: ArtifactClient, agent_id: str) -> bool:
        for record in client.list_artifact_installations(agent_id):
            plan = record.get("plan", record)
            operation_id = str(plan.get("operation_id", ""))
            artifact = plan.get("artifact", {})
            artifact_kind = artifact.get("kind")
            if record.get("status") == "activating":
                if artifact_kind != "edge_plugin":
                    client.report_artifact_installation_status(
                        operation_id,
                        "failed",
                        message="只有Edge插件允许进入重启激活确认阶段",
                    )
                    continue
                try:
                    message = self._installer.confirm_activation(
                        plan,
                        health_check=self._health_check,
                    )
                    client.report_artifact_installation_status(
                        operation_id, "succeeded", message=message
                    )
                except PluginRollbackError as error:
                    client.report_artifact_installation_status(
                        operation_id,
                        "rolled_back",
                        message=f"重启后健康检查失败并已回滚：{error}",
                    )
                    return True
                except Exception as error:
                    client.report_artifact_installation_status(
                        operation_id,
                        "failed",
                        message=f"重启后健康确认失败：{error}",
                    )
                continue
            try:

                def progress(
                    status: str,
                    message: str,
                    current_operation_id: str = operation_id,
                ) -> None:
                    client.report_artifact_installation_status(
                        current_operation_id, status, message=message
                    )

                if artifact_kind == "container_image":
                    distribution = artifact.get("distribution")
                    if isinstance(distribution, dict):
                        expected_path = _expected_download_path(artifact)
                        if (
                            distribution.get("transport") != "platform_api"
                            or distribution.get("download_path") != expected_path
                        ):
                            raise PluginInstallationError("容器镜像归档分发信息与制品契约不一致")
                        max_bytes = int(distribution.get("size_bytes", 0))
                        expected_digest = str(distribution.get("digest", ""))
                        client.report_artifact_installation_status(
                            operation_id,
                            "downloading",
                            message="正在流式下载固定摘要的Docker镜像归档",
                        )
                        self._staging_root.mkdir(parents=True, exist_ok=True)
                        with tempfile.TemporaryDirectory(
                            prefix="container-image-",
                            dir=self._staging_root,
                        ) as temporary_directory:
                            archive_path = Path(temporary_directory) / "image.tar"
                            client.download_artifact_to_file(
                                expected_path,
                                archive_path,
                                max_bytes=max_bytes,
                                expected_digest=expected_digest,
                            )
                            message = self._container_installer.install(
                                plan,
                                progress=progress,
                                archive_path=archive_path,
                            )
                    else:
                        message = self._container_installer.install(
                            plan,
                            progress=progress,
                        )
                    client.report_artifact_installation_status(
                        operation_id, "succeeded", message=message
                    )
                    # 重新注册后，控制面才能立即看到最新镜像盘点。
                    return True
                if artifact_kind == "tensorrt_engine":
                    distribution = artifact.get("distribution")
                    expected_path = _expected_download_path(artifact)
                    if (
                        not isinstance(distribution, dict)
                        or distribution.get("transport") != "platform_api"
                        or distribution.get("download_path") != expected_path
                    ):
                        raise PluginInstallationError("TensorRT Engine分发信息与制品契约不一致")
                    expected_digest = str(distribution.get("digest", ""))
                    if expected_digest.casefold() != str(artifact.get("digest", "")).casefold():
                        raise PluginInstallationError("TensorRT Engine内容摘要与分发摘要不一致")
                    client.report_artifact_installation_status(
                        operation_id,
                        "downloading",
                        message="正在流式下载固定摘要的TensorRT Engine",
                    )
                    self._staging_root.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(
                        prefix="managed-file-",
                        dir=self._staging_root,
                    ) as temporary_directory:
                        staged_file = Path(temporary_directory) / "artifact.engine"
                        client.download_artifact_to_file(
                            expected_path,
                            staged_file,
                            max_bytes=int(distribution.get("size_bytes", 0)),
                            expected_digest=expected_digest,
                        )
                        message = self._file_installer.install(
                            plan,
                            staged_file,
                            progress=progress,
                        )
                    client.report_artifact_installation_status(
                        operation_id, "succeeded", message=message
                    )
                    # 重新注册后，控制面才能立即看到最新文件盘点。
                    return True
                if artifact_kind == "runtime_package":
                    distribution = artifact.get("distribution", {})
                    client.report_artifact_installation_status(
                        operation_id,
                        "downloading",
                        message="正在下载固定摘要的Runtime Package",
                    )
                    content = client.download_artifact(
                        str(distribution.get("download_path", "")),
                        max_bytes=int(distribution.get("size_bytes", 0)),
                    )
                    self._runtime_package_installer.install(
                        plan,
                        content,
                        progress=progress,
                    )
                    client.report_artifact_installation_status(
                        operation_id,
                        "succeeded",
                        message="Runtime Package已安装并原子激活",
                    )
                    # 重新注册后平台才能立即看到新的版本化Runtime库存。
                    return True
                if artifact_kind == "calibration_profile":
                    distribution = artifact.get("distribution")
                    expected_path = _expected_download_path(artifact)
                    if (
                        not isinstance(distribution, dict)
                        or distribution.get("transport") != "platform_api"
                        or distribution.get("download_path") != expected_path
                    ):
                        raise PluginInstallationError("标定配置分发信息与制品契约不一致")
                    expected_digest = str(distribution.get("digest", ""))
                    if expected_digest.casefold() != str(artifact.get("digest", "")).casefold():
                        raise PluginInstallationError("标定配置内容摘要与分发摘要不一致")
                    client.report_artifact_installation_status(
                        operation_id,
                        "downloading",
                        message="正在下载固定摘要的标定配置",
                    )
                    self._staging_root.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(
                        prefix="calibration-profile-",
                        dir=self._staging_root,
                    ) as temporary_directory:
                        staged_file = Path(temporary_directory) / "profile.json"
                        client.download_artifact_to_file(
                            expected_path,
                            staged_file,
                            max_bytes=int(distribution.get("size_bytes", 0)),
                            expected_digest=expected_digest,
                        )
                        message = self._calibration_installer.install(
                            plan,
                            staged_file,
                            progress=progress,
                        )
                    client.report_artifact_installation_status(
                        operation_id, "succeeded", message=message
                    )
                    return True
                if artifact_kind != "edge_plugin":
                    raise PluginInstallationError(
                        f"Agent不支持安装此类制品：{artifact_kind or '未知'}"
                    )
                distribution = artifact.get("distribution", {})
                client.report_artifact_installation_status(
                    operation_id, "downloading", message="正在下载可信制品"
                )
                content = client.download_artifact(
                    str(distribution.get("download_path", "")),
                    max_bytes=int(distribution.get("size_bytes", 0)),
                )

                self._installer.install(plan, content, progress=progress)
                client.report_artifact_installation_status(
                    operation_id,
                    "activating",
                    message="插件已原子激活，等待Agent重启后加载与设备发现确认",
                )
                return True
            except PluginRollbackError as error:
                client.report_artifact_installation_status(
                    operation_id, "rolled_back", message=f"安装失败并已回滚：{error}"
                )
            except ManagedFileRollbackError as error:
                client.report_artifact_installation_status(
                    operation_id, "rolled_back", message=f"文件安装失败并已回滚：{error}"
                )
                return True
            except Exception as error:
                client.report_artifact_installation_status(
                    operation_id, "failed", message=f"安装失败：{error}"
                )
        return False
