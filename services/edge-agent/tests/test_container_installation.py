"""受控容器镜像准备测试。"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from robot_edge_agent.container_installation import (
    ContainerImageInstaller,
    ContainerInstallationError,
)
from robot_edge_agent.plugin_installation import ArtifactInstallationManager

DIGEST = "sha256:" + "a" * 64
LOCATOR = "registry.example:5000/robot/demo:1.2.3"
PINNED = f"registry.example:5000/robot/demo@{DIGEST}"


def container_plan(*, digest: str = DIGEST) -> dict[str, Any]:
    return {
        "operation_id": "artifact-install-container-01",
        "target_agent_id": "edge-01",
        "artifact": {
            "artifact_id": "runtime.demo.runtime-image",
            "kind": "container_image",
            "version": "1.2.3",
            "locator": LOCATOR,
            "digest": digest,
            "architecture": "aarch64",
            "source_ref": "runtime.demo",
        },
    }


class DockerRunner:
    def __init__(self, *, architecture: str = "aarch64") -> None:
        self.architecture = architecture
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            reference = command[3]
            if reference == LOCATOR and len(self.commands) == 1:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
            payload = [
                {
                    "Id": "sha256:image",
                    "Architecture": self.architecture,
                    "RepoDigests": [PINNED],
                }
            ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(command, 0, stdout="ok")


def test_container_installer_pulls_digest_verifies_and_tags() -> None:
    runner = DockerRunner()
    progress: list[str] = []

    message = ContainerImageInstaller(run=runner).install(
        container_plan(),
        progress=lambda status, _message: progress.append(status),
    )

    assert progress == ["downloading", "verifying", "installing"]
    assert ["docker", "image", "pull", PINNED] in runner.commands
    assert ["docker", "image", "tag", PINNED, LOCATOR] in runner.commands
    assert DIGEST in message


def test_container_installer_accepts_oci_arm64_for_linux_aarch64() -> None:
    runner = DockerRunner(architecture="arm64")

    ContainerImageInstaller(run=runner).install(
        container_plan(),
        progress=lambda *_args: None,
    )

    assert ["docker", "image", "tag", PINNED, LOCATOR] in runner.commands


def test_container_installer_imports_verified_archive_and_confirms_tag(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "image.tar"
    archive.write_bytes(b"docker archive")
    commands: list[list[str]] = []
    inspect_count = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        commands.append(command)
        if command[:3] != ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        inspect_count += 1
        if inspect_count == 1:
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"Id": DIGEST, "Architecture": "aarch64", "RepoDigests": []}]),
        )

    progress: list[str] = []
    message = ContainerImageInstaller(run=runner).install(
        container_plan(),
        archive_path=archive,
        progress=lambda status, _message: progress.append(status),
    )

    assert progress == ["verifying", "installing"]
    assert ["docker", "image", "load", "--input", str(archive)] in commands
    assert ["docker", "image", "tag", DIGEST, LOCATOR] in commands
    assert "归档导入" in message


def test_container_archive_import_accepts_oci_arm64_for_linux_aarch64(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "image.tar"
    archive.write_bytes(b"docker archive")
    inspect_count = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        if command[:3] != ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        inspect_count += 1
        if inspect_count == 1:
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"Id": DIGEST, "Architecture": "arm64", "RepoDigests": []}]),
        )

    ContainerImageInstaller(run=runner).install(
        container_plan(),
        archive_path=archive,
        progress=lambda *_args: None,
    )


def test_container_archive_import_restores_previous_tag_on_architecture_mismatch(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "image.tar"
    archive.write_bytes(b"docker archive")
    commands: list[list[str]] = []
    inspect_count = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        commands.append(command)
        if command[:3] != ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        inspect_count += 1
        payload = (
            [{"Id": "sha256:old", "Architecture": "aarch64", "RepoDigests": []}]
            if inspect_count == 1
            else [{"Id": DIGEST, "Architecture": "amd64", "RepoDigests": []}]
        )
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload))

    with pytest.raises(ContainerInstallationError, match="架构不匹配"):
        ContainerImageInstaller(run=runner).install(
            container_plan(),
            archive_path=archive,
            progress=lambda *_args: None,
        )

    assert ["docker", "image", "tag", "sha256:old", LOCATOR] in commands


def test_container_archive_import_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ContainerInstallationError, match="临时文件不存在"):
        ContainerImageInstaller(run=DockerRunner()).install(
            container_plan(),
            archive_path=tmp_path / "missing.tar",
            progress=lambda *_args: None,
        )


def test_container_archive_import_rejects_load_failure_and_wrong_digest(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "image.tar"
    archive.write_bytes(b"docker archive")

    def load_failure(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="")
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(ContainerInstallationError, match="归档导入失败"):
        ContainerImageInstaller(run=load_failure).install(
            container_plan(), archive_path=archive, progress=lambda *_args: None
        )

    inspect_count = 0

    def wrong_digest(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        if command[:3] != ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        inspect_count += 1
        if inspect_count == 1:
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    {
                        "Id": "sha256:" + "c" * 64,
                        "Architecture": "aarch64",
                        "RepoDigests": [],
                    }
                ]
            ),
        )

    with pytest.raises(ContainerInstallationError, match="内容摘要"):
        ContainerImageInstaller(run=wrong_digest).install(
            container_plan(), archive_path=archive, progress=lambda *_args: None
        )

    inspect_count = 0

    def missing_final_tag(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        if command[:3] != ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        inspect_count += 1
        if inspect_count in {1, 3}:
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"Id": DIGEST, "Architecture": "aarch64", "RepoDigests": []}]),
        )

    with pytest.raises(ContainerInstallationError, match="本地镜像标签"):
        ContainerImageInstaller(run=missing_final_tag).install(
            container_plan(), archive_path=archive, progress=lambda *_args: None
        )

    def archive_without_expected_tag(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    with pytest.raises(ContainerInstallationError, match="未包含Catalog声明的标签"):
        ContainerImageInstaller(run=archive_without_expected_tag).install(
            container_plan(), archive_path=archive, progress=lambda *_args: None
        )


def test_container_installer_rejects_unpinned_or_wrong_architecture() -> None:
    no_digest_runner = DockerRunner()
    with pytest.raises(ContainerInstallationError, match="SHA-256"):
        ContainerImageInstaller(run=no_digest_runner).install(
            container_plan(digest=""),
            progress=lambda *_args: None,
        )
    assert no_digest_runner.commands == []

    with pytest.raises(ContainerInstallationError, match="架构不匹配"):
        ContainerImageInstaller(run=DockerRunner(architecture="amd64")).install(
            container_plan(),
            progress=lambda *_args: None,
        )


@pytest.mark.parametrize(
    "locator",
    ["demo", "-demo:1", "https://registry.example/demo:1", "demo@sha256:abc"],
)
def test_container_installer_rejects_unsafe_locator(locator: str) -> None:
    plan = container_plan()
    plan["artifact"]["locator"] = locator

    with pytest.raises(ContainerInstallationError, match="定位符|显式标签"):
        ContainerImageInstaller(run=DockerRunner()).install(
            plan,
            progress=lambda *_args: None,
        )


def test_container_installer_rejects_invalid_docker_metadata_and_pull_failure() -> None:
    def invalid_metadata(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "image", "inspect"] and command[3] == LOCATOR:
            return subprocess.CompletedProcess(command, 1, stdout="")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="{")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    with pytest.raises(ContainerInstallationError, match="无法解析"):
        ContainerImageInstaller(run=invalid_metadata).install(
            container_plan(),
            progress=lambda *_args: None,
        )

    def failed_pull(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="")
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(ContainerInstallationError, match="Docker拉取失败"):
        ContainerImageInstaller(run=failed_pull).install(
            container_plan(),
            progress=lambda *_args: None,
        )


def test_container_installer_restores_previous_tag_after_verification_failure() -> None:
    commands: list[list[str]] = []
    inspect_count = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        commands.append(command)
        if command[:3] != ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        inspect_count += 1
        if inspect_count == 1:
            payload = [{"Id": "sha256:old", "Architecture": "aarch64", "RepoDigests": []}]
        elif inspect_count == 2:
            payload = [
                {
                    "Id": "sha256:new",
                    "Architecture": "aarch64",
                    "RepoDigests": [PINNED],
                }
            ]
        else:
            payload = [{"Id": "sha256:wrong", "Architecture": "aarch64", "RepoDigests": []}]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload))

    with pytest.raises(ContainerInstallationError, match="本地镜像标签"):
        ContainerImageInstaller(run=runner).install(
            container_plan(),
            progress=lambda *_args: None,
        )

    assert ["docker", "image", "tag", "sha256:old", LOCATOR] in commands


def test_installation_manager_prepares_container_without_downloading_platform_file(
    tmp_path: Path,
) -> None:
    runner = DockerRunner()
    plan = container_plan()

    class Client:
        def __init__(self) -> None:
            self.statuses: list[str] = []

        def list_artifact_installations(self, _agent_id: str) -> list[dict[str, Any]]:
            return [{"plan": plan, "status": "pending"}]

        def report_artifact_installation_status(
            self, _operation_id: str, status: str, *, message: str = ""
        ) -> dict[str, Any]:
            self.statuses.append(status)
            return {}

        def download_artifact(self, _path: str, *, max_bytes: int) -> bytes:
            raise AssertionError("容器镜像不得通过Platform API文件下载")

    client = Client()
    restart = ArtifactInstallationManager(
        tmp_path,
        container_run=runner,
    ).reconcile(client, "edge-01")

    assert restart is True
    assert client.statuses == ["downloading", "verifying", "installing", "succeeded"]


def test_installation_manager_streams_container_archive_before_import(
    tmp_path: Path,
) -> None:
    plan = container_plan()
    plan["artifact"]["distribution"] = {
        "transport": "platform_api",
        "download_path": ("/api/v1/artifacts/runtime.demo.runtime-image/content"),
        "file_name": "demo.tar",
        "media_type": "application/x-tar",
        "size_bytes": 1024,
        "digest": "sha256:" + "b" * 64,
    }
    inspect_count = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        if command[:3] != ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        inspect_count += 1
        if inspect_count == 1:
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"Id": DIGEST, "Architecture": "aarch64", "RepoDigests": []}]),
        )

    class Client:
        def __init__(self) -> None:
            self.statuses: list[str] = []
            self.downloaded = False

        def list_artifact_installations(self, _agent_id: str) -> list[dict[str, Any]]:
            return [{"plan": plan, "status": "pending"}]

        def report_artifact_installation_status(
            self, _operation_id: str, status: str, *, message: str = ""
        ) -> dict[str, Any]:
            self.statuses.append(status)
            return {}

        def download_artifact_to_file(
            self,
            _path: str,
            destination: Path,
            *,
            max_bytes: int,
            expected_digest: str,
        ) -> int:
            assert max_bytes == 1024
            assert expected_digest == "sha256:" + "b" * 64
            destination.write_bytes(b"archive")
            self.downloaded = True
            return 7

    client = Client()
    restart = ArtifactInstallationManager(
        tmp_path / "plugins",
        container_run=runner,
    ).reconcile(client, "edge-01")

    assert restart is True
    assert client.downloaded is True
    assert client.statuses == ["downloading", "verifying", "installing", "succeeded"]
    assert list((tmp_path / "staging").glob("container-image-*")) == []
