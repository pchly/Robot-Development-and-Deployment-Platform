"""受管插件安装、原子激活与回滚测试。"""

import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from robot_edge_agent.plugin_installation import (
    ArtifactInstallationManager,
    PluginInstallationError,
    PluginInstaller,
    PluginRollbackError,
    active_plugin_paths,
)


def wheel_bytes(version: str = "1.0.0") -> bytes:
    output = BytesIO()
    dist_info = f"robot_hardware_demo-{version}.dist-info"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("robot_hardware_demo.py", "def create_adapter():\n    return None\n")
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: robot-hardware-demo\nVersion: {version}\n",
        )
        archive.writestr(
            f"{dist_info}/entry_points.txt",
            "[robot_platform.hardware_adapters]\ndemo = robot_hardware_demo:create_adapter\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return output.getvalue()


def runtime_wheel_bytes(version: str = "1.0.0") -> bytes:
    output = BytesIO()
    dist_info = f"robot_demo_runtime-{version}.dist-info"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("robot_demo_runtime/__init__.py", "__version__ = '1.0.0'\n")
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: robot-demo-runtime\nVersion: {version}\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return output.getvalue()


def runtime_installation_plan(content: bytes) -> dict[str, Any]:
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    artifact_id = "simulation.demo.runtime"
    return {
        "operation_id": "artifact-install-runtime-1.0.0",
        "target_agent_id": "simulation-windows-01",
        "artifact": {
            "artifact_id": artifact_id,
            "kind": "runtime_package",
            "version": "1.0.0",
            "locator": "robot-demo-runtime",
            "source_ref": "simulation.demo",
            "digest": digest,
            "selected_variant_id": "windows-amd64-python",
            "distribution": {
                "transport": "platform_api",
                "download_path": (
                    f"/api/v1/artifacts/{artifact_id}/content?variant_id=windows-amd64-python"
                ),
                "file_name": "robot_demo_runtime-1.0.0-py3-none-any.whl",
                "media_type": "application/zip",
                "size_bytes": len(content),
                "digest": digest,
            },
        },
    }


def installation_plan(content: bytes, version: str = "1.0.0") -> dict[str, Any]:
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    artifact_id = "hardware.camera.demo.edge-plugin"
    return {
        "operation_id": f"artifact-install-demo-{version}",
        "target_agent_id": "edge-01",
        "artifact": {
            "artifact_id": artifact_id,
            "kind": "edge_plugin",
            "version": version,
            "locator": "robot-hardware-demo",
            "source_ref": "hardware.camera.demo",
            "digest": digest,
            "distribution": {
                "transport": "platform_api",
                "download_path": f"/api/v1/artifacts/{artifact_id}/content",
                "file_name": f"robot_hardware_demo-{version}-py3-none-any.whl",
                "media_type": "application/zip",
                "size_bytes": len(content),
                "digest": digest,
            },
        },
    }


def test_plugin_installer_activates_versioned_release(tmp_path: Path) -> None:
    content = wheel_bytes()
    progress: list[str] = []

    PluginInstaller(tmp_path).install(
        installation_plan(content),
        content,
        progress=lambda status, _message: progress.append(status),
    )

    paths = active_plugin_paths(tmp_path)
    assert progress == ["verifying", "installing"]
    assert len(paths) == 1
    assert paths[0].name.startswith("1.0.0-")
    marker = json.loads((paths[0] / ".artifact.json").read_text(encoding="utf-8"))
    assert marker["artifact_id"] == "hardware.camera.demo.edge-plugin"
    assert marker["digest"] == installation_plan(content)["artifact"]["digest"]


def test_plugin_installer_rejects_digest_mismatch_without_activation(
    tmp_path: Path,
) -> None:
    content = wheel_bytes()
    plan = installation_plan(content)
    plan["artifact"]["digest"] = "sha256:" + "0" * 64
    plan["artifact"]["distribution"]["digest"] = "sha256:" + "0" * 64

    with pytest.raises(PluginInstallationError, match="SHA-256"):
        PluginInstaller(tmp_path).install(plan, content, progress=lambda *_args: None)

    assert not (tmp_path / "active").exists()


def test_plugin_installer_restores_previous_pointer_after_activation_failure(
    tmp_path: Path,
) -> None:
    first = wheel_bytes("1.0.0")
    PluginInstaller(tmp_path).install(
        installation_plan(first, "1.0.0"),
        first,
        progress=lambda *_args: None,
    )
    pointer = tmp_path / "active" / "hardware.camera.demo.edge-plugin.json"
    previous = pointer.read_bytes()
    second = wheel_bytes("1.1.0")

    def reject_activation(_root: Path, _release: Path) -> None:
        raise RuntimeError("模拟激活后验证失败")

    with pytest.raises(PluginRollbackError, match="模拟激活后验证失败"):
        PluginInstaller(tmp_path, activation_validator=reject_activation).install(
            installation_plan(second, "1.1.0"),
            second,
            progress=lambda *_args: None,
        )

    assert pointer.read_bytes() == previous
    assert active_plugin_paths(tmp_path)[0].name.startswith("1.0.0-")


def test_plugin_installer_rolls_back_after_restart_health_failure(
    tmp_path: Path,
) -> None:
    first = wheel_bytes("0.9.0")
    first_plan = installation_plan(first, "0.9.0")
    installer = PluginInstaller(tmp_path)
    installer.install(first_plan, first, progress=lambda *_args: None)
    assert (
        installer.confirm_activation(
            first_plan,
            health_check=lambda _plan: (True, "healthy"),
        )
        == "healthy"
    )

    second = wheel_bytes("1.0.0")
    second_plan = installation_plan(second, "1.0.0")
    installer.install(second_plan, second, progress=lambda *_args: None)

    with pytest.raises(PluginRollbackError, match="probe failed"):
        installer.confirm_activation(
            second_plan,
            health_check=lambda _plan: (False, "probe failed"),
        )

    assert active_plugin_paths(tmp_path)[0].name.startswith("0.9.0-")


def test_installation_manager_reports_progress_and_requests_restart(
    tmp_path: Path,
) -> None:
    content = wheel_bytes()
    plan = installation_plan(content)

    class Client:
        def __init__(self) -> None:
            self.statuses: list[str] = []
            self.status = "pending"

        def list_artifact_installations(self, _agent_id: str) -> list[dict[str, Any]]:
            return [{"plan": plan, "status": self.status}]

        def report_artifact_installation_status(
            self, _operation_id: str, status: str, *, message: str = ""
        ) -> dict[str, Any]:
            self.statuses.append(status)
            self.status = status
            return {}

        def download_artifact(self, _path: str, *, max_bytes: int) -> bytes:
            assert max_bytes == len(content)
            return content

    client = Client()

    restart = ArtifactInstallationManager(tmp_path).reconcile(client, "edge-01")

    assert restart is True
    assert client.statuses == [
        "downloading",
        "verifying",
        "installing",
        "activating",
    ]

    restart = ArtifactInstallationManager(
        tmp_path,
        health_check=lambda _plan: (True, "插件和设备发现健康"),
    ).reconcile(client, "edge-01")

    assert restart is False
    assert client.statuses == [
        "downloading",
        "verifying",
        "installing",
        "activating",
        "succeeded",
    ]


def test_installation_manager_installs_variant_runtime_package(
    tmp_path: Path,
) -> None:
    content = runtime_wheel_bytes()
    plan = runtime_installation_plan(content)

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

        def download_artifact(self, path: str, *, max_bytes: int) -> bytes:
            assert path.endswith("?variant_id=windows-amd64-python")
            assert max_bytes == len(content)
            return content

    runtime_root = tmp_path / "runtime-packages"
    restart = ArtifactInstallationManager(
        tmp_path / "plugins",
        runtime_package_root=runtime_root,
    ).reconcile(Client(), "simulation-windows-01")

    assert restart is True
    pointer = runtime_root / "active" / "simulation.demo.runtime.json"
    assert pointer.is_file()
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    assert payload["digest"] == plan["artifact"]["digest"]
