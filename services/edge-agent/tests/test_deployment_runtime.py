"""DeploymentPlan端侧执行测试。"""

import hashlib
import json
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from robot_edge_agent.deployment_runtime import (
    DockerRealSenseYoloRuntime,
    EdgeDeploymentManager,
    MockWorkflowRuntime,
    MujocoWorkflowRuntime,
    RuntimeCommandError,
    RuntimeSnapshot,
    UnsupportedDeploymentPlan,
    WindowsWebcamYoloxMujocoRuntime,
    _camera_index_parameter,
    _load_windows_vision_runtime_api,
    _resolve_windows_vision_artifacts,
    create_workflow_runtime,
)
from robot_edge_agent.spatial import CameraIntrinsics, RigidTransform


def calibration_profile_bytes() -> bytes:
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
                    "device_id": "mujoco-six-axis-01",
                },
            },
            "transform": {
                "translation_m": {"x": 0.25, "y": 0.1, "z": 0.0},
                "rotation_rpy_rad": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            },
        },
        sort_keys=True,
    ).encode()


def mock_plan(*, desired_state: str = "running") -> dict[str, Any]:
    return {
        "plan_id": "plan-mock-01",
        "target_agent_id": "edge-01",
        "desired_state": desired_state,
        "workflow": {
            "workflow_id": "workflow.mock_detection",
            "version": "0.1.0",
            "name": "模拟目标检测",
            "nodes": [
                {
                    "node_id": "camera",
                    "component_ref": "hardware.camera.mock",
                    "component_version": "0.1.0",
                    "parameters": {"width": 640, "height": 480, "fps": 15},
                },
                {
                    "node_id": "detector",
                    "component_ref": "algorithm.vision.mock_detector",
                    "component_version": "0.1.0",
                    "parameters": {},
                },
            ],
            "links": [
                {
                    "source": {"node_id": "camera", "port": "color"},
                    "target": {"node_id": "detector", "port": "image"},
                }
            ],
        },
    }


def mock_pipeline_plan(*, desired_state: str = "running") -> dict[str, Any]:
    plan = mock_plan(desired_state=desired_state)
    plan["plan_id"] = "plan-mock-pipeline-01"
    plan["workflow"]["workflow_id"] = "workflow.mock_detection_pipeline"
    plan["workflow"]["name"] = "三节点模拟视觉流程"
    plan["workflow"]["nodes"].append(
        {
            "node_id": "filter",
            "component_ref": "algorithm.vision.mock_filter",
            "component_version": "0.1.0",
            "parameters": {"min_confidence": 0.6},
        }
    )
    plan["workflow"]["links"].append(
        {
            "source": {"node_id": "detector", "port": "detections"},
            "target": {"node_id": "filter", "port": "detections"},
        }
    )
    return plan


def mock_vision_arm_plan(*, desired_state: str = "running") -> dict[str, Any]:
    plan = mock_plan(desired_state=desired_state)
    plan["plan_id"] = "plan-mock-vision-arm-01"
    plan["workflow"]["workflow_id"] = "workflow.mock_vision_arm"
    plan["workflow"]["name"] = "视觉引导模拟机械臂"
    plan["workflow"]["nodes"].extend(
        [
            {
                "node_id": "target_selector",
                "component_ref": "algorithm.motion.mock_target_selector",
                "component_version": "0.1.0",
                "parameters": {"minimum_confidence": 0.65},
            },
            {
                "node_id": "arm",
                "component_ref": "hardware.arm.mock",
                "component_version": "0.1.0",
                "parameters": {
                    "command_timeout_ms": 700,
                    "max_velocity_scale": 0.15,
                },
            },
        ]
    )
    plan["workflow"]["links"].extend(
        [
            {
                "source": {"node_id": "detector", "port": "detections"},
                "target": {
                    "node_id": "target_selector",
                    "port": "detections",
                },
            },
            {
                "source": {"node_id": "target_selector", "port": "command"},
                "target": {"node_id": "arm", "port": "command"},
            },
        ]
    )
    return plan


def mock_safe_vision_arm_plan(*, desired_state: str = "running") -> dict[str, Any]:
    plan = mock_vision_arm_plan(desired_state=desired_state)
    plan["plan_id"] = "plan-mock-safe-vision-arm-01"
    plan["workflow"]["workflow_id"] = "workflow.mock_vision_arm_safety"
    plan["workflow"]["name"] = "视觉引导模拟机械臂（独立安全门）"
    plan["workflow"]["nodes"].insert(
        3,
        {
            "node_id": "safety_gate",
            "component_ref": "algorithm.motion.safety_gate",
            "component_version": "0.1.0",
            "parameters": {
                "command_ttl_ms": 300,
                "velocity_limit": 0.12,
                "command_authorized": 1,
                "emergency_stop": 0,
                "simulated_command_age_ms": 0,
            },
        },
    )
    plan["workflow"]["links"][-1] = {
        "source": {"node_id": "target_selector", "port": "command"},
        "target": {"node_id": "safety_gate", "port": "command"},
    }
    plan["workflow"]["links"].append(
        {
            "source": {"node_id": "safety_gate", "port": "safe_command"},
            "target": {"node_id": "arm", "port": "command"},
        }
    )
    return plan


def mujoco_vision_arm_plan(*, desired_state: str = "running") -> dict[str, Any]:
    plan = mock_plan(desired_state=desired_state)
    plan["plan_id"] = "plan-mujoco-vision-arm-01"
    plan["workflow"].update(
        {
            "workflow_id": "workflow.mujoco_vision_arm_safety",
            "version": "0.3.0",
            "name": "MuJoCo RGB-D空间定位机械臂（安全门）",
            "nodes": [
                *plan["workflow"]["nodes"],
                {
                    "node_id": "localizer",
                    "component_ref": "algorithm.spatial.rgbd_target_localizer",
                    "component_version": "0.1.0",
                    "parameters": {
                        "minimum_confidence": 0.65,
                        "calibration_id": "mock-camera-to-mujoco-arm-v1",
                        "simulated_detection_u_px": 320.0,
                        "simulated_detection_v_px": 240.0,
                        "simulated_depth_m": 0.45,
                        "target_ttl_ms": 300,
                    },
                },
                {
                    "node_id": "planner",
                    "component_ref": "algorithm.motion.cartesian_point_to_point",
                    "component_version": "0.1.0",
                    "parameters": {},
                },
                {
                    "node_id": "safety_gate",
                    "component_ref": "algorithm.motion.safety_gate",
                    "component_version": "0.1.0",
                    "parameters": {
                        "command_ttl_ms": 300,
                        "velocity_limit": 0.12,
                        "command_authorized": 1,
                        "emergency_stop": 0,
                        "simulated_command_age_ms": 0,
                    },
                },
                {
                    "node_id": "arm",
                    "component_ref": "hardware.arm.mujoco",
                    "component_version": "0.1.0",
                    "parameters": {
                        "command_timeout_ms": 700,
                        "max_velocity_scale": 0.15,
                        "simulation_steps": 800,
                    },
                },
            ],
            "links": [
                {
                    "source": {"node_id": "camera", "port": "color"},
                    "target": {"node_id": "detector", "port": "image"},
                },
                {
                    "source": {"node_id": "detector", "port": "detections"},
                    "target": {"node_id": "localizer", "port": "detections"},
                },
                {
                    "source": {"node_id": "camera", "port": "depth"},
                    "target": {"node_id": "localizer", "port": "depth"},
                },
                {
                    "source": {"node_id": "camera", "port": "camera_info"},
                    "target": {"node_id": "localizer", "port": "camera_info"},
                },
                {
                    "source": {"node_id": "localizer", "port": "target_pose"},
                    "target": {"node_id": "planner", "port": "target_pose"},
                },
                {
                    "source": {"node_id": "planner", "port": "command"},
                    "target": {"node_id": "safety_gate", "port": "command"},
                },
                {
                    "source": {"node_id": "safety_gate", "port": "safe_command"},
                    "target": {"node_id": "arm", "port": "command"},
                },
            ],
        }
    )
    plan["artifacts"] = [
        {
            "artifact_id": "simulation.mujoco.arm.runtime",
            "kind": "runtime_package",
            "version": "0.4.0",
            "locator": "robot-mujoco-arm-runtime",
            "digest": "sha256:" + "a" * 64,
            "architecture": "x86_64",
            "variant_id": "windows-amd64-python",
        },
        {
            "artifact_id": "calibration.camera.mock-to-mujoco-arm",
            "kind": "calibration_profile",
            "version": "1.0.0",
            "locator": "mock-camera-to-mujoco-arm-v1",
            "digest": "sha256:" + hashlib.sha256(calibration_profile_bytes()).hexdigest(),
            "architecture": None,
        },
    ]
    return plan


