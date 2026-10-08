"""受管TensorRT Engine文件安装测试。"""

import hashlib
from pathlib import Path
from typing import Any

import pytest

import robot_edge_agent.file_installation as file_installation
from robot_edge_agent.file_installation import (
    ManagedFileInstallationError,
    ManagedFileInstaller,
    ManagedFileRollbackError,
)
from robot_edge_agent.plugin_installation import ArtifactInstallationManager


def engine_plan(target: Path, content: bytes) -> dict[str, Any]:
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    artifact_id = "algorithm.demo.tensorrt-engine"
    return {
        "operation_id": "artifact-install-engine-01",
        "target_agent_id": "edge-01",
        "artifact": {
            "artifact_id": artifact_id,
            "kind": "tensorrt_engine",
            "version": "1.0.0",
            "locator": str(target),
            "digest": digest,
            "architecture": "aarch64",
            "source_ref": "algorithm.demo",
            "distribution": {
                "transport": "platform_api",
                "download_path": f"/api/v1/artifacts/{artifact_id}/content",
                "file_name": "demo.engine",
                "media_type": "application/octet-stream",
                "size_bytes": len(content),
                "digest": digest,
            },
        },
    }


def test_managed_file_installer_atomically_replaces_engine(tmp_path: Path) -> None:
    root = tmp_path / "cache" / "tensorrt"
    target = root / "demo" / "demo.engine"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old engine")
    staged = tmp_path / "staged.engine"
    staged.write_bytes(b"new engine")
    progress: list[str] = []

    message = ManagedFileInstaller((root,)).install(
        engine_plan(target, staged.read_bytes()),
        staged,
        progress=lambda status, _message: progress.append(status),
    )

    assert target.read_bytes() == b"new engine"
    assert progress == ["verifying", "installing"]
    assert "原子安装" in message
    assert list(target.parent.glob(".*.backup")) == []
    assert list(target.parent.glob(".*.new")) == []


def test_managed_file_installer_rejects_unmanaged_path_and_bad_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cache" / "tensorrt"
    staged = tmp_path / "staged.engine"
    staged.write_bytes(b"engine")
    outside = tmp_path / "outside.engine"
    plan = engine_plan(outside, staged.read_bytes())

    with pytest.raises(ManagedFileInstallationError, match="不在允许"):
        ManagedFileInstaller((root,)).install(plan, staged, progress=lambda *_args: None)

    target = root / "demo.engine"
    plan = engine_plan(target, staged.read_bytes())
    plan["artifact"]["distribution"]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ManagedFileInstallationError, match="分发契约"):
        ManagedFileInstaller((root,)).install(plan, staged, progress=lambda *_args: None)


def test_managed_file_installer_rejects_invalid_plan_inputs(tmp_path: Path) -> None:
    root = tmp_path / "cache" / "tensorrt"
    target = root / "demo.engine"
    staged = tmp_path / "staged.engine"
    staged.write_bytes(b"engine")

    with pytest.raises(ValueError, match="受管文件根目录"):
        ManagedFileInstaller(())

    plan = engine_plan(Path("relative.engine"), staged.read_bytes())
    with pytest.raises(ManagedFileInstallationError, match="绝对.engine路径"):
        ManagedFileInstaller((root,)).install(plan, staged, progress=lambda *_args: None)

    plan = engine_plan(target, staged.read_bytes())
    plan["artifact"]["kind"] = "container_image"
    with pytest.raises(ManagedFileInstallationError, match="不是TensorRT Engine"):
        ManagedFileInstaller((root,)).install(plan, staged, progress=lambda *_args: None)

    plan = engine_plan(target, staged.read_bytes())
    plan["operation_id"] = "bad operation id"
    with pytest.raises(ManagedFileInstallationError, match="事务ID格式"):
        ManagedFileInstaller((root,)).install(plan, staged, progress=lambda *_args: None)

    missing = tmp_path / "missing.engine"
    plan = engine_plan(target, b"missing")
    with pytest.raises(ManagedFileInstallationError, match="暂存文件不存在"):
        ManagedFileInstaller((root,)).install(plan, missing, progress=lambda *_args: None)

    plan = engine_plan(target, b"different engine")
    with pytest.raises(ManagedFileInstallationError, match="暂存文件SHA-256"):
        ManagedFileInstaller((root,)).install(plan, staged, progress=lambda *_args: None)


