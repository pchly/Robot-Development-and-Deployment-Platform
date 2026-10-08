"""标定配置契约、安装与库存测试。"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from robot_edge_agent.artifacts import collect_managed_calibration_profiles
from robot_edge_agent.calibration import (
    CalibrationInstallationError,
    CalibrationProfileError,
    CalibrationProfileInstaller,
    parse_calibration_profile,
)
from robot_edge_agent.plugin_installation import ArtifactInstallationManager


def profile_bytes(*, target_device_id: str = "mujoco-six-axis-01") -> bytes:
    return json.dumps(
        {
            "schema": "robot.calibration.camera-to-arm.v1",
            "calibration_id": "mock-camera-to-mujoco-arm-v1",
            "version": "1.0.0",
            "source": {
                "frame_id": "camera_optical",
                "device": {
                    "transport": "simulation",
                    "device_id": "mock-rgbd-camera-01",
                },
                "intrinsics": {
                    "width_px": 640,
                    "height_px": 480,
                    "fx_px": 600.0,
                    "fy_px": 600.0,
                    "cx_px": 320.0,
                    "cy_px": 240.0,
                },
            },
            "target": {
                "frame_id": "arm_base",
                "device": {
                    "adapter_id": "hardware.arm.mujoco",
                    "device_id": target_device_id,
                },
            },
            "transform": {
                "translation_m": {"x": 0.25, "y": 0.1, "z": 0.0},
                "rotation_rpy_rad": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            },
        },
        sort_keys=True,
    ).encode()


def calibration_plan(content: bytes) -> dict[str, Any]:
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    artifact_id = "calibration.camera.mock-to-mujoco-arm"
    return {
        "operation_id": "artifact-install-calibration-1.0.0",
        "target_agent_id": "simulation-windows-01",
        "artifact": {
            "artifact_id": artifact_id,
            "kind": "calibration_profile",
            "version": "1.0.0",
            "locator": "mock-camera-to-mujoco-arm-v1",
            "digest": digest,
            "distribution": {
                "transport": "platform_api",
                "download_path": f"/api/v1/artifacts/{artifact_id}/content",
                "file_name": "mock-camera-to-mujoco-arm-v1.json",
                "media_type": "application/json",
                "size_bytes": len(content),
                "digest": digest,
            },
        },
    }


def test_parse_calibration_profile_builds_spatial_contract() -> None:
    profile = parse_calibration_profile(profile_bytes())

    assert profile.calibration_id == "mock-camera-to-mujoco-arm-v1"
    assert profile.source_device_id == "mock-rgbd-camera-01"
    assert profile.target_adapter_id == "hardware.arm.mujoco"
    assert profile.intrinsics.width_px == 640
    assert profile.camera_to_target.translation_m == (0.25, 0.1, 0.0)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema="unknown"),
        lambda value: value["source"]["intrinsics"].update(fx_px=0),
        lambda value: value["target"]["device"].update(device_id=""),
    ],
)
def test_parse_calibration_profile_rejects_invalid_contract(mutate: Any) -> None:
    payload = json.loads(profile_bytes())
    mutate(payload)

    with pytest.raises(CalibrationProfileError):
        parse_calibration_profile(json.dumps(payload))


@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b"[]",
        json.dumps({"schema": "robot.calibration.camera-to-arm.v1"}).encode(),
        profile_bytes().replace(b'"fx_px": 600.0', b'"fx_px": true'),
        profile_bytes().replace(b'"width_px": 640', b'"width_px": 0.5'),
    ],
)
def test_parse_calibration_profile_rejects_malformed_values(content: bytes) -> None:
    with pytest.raises(CalibrationProfileError):
        parse_calibration_profile(content)


def test_installer_activates_version_and_inventory_verifies_content(tmp_path: Path) -> None:
    content = profile_bytes()
    staged = tmp_path / "profile.json"
    staged.write_bytes(content)
    root = tmp_path / "calibrations"
    progress: list[str] = []

    message = CalibrationProfileInstaller(root).install(
        calibration_plan(content),
        staged,
        progress=lambda status, _message: progress.append(status),
    )

    assert "mock-camera-to-mujoco-arm-v1@1.0.0" in message
    assert progress == ["verifying", "installing"]
    inventory = collect_managed_calibration_profiles([root])
    assert len(inventory) == 1
    assert inventory[0]["kind"] == "calibration_profile"
    assert inventory[0]["digest"] == calibration_plan(content)["artifact"]["digest"]

    profile_path = Path(str(inventory[0]["install_path"])) / "profile.json"
    profile_path.write_bytes(profile_bytes(target_device_id="changed-device"))
    assert collect_managed_calibration_profiles([root]) == []


def test_installer_rejects_digest_mismatch_without_pointer(tmp_path: Path) -> None:
    content = profile_bytes()
    staged = tmp_path / "profile.json"
    staged.write_bytes(content + b" ")
    root = tmp_path / "calibrations"

    with pytest.raises(CalibrationInstallationError, match="摘要"):
        CalibrationProfileInstaller(root).install(
            calibration_plan(content), staged, progress=lambda *_args: None
        )

    assert not (root / "active").exists()


@pytest.mark.parametrize(
    "change,missing_file",
    [
        (lambda plan: plan.update(operation_id="bad operation"), False),
        (lambda plan: plan["artifact"].update(kind="model"), False),
        (lambda plan: plan["artifact"].update(digest="invalid"), False),
        (lambda _plan: None, True),
        (lambda plan: plan["artifact"].update(locator="other-calibration"), False),
    ],
)
def test_installer_rejects_invalid_plan_or_staged_file(
    tmp_path: Path, change: Any, missing_file: bool
) -> None:
    content = profile_bytes()
    plan = calibration_plan(content)
    change(plan)
    staged = tmp_path / "profile.json"
    if not missing_file:
        staged.write_bytes(content)

    with pytest.raises(CalibrationInstallationError):
        CalibrationProfileInstaller(tmp_path / "calibrations").install(
            plan, staged, progress=lambda *_args: None
        )


def test_installer_is_idempotent_and_preserves_pointer_on_conflict(tmp_path: Path) -> None:
    content = profile_bytes()
    plan = calibration_plan(content)
    staged = tmp_path / "profile.json"
    staged.write_bytes(content)
    root = tmp_path / "calibrations"
    installer = CalibrationProfileInstaller(root)
    installer.install(plan, staged, progress=lambda *_args: None)
    pointer = root / "active" / "calibration.camera.mock-to-mujoco-arm.json"
    previous_pointer = pointer.read_bytes()

    installer.install(plan, staged, progress=lambda *_args: None)
    assert pointer.read_bytes() == previous_pointer

    inventory = collect_managed_calibration_profiles([root])
    marker = Path(str(inventory[0]["install_path"])) / ".artifact.json"
    marker.write_text("{}", encoding="utf-8")
    with pytest.raises(CalibrationInstallationError, match="既有标定版本"):
        installer.install(plan, staged, progress=lambda *_args: None)
    assert pointer.read_bytes() == previous_pointer


def test_installation_manager_streams_calibration_profile(tmp_path: Path) -> None:
    content = profile_bytes()
    plan = calibration_plan(content)

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
            assert download_path.endswith("calibration.camera.mock-to-mujoco-arm/content")
            assert max_bytes == len(content)
            assert expected_digest == plan["artifact"]["digest"]
            destination.write_bytes(content)
            return len(content)

    root = tmp_path / "calibrations"
    client = Client()
    restart = ArtifactInstallationManager(
        tmp_path / "plugins", calibration_root=root
    ).reconcile(client, "simulation-windows-01")

    assert restart is True
    assert client.statuses == ["downloading", "verifying", "installing", "succeeded"]
    assert len(collect_managed_calibration_profiles([root])) == 1