def legacy_mujoco_spatial_plan(*, desired_state: str = "running") -> dict[str, Any]:
    """还原0.2修订的内嵌标定快照，用于验证历史版本仍可回退执行。"""

    plan = mujoco_vision_arm_plan(desired_state=desired_state)
    plan["workflow"]["version"] = "0.2.0"
    plan["artifacts"] = [plan["artifacts"][0]]
    localizer = next(
        node
        for node in plan["workflow"]["nodes"]
        if node["component_ref"] == "algorithm.spatial.rgbd_target_localizer"
    )
    localizer["parameters"].update(
        {
            "camera_fx_px": 610.0,
            "camera_fy_px": 611.0,
            "camera_cx_px": 319.0,
            "camera_cy_px": 241.0,
            "camera_to_base_x_m": 0.2,
            "camera_to_base_y_m": 0.15,
            "camera_to_base_z_m": 0.05,
            "camera_to_base_roll_rad": 0.01,
            "camera_to_base_pitch_rad": 0.02,
            "camera_to_base_yaw_rad": 0.03,
        }
    )
    return plan


def managed_mujoco_inventory(runtime_root: Path) -> list[dict[str, Any]]:
    release = runtime_root / "releases" / "mujoco-release"
    package = release / "robot_mujoco_arm"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    calibration_release = (
        runtime_root.resolve().parent
        / "calibrations"
        / "releases"
        / "calibration.camera.mock-to-mujoco-arm"
        / "1.0.0-test"
    )
    calibration_release.mkdir(parents=True, exist_ok=True)
    (calibration_release / "profile.json").write_bytes(calibration_profile_bytes())
    return [
        {
            "artifact_id": "simulation.mujoco.arm.runtime",
            "kind": "runtime_package",
            "locator": "robot-mujoco-arm-runtime",
            "version": "0.4.0",
            "digest": "sha256:" + "a" * 64,
            "install_path": str(release),
            "active": True,
        },
        {
            "artifact_id": "calibration.camera.mock-to-mujoco-arm",
            "kind": "calibration_profile",
            "locator": "mock-camera-to-mujoco-arm-v1",
            "version": "1.0.0",
            "digest": "sha256:" + hashlib.sha256(calibration_profile_bytes()).hexdigest(),
            "install_path": str(calibration_release),
            "active": True,
        },
    ]


def windows_webcam_yolox_mujoco_plan() -> dict[str, Any]:
    return {
        "plan_id": "plan-windows-webcam-yolox-mujoco-01",
        "target_agent_id": "simulation-windows-01",
        "desired_state": "running",
        "workflow": {
            "workflow_id": "workflow.windows_webcam_yolox_mujoco",
            "version": "0.1.5",
            "name": "Windows摄像头识别手机→MuJoCo机械臂",
            "nodes": [
                {
                    "node_id": "camera",
                    "component_ref": "hardware.camera.windows-webcam",
                    "component_version": "0.1.0",
                    "parameters": {
                        "camera_index": "windows-camera-0",
                        "width": 640,
                        "height": 480,
                        "fps": 15,
                    },
                },
                {
                    "node_id": "detector",
                    "component_ref": "algorithm.vision.yolox_onnx",
                    "component_version": "0.1.0",
                    "parameters": {
                        "target_label": "cell phone",
                        "confidence": 0.3,
                        "nms": 0.45,
                    },
                },
                {
                    "node_id": "target_mapper",
                    "component_ref": "algorithm.motion.detection_to_sim_target",
                    "component_version": "0.1.0",
                    "parameters": {
                        "minimum_confidence": 0.5,
                        "target_x_m": 0.35,
                        "horizontal_span_m": 0.4,
                        "vertical_span_m": 0.3,
                        "center_z_m": 0.4,
                    },
                },
                {
                    "node_id": "safety_gate",
                    "component_ref": "algorithm.motion.safety_gate",
                    "component_version": "0.1.0",
                    "parameters": {
                        "command_ttl_ms": 300,
                        "velocity_limit": 0.2,
                        "command_authorized": 1,
                        "emergency_stop": 0,
                        "simulated_command_age_ms": 0,
                    },
                },
                {
                    "node_id": "arm",
                    "component_ref": "hardware.arm.mujoco",
                    "component_version": "0.1.0",
                    "parameters": {
                        "command_timeout_ms": 700,
                        "max_velocity_scale": 0.15,
                        "simulation_steps": 600,
                    },
                },
            ],
            "links": [
                {
                    "source": {"node_id": "camera", "port": "color"},
                    "target": {"node_id": "detector", "port": "image"},
                },
                {
                    "source": {"node_id": "detector", "port": "detections"},
                    "target": {"node_id": "target_mapper", "port": "detections"},
                },
                {
                    "source": {"node_id": "target_mapper", "port": "command"},
                    "target": {"node_id": "safety_gate", "port": "command"},
                },
                {
                    "source": {"node_id": "safety_gate", "port": "safe_command"},
                    "target": {"node_id": "arm", "port": "command"},
                },
            ],
        },
        "artifacts": [
            {
                "artifact_id": "hardware.camera.windows-webcam.edge-plugin",
                "kind": "edge_plugin",
                "version": "0.1.2",
                "locator": "robot-hardware-windows-webcam",
                "digest": "sha256:" + "b" * 64,
                "architecture": "x86_64",
            },
            {
                "artifact_id": "simulation.mujoco.arm.runtime",
                "kind": "runtime_package",
                "version": "0.4.0",
                "locator": "robot-mujoco-arm-runtime",
                "digest": "sha256:" + "a" * 64,
                "architecture": "x86_64",
            },
            {
                "artifact_id": "runtime.windows-webcam-yolox-mujoco.runtime-package",
                "kind": "runtime_package",
                "version": "0.2.0",
                "locator": "robot-windows-webcam-yolox-mujoco-runtime",
                "digest": "sha256:" + "c" * 64,
                "architecture": "x86_64",
            },
        ],
    }


def managed_windows_vision_inventory(runtime_root: Path) -> list[dict[str, Any]]:
    inventory = managed_mujoco_inventory(runtime_root)[:1]
    vision_release = runtime_root / "releases" / "windows-vision-release"
    vision_package = vision_release / "robot_windows_webcam_yolox_mujoco"
    (vision_package / "models").mkdir(parents=True, exist_ok=True)
    (vision_package / "__init__.py").write_text("", encoding="utf-8")
    (vision_package / "models" / "yolox_nano.onnx").write_bytes(b"test-model")
    inventory.extend(
        [
            {
                "artifact_id": "hardware.camera.windows-webcam.edge-plugin",
                "kind": "edge_plugin",
                "locator": "robot-hardware-windows-webcam",
                "version": "0.1.2",
                "digest": "sha256:" + "b" * 64,
                "install_path": str(runtime_root.parent / "plugins" / "webcam"),
                "active": True,
            },
            {
                "artifact_id": "runtime.windows-webcam-yolox-mujoco.runtime-package",
                "kind": "runtime_package",
                "locator": "robot-windows-webcam-yolox-mujoco-runtime",
                "version": "0.2.0",
                "digest": "sha256:" + "c" * 64,
                "install_path": str(vision_release),
                "active": True,
            },
        ]
    )
    return inventory


def real_plan(*, desired_state: str = "running") -> dict[str, Any]:
    return {
        "plan_id": "plan-real-01",
        "target_agent_id": "edge-01",
        "desired_state": desired_state,
        "artifacts": [
            {
                "artifact_id": "hardware.camera.realsense.edge-plugin",
                "kind": "edge_plugin",
                "version": "0.1.0",
                "locator": "robot-hardware-realsense",
                "digest": "sha256:4dabedc238dd67d03283656db2d55011b59fc2392273065e6557a9ca998f2392",
                "architecture": None,
            },
            {
                "artifact_id": "runtime.realsense-yolox.runtime-image",
                "kind": "container_image",
                "version": "0.1.3",
                "locator": "robot-platform/realsense-yolox-runtime:0.1.3",
                "digest": None,
                "architecture": "aarch64",
            },
            {
                "artifact_id": "algorithm.vision.yolo.tensorrt-engine",
                "kind": "tensorrt_engine",
                "version": "0.1.0",
                "locator": (
                    "/data/robot-platform/cache/tensorrt/"
                    "yolox-nano-c789161e-sm87-trt10.3-fp16/yolox_nano-fp16.engine"
                ),
                "digest": (
                    "sha256:719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17"
                ),
                "architecture": "aarch64",
            },
        ],
        "workflow": {
            "workflow_id": "workflow.realsense_yolox",
            "version": "0.1.0",
            "name": "RealSense目标检测",
            "nodes": [
                {
                    "node_id": "camera",
                    "component_ref": "hardware.camera.realsense",
                    "component_version": "0.1.0",
                    "parameters": {
                        "serial": "125322060182",
                        "width": 640,
                        "height": 480,
                        "fps": 30,
                    },
                },
                {
                    "node_id": "detector",
                    "component_ref": "algorithm.vision.yolo",
                    "component_version": "0.1.0",
                    "parameters": {
                        "model_id": "yolox.nano.coco",
                        "engine_path": (
                            "/data/robot-platform/cache/tensorrt/"
                            "yolox-nano-c789161e-sm87-trt10.3-fp16/yolox_nano-fp16.engine"
                        ),
                        "confidence": 0.3,
                        "nms": 0.45,
                        "duration_seconds": 3600,
                    },
                },
            ],
            "links": [
                {
                    "source": {"node_id": "camera", "port": "color"},
                    "target": {"node_id": "detector", "port": "image"},
                }
            ],
        },
    }