def test_managed_file_installer_rejects_existing_transaction_temp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cache" / "tensorrt"
    target = root / "demo.engine"
    target.parent.mkdir(parents=True)
    staged = tmp_path / "staged.engine"
    staged.write_bytes(b"engine")
    plan = engine_plan(target, staged.read_bytes())
    temporary = target.parent / f".{target.name}.{plan['operation_id']}.new"
    temporary.write_bytes(b"stale transaction")

    with pytest.raises(ManagedFileInstallationError, match="临时路径已存在"):
        ManagedFileInstaller((root,)).install(plan, staged, progress=lambda *_args: None)


def test_managed_file_installer_rejects_corrupt_temporary_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "cache" / "tensorrt"
    target = root / "demo.engine"
    staged = tmp_path / "staged.engine"
    staged.write_bytes(b"engine")
    real_sha256 = file_installation._sha256

    def fail_temporary_verification(path: Path) -> str:
        if path.name.endswith(".new"):
            return "sha256:" + "f" * 64
        return real_sha256(path)

    monkeypatch.setattr(file_installation, "_sha256", fail_temporary_verification)

    with pytest.raises(ManagedFileInstallationError, match="临时副本摘要"):
        ManagedFileInstaller((root,)).install(
            engine_plan(target, staged.read_bytes()),
            staged,
            progress=lambda *_args: None,
        )

    assert not target.exists()
    assert list(target.parent.glob(".*.new")) == []


def test_managed_file_installer_removes_new_target_after_final_verify_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "cache" / "tensorrt"
    target = root / "demo.engine"
    staged = tmp_path / "staged.engine"
    staged.write_bytes(b"new engine")
    real_sha256 = file_installation._sha256

    def fail_final_verification(path: Path) -> str:
        if path == target:
            return "sha256:" + "f" * 64
        return real_sha256(path)

    monkeypatch.setattr(file_installation, "_sha256", fail_final_verification)

    with pytest.raises(ManagedFileInstallationError, match="原子替换后摘要"):
        ManagedFileInstaller((root,)).install(
            engine_plan(target, staged.read_bytes()),
            staged,
            progress=lambda *_args: None,
        )

    assert not target.exists()


def test_managed_file_installer_restores_old_engine_after_final_verify_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "cache" / "tensorrt"
    target = root / "demo.engine"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old engine")
    staged = tmp_path / "staged.engine"
    staged.write_bytes(b"new engine")
    expected_digest = f"sha256:{hashlib.sha256(staged.read_bytes()).hexdigest()}"
    real_sha256 = file_installation._sha256

    def fail_final_verification(path: Path) -> str:
        if path == target:
            return "sha256:" + "f" * 64
        return real_sha256(path)

    monkeypatch.setattr(file_installation, "_sha256", fail_final_verification)

    with pytest.raises(ManagedFileRollbackError, match="替换后摘要"):
        ManagedFileInstaller((root,)).install(
            engine_plan(target, staged.read_bytes()),
            staged,
            progress=lambda *_args: None,
        )

    assert expected_digest != real_sha256(target)
    assert target.read_bytes() == b"old engine"


def test_installation_manager_streams_and_installs_tensorrt_engine(
    tmp_path: Path,
) -> None:
    content = b"managed engine"
    managed_root = tmp_path / "cache" / "tensorrt"
    target = managed_root / "demo" / "demo.engine"
    plan = engine_plan(target, content)

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

        def download_artifact_to_file(
            self,
            download_path: str,
            destination: Path,
            *,
            max_bytes: int,
            expected_digest: str,
        ) -> int:
            assert download_path.endswith("algorithm.demo.tensorrt-engine/content")
            assert max_bytes == len(content)
            assert expected_digest == plan["artifact"]["digest"]
            destination.write_bytes(content)
            return len(content)

    client = Client()
    restart = ArtifactInstallationManager(
        tmp_path / "plugins",
        managed_file_roots=(managed_root,),
    ).reconcile(client, "edge-01")

    assert restart is True
    assert target.read_bytes() == content
    assert client.statuses == ["downloading", "verifying", "installing", "succeeded"]
    assert list((tmp_path / "staging").glob("managed-file-*")) == []
