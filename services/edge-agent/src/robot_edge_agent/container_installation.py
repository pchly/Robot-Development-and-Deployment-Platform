"""受控容器镜像拉取、摘要校验与标签切换。"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_ARCHITECTURE_ALIASES = {
    "aarch64": "arm64",
    "arm64": "arm64",
    "amd64": "amd64",
    "x86_64": "amd64",
}


class ContainerInstallationError(RuntimeError):
    """容器镜像无法安全拉取或校验。"""


def _normalize_architecture(value: object) -> str:
    """把Linux machine和OCI镜像使用的常见架构别名归一化。"""

    normalized = str(value or "").strip().casefold()
    return _ARCHITECTURE_ALIASES.get(normalized, normalized)


def _split_tag(locator: str) -> tuple[str, str]:
    """解析显式标签；拒绝协议、摘要和可能被解释成参数的输入。"""

    value = locator.strip()
    if (
        not value
        or value.startswith("-")
        or "://" in value
        or "@" in value
        or any(character.isspace() for character in value)
    ):
        raise ContainerInstallationError("容器镜像定位符格式不合法")
    last_slash = value.rfind("/")
    last_colon = value.rfind(":")
    if last_colon <= last_slash:
        raise ContainerInstallationError("容器镜像定位符必须包含显式标签")
    repository, tag = value[:last_colon], value[last_colon + 1 :]
    if not _REPOSITORY_PATTERN.fullmatch(repository) or not _TAG_PATTERN.fullmatch(tag):
        raise ContainerInstallationError("容器镜像仓库或标签格式不合法")
    return repository, tag


def _inspect_image(
    reference: str,
    *,
    run: CommandRunner,
) -> dict[str, Any] | None:
    result = run(
        ["docker", "image", "inspect", reference],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ContainerInstallationError("Docker返回了无法解析的镜像元数据") from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ContainerInstallationError("Docker镜像元数据结构不符合预期")
    return payload[0]


class ContainerImageInstaller:
    """只允许按固定摘要拉取或导入镜像，并在校验后更新标签。"""

    def __init__(self, *, run: CommandRunner = subprocess.run) -> None:
        self._run = run

    def install(
        self,
        plan: dict[str, Any],
        *,
        progress: Callable[[str, str], None],
        archive_path: Path | None = None,
    ) -> str:
        artifact = plan.get("artifact", {})
        if artifact.get("kind") != "container_image":
            raise ContainerInstallationError("安装计划不是容器镜像制品")
        locator = str(artifact.get("locator", ""))
        repository, _tag = _split_tag(locator)
        digest = str(artifact.get("digest", "")).lower()
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise ContainerInstallationError("容器镜像必须由Catalog固定SHA-256摘要")
        architecture = _normalize_architecture(artifact.get("architecture"))
        previous = _inspect_image(locator, run=self._run)

        if archive_path is not None:
            return self._import_archive(
                archive_path,
                locator=locator,
                digest=digest,
                architecture=architecture,
                previous=previous,
                progress=progress,
            )

        pinned_reference = f"{repository}@{digest}"

        progress("downloading", f"正在按固定摘要拉取 {pinned_reference}")
        try:
            self._run(
                ["docker", "image", "pull", pinned_reference],
                check=True,
                capture_output=True,
                text=True,
                timeout=1800,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ContainerInstallationError(f"Docker拉取失败：{error}") from error

        progress("verifying", "正在校验镜像摘要与目标架构")
        pulled = _inspect_image(pinned_reference, run=self._run)
        if pulled is None:
            raise ContainerInstallationError("拉取后无法读取固定摘要镜像")
        repo_digests = pulled.get("RepoDigests", [])
        if not isinstance(repo_digests, list) or pinned_reference not in repo_digests:
            raise ContainerInstallationError("Docker镜像仓库摘要与Catalog不一致")
        actual_architecture = _normalize_architecture(pulled.get("Architecture"))
        if architecture and actual_architecture != architecture:
            raise ContainerInstallationError(
                f"镜像架构不匹配：期望{architecture}，实际{actual_architecture or '未知'}"
            )

        progress("installing", f"校验通过，正在更新本地标签 {locator}")
        switched = False
        try:
            self._run(
                ["docker", "image", "tag", pinned_reference, locator],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            switched = True
            tagged = _inspect_image(locator, run=self._run)
            if tagged is None or tagged.get("Id") != pulled.get("Id"):
                raise ContainerInstallationError("本地镜像标签未指向已校验内容")
        except (OSError, subprocess.SubprocessError, ContainerInstallationError) as error:
            if switched and previous and previous.get("Id"):
                try:
                    self._run(
                        ["docker", "image", "tag", str(previous["Id"]), locator],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except (OSError, subprocess.SubprocessError) as rollback_error:
                    raise ContainerInstallationError(
                        f"镜像标签切换失败且无法恢复旧标签：{rollback_error}"
                    ) from error
            if isinstance(error, ContainerInstallationError):
                raise
            raise ContainerInstallationError(f"镜像标签切换失败：{error}") from error

        return f"镜像已按摘要校验并准备完成：{locator}（{digest}）"

    def _import_archive(
        self,
        archive_path: Path,
        *,
        locator: str,
        digest: str,
        architecture: str,
        previous: dict[str, Any] | None,
        progress: Callable[[str, str], None],
    ) -> str:
        """从已由客户端下载并校验的临时归档导入镜像。"""

        if not archive_path.is_file():
            raise ContainerInstallationError("镜像归档临时文件不存在")
        progress("verifying", "镜像归档SHA-256已校验，正在导入Docker")
        try:
            self._run(
                ["docker", "image", "load", "--input", str(archive_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=7200,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ContainerInstallationError(f"Docker镜像归档导入失败：{error}") from error

        imported = _inspect_image(locator, run=self._run)
        try:
            if imported is None:
                raise ContainerInstallationError("镜像归档未包含Catalog声明的标签")
            actual_architecture = _normalize_architecture(imported.get("Architecture"))
            if architecture and actual_architecture != architecture:
                raise ContainerInstallationError(
                    f"镜像架构不匹配：期望{architecture}，实际{actual_architecture or '未知'}"
                )
            image_id = str(imported.get("Id", "")).casefold()
            repo_digests = imported.get("RepoDigests", [])
            digest_matches = image_id == digest or (
                isinstance(repo_digests, list)
                and any(str(item).casefold().endswith(f"@{digest}") for item in repo_digests)
            )
            if not digest_matches:
                raise ContainerInstallationError("导入镜像的内容摘要与Catalog不一致")

            progress("installing", f"校验通过，正在确认本地标签 {locator}")
            self._run(
                ["docker", "image", "tag", image_id, locator],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            tagged = _inspect_image(locator, run=self._run)
            if tagged is None or str(tagged.get("Id", "")).casefold() != image_id:
                raise ContainerInstallationError("本地镜像标签未指向已校验内容")
        except (OSError, subprocess.SubprocessError, ContainerInstallationError) as error:
            if previous and previous.get("Id"):
                try:
                    self._run(
                        ["docker", "image", "tag", str(previous["Id"]), locator],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except (OSError, subprocess.SubprocessError) as rollback_error:
                    raise ContainerInstallationError(
                        f"镜像归档校验失败且无法恢复旧标签：{rollback_error}"
                    ) from error
            elif imported is not None:
                try:
                    self._run(
                        ["docker", "image", "rm", locator],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except (OSError, subprocess.SubprocessError) as rollback_error:
                    raise ContainerInstallationError(
                        f"镜像归档校验失败且无法移除未校验标签：{rollback_error}"
                    ) from error
            if isinstance(error, ContainerInstallationError):
                raise
            raise ContainerInstallationError(f"镜像标签确认失败：{error}") from error

        return f"镜像已从固定摘要归档导入并准备完成：{locator}（{digest}）"