def real_artifact_inventory() -> list[dict[str, Any]]:
    return [
        {
            "kind": "edge_plugin",
            "locator": "robot-hardware-realsense",
            "version": "0.1.0",
        },
        {
            "kind": "container_image",
            "locator": "robot-platform/realsense-yolox-runtime:0.1.3",
            "version": "0.1.3",
        },
        {
            "kind": "tensorrt_engine",
            "locator": (
                "/data/robot-platform/cache/tensorrt/"
                "yolox-nano-c789161e-sm87-trt10.3-fp16/yolox_nano-fp16.engine"
            ),
            "digest": ("sha256:719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17"),
        },
    ]


class FakeDeploymentClient:
    """记录端侧协调器回报的状态。"""

    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self.plans = plans
        self.reports: list[tuple[str, str, str, dict[str, float]]] = []
        self.previews: list[tuple[str, bytes, str]] = []
        self.preview_response: dict[str, Any] = {}

    def list_deployments(self, agent_id: str) -> list[dict[str, Any]]:
        assert agent_id == "edge-01"
        return self.plans

    def report_deployment_status(
        self,
        plan_id: str,
        status: str,
        *,
        message: str = "",
        metrics: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        self.reports.append((plan_id, status, message, metrics or {}))
        return {}

    def upload_deployment_preview(
        self,
        plan_id: str,
        image: bytes,
        *,
        content_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        self.previews.append((plan_id, image, content_type))
        return self.preview_response


class FakeDocker:
    """模拟一个由平台独占管理的Docker容器。"""

    def __init__(self) -> None:
        self.exists = False
        self.running = False
        self.exit_code = 0
        self.image_exists = True
        self.owned = True
        self.invalid_inspection: str | None = None
        self.missing_state = False
        self.state_error = ""
        self.fail_removal = False
        self.fail_run = False
        self.fail_logs = False
        self.fail_stop = False
        self.fail_list = False
        self.logs_content: str | None = None
        self.other_managed: dict[str, dict[str, object]] = {}
        self.commands: list[tuple[str, ...]] = []

    def inspection(self) -> str:
        labels = {
            "robot.platform.managed": "true",
            "robot.platform.plan_id": "plan-real-01",
        }
        if not self.owned:
            labels["robot.platform.managed"] = "false"
        inspection: dict[str, object] = {
            "Config": {
                "Image": "robot-platform/realsense-yolox-runtime:0.1.3",
                "Labels": labels,
            }
        }
        if not self.missing_state:
            inspection["State"] = {
                "Running": self.running,
                "ExitCode": self.exit_code,
                "Error": self.state_error,
            }
        return json.dumps([inspection])

    def __call__(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command = tuple(arguments)
        self.commands.append(command)
        if command[:3] == ("docker", "image", "inspect"):
            return subprocess.CompletedProcess(command, 0 if self.image_exists else 1, "[]", "")
        if command[:2] == ("docker", "ps"):
            if self.fail_list:
                return subprocess.CompletedProcess(command, 1, "", "list failed")
            return subprocess.CompletedProcess(command, 0, "\n".join(self.other_managed), "")
        if command[:3] == ("docker", "container", "inspect"):
            target = command[3]
            if target in self.other_managed:
                return subprocess.CompletedProcess(
                    command, 0, json.dumps([self.other_managed[target]]), ""
                )
            if not self.exists:
                return subprocess.CompletedProcess(command, 1, "", "not found")
            return subprocess.CompletedProcess(
                command, 0, self.invalid_inspection or self.inspection(), ""
            )
        if command[:2] == ("docker", "run"):
            if self.fail_run:
                return subprocess.CompletedProcess(command, 1, "", "run failed")
            self.exists = True
            self.running = True
            return subprocess.CompletedProcess(command, 0, "container-id", "")
        if command[:2] == ("docker", "logs"):
            if self.fail_logs:
                return subprocess.CompletedProcess(command, 1, "", "logs failed")
            if self.logs_content is not None:
                return subprocess.CompletedProcess(command, 0, self.logs_content, "")
            output = json.dumps(
                {
                    "state": "running",
                    "configured_fps": 30,
                    "captured_frames": 150,
                    "inferred_frames": 140,
                    "dropped_frames": 0,
                    "inference_mean_ms": 5.9,
                    "pipeline_p95_ms": 16.8,
                }
            )
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[:2] == ("docker", "stop"):
            if self.fail_stop:
                return subprocess.CompletedProcess(command, 1, "", "stop failed")
            self.running = False
            return subprocess.CompletedProcess(command, 0, "stopped", "")
        if command[:3] == ("docker", "container", "rm"):
            if self.fail_removal:
                return subprocess.CompletedProcess(command, 1, "", "remove failed")
            self.exists = False
            return subprocess.CompletedProcess(command, 0, "removed", "")
        raise AssertionError(f"未处理命令：{command}")


def test_manager_starts_plan_and_refreshes_metrics_without_duplicate_start() -> None:
    client = FakeDeploymentClient([mock_plan()])
    manager = EdgeDeploymentManager()

    manager.reconcile(client, "edge-01")
    manager.reconcile(client, "edge-01")

    assert [report[1] for report in client.reports] == ["starting", "running", "running"]
    assert client.reports[1][3]["configured_fps"] == 15.0
    assert client.reports[2][3]["runtime_ticks"] == 2.0


def test_manager_rejects_non_allowlisted_plan_without_crashing_agent() -> None:
    plan = mock_plan()
    plan["workflow"]["nodes"][1]["component_ref"] = "algorithm.vision.unknown"
    client = FakeDeploymentClient([plan])

    EdgeDeploymentManager().reconcile(client, "edge-01")

    assert [report[1] for report in client.reports] == ["starting", "failed"]
    assert "没有匹配" in client.reports[-1][2]


def test_manager_applies_stop_instruction() -> None:
    class FakeRuntime:
        plan_id = "plan-mock-01"

        def __init__(self) -> None:
            self.stopped = False

        def start(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("running", "运行中", {})

        def tick(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("running", "运行中", {})

        def stop(self) -> None:
            self.stopped = True

    runtime = FakeRuntime()
    manager = EdgeDeploymentManager(runtime_factory=lambda _plan: runtime)
    running_client = FakeDeploymentClient([mock_plan()])
    manager.reconcile(running_client, "edge-01")
    stopped_client = FakeDeploymentClient([mock_plan(desired_state="stopped")])

    manager.reconcile(stopped_client, "edge-01")

    assert runtime.stopped is True
    assert stopped_client.reports[-1][1:] == ("stopped", "Runtime已停止并清理", {})


def test_real_runtime_starts_with_restricted_docker_arguments_and_reports_metrics() -> None:
    docker = FakeDocker()
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)

    started = runtime.start()
    observed = runtime.tick()

    run_command = next(command for command in docker.commands if command[:2] == ("docker", "run"))
    assert started.status == "running"
    assert observed.metrics["inferred_frames"] == 140.0
    assert "--privileged" not in run_command
    assert run_command[run_command.index("--network") : run_command.index("--network") + 2] == (
        "--network",
        "none",
    )
    assert "/dev:/dev" in run_command
    assert "robot.platform.plan_id=plan-real-01" in run_command
    assert "robot.platform.resource=hardware.camera.realsense" in run_command
    assert "robot.platform.device_selector=125322060182" in run_command


def test_real_runtime_rejects_second_container_using_realsense() -> None:
    docker = FakeDocker()
    docker.other_managed["old-container"] = {
        "Name": "/robot-plan-old",
        "Config": {
            "Image": "robot-platform/realsense-yolox-runtime:0.1.3",
            "Labels": {
                "robot.platform.managed": "true",
                "robot.platform.plan_id": "plan-old",
            },
        },
        "State": {"Running": True, "ExitCode": 0, "Error": ""},
    }
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)

    with pytest.raises(RuntimeCommandError, match="plan-old.*占用"):
        runtime.start()

    assert not any(command[:2] == ("docker", "run") for command in docker.commands)


def test_real_runtime_allows_unrelated_managed_container() -> None:
    docker = FakeDocker()
    docker.other_managed["other-container"] = {
        "Config": {
            "Image": "robot-platform/another-runtime:1.0.0",
            "Labels": {
                "robot.platform.managed": "true",
                "robot.platform.plan_id": "plan-other",
                "robot.platform.resource": "hardware.arm.sr101",
            },
        },
        "State": {"Running": True, "ExitCode": 0, "Error": ""},
    }

    snapshot = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker).start()

    assert snapshot.status == "running"
    assert any(command[:2] == ("docker", "run") for command in docker.commands)


def test_real_runtime_adopts_owned_running_container_and_can_stop_it() -> None:
    docker = FakeDocker()
    docker.exists = True
    docker.running = True
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)

    snapshot = runtime.start()
    runtime.stop()

    assert snapshot.status == "running"
    assert not any(command[:2] == ("docker", "run") for command in docker.commands)
    assert any(command[:2] == ("docker", "stop") for command in docker.commands)
    assert docker.exists is False


def test_real_runtime_reports_unexpected_container_exit() -> None:
    docker = FakeDocker()
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)
    runtime.start()
    docker.running = False
    docker.exit_code = 7

    snapshot = runtime.tick()

    assert snapshot.status == "failed"
    assert "exit_code=7" in snapshot.message


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("serial", "bad serial", "序列号"),
        ("engine_path", "/tmp/model.engine", "受管缓存目录"),
        (
            "engine_path",
            "/data/robot-platform/cache/tensorrt/../outside.engine",
            "受管缓存目录",
        ),
        ("model_id", "another-model", "yolox.nano.coco"),
        ("confidence", 1.2, "confidence"),
    ],
)
def test_real_runtime_rejects_untrusted_parameters(field: str, value: object, message: str) -> None:
    plan = real_plan()
    target = plan["workflow"]["nodes"][0 if field == "serial" else 1]["parameters"]
    target[field] = value

    with pytest.raises(UnsupportedDeploymentPlan, match=message):
        DockerRealSenseYoloRuntime(plan)


