"""Platform API 接口测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from robot_platform_api.deployments import DeploymentRegistry
from robot_platform_api.main import create_app
from robot_platform_api.models import ArtifactDefinition, ArtifactDistribution
from robot_platform_api.registry import AgentRegistry


class Clock:
    """可由测试推进的时钟。"""

    def __init__(self) -> None:
        self.current = datetime(2026, 10, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def registration_payload() -> dict[str, object]:
    return {
        "agent_id": "edge-dev-01",
        "name": "开发节点",
        "version": "0.1.0",
        "capabilities": ["simulation"],
        "metadata": {"os": "Windows"},
    }


def mock_workflow_payload() -> dict[str, object]:
    return {
        "workflow_id": "workflow.mock_detection",
        "version": "0.1.0",
        "name": "模拟相机目标检测",
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
            },
        ],
        "links": [
            {
                "source": {"node_id": "camera", "port": "color"},
                "target": {"node_id": "detector", "port": "image"},
            }
        ],
    }


def mock_pipeline_workflow_payload() -> dict[str, object]:
    workflow = mock_workflow_payload()
    workflow["workflow_id"] = "workflow.mock_detection_pipeline"
    workflow["name"] = "三节点模拟视觉流程"
    workflow["nodes"].append(
        {
            "node_id": "filter",
            "component_ref": "algorithm.vision.mock_filter",
            "component_version": "0.1.0",
            "parameters": {"min_confidence": 0.5},
        }
    )
    workflow["links"].append(
        {
            "source": {"node_id": "detector", "port": "detections"},
            "target": {"node_id": "filter", "port": "detections"},
        }
    )
    return workflow


def mock_vision_arm_workflow_payload() -> dict[str, object]:
    workflow = mock_workflow_payload()
    workflow["workflow_id"] = "workflow.mock_vision_arm"
    workflow["name"] = "视觉引导模拟机械臂"
    workflow["nodes"].extend(
        [
            {
                "node_id": "target_selector",
                "component_ref": "algorithm.motion.mock_target_selector",
                "component_version": "0.1.0",
                "parameters": {"minimum_confidence": 0.6},
            },
            {
                "node_id": "arm",
                "component_ref": "hardware.arm.mock",
                "component_version": "0.1.0",
                "parameters": {
                    "command_timeout_ms": 500,
                    "max_velocity_scale": 0.2,
                },
            },
        ]
    )
    workflow["links"].extend(
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
    return workflow


def mock_safe_vision_arm_workflow_payload() -> dict[str, object]:
    workflow = mock_vision_arm_workflow_payload()
    workflow["workflow_id"] = "workflow.mock_vision_arm_safety"
    workflow["name"] = "视觉引导模拟机械臂（独立安全门）"
    workflow["nodes"].insert(
        3,
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
    )
    workflow["links"][-1] = {
        "source": {"node_id": "target_selector", "port": "command"},
        "target": {"node_id": "safety_gate", "port": "command"},
    }
    workflow["links"].append(
        {
            "source": {"node_id": "safety_gate", "port": "safe_command"},
            "target": {"node_id": "arm", "port": "command"},
        }
    )
    return workflow


def real_workflow_payload() -> dict[str, object]:
    workflow = mock_workflow_payload()
    workflow["workflow_id"] = "workflow.realsense_yolox"
    workflow["name"] = "RealSense目标检测"
    workflow["nodes"] = [
        {
            "node_id": "camera",
            "component_ref": "hardware.camera.realsense",
            "component_version": "0.1.0",
            "parameters": {"serial": "125322060182", "fps": 30},
        },
        {
            "node_id": "detector",
            "component_ref": "algorithm.vision.yolo",
            "component_version": "0.1.0",
            "parameters": {"model_id": "yolox.nano.coco"},
        },
    ]
    return workflow


def windows_webcam_yolox_mujoco_workflow_payload() -> dict[str, object]:
    return {
        "workflow_id": "workflow.windows_webcam_yolox_mujoco",
        "version": "0.1.2",
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
                    "model_id": "yolox.nano.coco",
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
    }


def test_health_returns_version() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.29.0"}


def test_agent_can_register_and_heartbeat() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = ["simulation", "artifact.inventory.v1"]
    payload["installed_artifacts"] = [
        {
            "kind": "edge_plugin",
            "locator": "robot-hardware-demo",
            "version": "1.0.0",
            "digest": "sha256:abc",
            "artifact_id": "hardware.camera.demo.edge-plugin",
            "install_path": "/data/robot-platform/edge-agent/plugins/releases/demo/1.0.0",
            "active": True,
        }
    ]

    register_response = client.post("/api/v1/agents/register", json=payload)
    heartbeat_response = client.post(
        "/api/v1/agents/edge-dev-01/heartbeat",
        json={
            "sequence": 7,
            "metrics": {
                "uptime_s": 120.5,
                "memory_total_bytes": 1024,
                "memory_available_bytes": 512,
                "max_temperature_c": 47.5,
            },
            "devices": [
                {
                    "device_id": "usb-8086-0b5c-abcdef123456",
                    "transport": "usb",
                    "name": "Intel RealSense D455",
                    "manufacturer": "Intel RealSense",
                    "vendor_id": "8086",
                    "product_id": "0b5c",
                    "serial": "123456789",
                    "device_nodes": ["/dev/bus/usb/001/005"],
                    "metadata": {"speed_mbps": "5000"},
                }
            ],
        },
    )

    assert register_response.status_code == 201
    assert register_response.json()["status"] == "online"
    assert register_response.json()["installed_artifacts"][0]["digest"] == "sha256:abc"
    assert register_response.json()["installed_artifacts"][0]["active"] is True
    assert register_response.json()["installed_artifacts"][0]["artifact_id"] == (
        "hardware.camera.demo.edge-plugin"
    )
    assert heartbeat_response.status_code == 200
    assert heartbeat_response.json()["heartbeat_sequence"] == 7
    assert heartbeat_response.json()["metrics"]["memory_available_bytes"] == 512
    assert heartbeat_response.json()["metrics"]["max_temperature_c"] == 47.5
    assert heartbeat_response.json()["devices"][0]["name"] == "Intel RealSense D455"


def test_heartbeat_without_metrics_preserves_previous_metrics() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    client.post(
        "/api/v1/agents/edge-dev-01/heartbeat",
        json={"sequence": 1, "metrics": {"uptime_s": 10}},
    )

    response = client.post(
        "/api/v1/agents/edge-dev-01/heartbeat",
        json={"sequence": 2},
    )

    assert response.status_code == 200
    assert response.json()["metrics"]["uptime_s"] == 10


def test_unknown_agent_heartbeat_returns_not_found() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/agents/missing/heartbeat", json={"sequence": 1})

    assert response.status_code == 404
    assert response.json()["detail"] == "Edge Agent 尚未注册"


def test_agent_becomes_offline_after_threshold() -> None:
    clock = Clock()
    registry = AgentRegistry(offline_after_s=5, now=clock.now)
    client = TestClient(create_app(registry))
    client.post("/api/v1/agents/register", json=registration_payload())

    clock.advance(seconds=6)
    response = client.get("/api/v1/agents")

    assert response.status_code == 200
    assert response.json()[0]["status"] == "offline"


def test_registration_validates_agent_id() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["agent_id"] = "invalid agent id"

    response = client.post("/api/v1/agents/register", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("transport", ["usb", "can", "serial", "ethernet", "simulation"])
def test_heartbeat_accepts_supported_device_transports(transport: str) -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())

    response = client.post(
        "/api/v1/agents/edge-dev-01/heartbeat",
        json={
            "sequence": 1,
            "devices": [
                {
                    "device_id": f"{transport}-device-01",
                    "transport": transport,
                    "name": "测试设备",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["devices"][0]["transport"] == transport


def test_catalog_exposes_realsense_and_yolo_definitions() -> None:
    client = TestClient(create_app())

    hardware_response = client.get("/api/v1/catalog/hardware")
    algorithm_response = client.get("/api/v1/catalog/algorithms")
    recipe_response = client.get("/api/v1/catalog/workflow-recipes")
    artifact_response = client.get("/api/v1/catalog/artifacts")

    assert hardware_response.status_code == 200
    assert algorithm_response.status_code == 200
    assert recipe_response.status_code == 200
    assert artifact_response.status_code == 200
    hardware = {item["hardware_id"]: item for item in hardware_response.json()}
    algorithms = {item["algorithm_id"]: item for item in algorithm_response.json()}
    assert hardware["hardware.camera.realsense"]["readiness"] == "validated"
    assert hardware["hardware.camera.windows-webcam"]["discovery"]["transport"] == "usb"
    assert hardware["hardware.camera.mock"]["readiness"] == "validated"
    assert hardware["hardware.arm.mock"]["inputs"] == [
        {
            "name": "command",
            "data_type": "robot.motion.command.v1",
            "required": True,
        }
    ]
    assert hardware["hardware.arm.mujoco"]["discovery"]["transport"] == "simulation"
    assert hardware["hardware.arm.mujoco"]["inputs"][0]["data_type"] == ("robot.motion.command.v1")
    assert algorithms["algorithm.vision.yolo"]["artifact_formats"] == [
        "onnx",
        "tensorrt-engine",
    ]
    assert algorithms["algorithm.vision.yolo"]["readiness"] == "validated"
    assert algorithms["algorithm.vision.mock_detector"]["readiness"] == "validated"
    assert algorithms["algorithm.vision.mock_filter"]["readiness"] == "validated"
    assert (
        algorithms["algorithm.motion.mock_target_selector"]["outputs"][0]["data_type"]
        == "robot.motion.command.v1"
    )
    localizer = algorithms["algorithm.spatial.rgbd_target_localizer"]
    assert [item["data_type"] for item in localizer["inputs"]] == [
        "vision.detections.v1",
        "image.depth16.v1",
        "camera.intrinsics.v1",
    ]
    assert localizer["outputs"][0]["data_type"] == "robot.target_pose.v1"
    planner = algorithms["algorithm.motion.cartesian_point_to_point"]
    assert planner["inputs"][0]["data_type"] == "robot.target_pose.v1"
    assert planner["outputs"][0]["data_type"] == "robot.motion.command.v1"
    safety_gate = algorithms["algorithm.motion.safety_gate"]
    assert safety_gate["inputs"][0]["data_type"] == "robot.motion.command.v1"
    assert safety_gate["outputs"][0] == {
        "name": "safe_command",
        "data_type": "robot.motion.command.v1",
        "required": True,
    }
    assert algorithms["algorithm.vision.yolox_onnx"]["inputs"][0]["data_type"] == (
        "image.rgb8.v1"
    )
    assert algorithms["algorithm.motion.detection_to_sim_target"]["outputs"][0][
        "data_type"
    ] == "robot.motion.command.v1"
    recipes = {item["recipe_id"]: item for item in recipe_response.json()}
    real_recipe = recipes["workflow.realsense_yolox"]
    assert [component["component_ref"] for component in real_recipe["components"]] == [
        "hardware.camera.realsense",
        "algorithm.vision.yolo",
    ]
    serial_parameter = next(
        parameter for parameter in real_recipe["parameters"] if parameter["key"] == "serial"
    )
    assert serial_parameter["binding_source"] == "target_hardware_devices"
    assert {parameter["key"] for parameter in real_recipe["parameters"]} >= {
        "serial",
        "fps",
        "confidence",
    }
    pipeline = recipes["workflow.mock_detection_pipeline"]
    assert len(pipeline["components"]) == 3
    assert len(pipeline["links"]) == 2
    assert any(
        parameter["node_id"] == "filter" and parameter["key"] == "min_confidence"
        for parameter in pipeline["parameters"]
    )
    actuator_recipe = recipes["workflow.mock_vision_arm"]
    assert len(actuator_recipe["components"]) == 4
    assert len(actuator_recipe["links"]) == 3
    assert actuator_recipe["required_capabilities"] == [
        "simulation",
        "actuator.safety.v1",
    ]
    safe_actuator_recipe = recipes["workflow.mock_vision_arm_safety"]
    assert safe_actuator_recipe["version"] == "0.2.0"
    assert len(safe_actuator_recipe["components"]) == 5
    assert len(safe_actuator_recipe["links"]) == 4
    assert safe_actuator_recipe["required_capabilities"] == [
        "simulation",
        "actuator.safety.v1",
        "actuator.safety-gate.v1",
    ]
    mujoco_recipe = recipes["workflow.mujoco_vision_arm_safety"]
    assert mujoco_recipe["version"] == "0.3.0"
    assert [component["component_ref"] for component in mujoco_recipe["components"]] == [
        "hardware.camera.mock",
        "algorithm.vision.mock_detector",
        "algorithm.spatial.rgbd_target_localizer",
        "algorithm.motion.cartesian_point_to_point",
        "algorithm.motion.safety_gate",
        "hardware.arm.mujoco",
    ]
    assert len(mujoco_recipe["links"]) == 7
    assert mujoco_recipe["required_capabilities"] == [
        "simulation",
        "simulation.mujoco",
        "actuator.safety.v1",
        "actuator.safety-gate.v1",
        "artifact.inventory.v1",
        "spatial.calibration.v1",
    ]
    assert mujoco_recipe["required_artifact_ids"] == [
        "simulation.mujoco.arm.runtime",
        "calibration.camera.mock-to-mujoco-arm",
    ]
    windows_recipe = recipes["workflow.windows_webcam_yolox_mujoco"]
    assert [component["component_ref"] for component in windows_recipe["components"]] == [
        "hardware.camera.windows-webcam",
        "algorithm.vision.yolox_onnx",
        "algorithm.motion.detection_to_sim_target",
        "algorithm.motion.safety_gate",
        "hardware.arm.mujoco",
    ]
    assert windows_recipe["required_artifact_ids"] == [
        "hardware.camera.windows-webcam.edge-plugin",
        "simulation.mujoco.arm.runtime",
        "runtime.windows-webcam-yolox-mujoco.runtime-package",
    ]
    artifacts = {item["artifact_id"]: item for item in artifact_response.json()}
    assert artifacts["hardware.camera.realsense.edge-plugin"]["kind"] == "edge_plugin"
    assert artifacts["hardware.camera.realsense.edge-plugin"]["scope"] == "edge_runtime"
    plugin_distribution = artifacts["hardware.camera.realsense.edge-plugin"]["distribution"]
    assert plugin_distribution == {
        "transport": "platform_api",
        "download_path": ("/api/v1/artifacts/hardware.camera.realsense.edge-plugin/content"),
        "file_name": "robot_hardware_realsense-0.1.0-py3-none-any.whl",
        "media_type": "application/zip",
        "size_bytes": 4334,
        "digest": ("sha256:4dabedc238dd67d03283656db2d55011b59fc2392273065e6557a9ca998f2392"),
    }
    assert artifacts["algorithm.vision.yolo.model.yolox.nano.coco"]["scope"] == ("build_source")
    assert artifacts["algorithm.vision.yolo.tensorrt-engine"]["kind"] == "tensorrt_engine"
    assert artifacts["algorithm.vision.yolo.tensorrt-engine"]["digest"] == (
        "sha256:719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17"
    )
    calibration = artifacts["calibration.camera.mock-to-mujoco-arm"]
    assert calibration["kind"] == "calibration_profile"
    assert calibration["locator"] == "mock-camera-to-mujoco-arm-v1"
    assert calibration["distribution"]["media_type"] == "application/json"
    assert artifacts["hardware.camera.windows-webcam.edge-plugin"]["distribution"][
        "digest"
    ] == "sha256:9660b339f1f50025d16b7610f2c083f398aa1a6c1dc4227ab111e81ea916cdb1"
    assert artifacts["runtime.windows-webcam-yolox-mujoco.runtime-package"]["kind"] == (
        "runtime_package"
    )
    assert (
        artifacts["runtime.realsense-yolox.runtime-image"]["locator"]
        == "robot-platform/realsense-yolox-runtime:0.1.3"
    )
    assert real_recipe["required_artifact_ids"] == [
        "hardware.camera.realsense.edge-plugin",
        "runtime.realsense-yolox.runtime-image",
        "algorithm.vision.yolo.tensorrt-engine",
    ]


def test_catalog_only_serves_digest_pinned_distribution_files() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/artifacts/hardware.camera.realsense.edge-plugin/content")
    unavailable = client.get("/api/v1/artifacts/runtime.realsense-yolox.runtime-image/content")
    unknown = client.get("/api/v1/artifacts/unknown/content")

    assert response.status_code == 200
    assert len(response.content) == 4334
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-artifact-digest"] == (
        "sha256:4dabedc238dd67d03283656db2d55011b59fc2392273065e6557a9ca998f2392"
    )
    assert response.headers["content-disposition"] == (
        'attachment; filename="robot_hardware_realsense-0.1.0-py3-none-any.whl"'
    )
    assert unavailable.status_code == 404
    assert unknown.status_code == 404


def test_artifact_preparation_preview_reports_ready_inventory() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["metadata"] = {"os": "Linux", "machine": "aarch64"}
    payload["installed_artifacts"] = [
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
    client.post("/api/v1/agents/register", json=payload)

    response = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_ids": [
                "hardware.camera.realsense.edge-plugin",
                "runtime.realsense-yolox.runtime-image",
                "algorithm.vision.yolo.tensorrt-engine",
            ],
        },
    )

    assert response.status_code == 200
    plan = response.json()
    assert plan["plan_id"].startswith("prepare-")
    assert plan["ready_for_deployment"] is True
    assert plan["requires_changes"] is False
    assert plan["execution_enabled"] is False
    assert [item["state"] for item in plan["items"]] == [
        "ready",
        "ready",
        "ready",
    ]
    assert all(item["strategy"] == "none" for item in plan["items"])
    assert len(plan["warnings"]) == 1
    assert "未上报可比较摘要" in plan["warnings"][0]


def test_agent_artifact_resolution_selects_windows_mujoco_variant() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = ["artifact.inventory.v1", "simulation.mujoco"]
    payload["metadata"] = {"operating_system": "Windows", "machine": "AMD64"}
    client.post("/api/v1/agents/register", json=payload)

    response = client.get("/api/v1/agents/edge-dev-01/artifact-resolutions")

    assert response.status_code == 200
    resolution = next(
        item for item in response.json() if item["artifact_id"] == "simulation.mujoco.arm.runtime"
    )
    assert resolution["compatible"] is True
    assert resolution["selected_variant_id"] == "windows-amd64-python"
    assert resolution["artifact"]["locator"] == "robot-mujoco-arm-runtime"
    assert resolution["artifact"]["distribution"]["download_path"].endswith(
        "?variant_id=windows-amd64-python"
    )


def test_windows_agent_can_request_mujoco_runtime_package_installation() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = [
        "artifact.inventory.v1",
        "artifact.installation.v1",
        "artifact.runtime-package-install.v1",
        "simulation.mujoco",
    ]
    payload["metadata"] = {"operating_system": "Windows", "machine": "AMD64"}
    client.post("/api/v1/agents/register", json=payload)

    prepared = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_ids": ["simulation.mujoco.arm.runtime"],
        },
    )
    created = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": "simulation.mujoco.arm.runtime",
        },
    )

    assert prepared.status_code == 200
    assert prepared.json()["items"][0]["state"] == "installable"
    assert prepared.json()["items"][0]["strategy"] == "install_wheel"
    assert created.status_code == 201
    artifact = created.json()["plan"]["artifact"]
    assert artifact["selected_variant_id"] == "windows-amd64-python"
    assert artifact["kind"] == "runtime_package"


def test_calibration_profile_is_installable_then_ready_by_digest() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = [
        "artifact.inventory.v1",
        "artifact.installation.v1",
        "artifact.calibration-profile-install.v1",
        "spatial.calibration.v1",
    ]
    client.post("/api/v1/agents/register", json=payload)

    artifact_id = "calibration.camera.mock-to-mujoco-arm"
    prepared = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={"target_agent_id": "edge-dev-01", "artifact_ids": [artifact_id]},
    )

    assert prepared.status_code == 200
    item = prepared.json()["items"][0]
    assert item["state"] == "installable"
    assert item["strategy"] == "install_config"
    created = client.post(
        "/api/v1/artifact-installations",
        json={"target_agent_id": "edge-dev-01", "artifact_id": artifact_id},
    )
    assert created.status_code == 201
    assert created.json()["plan"]["artifact"]["kind"] == "calibration_profile"

    artifact = item["artifact"]
    payload["installed_artifacts"] = [
        {
            "kind": "calibration_profile",
            "locator": artifact["locator"],
            "version": artifact["version"],
            "digest": artifact["digest"],
            "artifact_id": artifact_id,
            "active": True,
        }
    ]
    client.post("/api/v1/agents/register", json=payload)
    ready = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={"target_agent_id": "edge-dev-01", "artifact_ids": [artifact_id]},
    )

    assert ready.status_code == 200
    assert ready.json()["items"][0]["state"] == "ready"


def test_artifact_preparation_blocks_when_no_variant_matches() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = ["artifact.inventory.v1"]
    payload["metadata"] = {"operating_system": "Linux", "machine": "riscv64"}
    client.post("/api/v1/agents/register", json=payload)

    response = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_ids": ["simulation.mujoco.arm.runtime"],
        },
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["state"] == "blocked"
    assert "没有与目标节点环境兼容的发行变体" in item["reason"]
    assert "linux-arm64-python" in item["reason"]


def test_artifact_preparation_preview_never_invents_install_commands() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["metadata"] = {"os": "Linux", "machine": "aarch64"}
    client.post("/api/v1/agents/register", json=payload)

    response = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_ids": [
                "hardware.camera.realsense.edge-plugin",
                "runtime.realsense-yolox.runtime-image",
                "algorithm.vision.yolo.tensorrt-engine",
                "algorithm.vision.yolo.model.yolox.nano.coco",
            ],
        },
    )

    assert response.status_code == 200
    plan = response.json()
    assert plan["ready_for_deployment"] is False
    assert plan["requires_changes"] is True
    assert plan["execution_enabled"] is False
    assert [item["state"] for item in plan["items"]] == [
        "installable",
        "manual_required",
        "installable",
        "not_required",
    ]
    assert [item["strategy"] for item in plan["items"]] == [
        "install_wheel",
        "manual",
        "copy_file",
        "none",
    ]
    assert "commands" not in plan
    assert all("command" not in item for item in plan["items"])


def test_artifact_preparation_preview_repairs_digest_and_blocks_architecture() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["metadata"] = {"os": "Linux", "machine": "x86_64"}
    payload["installed_artifacts"] = [
        {
            "kind": "edge_plugin",
            "locator": "robot-hardware-realsense",
            "version": "0.1.0",
            "digest": "sha256:wrong",
        }
    ]
    client.post("/api/v1/agents/register", json=payload)

    response = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_ids": [
                "hardware.camera.realsense.edge-plugin",
                "algorithm.vision.yolo.tensorrt-engine",
            ],
        },
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["state"] for item in items] == ["installable", "blocked"]
    assert items[0]["strategy"] == "install_wheel"
    assert "摘要" in items[0]["reason"]
    assert "aarch64" in items[1]["reason"]


def test_artifact_preparation_preview_rejects_unknown_input() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())

    missing_agent = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={"target_agent_id": "missing", "artifact_ids": ["unknown"]},
    )
    missing_artifact = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={"target_agent_id": "edge-dev-01", "artifact_ids": ["unknown"]},
    )
    duplicate = client.post(
        "/api/v1/artifact-preparation-plans/preview",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_ids": [
                "hardware.camera.realsense.edge-plugin",
                "hardware.camera.realsense.edge-plugin",
            ],
        },
    )

    assert missing_agent.status_code == 404
    assert missing_artifact.status_code == 404
    assert duplicate.status_code == 422


def test_artifact_installation_transaction_progresses_to_success() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = [
        "artifact.inventory.v1",
        "artifact.installation.v1",
        "artifact.activation-health.v1",
    ]
    client.post("/api/v1/agents/register", json=payload)

    created = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": "hardware.camera.realsense.edge-plugin",
        },
    )

    assert created.status_code == 201
    record = created.json()
    operation_id = record["plan"]["operation_id"]
    assert record["status"] == "pending"
    assert record["plan"]["artifact"]["distribution"]["transport"] == "platform_api"
    duplicate = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": "hardware.camera.realsense.edge-plugin",
        },
    )
    assert duplicate.status_code == 409
    active = client.get("/api/v1/agents/edge-dev-01/artifact-installations")
    assert [item["plan"]["operation_id"] for item in active.json()] == [operation_id]

    for installation_status in (
        "downloading",
        "verifying",
        "installing",
        "activating",
        "succeeded",
    ):
        updated = client.post(
            f"/api/v1/artifact-installations/{operation_id}/status",
            json={"status": installation_status, "message": installation_status},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == installation_status

    assert client.get("/api/v1/agents/edge-dev-01/artifact-installations").json() == []
    records = client.get("/api/v1/artifact-installations").json()
    assert records[0]["status"] == "succeeded"


def test_artifact_installation_enforces_capability_and_transitions() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    unsupported_agent = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": "hardware.camera.realsense.edge-plugin",
        },
    )
    assert unsupported_agent.status_code == 409

    payload = registration_payload()
    payload["capabilities"] = ["artifact.inventory.v1", "artifact.installation.v1"]
    client.post("/api/v1/agents/register", json=payload)
    missing_health_confirmation = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": "hardware.camera.realsense.edge-plugin",
        },
    )
    assert missing_health_confirmation.status_code == 409

    payload["capabilities"] = [
        "artifact.inventory.v1",
        "artifact.installation.v1",
        "artifact.activation-health.v1",
    ]
    client.post("/api/v1/agents/register", json=payload)
    unsupported_artifact = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": "runtime.realsense-yolox.runtime-image",
        },
    )
    assert unsupported_artifact.status_code == 422

    created = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": "hardware.camera.realsense.edge-plugin",
        },
    ).json()
    invalid = client.post(
        f"/api/v1/artifact-installations/{created['plan']['operation_id']}/status",
        json={"status": "succeeded"},
    )
    missing = client.post(
        "/api/v1/artifact-installations/missing/status",
        json={"status": "failed"},
    )
    assert invalid.status_code == 409
    assert missing.status_code == 404


def test_container_image_installation_requires_digest_and_agent_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    artifact = ArtifactDefinition(
        artifact_id="runtime.demo.runtime-image",
        name="Demo Runtime",
        kind="container_image",
        scope="edge_runtime",
        version="1.2.3",
        locator="registry.example/robot/demo:1.2.3",
        digest=digest,
        architecture="aarch64",
        source_ref="runtime.demo",
    )

    def find_test_artifact(artifact_id: str) -> ArtifactDefinition | None:
        return artifact if artifact_id == artifact.artifact_id else None

    monkeypatch.setattr("robot_platform_api.main.find_artifact", find_test_artifact)
    monkeypatch.setattr(
        "robot_platform_api.artifact_preparation.find_artifact",
        find_test_artifact,
    )
    client = TestClient(create_app())
    payload = registration_payload()
    payload["metadata"] = {"os": "Linux", "machine": "aarch64"}
    payload["capabilities"] = [
        "artifact.inventory.v1",
        "artifact.installation.v1",
        "artifact.container-pull.v1",
    ]
    client.post("/api/v1/agents/register", json=payload)

    created = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": artifact.artifact_id,
        },
    )

    assert created.status_code == 201
    assert created.json()["plan"]["artifact"]["digest"] == digest

    artifact_without_digest = artifact.model_copy(update={"digest": None})
    monkeypatch.setattr(
        "robot_platform_api.main.find_artifact",
        lambda _artifact_id: artifact_without_digest,
    )
    rejected = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": artifact.artifact_id,
        },
    )
    assert rejected.status_code == 422


def test_container_archive_installation_requires_import_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = ArtifactDefinition(
        artifact_id="runtime.demo.runtime-image",
        name="Demo Runtime",
        kind="container_image",
        scope="edge_runtime",
        version="1.2.3",
        locator="robot/demo:1.2.3",
        digest="sha256:" + "a" * 64,
        architecture="aarch64",
        source_ref="runtime.demo",
        distribution=ArtifactDistribution(
            download_path="/api/v1/artifacts/runtime.demo.runtime-image/content",
            file_name="demo.tar.gz",
            media_type="application/gzip",
            size_bytes=1024,
            digest="sha256:" + "b" * 64,
        ),
    )

    def find_test_artifact(artifact_id: str) -> ArtifactDefinition | None:
        return artifact if artifact_id == artifact.artifact_id else None

    monkeypatch.setattr("robot_platform_api.main.find_artifact", find_test_artifact)
    monkeypatch.setattr(
        "robot_platform_api.artifact_preparation.find_artifact",
        find_test_artifact,
    )
    client = TestClient(create_app())
    payload = registration_payload()
    payload["metadata"] = {"os": "Linux", "machine": "aarch64"}
    payload["capabilities"] = [
        "artifact.inventory.v1",
        "artifact.installation.v1",
        "artifact.container-pull.v1",
    ]
    client.post("/api/v1/agents/register", json=payload)

    unsupported = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": artifact.artifact_id,
        },
    )
    assert unsupported.status_code == 409
    assert "归档导入" in unsupported.json()["detail"]

    payload["capabilities"].append("artifact.container-import.v1")
    client.post("/api/v1/agents/register", json=payload)
    created = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": artifact.artifact_id,
        },
    )
    assert created.status_code == 201


def test_tensorrt_engine_installation_requires_distribution_and_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = ArtifactDefinition(
        artifact_id="algorithm.demo.tensorrt-engine",
        name="Demo TensorRT Engine",
        kind="tensorrt_engine",
        scope="edge_runtime",
        version="1.2.3",
        locator="/data/robot-platform/cache/tensorrt/demo/demo.engine",
        digest="sha256:" + "a" * 64,
        architecture="aarch64",
        source_ref="algorithm.demo",
        distribution=ArtifactDistribution(
            download_path=("/api/v1/artifacts/algorithm.demo.tensorrt-engine/content"),
            file_name="demo.engine",
            media_type="application/octet-stream",
            size_bytes=1024,
            digest="sha256:" + "a" * 64,
        ),
    )

    def find_test_artifact(artifact_id: str) -> ArtifactDefinition | None:
        return artifact if artifact_id == artifact.artifact_id else None

    monkeypatch.setattr("robot_platform_api.main.find_artifact", find_test_artifact)
    monkeypatch.setattr(
        "robot_platform_api.artifact_preparation.find_artifact",
        find_test_artifact,
    )
    client = TestClient(create_app())
    payload = registration_payload()
    payload["metadata"] = {"os": "Linux", "machine": "aarch64"}
    payload["capabilities"] = ["artifact.inventory.v1", "artifact.installation.v1"]
    client.post("/api/v1/agents/register", json=payload)

    unsupported = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": artifact.artifact_id,
        },
    )
    assert unsupported.status_code == 409
    assert "受管文件" in unsupported.json()["detail"]

    payload["capabilities"].append("artifact.file-install.v1")
    client.post("/api/v1/agents/register", json=payload)
    created = client.post(
        "/api/v1/artifact-installations",
        json={
            "target_agent_id": "edge-dev-01",
            "artifact_id": artifact.artifact_id,
        },
    )
    assert created.status_code == 201
    assert created.json()["plan"]["artifact"]["distribution"]["file_name"] == ("demo.engine")


def test_mock_workflow_validates_and_compiles_for_online_agent() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())

    validation = client.post("/api/v1/workflows/validate", json=mock_workflow_payload())
    compilation = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )

    assert validation.status_code == 200
    assert validation.json() == {"compatible": True, "issues": []}
    assert compilation.status_code == 200
    assert compilation.json()["report"]["compatible"] is True
    assert compilation.json()["plan"]["plan_id"].startswith("plan-")
    assert compilation.json()["plan"]["components"] == [
        {
            "node_id": "camera",
            "component_ref": "hardware.camera.mock",
            "component_version": "0.1.0",
            "kind": "hardware",
        },
        {
            "node_id": "detector",
            "component_ref": "algorithm.vision.mock_detector",
            "component_version": "0.1.0",
            "kind": "algorithm",
        },
    ]


def test_three_node_mock_pipeline_validates_and_compiles() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    workflow = mock_pipeline_workflow_payload()

    validation = client.post("/api/v1/workflows/validate", json=workflow)
    compilation = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": workflow},
    )

    assert validation.status_code == 200
    assert validation.json() == {"compatible": True, "issues": []}
    assert compilation.status_code == 200
    assert compilation.json()["report"] == {"compatible": True, "issues": []}
    assert len(compilation.json()["plan"]["components"]) == 3


def test_mock_vision_arm_validates_and_requires_actuator_capability() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = ["simulation", "actuator.safety.v1"]
    client.post("/api/v1/agents/register", json=payload)
    workflow = mock_vision_arm_workflow_payload()

    validation = client.post("/api/v1/workflows/validate", json=workflow)
    compilation = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": workflow},
    )

    assert validation.json() == {"compatible": True, "issues": []}
    assert compilation.json()["report"] == {"compatible": True, "issues": []}
    assert len(compilation.json()["plan"]["components"]) == 4
    assert [component["kind"] for component in compilation.json()["plan"]["components"]] == [
        "hardware",
        "algorithm",
        "algorithm",
        "hardware",
    ]

    missing_command = mock_vision_arm_workflow_payload()
    missing_command["links"] = missing_command["links"][:-1]
    rejected = client.post("/api/v1/workflows/validate", json=missing_command)
    assert rejected.json()["compatible"] is False
    assert any(
        issue["code"] == "required_input_unconnected" and issue["node_id"] == "arm"
        for issue in rejected.json()["issues"]
    )


def test_mock_vision_arm_compile_rejects_agent_without_safety_capability() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())

    compilation = client.post(
        "/api/v1/deployment-plans/compile",
        json={
            "target_agent_id": "edge-dev-01",
            "workflow": mock_vision_arm_workflow_payload(),
        },
    )

    assert compilation.json()["report"]["compatible"] is False
    issue = next(
        item
        for item in compilation.json()["report"]["issues"]
        if item["code"] == "target_capability_missing"
    )
    assert "actuator.safety.v1" in issue["message"]


def test_safe_vision_arm_requires_gate_capability_and_compiles_five_nodes() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = [
        "simulation",
        "actuator.safety.v1",
        "actuator.safety-gate.v1",
    ]
    client.post("/api/v1/agents/register", json=payload)

    compilation = client.post(
        "/api/v1/deployment-plans/compile",
        json={
            "target_agent_id": "edge-dev-01",
            "workflow": mock_safe_vision_arm_workflow_payload(),
        },
    )

    assert compilation.json()["report"] == {"compatible": True, "issues": []}
    assert len(compilation.json()["plan"]["components"]) == 5

    missing_gate_capability = registration_payload()
    missing_gate_capability["agent_id"] = "edge-dev-02"
    missing_gate_capability["capabilities"] = ["simulation", "actuator.safety.v1"]
    client.post("/api/v1/agents/register", json=missing_gate_capability)
    rejected = client.post(
        "/api/v1/deployment-plans/compile",
        json={
            "target_agent_id": "edge-dev-02",
            "workflow": mock_safe_vision_arm_workflow_payload(),
        },
    )
    issue = next(
        item
        for item in rejected.json()["report"]["issues"]
        if item["code"] == "target_capability_missing"
    )
    assert "actuator.safety-gate.v1" in issue["message"]


def test_realsense_yolo_workflow_is_validated_after_hardware_acceptance() -> None:
    client = TestClient(create_app())
    workflow = real_workflow_payload()

    response = client.post("/api/v1/workflows/validate", json=workflow)

    assert response.status_code == 200
    assert response.json() == {"compatible": True, "issues": []}


def test_real_workflow_requires_runtime_capabilities_and_connected_camera() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())

    rejected = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": real_workflow_payload()},
    )

    assert rejected.json()["report"]["compatible"] is False
    assert {issue["code"] for issue in rejected.json()["report"]["issues"]} == {
        "target_capability_missing",
        "required_device_missing",
    }


def test_real_workflow_compiles_for_capable_jetson_with_realsense() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = [
        "jetson",
        "runtime.docker",
        "camera.realsense",
        "inference.tensorrt",
        "artifact.inventory.v1",
    ]
    payload["installed_artifacts"] = [
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
    client.post("/api/v1/agents/register", json=payload)
    client.post(
        "/api/v1/agents/edge-dev-01/heartbeat",
        json={
            "sequence": 1,
            "devices": [
                {
                    "device_id": "usb-8086-0ad3-d415",
                    "transport": "usb",
                    "name": "Intel RealSense D415",
                    "vendor_id": "8086",
                    "product_id": "0ad3",
                }
            ],
        },
    )

    response = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": real_workflow_payload()},
    )

    assert response.json()["report"] == {"compatible": True, "issues": []}
    assert response.json()["plan"]["desired_state"] == "running"
    assert [item["artifact_id"] for item in response.json()["plan"]["artifacts"]] == [
        "hardware.camera.realsense.edge-plugin",
        "runtime.realsense-yolox.runtime-image",
        "algorithm.vision.yolo.tensorrt-engine",
    ]
    assert response.json()["plan"]["artifacts"][2]["architecture"] == "aarch64"

    payload["installed_artifacts"][2]["digest"] = "sha256:wrong"
    client.post("/api/v1/agents/register", json=payload)
    mismatch = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": real_workflow_payload()},
    )
    assert mismatch.json()["plan"] is None
    assert {issue["code"] for issue in mismatch.json()["report"]["issues"]} == {
        "artifact_digest_mismatch"
    }


def test_real_workflow_rejects_missing_required_runtime_artifacts() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["capabilities"] = [
        "jetson",
        "runtime.docker",
        "camera.realsense",
        "inference.tensorrt",
        "artifact.inventory.v1",
    ]
    client.post("/api/v1/agents/register", json=payload)
    client.post(
        "/api/v1/agents/edge-dev-01/heartbeat",
        json={
            "sequence": 1,
            "devices": [
                {
                    "device_id": "usb-8086-0ad3-d415",
                    "transport": "usb",
                    "name": "Intel RealSense D415",
                    "vendor_id": "8086",
                    "product_id": "0ad3",
                }
            ],
        },
    )

    response = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": real_workflow_payload()},
    )

    assert response.json()["plan"] is None
    assert {issue["code"] for issue in response.json()["report"]["issues"]} == {
        "required_artifact_missing"
    }


def test_windows_camera_yolox_mujoco_loop_compiles_for_prepared_node() -> None:
    client = TestClient(create_app())
    payload = registration_payload()
    payload["agent_id"] = "simulation-windows-01"
    payload["name"] = "WindowsMuJoCoSimulationNode"
    payload["version"] = "0.19.0"
    payload["metadata"] = {"operating_system": "Windows", "machine": "AMD64"}
    payload["capabilities"] = [
        "hardware.camera.windows-webcam",
        "simulation",
        "simulation.mujoco",
        "inference.onnxruntime.cpu",
        "actuator.safety.v1",
        "actuator.safety-gate.v1",
        "artifact.inventory.v1",
    ]
    payload["installed_artifacts"] = [
        {
            "artifact_id": "hardware.camera.windows-webcam.edge-plugin",
            "kind": "edge_plugin",
            "locator": "robot-hardware-windows-webcam",
            "version": "0.1.2",
            "digest": (
                "sha256:9660b339f1f50025d16b7610f2c083f398aa1a6c1dc4227ab111e81ea916cdb1"
            ),
            "active": True,
        },
        {
            "artifact_id": "simulation.mujoco.arm.runtime",
            "kind": "runtime_package",
            "locator": "robot-mujoco-arm-runtime",
            "version": "0.4.0",
            "digest": (
                    "sha256:1d0e8541c8244c2d0e91c544ee33e52fd215f7bf3d63a607751359f681d0c571"
            ),
            "active": True,
        },
        {
            "artifact_id": "runtime.windows-webcam-yolox-mujoco.runtime-package",
            "kind": "runtime_package",
            "locator": "robot-windows-webcam-yolox-mujoco-runtime",
            "version": "0.2.0",
            "digest": (
                    "sha256:7bb267e47b85cdae0b01012f39348a9e117250514322b3707f00f1ee8de28752"
            ),
            "active": True,
        },
    ]
    client.post("/api/v1/agents/register", json=payload)
    client.post(
        "/api/v1/agents/simulation-windows-01/heartbeat",
        json={
            "sequence": 1,
            "devices": [
                {
                    "device_id": "windows-camera-0",
                    "transport": "usb",
                    "name": "Windows Camera 0",
                    "manufacturer": "Windows Media Capture",
                }
            ],
        },
    )

    response = client.post(
        "/api/v1/deployment-plans/compile",
        json={
            "target_agent_id": "simulation-windows-01",
            "workflow": windows_webcam_yolox_mujoco_workflow_payload(),
        },
    )

    assert response.status_code == 200
    assert response.json()["report"] == {"compatible": True, "issues": []}
    assert [item["artifact_id"] for item in response.json()["plan"]["artifacts"]] == [
        "hardware.camera.windows-webcam.edge-plugin",
        "simulation.mujoco.arm.runtime",
        "runtime.windows-webcam-yolox-mujoco.runtime-package",
    ]


def test_typed_but_unimplemented_component_pair_has_no_runtime_recipe() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    workflow = real_workflow_payload()
    workflow["nodes"][1]["component_ref"] = "algorithm.vision.mock_detector"

    response = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "edge-dev-01", "workflow": workflow},
    )

    assert response.status_code == 200
    assert response.json()["plan"] is None
    assert {issue["code"] for issue in response.json()["report"]["issues"]} == {
        "runtime_recipe_not_found"
    }


def test_compile_reports_missing_target_agent() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/deployment-plans/compile",
        json={"target_agent_id": "missing", "workflow": mock_workflow_payload()},
    )

    assert response.status_code == 200
    assert response.json()["plan"] is None
    assert response.json()["report"]["issues"][0]["code"] == "target_agent_not_found"


def test_workflow_reports_unconnected_required_input() -> None:
    client = TestClient(create_app())
    workflow = mock_workflow_payload()
    workflow["links"] = []

    response = client.post("/api/v1/workflows/validate", json=workflow)

    assert response.status_code == 200
    assert response.json()["compatible"] is False
    assert response.json()["issues"][0]["code"] == "required_input_unconnected"


def test_workflow_rejects_duplicate_input_connections() -> None:
    client = TestClient(create_app())
    workflow = mock_workflow_payload()
    workflow["links"].append(workflow["links"][0].copy())

    response = client.post("/api/v1/workflows/validate", json=workflow)

    assert response.status_code == 200
    assert "input_already_connected" in {issue["code"] for issue in response.json()["issues"]}


def test_workflow_rejects_cycles() -> None:
    client = TestClient(create_app())
    workflow = mock_workflow_payload()
    workflow["links"].append(
        {
            "source": {"node_id": "detector", "port": "detections"},
            "target": {"node_id": "detector", "port": "image"},
        }
    )

    response = client.post("/api/v1/workflows/validate", json=workflow)

    assert response.status_code == 200
    assert "workflow_cycle" in {issue["code"] for issue in response.json()["issues"]}


def test_deployment_is_submitted_and_agent_reports_status() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())

    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]
    active = client.get("/api/v1/agents/edge-dev-01/deployments")
    starting = client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={"status": "starting", "message": "正在启动", "metrics": {}},
    )
    running = client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={
            "status": "running",
            "message": "模拟工作流运行中",
            "metrics": {"configured_fps": 15.0},
        },
    )
    records = client.get("/api/v1/deployments")

    assert submission.status_code == 200
    assert active.json()[0]["plan_id"] == plan_id
    assert starting.json()["status"] == "starting"
    assert running.json()["status"] == "running"
    assert records.json()[0]["metrics"] == {"configured_fps": 15.0}


def test_terminal_status_preserves_last_nonempty_runtime_metrics() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]
    client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={"status": "starting", "message": "正在启动"},
    )
    client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={
            "status": "running",
            "message": "运行中",
            "metrics": {"motion_commands_accepted": 7.0, "actuator_faults": 0.0},
        },
    )
    client.post(f"/api/v1/deployments/{plan_id}/stop")

    stopped = client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={"status": "stopped", "message": "已安全停止"},
    )

    assert stopped.status_code == 200
    assert stopped.json()["metrics"] == {
        "motion_commands_accepted": 7.0,
        "actuator_faults": 0.0,
    }


def test_terminal_deployment_generates_structured_acceptance_report_and_events() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "running"})
    client.post(f"/api/v1/deployments/{plan_id}/stop")

    stopped = client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={
            "status": "stopped",
            "message": "已安全停止",
            "metrics": {
                "configured_fps": 15.0,
                "control_loop_actual_fps": 14.5,
                "control_tick_p95_ms": 55.0,
                "control_deadline_misses": 2.0,
                "runtime_ticks": 200.0,
                "inference_mean_ms": 35.0,
                "preview_age_latest_ms": 120.0,
            },
        },
    )

    record = stopped.json()
    assert record["attempt"] == 1
    assert [event["status"] for event in record["events"]] == [
        "pending",
        "starting",
        "running",
        "stopping",
        "stopped",
    ]
    report = record["acceptance_reports"][0]
    assert report["schema_version"] == "runtime-acceptance.v1"
    assert report["verdict"] == "passed"
    assert report["evidence_metrics"]["control_loop_actual_fps"] == 14.5
    assert {check["check_id"] for check in report["checks"]} >= {
        "runtime_terminal_state",
        "control_frequency",
        "control_latency_p95",
        "control_deadlines",
        "preview_freshness",
    }


def test_acceptance_report_distinguishes_failed_and_incomplete_runs() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    payload = {"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()}
    failed_submission = client.post("/api/v1/deployments", json=payload).json()
    failed_id = failed_submission["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{failed_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{failed_id}/status", json={"status": "running"})
    failed = client.post(
        f"/api/v1/deployments/{failed_id}/status",
        json={
            "status": "failed",
            "message": "控制频率持续不达标",
            "metrics": {
                "configured_fps": 15.0,
                "control_loop_actual_fps": 8.0,
                "control_tick_p95_ms": 140.0,
            },
        },
    ).json()
    assert failed["acceptance_reports"][-1]["verdict"] == "failed"

    restarted_response = client.post("/api/v1/deployments", json=payload)
    assert restarted_response.status_code == 200, restarted_response.text
    restarted = next(
        record
        for record in client.get("/api/v1/deployments").json()
        if record["plan"]["plan_id"] == failed_id
    )
    assert restarted["attempt"] == 2
    assert len(restarted["acceptance_reports"]) == 1
    client.post(f"/api/v1/deployments/{failed_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{failed_id}/status", json={"status": "running"})
    client.post(f"/api/v1/deployments/{failed_id}/stop")
    incomplete = client.post(
        f"/api/v1/deployments/{failed_id}/status",
        json={"status": "stopped", "message": "已停止但没有指标"},
    ).json()
    assert incomplete["acceptance_reports"][-1]["attempt"] == 2
    assert incomplete["acceptance_reports"][-1]["verdict"] == "incomplete"


def test_deployment_submission_is_idempotent() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    payload = {"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()}

    first = client.post("/api/v1/deployments", json=payload)
    second = client.post("/api/v1/deployments", json=payload)

    assert first.json()["plan"]["plan_id"] == second.json()["plan"]["plan_id"]
    assert len(client.get("/api/v1/deployments").json()) == 1


def test_invalid_deployment_transition_returns_conflict() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]

    response = client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={"status": "stopped"},
    )

    assert response.status_code == 409
    assert "pending转换为stopped" in response.json()["detail"]


def test_unknown_deployment_status_returns_not_found() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/deployments/missing/status",
        json={"status": "starting"},
    )

    assert response.status_code == 404


def test_running_deployment_can_be_stopped_and_restarted() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    payload = {"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()}
    submission = client.post("/api/v1/deployments", json=payload)
    plan_id = submission.json()["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "running"})

    stopping = client.post(f"/api/v1/deployments/{plan_id}/stop")
    instructions = client.get("/api/v1/agents/edge-dev-01/deployments")
    stopped = client.post(
        f"/api/v1/deployments/{plan_id}/status",
        json={"status": "stopped", "message": "Runtime已停止并清理"},
    )
    restarted = client.post("/api/v1/deployments", json=payload)

    assert stopping.status_code == 200
    assert stopping.json()["status"] == "stopping"
    assert instructions.json()[0]["desired_state"] == "stopped"
    assert stopped.json()["status"] == "stopped"
    assert restarted.json()["plan"]["desired_state"] == "running"
    assert client.get("/api/v1/deployments").json()[0]["status"] == "pending"


def test_historical_deployment_rollback_waits_for_active_runtime_to_stop() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    first_workflow = mock_workflow_payload()
    first = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": first_workflow},
    ).json()
    first_plan_id = first["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{first_plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{first_plan_id}/status", json={"status": "running"})
    client.post(f"/api/v1/deployments/{first_plan_id}/stop")
    client.post(f"/api/v1/deployments/{first_plan_id}/status", json={"status": "stopped"})

    second_workflow = mock_workflow_payload()
    second_workflow["nodes"][0]["parameters"]["fps"] = 20
    second = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": second_workflow},
    ).json()
    second_plan_id = second["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{second_plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{second_plan_id}/status", json={"status": "running"})

    rollback = client.post(f"/api/v1/deployments/{first_plan_id}/rollback")
    duplicate = client.post(f"/api/v1/deployments/{first_plan_id}/rollback")
    rollback_record = rollback.json()
    rollback_plan_id = rollback_record["plan"]["plan_id"]
    active_while_stopping = client.get("/api/v1/agents/edge-dev-01/deployments")

    assert rollback.status_code == 200
    assert rollback_record["status"] == "queued"
    assert rollback_record["plan"]["rollback_of_plan_id"] == first_plan_id
    assert rollback_record["plan"]["supersedes_plan_ids"] == [second_plan_id]
    assert rollback_record["plan"]["workflow"]["nodes"][0]["parameters"]["fps"] == 15
    assert duplicate.json()["plan"]["plan_id"] == rollback_plan_id
    assert [item["plan_id"] for item in active_while_stopping.json()] == [second_plan_id]
    assert active_while_stopping.json()[0]["desired_state"] == "stopped"

    client.post(f"/api/v1/deployments/{second_plan_id}/status", json={"status": "stopped"})
    records = client.get("/api/v1/deployments").json()
    promoted = next(item for item in records if item["plan"]["plan_id"] == rollback_plan_id)
    active_after_stop = client.get("/api/v1/agents/edge-dev-01/deployments")

    assert promoted["status"] == "pending"
    assert active_after_stop.json()[0]["plan_id"] == rollback_plan_id
    assert active_after_stop.json()[0]["desired_state"] == "running"


def _create_queued_rollback(client: TestClient) -> tuple[str, str]:
    client.post("/api/v1/agents/register", json=registration_payload())
    first = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    ).json()
    first_id = first["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{first_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{first_id}/status", json={"status": "running"})
    client.post(f"/api/v1/deployments/{first_id}/stop")
    client.post(f"/api/v1/deployments/{first_id}/status", json={"status": "stopped"})

    second_workflow = mock_workflow_payload()
    second_workflow["nodes"][0]["parameters"]["fps"] = 20
    second = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": second_workflow},
    ).json()
    second_id = second["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{second_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{second_id}/status", json={"status": "running"})
    rollback = client.post(f"/api/v1/deployments/{first_id}/rollback").json()
    return second_id, rollback["plan"]["plan_id"]


def test_rollback_stays_queued_when_runtime_stop_fails() -> None:
    client = TestClient(create_app())
    blocking_plan_id, rollback_plan_id = _create_queued_rollback(client)

    failed = client.post(
        f"/api/v1/deployments/{blocking_plan_id}/status",
        json={"status": "failed", "message": "容器停止失败"},
    )
    records = client.get("/api/v1/deployments").json()
    rollback = next(item for item in records if item["plan"]["plan_id"] == rollback_plan_id)

    assert failed.status_code == 200
    assert rollback["status"] == "queued"
    assert "停止失败" in rollback["message"]
    assert client.get("/api/v1/agents/edge-dev-01/deployments").json() == []


def test_queued_rollback_can_be_cancelled() -> None:
    client = TestClient(create_app())
    _blocking_plan_id, rollback_plan_id = _create_queued_rollback(client)

    cancelled = client.post(f"/api/v1/deployments/{rollback_plan_id}/stop")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "stopped"
    assert cancelled.json()["plan"]["desired_state"] == "stopped"
    assert "已取消" in cancelled.json()["message"]


def test_running_deployment_cannot_be_used_as_rollback_source() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    deployment = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    ).json()

    response = client.post(f"/api/v1/deployments/{deployment['plan']['plan_id']}/rollback")

    assert response.status_code == 409
    assert "已停止或失败" in response.json()["detail"]


def test_rollback_unknown_deployment_returns_not_found() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/deployments/missing/rollback")

    assert response.status_code == 404


def test_stop_unknown_deployment_returns_not_found() -> None:
    response = TestClient(create_app()).post("/api/v1/deployments/missing/stop")

    assert response.status_code == 404


def test_same_hardware_cannot_be_claimed_by_two_active_plans() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    first_workflow = mock_workflow_payload()
    second_workflow = mock_workflow_payload()
    second_workflow["nodes"][0]["parameters"]["fps"] = 20

    first = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": first_workflow},
    )
    rejected = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": second_workflow},
    )

    assert first.status_code == 200
    assert rejected.status_code == 409
    assert "硬件资源已被计划" in rejected.json()["detail"]


def test_agent_can_upload_and_studio_can_fetch_jpeg_preview() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "running"})
    jpeg = b"\xff\xd8preview-bytes\xff\xd9"

    upload = client.post(
        f"/api/v1/deployments/{plan_id}/preview",
        content=jpeg,
        headers={"Content-Type": "image/jpeg"},
    )
    preview = client.get(f"/api/v1/deployments/{plan_id}/preview")
    record = client.get("/api/v1/deployments").json()[0]

    assert upload.status_code == 200
    assert upload.json()["size_bytes"] == len(jpeg)
    assert preview.content == jpeg
    assert preview.headers["content-type"] == "image/jpeg"
    assert preview.headers["cache-control"] == "no-store"
    assert record["preview_updated_at"] is not None


def test_agent_can_upload_and_studio_can_fetch_png_preview() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "running"})
    png = b"\x89PNG\r\n\x1a\npreviewIEND\xaeB`\x82"

    upload = client.post(
        f"/api/v1/deployments/{plan_id}/preview",
        content=png,
        headers={"Content-Type": "image/png"},
    )
    preview = client.get(f"/api/v1/deployments/{plan_id}/preview")
    record = client.get("/api/v1/deployments").json()[0]

    assert upload.status_code == 200
    assert upload.json()["content_type"] == "image/png"
    assert preview.content == png
    assert preview.headers["content-type"] == "image/png"
    assert record["preview_content_type"] == "image/png"


def test_live_preview_session_temporarily_requests_higher_frame_rate() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "running"})
    jpeg = b"\xff\xd8preview-bytes\xff\xd9"

    started = client.post(f"/api/v1/deployments/{plan_id}/live-preview-session")
    high_rate_upload = client.post(
        f"/api/v1/deployments/{plan_id}/preview",
        content=jpeg,
        headers={"Content-Type": "image/jpeg"},
    )
    stopped = client.delete(f"/api/v1/deployments/{plan_id}/live-preview-session")
    normal_rate_upload = client.post(
        f"/api/v1/deployments/{plan_id}/preview",
        content=jpeg,
        headers={"Content-Type": "image/jpeg"},
    )

    assert started.status_code == 200
    assert started.json()["active"] is True
    assert started.json()["requested_fps"] == 10
    assert high_rate_upload.json()["live_session_active"] is True
    assert high_rate_upload.json()["requested_fps"] == 10
    assert stopped.json()["active"] is False
    assert normal_rate_upload.json()["live_session_active"] is False
    assert normal_rate_upload.json()["requested_fps"] == 5


def test_live_preview_session_requires_running_deployment() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]

    rejected = client.post(f"/api/v1/deployments/{plan_id}/live-preview-session")
    missing = client.post("/api/v1/deployments/missing/live-preview-session")

    assert rejected.status_code == 409
    assert missing.status_code == 404


def test_live_preview_session_expires_without_browser_renewal() -> None:
    clock = Clock()
    deployments = DeploymentRegistry(now=clock.now)
    client = TestClient(create_app(deployment_registry=deployments))
    client.post("/api/v1/agents/register", json=registration_payload())
    submission = client.post(
        "/api/v1/deployments",
        json={"target_agent_id": "edge-dev-01", "workflow": mock_workflow_payload()},
    )
    plan_id = submission.json()["plan"]["plan_id"]
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
    client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "running"})
    client.post(f"/api/v1/deployments/{plan_id}/live-preview-session")

    clock.advance(seconds=16)
    upload = client.post(
        f"/api/v1/deployments/{plan_id}/preview",
        content=b"\xff\xd8preview-bytes\xff\xd9",
        headers={"Content-Type": "image/jpeg"},
    )

    assert upload.json()["live_session_active"] is False
    assert upload.json()["requested_fps"] == 5


def test_preview_upload_rejects_invalid_content_and_unknown_plan() -> None:
    client = TestClient(create_app())

    invalid = client.post(
        "/api/v1/deployments/missing/preview",
        content=b"not-jpeg",
        headers={"Content-Type": "image/jpeg"},
    )
    wrong_type = client.post(
        "/api/v1/deployments/missing/preview",
        content=b"not-an-image",
        headers={"Content-Type": "text/plain"},
    )
    missing = client.post(
        "/api/v1/deployments/missing/preview",
        content=b"\xff\xd8ok\xff\xd9",
        headers={"Content-Type": "image/jpeg"},
    )

    assert invalid.status_code == 413
    assert wrong_type.status_code == 415
    assert missing.status_code == 404