def test_runtime_factory_selects_real_and_mock_implementations(tmp_path: Path) -> None:
    assert create_workflow_runtime(mock_plan()).plan_id == "plan-mock-01"
    assert create_workflow_runtime(mock_pipeline_plan()).plan_id == "plan-mock-pipeline-01"
    assert isinstance(create_workflow_runtime(mock_safe_vision_arm_plan()), MockWorkflowRuntime)
    runtime_root = tmp_path / "runtime-packages"
    mujoco_inventory = managed_mujoco_inventory(runtime_root)
    assert isinstance(
        create_workflow_runtime(
            mujoco_vision_arm_plan(),
            artifact_inventory=lambda: mujoco_inventory,
            runtime_package_root=runtime_root,
        ),
        MujocoWorkflowRuntime,
    )
    assert isinstance(
        create_workflow_runtime(
            real_plan(), artifact_inventory=real_artifact_inventory, machine="aarch64"
        ),
        DockerRealSenseYoloRuntime,
    )


def test_runtime_factory_rejects_missing_or_changed_real_artifacts() -> None:
    missing_contract = real_plan()
    missing_contract.pop("artifacts")
    with pytest.raises(UnsupportedDeploymentPlan, match="缺少端侧制品契约"):
        create_workflow_runtime(
            missing_contract,
            artifact_inventory=real_artifact_inventory,
            machine="aarch64",
        )

    changed_inventory = real_artifact_inventory()
    changed_inventory[2]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(UnsupportedDeploymentPlan, match="摘要不匹配"):
        create_workflow_runtime(
            real_plan(),
            artifact_inventory=lambda: changed_inventory,
            machine="aarch64",
        )


def test_runtime_factory_rejects_wrong_architecture_and_allows_legacy_stop() -> None:
    with pytest.raises(UnsupportedDeploymentPlan, match="要求aarch64架构"):
        create_workflow_runtime(
            real_plan(),
            artifact_inventory=real_artifact_inventory,
            machine="x86_64",
        )

    stopped = real_plan(desired_state="stopped")
    stopped.pop("artifacts")
    runtime = create_workflow_runtime(
        stopped,
        artifact_inventory=lambda: (_ for _ in ()).throw(AssertionError()),
        machine="aarch64",
    )
    assert isinstance(runtime, DockerRealSenseYoloRuntime)


def test_three_node_mock_pipeline_reports_filter_metrics() -> None:
    runtime = MockWorkflowRuntime(mock_pipeline_plan())

    snapshot = runtime.start()

    assert snapshot.status == "running"
    assert snapshot.message == "三节点模拟工作流运行中"
    assert snapshot.metrics["filter_min_confidence"] == 0.6
    assert snapshot.metrics["filtered_detections_emitted"] == 1.0
    runtime.stop()


def test_mock_vision_arm_reports_motion_metrics_and_safe_stop() -> None:
    runtime = MockWorkflowRuntime(mock_vision_arm_plan())

    snapshot = runtime.start()

    assert snapshot.status == "running"
    assert snapshot.message == "视觉引导模拟机械臂运行中"
    assert snapshot.metrics["motion_commands_emitted"] == 1.0
    assert snapshot.metrics["motion_commands_accepted"] == 1.0
    assert snapshot.metrics["target_minimum_confidence"] == 0.65
    assert snapshot.metrics["command_timeout_ms"] == 700.0
    assert snapshot.metrics["max_velocity_scale"] == 0.15
    assert snapshot.metrics["actuator_faults"] == 0.0
    assert runtime.safety_state == "command_enabled"

    stopped = runtime.stop()

    assert runtime.safety_state == "safe_stopped"
    assert stopped.message == "模拟机械臂已进入安全停止状态"
    assert stopped.metrics["actuator_safe_stop"] == 1.0


def test_mujoco_runtime_executes_managed_simulation_and_stops_safely(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime-packages"
    inventory = managed_mujoco_inventory(runtime_root)

    class FakeTarget:
        def __init__(self, x: float, y: float, z: float) -> None:
            self.coordinates = (x, y, z)

    class FakeMujocoArm:
        def __init__(self, *, safety_state: str) -> None:
            self.safety_state = safety_state

        def enable_commands(self) -> None:
            self.safety_state = "command_enabled"

        def emergency_stop(self) -> None:
            self.safety_state = "emergency_stopped"

        def reset_safety(self, *, authorized: bool, emergency_stop_active: bool = False) -> bool:
            self.safety_state = (
                "emergency_stopped"
                if emergency_stop_active
                else "command_enabled"
                if authorized
                else "unauthorized"
            )
            return authorized and not emergency_stop_active

        def command_target(self, target: FakeTarget, *, simulation_steps: int) -> SimpleNamespace:
            assert target.coordinates == (0.25, 0.1, 0.45)
            assert self.safety_state == "command_enabled"
            return SimpleNamespace(
                position_error_m=0.008,
                target_reached=True,
                simulation_steps=simulation_steps,
                end_effector=(0.344, 0.048, 0.396),
            )

        def render_png(self, *, width: int, height: int) -> bytes:
            assert (width, height) == (480, 360)
            return b"\x89PNG\r\n\x1a\npreviewIEND\xaeB`\x82"

    runtime = MujocoWorkflowRuntime(
        mujoco_vision_arm_plan(),
        installed_artifacts=inventory,
        runtime_package_root=runtime_root,
        runtime_loader=lambda _path: (FakeMujocoArm, FakeTarget),
    )

    snapshot = runtime.start()

    assert snapshot.status == "running"
    assert snapshot.message == "MuJoCo视觉引导机械臂仿真运行中"
    assert snapshot.metrics["motion_commands_accepted"] == 1.0
    assert snapshot.metrics["mujoco_target_reached"] == 1.0
    assert snapshot.metrics["mujoco_position_error_m"] == 0.008
    assert snapshot.metrics["mujoco_simulation_steps"] == 800.0
    assert snapshot.metrics["target_pose_valid"] == 1.0
    assert snapshot.metrics["target_pose_x_m"] == pytest.approx(0.25)
    assert snapshot.metrics["target_pose_y_m"] == pytest.approx(0.1)
    assert snapshot.metrics["target_pose_z_m"] == pytest.approx(0.45)
    assert snapshot.metrics["target_pose_ttl_ms"] == 300.0
    assert runtime.safety_state == "command_enabled"
    assert runtime.preview_content_type == "image/png"
    assert runtime.read_preview() == b"\x89PNG\r\n\x1a\npreviewIEND\xaeB`\x82"
    runtime._arm_runtime.render_png = lambda **_kwargs: b"invalid"
    assert runtime.read_preview() is None

    def fail_preview(**_kwargs: int) -> bytes:
        raise RuntimeError("renderer unavailable")

    runtime._arm_runtime.render_png = fail_preview
    assert runtime.read_preview() is None

    runtime.emergency_stop()
    assert runtime.safety_state == "emergency_stopped"
    assert runtime.reset_safety(authorized=True) is True
    runtime.inject_safety_fault("test_fault")
    assert runtime.safety_state == "fault_latched"
    assert runtime.reset_safety(authorized=True) is True

    stopped = runtime.stop()

    assert stopped.message == "MuJoCo机械臂已进入安全停止状态"
    assert stopped.metrics["actuator_safe_stop"] == 1.0
    assert runtime.safety_state == "safe_stopped"


def test_windows_webcam_yolox_runtime_closes_real_input_to_safe_simulation(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime-packages"
    inventory = managed_windows_vision_inventory(runtime_root)
    created: dict[str, Any] = {}

    class FakeDetection:
        confidence = 0.91
        center = (480.0, 120.0)

    class FakeVisionRuntime:
        def __init__(self, **config: Any) -> None:
            created["vision_config"] = config
            created["vision"] = self
            self.started = False
            self.stopped = False
            self.target_enabled = True

        def start(self) -> None:
            self.started = True

        def detect(self) -> SimpleNamespace:
            target = FakeDetection() if self.target_enabled else None
            return SimpleNamespace(
                width=640,
                height=480,
                detections=((target,) if target is not None else ()),
                target=target,
                inference_ms=12.5,
                frame_sequence=1,
            )

        def compose_preview(self, arm_png: bytes) -> bytes:
            assert arm_png.startswith(b"\x89PNG")
            return b"\xff\xd8combined-preview\xff\xd9"

        def snapshot_preview_frame(self) -> bytes:
            return b"camera-snapshot"

        def compose_preview_rgb(self, camera_frame: bytes, arm_pixels: bytes) -> bytes:
            assert camera_frame == b"camera-snapshot"
            assert arm_pixels == b"arm-rgb"
            return b"\xff\xd8snapshot-preview\xff\xd9"

        def stop(self) -> None:
            self.stopped = True

    class FakeTarget:
        def __init__(self, x: float, y: float, z: float) -> None:
            self.coordinates = (x, y, z)

    class FakeMujocoArm:
        timestep_s = 0.002

        def __init__(self, *, safety_state: str) -> None:
            self.safety_state = safety_state
            created["arm"] = self

        def enable_commands(self) -> None:
            self.safety_state = "command_enabled"

        def emergency_stop(self) -> None:
            self.safety_state = "emergency_stopped"

        def set_target(self, target: FakeTarget) -> None:
            created["target"] = target.coordinates

        def track_target(self, target: FakeTarget) -> SimpleNamespace:
            created["target"] = target.coordinates
            return SimpleNamespace(
                position_error_m=0.009,
                target_reached=True,
                simulation_steps=0,
                end_effector=target.coordinates,
            )

        def advance(
            self, *, simulation_steps: int, velocity_scale: float
        ) -> SimpleNamespace:
            created["simulation_steps"] = simulation_steps
            created["velocity_scale"] = velocity_scale
            return SimpleNamespace(
                position_error_m=0.08,
                target_reached=False,
                simulation_steps=simulation_steps,
                end_effector=(0.1, 0.0, 0.3),
            )

        def hold_position(self) -> None:
            created["held"] = True

        def render_png(self, *, width: int, height: int) -> bytes:
            assert (width, height) == (480, 360)
            return b"\x89PNG\r\n\x1a\narmIEND\xaeB`\x82"

        def snapshot_preview(self) -> str:
            return "arm-snapshot"

        def render_snapshot_rgb(
            self,
            snapshot: str,
            *,
            width: int,
            height: int,
        ) -> bytes:
            assert snapshot == "arm-snapshot"
            assert (width, height) == (480, 360)
            return b"arm-rgb"

        def close_preview(self) -> None:
            created["preview_closed"] = True

    runtime = WindowsWebcamYoloxMujocoRuntime(
        windows_webcam_yolox_mujoco_plan(),
        installed_artifacts=inventory,
        runtime_package_root=runtime_root,
        vision_loader=lambda _path: FakeVisionRuntime,
        mujoco_loader=lambda _path: (FakeMujocoArm, FakeTarget),
    )

    snapshot = runtime.start()

    assert created["vision_config"] == {
        "camera_index": 0,
        "width": 640,
        "height": 480,
        "fps": 15,
        "confidence": 0.3,
        "nms_threshold": 0.45,
        "target_label": "cell phone",
    }
    assert created["target"] == pytest.approx((0.35, -0.1, 0.475))
    assert snapshot.status == "running"
    assert snapshot.metrics["target_detected"] == 1.0
    assert snapshot.metrics["target_confidence"] == pytest.approx(0.91)
    assert snapshot.metrics["phone_detected"] == 1.0
    assert snapshot.metrics["motion_commands_accepted"] == 1.0
    assert snapshot.metrics["mujoco_target_reached"] == 1.0
    preview_payload = runtime.capture_preview()
    assert preview_payload == (b"camera-snapshot", "arm-snapshot")
    assert runtime.render_preview(preview_payload) == b"\xff\xd8snapshot-preview\xff\xd9"
    runtime.close_preview()
    assert created["preview_closed"] is True
    assert snapshot.metrics["mujoco_simulation_steps"] == 0.0
    assert snapshot.metrics["mujoco_realtime_tracking"] == 1.0
    assert "simulation_steps" not in created
    assert runtime.preview_content_type == "image/jpeg"
    assert runtime.read_preview() == b"\xff\xd8combined-preview\xff\xd9"

    created["vision"].target_enabled = False
    assert runtime._last_command_monotonic is not None
    runtime._last_command_monotonic -= 1.0
    timed_out = runtime.tick()

    assert timed_out.metrics["actuator_command_timed_out"] == 1.0
    assert timed_out.metrics["actuator_safe_stop"] == 1.0
    assert created["held"] is True
    still_timed_out = runtime.tick()
    assert still_timed_out.metrics["actuator_command_timed_out"] == 1.0
    assert still_timed_out.metrics["actuator_safe_stop"] == 1.0

    stopped = runtime.stop()

    assert stopped.message == "Windows视觉闭环与MuJoCo机械臂已安全停止"
    assert stopped.metrics["actuator_safe_stop"] == 1.0
    assert runtime.safety_state == "safe_stopped"
    assert runtime._vision_runtime.stopped is True
    assert created["arm"].safety_state == "emergency_stopped"

    # Historical 0.1.3 revisions retain the dynamics-paced behavior while
    # 0.1.4+ explicitly selects low-latency kinematic tracking.
    legacy_plan = windows_webcam_yolox_mujoco_plan()
    legacy_plan["workflow"]["version"] = "0.1.3"
    legacy = WindowsWebcamYoloxMujocoRuntime(
        legacy_plan,
        installed_artifacts=inventory,
        runtime_package_root=runtime_root,
        vision_loader=lambda _path: FakeVisionRuntime,
        mujoco_loader=lambda _path: (FakeMujocoArm, FakeTarget),
    )

    legacy_snapshot = legacy.start()

    assert legacy_snapshot.metrics["mujoco_realtime_tracking"] == 0.0
    assert legacy_snapshot.metrics["mujoco_steps_per_tick"] == 33.0
    assert legacy_snapshot.metrics["mujoco_physics_rate_hz"] == 500.0
    assert created["simulation_steps"] == 33
    assert created["velocity_scale"] == pytest.approx(0.15)
    legacy.stop()


def test_windows_webcam_yolox_runtime_waits_when_phone_is_absent(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"

    class FakeVisionRuntime:
        def __init__(self, **_config: Any) -> None:
            pass

        def start(self) -> None:
            pass

        def detect(self) -> SimpleNamespace:
            return SimpleNamespace(
                width=640,
                height=480,
                detections=(),
                target=None,
                inference_ms=8.0,
                frame_sequence=1,
            )

        def stop(self) -> None:
            pass

    class FakeArm:
        def __init__(self, *, safety_state: str) -> None:
            self.safety_state = safety_state

        def enable_commands(self) -> None:
            self.safety_state = "command_enabled"

        def emergency_stop(self) -> None:
            self.safety_state = "emergency_stopped"

    runtime = WindowsWebcamYoloxMujocoRuntime(
        windows_webcam_yolox_mujoco_plan(),
        installed_artifacts=managed_windows_vision_inventory(runtime_root),
        runtime_package_root=runtime_root,
        vision_loader=lambda _path: FakeVisionRuntime,
        mujoco_loader=lambda _path: (FakeArm, object),
    )

    snapshot = runtime.start()

    assert snapshot.message == "Windows摄像头与YOLOX运行中，等待手机进入画面"
    assert snapshot.metrics["target_detected"] == 0.0
    assert snapshot.metrics["phone_detected"] == 0.0
    assert snapshot.metrics["motion_commands_emitted"] == 0.0
    assert snapshot.metrics["motion_commands_accepted"] == 0.0
    runtime.stop()


def test_windows_webcam_yolox_runtime_rejects_changed_artifact(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"
    inventory = managed_windows_vision_inventory(runtime_root)
    inventory[-1]["digest"] = "sha256:" + "d" * 64

    with pytest.raises(UnsupportedDeploymentPlan, match="摘要不匹配"):
        WindowsWebcamYoloxMujocoRuntime(
            windows_webcam_yolox_mujoco_plan(),
            installed_artifacts=inventory,
            runtime_package_root=runtime_root,
        )


def test_windows_webcam_runtime_rejects_model_substitution(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"
    plan = windows_webcam_yolox_mujoco_plan()
    plan["workflow"]["nodes"][1]["parameters"]["model_id"] = "untrusted.model"

    with pytest.raises(UnsupportedDeploymentPlan, match="固定的YOLOX Nano COCO"):
        WindowsWebcamYoloxMujocoRuntime(
            plan,
            installed_artifacts=managed_windows_vision_inventory(runtime_root),
            runtime_package_root=runtime_root,
        )


def test_stopped_windows_webcam_plan_does_not_require_removed_artifacts(tmp_path: Path) -> None:
    plan = windows_webcam_yolox_mujoco_plan()
    plan["desired_state"] = "stopped"

    runtime = create_workflow_runtime(
        plan,
        artifact_inventory=lambda: [],
        runtime_package_root=tmp_path / "runtime-packages",
    )

    assert isinstance(runtime, WindowsWebcamYoloxMujocoRuntime)
    assert runtime.stop().metrics["actuator_safe_stop"] == 1.0


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("camera-x", "必须选择"),
        (None, "格式不合法"),
        ("16", "必须位于"),
    ],
)
def test_windows_camera_index_contract_rejects_invalid_values(
    value: object,
    message: str,
) -> None:
    with pytest.raises(UnsupportedDeploymentPlan, match=message):
        _camera_index_parameter({"camera_index": value})

    assert _camera_index_parameter({"camera_index": 2}) == 2


def test_windows_vision_artifact_contract_reports_actionable_failures(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"
    plan = windows_webcam_yolox_mujoco_plan()
    inventory = managed_windows_vision_inventory(runtime_root)

    with pytest.raises(UnsupportedDeploymentPlan, match="缺少端侧制品契约"):
        _resolve_windows_vision_artifacts(
            {"artifacts": None},
            installed_artifacts=inventory,
            runtime_package_root=runtime_root,
        )

    incomplete = {"artifacts": plan["artifacts"][:-1]}
    with pytest.raises(UnsupportedDeploymentPlan, match="制品集合不符合白名单"):
        _resolve_windows_vision_artifacts(
            incomplete,
            installed_artifacts=inventory,
            runtime_package_root=runtime_root,
        )

    invalid = json.loads(json.dumps(plan))
    invalid["artifacts"][0]["locator"] = "untrusted-plugin"
    with pytest.raises(UnsupportedDeploymentPlan, match="制品契约字段不合法"):
        _resolve_windows_vision_artifacts(
            invalid,
            installed_artifacts=inventory,
            runtime_package_root=runtime_root,
        )

    with pytest.raises(UnsupportedDeploymentPlan, match="缺少已激活制品"):
        _resolve_windows_vision_artifacts(
            plan,
            installed_artifacts=inventory[:-1],
            runtime_package_root=runtime_root,
        )

    outside = managed_windows_vision_inventory(runtime_root)
    outside[-1]["install_path"] = str(tmp_path / "outside")
    with pytest.raises(UnsupportedDeploymentPlan, match="不在受管发布目录"):
        _resolve_windows_vision_artifacts(
            plan,
            installed_artifacts=outside,
            runtime_package_root=runtime_root,
        )

    incomplete_install = managed_windows_vision_inventory(runtime_root)
    empty_release = runtime_root / "releases" / "empty"
    empty_release.mkdir(parents=True)
    incomplete_install[-1]["install_path"] = str(empty_release)
    with pytest.raises(UnsupportedDeploymentPlan, match="安装内容不完整"):
        _resolve_windows_vision_artifacts(
            plan,
            installed_artifacts=incomplete_install,
            runtime_package_root=runtime_root,
        )


def test_windows_vision_loader_accepts_only_managed_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "robot_windows_webcam_yolox_mujoco"
    package.mkdir()
    (package / "__init__.py").write_text(
        "class WindowsWebcamYoloxRuntime:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "robot_windows_webcam_yolox_mujoco", raising=False)

    runtime_type = _load_windows_vision_runtime_api(tmp_path)

    assert runtime_type.__name__ == "WindowsWebcamYoloxRuntime"
    assert _load_windows_vision_runtime_api(tmp_path) is runtime_type

    unmanaged = SimpleNamespace(
        __file__=str(tmp_path.parent / "unmanaged" / "__init__.py"),
        WindowsWebcamYoloxRuntime=object,
    )
    monkeypatch.setitem(sys.modules, "robot_windows_webcam_yolox_mujoco", unmanaged)
    with pytest.raises(RuntimeCommandError, match="非受管路径"):
        _load_windows_vision_runtime_api(tmp_path)


def test_windows_webcam_runtime_preview_and_emergency_stop_are_bounded(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"

    class IdleVision:
        def __init__(self, **_config: Any) -> None:
            pass

        def start(self) -> None:
            pass

        def detect(self) -> SimpleNamespace:
            return SimpleNamespace(
                width=640,
                height=480,
                detections=(),
                target=None,
                inference_ms=1.0,
                frame_sequence=1,
            )

        def compose_preview(self, _arm_png: bytes) -> bytes:
            return b"invalid"

        def stop(self) -> None:
            pass

    class IdleArm:
        def __init__(self, *, safety_state: str) -> None:
            self.safety_state = safety_state

        def enable_commands(self) -> None:
            self.safety_state = "command_enabled"

        def emergency_stop(self) -> None:
            self.safety_state = "emergency_stopped"

        def render_png(self, **_size: int) -> bytes:
            return b"png"

    runtime = WindowsWebcamYoloxMujocoRuntime(
        windows_webcam_yolox_mujoco_plan(),
        installed_artifacts=managed_windows_vision_inventory(runtime_root),
        runtime_package_root=runtime_root,
        vision_loader=lambda _path: IdleVision,
        mujoco_loader=lambda _path: (IdleArm, object),
    )
    assert runtime.read_preview() is None
    runtime.start()
    assert runtime.read_preview() is None
    runtime.emergency_stop()
    assert runtime.safety_state == "emergency_stopped"
    assert runtime._arm_runtime.safety_state == "emergency_stopped"


def test_mujoco_runtime_preserves_legacy_0_2_calibration_snapshot(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"
    runtime = MujocoWorkflowRuntime(
        legacy_mujoco_spatial_plan(),
        installed_artifacts=managed_mujoco_inventory(runtime_root),
        runtime_package_root=runtime_root,
    )

    assert runtime._intrinsics == CameraIntrinsics(
        frame_id="camera_optical",
        width_px=640,
        height_px=480,
        fx_px=610.0,
        fy_px=611.0,
        cx_px=319.0,
        cy_px=241.0,
    )
    assert runtime._camera_to_base == RigidTransform(
        source_frame="camera_optical",
        target_frame="arm_base",
        calibration_id="mock-camera-to-mujoco-arm-v1",
        translation_m=(0.2, 0.15, 0.05),
        roll_rad=0.01,
        pitch_rad=0.02,
        yaw_rad=0.03,
    )


def test_mujoco_runtime_requires_managed_calibration_from_0_3(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"
    plan = mujoco_vision_arm_plan()
    plan["artifacts"] = [plan["artifacts"][0]]

    with pytest.raises(UnsupportedDeploymentPlan, match="制品集合不符合白名单"):
        MujocoWorkflowRuntime(
            plan,
            installed_artifacts=managed_mujoco_inventory(runtime_root),
            runtime_package_root=runtime_root,
        )


def test_mujoco_runtime_loads_api_from_managed_release(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"
    inventory = managed_mujoco_inventory(runtime_root)
    package_init = Path(str(inventory[0]["install_path"])) / "robot_mujoco_arm" / "__init__.py"
    package_init.write_text(
        """
from types import SimpleNamespace

class CartesianTarget:
    def __init__(self, x, y, z):
        self.coordinates = (x, y, z)

class MujocoArmRuntime:
    def __init__(self, *, safety_state):
        self.safety_state = safety_state

    def enable_commands(self):
        self.safety_state = "command_enabled"

    def emergency_stop(self):
        self.safety_state = "emergency_stopped"

    def reset_safety(self, *, authorized, emergency_stop_active=False):
        self.safety_state = "command_enabled" if authorized else "unauthorized"
        return authorized and not emergency_stop_active

    def command_target(self, target, *, simulation_steps):
        return SimpleNamespace(
            position_error_m=0.01,
            target_reached=True,
            simulation_steps=simulation_steps,
            end_effector=target.coordinates,
        )
""".strip(),
        encoding="utf-8",
    )
    runtime = MujocoWorkflowRuntime(
        mujoco_vision_arm_plan(),
        installed_artifacts=inventory,
        runtime_package_root=runtime_root,
    )

    snapshot = runtime.start()

    assert snapshot.metrics["mujoco_position_error_m"] == 0.01
    assert snapshot.metrics["mujoco_target_reached"] == 1.0
    runtime.stop()

    second_runtime = MujocoWorkflowRuntime(
        mujoco_vision_arm_plan(),
        installed_artifacts=inventory,
        runtime_package_root=runtime_root,
    )
    assert second_runtime.start().metrics["motion_commands_accepted"] == 1.0
    second_runtime.stop()


def test_mujoco_runtime_rejects_unmanaged_or_changed_package(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-packages"
    inventory = managed_mujoco_inventory(runtime_root)
    inventory[0]["digest"] = "sha256:" + "b" * 64

    with pytest.raises(UnsupportedDeploymentPlan, match="摘要不匹配"):
        MujocoWorkflowRuntime(
            mujoco_vision_arm_plan(),
            installed_artifacts=inventory,
            runtime_package_root=runtime_root,
        )

    unmanaged = managed_mujoco_inventory(runtime_root)
    outside_release = tmp_path / "outside-release"
    (outside_release / "robot_mujoco_arm").mkdir(parents=True)
    (outside_release / "robot_mujoco_arm" / "__init__.py").write_text("", encoding="utf-8")
    unmanaged[0]["install_path"] = str(outside_release)
    with pytest.raises(UnsupportedDeploymentPlan, match="不在受管发布目录"):
        MujocoWorkflowRuntime(
            mujoco_vision_arm_plan(),
            installed_artifacts=unmanaged,
            runtime_package_root=runtime_root,
        )

    incomplete = managed_mujoco_inventory(runtime_root)
    missing_package_release = runtime_root / "releases" / "missing-package"
    missing_package_release.mkdir(parents=True)
    incomplete[0]["install_path"] = str(missing_package_release)
    with pytest.raises(UnsupportedDeploymentPlan, match="安装内容不完整"):
        MujocoWorkflowRuntime(
            mujoco_vision_arm_plan(),
            installed_artifacts=incomplete,
            runtime_package_root=runtime_root,
        )


def test_mock_vision_arm_rejects_invalid_safety_parameters() -> None:
    plan = mock_vision_arm_plan()
    plan["workflow"]["nodes"][3]["parameters"]["command_timeout_ms"] = 10

    with pytest.raises(UnsupportedDeploymentPlan, match="command_timeout_ms"):
        MockWorkflowRuntime(plan)


def test_safety_gate_forwards_authorized_fresh_commands_and_stops_safely() -> None:
    runtime = MockWorkflowRuntime(mock_safe_vision_arm_plan())

    snapshot = runtime.start()

    assert runtime.safety_state == "command_enabled"
    assert snapshot.metrics["safety_commands_received"] == 1.0
    assert snapshot.metrics["safety_commands_forwarded"] == 1.0
    assert snapshot.metrics["safety_commands_blocked"] == 0.0
    assert snapshot.metrics["motion_commands_accepted"] == 1.0
    assert snapshot.metrics["safety_gate_velocity_limit"] == 0.12

    stopped = runtime.stop()

    assert runtime.safety_state == "safe_stopped"
    assert stopped.metrics["safety_gate_enabled"] == 0.0
    assert stopped.metrics["actuator_safe_stop"] == 1.0


def test_safety_gate_latches_timeout_and_requires_explicit_reset() -> None:
    plan = mock_safe_vision_arm_plan()
    plan["workflow"]["nodes"][3]["parameters"]["simulated_command_age_ms"] = 350
    runtime = MockWorkflowRuntime(plan)

    timed_out = runtime.start()

    assert runtime.safety_state == "fault_latched"
    assert timed_out.metrics["safety_commands_blocked"] == 1.0
    assert timed_out.metrics["motion_commands_accepted"] == 0.0
    assert timed_out.metrics["safety_gate_fault_latched"] == 1.0

    assert runtime.reset_safety(authorized=True) is True
    assert runtime.safety_state == "command_enabled"

    recovered = runtime.tick()

    assert runtime.safety_state == "fault_latched"
    assert recovered.metrics["safety_gate_resets"] == 1.0
    assert recovered.metrics["safety_commands_blocked"] == 2.0
    runtime.stop()


def test_safety_gate_emergency_stop_cannot_reset_while_signal_is_active() -> None:
    runtime = MockWorkflowRuntime(mock_safe_vision_arm_plan())
    runtime.start()

    runtime.emergency_stop()

    assert runtime.safety_state == "emergency_stopped"
    assert runtime.reset_safety(authorized=True, emergency_stop_active=True) is False
    assert runtime.safety_state == "emergency_stopped"
    assert runtime.reset_safety(authorized=True, emergency_stop_active=False) is True
    assert runtime.safety_state == "command_enabled"
    runtime.stop()


def test_safety_gate_blocks_unauthorized_commands_and_latches_injected_fault() -> None:
    plan = mock_safe_vision_arm_plan()
    plan["workflow"]["nodes"][3]["parameters"]["command_authorized"] = 0
    runtime = MockWorkflowRuntime(plan)

    unauthorized = runtime.start()

    assert runtime.safety_state == "unauthorized"
    assert unauthorized.metrics["motion_commands_accepted"] == 0.0
    assert unauthorized.metrics["safety_commands_blocked"] == 1.0

    assert runtime.reset_safety(authorized=True) is True
    runtime.inject_safety_fault("simulated_drive_fault")
    assert runtime.safety_state == "fault_latched"

    faulted = runtime.tick()

    assert faulted.metrics["safety_gate_fault_latched"] == 1.0
    assert faulted.metrics["safety_commands_blocked"] == 2.0
    runtime.stop()


def test_legacy_actuator_pipeline_rejects_gate_only_controls() -> None:
    runtime = MockWorkflowRuntime(mock_vision_arm_plan())

    with pytest.raises(UnsupportedDeploymentPlan, match="独立安全门"):
        runtime.emergency_stop()
    with pytest.raises(UnsupportedDeploymentPlan, match="独立安全门"):
        runtime.inject_safety_fault()
    with pytest.raises(UnsupportedDeploymentPlan, match="独立安全门"):
        runtime.reset_safety()


def test_invalid_link_is_rejected() -> None:
    plan = real_plan()
    plan["workflow"]["links"][0]["source"]["port"] = "depth"

    with pytest.raises(UnsupportedDeploymentPlan, match="拓扑不一致"):
        DockerRealSenseYoloRuntime(plan)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda plan: plan.update({"plan_id": "bad id"}), "plan_id"),
        (lambda plan: plan["workflow"].update({"nodes": "bad"}), "节点或连接"),
        (lambda plan: plan["workflow"].update({"nodes": ["bad"]}), "节点格式"),
        (lambda plan: plan["workflow"].update({"links": ["bad"]}), "连接格式"),
        (
            lambda plan: plan["workflow"]["nodes"][0].update({"parameters": "bad"}),
            "组件参数",
        ),
        (lambda plan: plan["workflow"].update({"links": []}), "必须且只能"),
        (
            lambda plan: plan["workflow"].update({"links": [{"source": "bad"}]}),
            "端口引用",
        ),
    ],
)
def test_malformed_plans_are_rejected(mutation: Any, message: str) -> None:
    plan = real_plan()
    mutation(plan)

    with pytest.raises(UnsupportedDeploymentPlan, match=message):
        DockerRealSenseYoloRuntime(plan)


def test_invalid_integer_and_non_numeric_float_are_rejected() -> None:
    plan = real_plan()
    plan["workflow"]["nodes"][0]["parameters"]["fps"] = True
    with pytest.raises(UnsupportedDeploymentPlan, match="fps"):
        DockerRealSenseYoloRuntime(plan)

    plan = real_plan()
    plan["workflow"]["nodes"][1]["parameters"]["confidence"] = "high"
    with pytest.raises(UnsupportedDeploymentPlan, match="数值"):
        DockerRealSenseYoloRuntime(plan)


def test_mock_runtime_rejects_components_and_versions_and_can_stop() -> None:
    plan = mock_plan()
    plan["workflow"]["nodes"] = plan["workflow"]["nodes"][:1]
    with pytest.raises(UnsupportedDeploymentPlan, match="模拟Runtime"):
        MockWorkflowRuntime(plan)

    plan = mock_plan()
    plan["workflow"]["nodes"][0]["component_version"] = "9.0.0"
    with pytest.raises(UnsupportedDeploymentPlan, match="0.1.0"):
        MockWorkflowRuntime(plan)

    runtime = MockWorkflowRuntime(mock_plan())
    runtime.start()
    runtime.stop()


def test_real_runtime_rejects_components_and_versions() -> None:
    plan = real_plan()
    plan["workflow"]["nodes"] = plan["workflow"]["nodes"][:1]
    with pytest.raises(UnsupportedDeploymentPlan, match="RealSense和YOLO"):
        DockerRealSenseYoloRuntime(plan)

    plan = real_plan()
    plan["workflow"]["nodes"][0]["component_version"] = "9.0.0"
    with pytest.raises(UnsupportedDeploymentPlan, match="0.1.0"):
        DockerRealSenseYoloRuntime(plan)


@pytest.mark.parametrize("inspection", ["not-json", "{}", "[]", "[1]"])
def test_real_runtime_rejects_invalid_docker_inspection(inspection: str) -> None:
    docker = FakeDocker()
    docker.exists = True
    docker.invalid_inspection = inspection
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)

    with pytest.raises(RuntimeCommandError, match="Docker inspect"):
        runtime.start()


def test_real_runtime_start_failure_modes_are_reported() -> None:
    docker = FakeDocker()
    docker.image_exists = False
    with pytest.raises(RuntimeCommandError, match="找不到融合Runtime镜像"):
        DockerRealSenseYoloRuntime(real_plan(), command_runner=docker).start()

    docker = FakeDocker()
    docker.exists = True
    docker.owned = False
    with pytest.raises(RuntimeCommandError, match="非平台任务占用"):
        DockerRealSenseYoloRuntime(real_plan(), command_runner=docker).start()

    docker = FakeDocker()
    docker.exists = True
    docker.fail_removal = True
    with pytest.raises(RuntimeCommandError, match="remove failed"):
        DockerRealSenseYoloRuntime(real_plan(), command_runner=docker).start()

    docker = FakeDocker()
    docker.fail_run = True
    with pytest.raises(RuntimeCommandError, match="run failed"):
        DockerRealSenseYoloRuntime(real_plan(), command_runner=docker).start()


def test_real_runtime_handles_missing_or_non_metric_logs() -> None:
    docker = FakeDocker()
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)
    runtime.start()
    docker.fail_logs = True
    assert runtime.tick().metrics == {"configured_fps": 30.0}

    docker.fail_logs = False
    docker.logs_content = "not-json\n" + json.dumps({"state": "starting"})
    assert runtime.tick().metrics == {"configured_fps": 30.0}


def test_real_runtime_tick_reports_invalid_container_states() -> None:
    docker = FakeDocker()
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)
    assert runtime.tick().message == "融合Runtime容器不存在"

    docker.exists = True
    docker.owned = False
    assert runtime.tick().message == "检测到同名非平台容器"

    docker.owned = True
    docker.missing_state = True
    assert runtime.tick().message == "Docker容器状态不可读"

    docker.missing_state = False
    docker.state_error = "gpu unavailable"
    assert "gpu unavailable" in runtime.tick().message


def test_real_runtime_stop_is_idempotent_and_protects_foreign_container() -> None:
    docker = FakeDocker()
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)
    runtime.stop()

    docker.exists = True
    docker.owned = False
    with pytest.raises(RuntimeCommandError, match="拒绝停止"):
        runtime.stop()


def test_real_runtime_reports_stop_and_removal_failures() -> None:
    docker = FakeDocker()
    docker.exists = True
    docker.running = True
    docker.fail_stop = True
    runtime = DockerRealSenseYoloRuntime(real_plan(), command_runner=docker)
    with pytest.raises(RuntimeCommandError, match="stop failed"):
        runtime.stop()

    docker.fail_stop = False
    docker.fail_removal = True
    with pytest.raises(RuntimeCommandError, match="remove failed"):
        runtime.stop()


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (b"\xff\xd8preview\xff\xd9", b"\xff\xd8preview\xff\xd9"),
        (b"not-jpeg", None),
        (b"\xff\xd8" + b"x" * (256 * 1024) + b"\xff\xd9", None),
    ],
    ids=["valid-jpeg", "invalid-format", "oversized-jpeg"],
)
def test_real_runtime_reads_only_bounded_complete_jpeg(
    image: bytes, expected: bytes | None
) -> None:
    runtime = DockerRealSenseYoloRuntime(real_plan(), preview_reader=lambda _path: image)

    assert runtime.read_preview() == expected


def test_real_runtime_skips_missing_preview() -> None:
    def missing(_path: object) -> bytes:
        raise FileNotFoundError

    runtime = DockerRealSenseYoloRuntime(real_plan(), preview_reader=missing)

    assert runtime.read_preview() is None


def test_manager_isolates_runtime_failures_and_unknown_desired_state() -> None:
    class BrokenRuntime:
        plan_id = "plan-mock-01"

        def __init__(self, *, fail_start: bool = False) -> None:
            self.fail_start = fail_start

        def start(self) -> RuntimeSnapshot:
            if self.fail_start:
                raise RuntimeError("start failed")
            return RuntimeSnapshot("running", "ok", {})

        def tick(self) -> RuntimeSnapshot:
            raise RuntimeError("tick failed")

        def stop(self) -> None:
            raise RuntimeError("stop failed")

    missing_id_client = FakeDeploymentClient([{"workflow": {}}])
    EdgeDeploymentManager().reconcile(missing_id_client, "edge-01")
    assert missing_id_client.reports == []

    unknown_plan = mock_plan(desired_state="paused")
    unknown_client = FakeDeploymentClient([unknown_plan])
    EdgeDeploymentManager().reconcile(unknown_client, "edge-01")
    assert unknown_client.reports[-1][1] == "failed"

    runtime = BrokenRuntime()
    manager = EdgeDeploymentManager(runtime_factory=lambda _plan: runtime)
    first_client = FakeDeploymentClient([mock_plan()])
    manager.reconcile(first_client, "edge-01")
    second_client = FakeDeploymentClient([mock_plan()])
    manager.reconcile(second_client, "edge-01")
    assert second_client.reports[-1][2] == "tick failed"

    start_client = FakeDeploymentClient([mock_plan()])
    EdgeDeploymentManager(runtime_factory=lambda _plan: BrokenRuntime(fail_start=True)).reconcile(
        start_client, "edge-01"
    )
    assert start_client.reports[-1][2] == "start failed"

    stop_client = FakeDeploymentClient([mock_plan(desired_state="stopped")])
    EdgeDeploymentManager(runtime_factory=lambda _plan: BrokenRuntime()).reconcile(
        stop_client, "edge-01"
    )
    assert stop_client.reports[-1][2] == "stop failed"


def test_manager_removes_runtime_that_reports_failed() -> None:
    class FailedRuntime:
        plan_id = "plan-mock-01"

        def start(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("failed", "bad", {})

        def tick(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("failed", "bad", {})

        def stop(self) -> None:
            return None

    runtime = FailedRuntime()
    manager = EdgeDeploymentManager(runtime_factory=lambda _plan: runtime)
    client = FakeDeploymentClient([mock_plan()])

    manager.reconcile(client, "edge-01")
    manager.reconcile(client, "edge-01")

    assert [report[1] for report in client.reports] == [
        "starting",
        "failed",
        "starting",
        "failed",
    ]


def test_manager_uploads_runtime_preview_after_status() -> None:
    class PreviewRuntime:
        plan_id = "plan-mock-01"

        def start(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("running", "ok", {})

        def tick(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("running", "ok", {})

        def stop(self) -> None:
            return None

        def read_preview(self) -> bytes:
            return b"\xff\xd8preview\xff\xd9"

    client = FakeDeploymentClient([mock_plan()])
    manager = EdgeDeploymentManager(runtime_factory=lambda _plan: PreviewRuntime())

    manager.reconcile(client, "edge-01")

    assert client.previews == [("plan-mock-01", b"\xff\xd8preview\xff\xd9", "image/jpeg")]
    client.preview_response = {"requested_fps": 10}
    result = manager._publish_preview(client, "plan-mock-01", PreviewRuntime())
    assert result.requested_interval_s == 0.1
    assert result.image_bytes == len(b"\xff\xd8preview\xff\xd9")
    assert result.render_ms >= 0.0
    assert result.upload_ms >= 0.0


def test_manager_runs_opt_in_runtime_independently_from_heartbeat() -> None:
    reached_three_ticks = threading.Event()

    class ContinuousRuntime:
        plan_id = "plan-mock-01"
        execution_interval_s = 0.01
        preview_interval_s = 0.01

        def __init__(self) -> None:
            self.ticks = 0
            self.stops = 0

        def start(self) -> RuntimeSnapshot:
            self.ticks += 1
            return RuntimeSnapshot("running", "started", {"runtime_ticks": 1.0})

        def tick(self) -> RuntimeSnapshot:
            self.ticks += 1
            if self.ticks >= 3:
                reached_three_ticks.set()
            return RuntimeSnapshot(
                "running",
                "running",
                {"runtime_ticks": float(self.ticks)},
            )

        def stop(self) -> RuntimeSnapshot:
            self.stops += 1
            return RuntimeSnapshot("running", "stopped", {"runtime_ticks": float(self.ticks)})

        def read_preview(self) -> bytes:
            return b"\xff\xd8preview\xff\xd9"

    runtime = ContinuousRuntime()
    client = FakeDeploymentClient([mock_plan()])
    manager = EdgeDeploymentManager(runtime_factory=lambda _plan: runtime)

    manager.reconcile(client, "edge-01")
    assert reached_three_ticks.wait(timeout=1.0)
    assert runtime.ticks >= 3
    assert len(client.previews) >= 2
    manager.reconcile(client, "edge-01")
    assert client.reports[-1][3]["runtime_ticks"] >= 3

    client.plans = [mock_plan(desired_state="stopped")]
    manager.reconcile(client, "edge-01")
    assert runtime.stops == 1


def test_slow_preview_does_not_block_control_loop_and_drops_stale_frames() -> None:
    reached_ten_ticks = threading.Event()
    release_preview = threading.Event()
    preview_started = threading.Event()

    class SnapshotRuntime:
        plan_id = "plan-mock-01"
        execution_interval_s = 0.005
        preview_interval_s = 0.005

        def __init__(self) -> None:
            self.ticks = 0

        def start(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("running", "started", {"runtime_ticks": 0.0})

        def tick(self) -> RuntimeSnapshot:
            self.ticks += 1
            if self.ticks >= 10:
                reached_ten_ticks.set()
            return RuntimeSnapshot(
                "running",
                "running",
                {"runtime_ticks": float(self.ticks)},
            )

        def capture_preview(self) -> int:
            return self.ticks

        def render_preview(self, payload: object) -> bytes:
            assert isinstance(payload, int)
            preview_started.set()
            release_preview.wait(timeout=1.0)
            return b"\xff\xd8preview\xff\xd9"

        def stop(self) -> RuntimeSnapshot:
            return RuntimeSnapshot("running", "stopped", {"runtime_ticks": float(self.ticks)})

    runtime = SnapshotRuntime()
    client = FakeDeploymentClient([mock_plan()])
    manager = EdgeDeploymentManager(runtime_factory=lambda _plan: runtime)

    manager.reconcile(client, "edge-01")
    assert preview_started.wait(timeout=1.0)
    assert reached_ten_ticks.wait(timeout=1.0)
    assert runtime.ticks >= 10

    snapshot, error = manager._workers[runtime.plan_id].observe()
    assert error is None
    assert snapshot.metrics["control_loop_actual_fps"] > 0.0
    assert snapshot.metrics["preview_frames_dropped"] > 0.0

    release_preview.set()
    client.plans = [mock_plan(desired_state="stopped")]
    manager.reconcile(client, "edge-01")
