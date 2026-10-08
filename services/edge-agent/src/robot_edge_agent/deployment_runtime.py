"""端侧DeploymentPlan白名单执行、容器观测与状态协调。"""

from __future__ import annotations

import hashlib
import importlib
import json
import platform
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from robot_edge_agent.adapters import AdapterController
from robot_edge_agent.adapters.mock_camera import MockCameraAdapter
from robot_edge_agent.artifacts import (
    DEFAULT_RUNTIME_PACKAGE_ROOTS,
    collect_installed_artifacts,
)
from robot_edge_agent.calibration import (
    CalibrationProfile,
    CalibrationProfileError,
    load_calibration_profile,
)
from robot_edge_agent.spatial import (
    CameraIntrinsics,
    CartesianWorkspace,
    DetectionSample,
    RigidTransform,
    SpatialContractError,
    localize_detection,
    plan_cartesian_target,
)

RuntimeStatus = Literal["running", "failed"]
PreviewContentType = Literal["image/jpeg", "image/png"]
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
PreviewReader = Callable[[PurePosixPath], bytes]
ArtifactInventory = Callable[[], list[dict[str, Any]]]
MujocoRuntimeLoader = Callable[[Path], tuple[type[Any], type[Any]]]
WindowsVisionRuntimeLoader = Callable[[Path], type[Any]]

REALSENSE_YOLOX_IMAGE = "robot-platform/realsense-yolox-runtime:0.1.3"
REALSENSE_RESOURCE = "hardware.camera.realsense"
ENGINE_CACHE_ROOT = PurePosixPath("/data/robot-platform/cache/tensorrt")
RUN_OUTPUT_ROOT = PurePosixPath("/data/robot-platform/runs")
MAX_PREVIEW_BYTES = 256 * 1024
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$")

REALSENSE_PLUGIN_ARTIFACT = "hardware.camera.realsense.edge-plugin"
REALSENSE_RUNTIME_ARTIFACT = "runtime.realsense-yolox.runtime-image"
YOLOX_ENGINE_ARTIFACT = "algorithm.vision.yolo.tensorrt-engine"
MUJOCO_RUNTIME_ARTIFACT = "simulation.mujoco.arm.runtime"
MUJOCO_RUNTIME_LOCATOR = "robot-mujoco-arm-runtime"
MUJOCO_RUNTIME_VERSION = "0.4.0"
MUJOCO_CALIBRATION_ARTIFACT = "calibration.camera.mock-to-mujoco-arm"
MUJOCO_CALIBRATION_LOCATOR = "mock-camera-to-mujoco-arm-v1"
MUJOCO_CALIBRATION_VERSION = "1.0.0"
WINDOWS_WEBCAM_PLUGIN_ARTIFACT = "hardware.camera.windows-webcam.edge-plugin"
WINDOWS_WEBCAM_PLUGIN_LOCATOR = "robot-hardware-windows-webcam"
WINDOWS_WEBCAM_PLUGIN_VERSION = "0.1.2"
WINDOWS_VISION_RUNTIME_ARTIFACT = "runtime.windows-webcam-yolox-mujoco.runtime-package"
WINDOWS_VISION_RUNTIME_LOCATOR = "robot-windows-webcam-yolox-mujoco-runtime"
WINDOWS_VISION_RUNTIME_VERSION = "0.2.0"
_WINDOWS_VISION_REQUIRED_ARTIFACTS = {
    WINDOWS_WEBCAM_PLUGIN_ARTIFACT,
    MUJOCO_RUNTIME_ARTIFACT,
    WINDOWS_VISION_RUNTIME_ARTIFACT,
}
_REAL_REQUIRED_ARTIFACTS = {
    REALSENSE_PLUGIN_ARTIFACT,
    REALSENSE_RUNTIME_ARTIFACT,
    YOLOX_ENGINE_ARTIFACT,
}


class DeploymentClient(Protocol):
    """部署协调器所需的平台客户端接口。"""

    def list_deployments(self, agent_id: str) -> list[dict[str, Any]]: ...

    def report_deployment_status(
        self,
        plan_id: str,
        status: str,
        *,
        message: str = "",
        metrics: dict[str, float] | None = None,
    ) -> dict[str, Any]: ...

    def upload_deployment_preview(
        self,
        plan_id: str,
        image: bytes,
        *,
        content_type: PreviewContentType = "image/jpeg",
    ) -> dict[str, Any]: ...


class UnsupportedDeploymentPlan(ValueError):
    """计划包含当前Edge版本不允许执行的组件、参数或连接。"""


class RuntimeCommandError(RuntimeError):
    """受管Runtime命令执行失败。"""


@dataclass(frozen=True)
class RuntimeSnapshot:
    """一次Runtime观测结果。"""

    status: RuntimeStatus
    message: str
    metrics: dict[str, float]


@dataclass(frozen=True)
class _PreviewPayload:
    """A latest-only immutable preview snapshot captured by the control loop."""

    captured_monotonic: float
    value: object | None


@dataclass(frozen=True)
class _PreviewPublishResult:
    requested_interval_s: float | None
    render_ms: float
    upload_ms: float
    image_bytes: int


class WorkflowRuntime(Protocol):
    """EdgeDeploymentManager可协调的最小运行时接口。"""

    plan_id: str

    def start(self) -> RuntimeSnapshot: ...

    def tick(self) -> RuntimeSnapshot: ...

    def stop(self) -> RuntimeSnapshot | None: ...


def _default_command_runner(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _default_preview_reader(path: PurePosixPath) -> bytes:
    return Path(str(path)).read_bytes()


def _workflow_parts(
    plan: Mapping[str, Any],
) -> tuple[str, list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    plan_id = str(plan.get("plan_id", ""))
    workflow = plan.get("workflow")
    if not plan_id or not _SAFE_VALUE.fullmatch(plan_id) or not isinstance(workflow, Mapping):
        raise UnsupportedDeploymentPlan("部署计划缺少合法plan_id或workflow")
    raw_nodes = workflow.get("nodes")
    raw_links = workflow.get("links")
    if not isinstance(raw_nodes, list) or not isinstance(raw_links, list):
        raise UnsupportedDeploymentPlan("工作流节点或连接格式错误")
    if not all(isinstance(node, Mapping) for node in raw_nodes):
        raise UnsupportedDeploymentPlan("工作流节点格式错误")
    if not all(isinstance(link, Mapping) for link in raw_links):
        raise UnsupportedDeploymentPlan("工作流连接格式错误")
    return plan_id, raw_nodes, raw_links


def _node_parameters(node: Mapping[str, Any]) -> Mapping[str, Any]:
    parameters = node.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise UnsupportedDeploymentPlan("组件参数必须是对象")
    return parameters


def _integer_parameter(
    parameters: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int
) -> int:
    value = parameters.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise UnsupportedDeploymentPlan(f"参数{name}必须位于[{minimum}, {maximum}]的整数范围")
    return value


def _float_parameter(
    parameters: Mapping[str, Any], name: str, default: float, minimum: float, maximum: float
) -> float:
    value = parameters.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnsupportedDeploymentPlan(f"参数{name}必须是数值")
    result = float(value)
    if not minimum <= result <= maximum:
        raise UnsupportedDeploymentPlan(f"参数{name}必须位于[{minimum}, {maximum}]范围")
    return result


def _string_parameter(
    parameters: Mapping[str, Any], name: str, default: str, *, maximum: int = 128
) -> str:
    value = parameters.get(name, default)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise UnsupportedDeploymentPlan(f"参数{name}必须是长度不超过{maximum}的非空字符串")
    return value


def _camera_index_parameter(parameters: Mapping[str, Any]) -> int:
    value = parameters.get("camera_index", "0")
    if isinstance(value, int) and not isinstance(value, bool):
        index = value
    elif isinstance(value, str):
        match = re.fullmatch(r"(?:windows-camera-)?([0-9]+)", value.strip())
        if match is None:
            raise UnsupportedDeploymentPlan("Windows摄像头必须选择目标节点发现的设备")
        index = int(match.group(1))
    else:
        raise UnsupportedDeploymentPlan("Windows摄像头索引格式不合法")
    if not 0 <= index <= 15:
        raise UnsupportedDeploymentPlan("Windows摄像头索引必须位于[0, 15]")
    return index


def _validate_links(
    links: list[Mapping[str, Any]],
    expected: list[tuple[Mapping[str, Any], str, Mapping[str, Any], str]],
) -> None:
    if len(links) != len(expected):
        raise UnsupportedDeploymentPlan(f"工作流必须且只能包含{len(expected)}个连接")
    actual: set[tuple[str, str, str, str]] = set()
    for link in links:
        source = link.get("source")
        target = link.get("target")
        if not isinstance(source, Mapping) or not isinstance(target, Mapping):
            raise UnsupportedDeploymentPlan("工作流端口引用格式错误")
        actual.add(
            (
                str(source.get("node_id")),
                str(source.get("port")),
                str(target.get("node_id")),
                str(target.get("port")),
            )
        )
    required = {
        (
            str(source_node.get("node_id")),
            source_port,
            str(target_node.get("node_id")),
            target_port,
        )
        for source_node, source_port, target_node, target_port in expected
    }
    if actual != required:
        raise UnsupportedDeploymentPlan("工作流连接与已验证Runtime拓扑不一致")


def _normalized_artifact_locator(value: object) -> str:
    return str(value).strip().replace("\\", "/").casefold()


def _verify_real_artifact_contract(
    plan: Mapping[str, Any],
    *,
    engine_path: PurePosixPath,
    installed_artifacts: list[dict[str, Any]],
    machine: str,
) -> None:
    """在创建容器前复核计划锁定的三项真实Runtime制品。"""

    raw_artifacts = plan.get("artifacts")
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, Mapping) for item in raw_artifacts
    ):
        raise UnsupportedDeploymentPlan("真实视觉部署计划缺少端侧制品契约")
    by_id = {str(item.get("artifact_id", "")): item for item in raw_artifacts}
    if set(by_id) != _REAL_REQUIRED_ARTIFACTS:
        raise UnsupportedDeploymentPlan("真实视觉部署计划的制品集合不符合白名单")

    expected = {
        REALSENSE_PLUGIN_ARTIFACT: (
            "edge_plugin",
            "robot-hardware-realsense",
            "0.1.0",
            None,
        ),
        REALSENSE_RUNTIME_ARTIFACT: (
            "container_image",
            REALSENSE_YOLOX_IMAGE,
            "0.1.3",
            "aarch64",
        ),
        YOLOX_ENGINE_ARTIFACT: (
            "tensorrt_engine",
            str(engine_path),
            "0.1.0",
            "aarch64",
        ),
    }
    normalized_machine = machine.casefold()
    for artifact_id, (kind, locator, version, architecture) in expected.items():
        declared = by_id[artifact_id]
        if (
            declared.get("kind") != kind
            or _normalized_artifact_locator(declared.get("locator"))
            != _normalized_artifact_locator(locator)
            or declared.get("version") != version
            or declared.get("architecture") != architecture
        ):
            raise UnsupportedDeploymentPlan(f"制品契约字段不合法：{artifact_id}")
        if architecture and normalized_machine != architecture.casefold():
            raise UnsupportedDeploymentPlan(
                f"制品{artifact_id}要求{architecture}架构，当前为{machine}"
            )

        matching = next(
            (
                installed
                for installed in installed_artifacts
                if installed.get("kind") == kind
                and _normalized_artifact_locator(installed.get("locator"))
                == _normalized_artifact_locator(locator)
                and (not installed.get("version") or installed.get("version") == version)
            ),
            None,
        )
        if matching is None:
            raise UnsupportedDeploymentPlan(f"目标节点缺少运行制品：{artifact_id}")

        declared_digest = str(declared.get("digest") or "")
        installed_digest = str(matching.get("digest") or "")
        if artifact_id == YOLOX_ENGINE_ARTIFACT:
            if not _SHA256_DIGEST.fullmatch(declared_digest):
                raise UnsupportedDeploymentPlan("TensorRT Engine制品缺少合法SHA-256摘要")
            if not installed_digest:
                raise UnsupportedDeploymentPlan("本机TensorRT Engine尚未完成摘要盘点")
        if (
            declared_digest
            and installed_digest
            and (declared_digest.casefold() != installed_digest.casefold())
        ):
            raise UnsupportedDeploymentPlan(f"运行制品摘要不匹配：{artifact_id}")


def _resolve_mujoco_runtime_package(
    plan: Mapping[str, Any],
    *,
    installed_artifacts: list[dict[str, Any]],
    runtime_package_root: Path,
    expected_artifact_ids: set[str] | None = None,
) -> Path:
    """校验部署计划锁定的MuJoCo Runtime，并返回受管激活版本目录。"""

    raw_artifacts = plan.get("artifacts")
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, Mapping) for item in raw_artifacts
    ):
        raise UnsupportedDeploymentPlan("MuJoCo部署计划缺少端侧制品契约")
    by_id = {str(item.get("artifact_id", "")): item for item in raw_artifacts}
    if set(by_id) != (expected_artifact_ids or {MUJOCO_RUNTIME_ARTIFACT}):
        raise UnsupportedDeploymentPlan("MuJoCo部署计划的制品集合不符合白名单")

    declared = by_id[MUJOCO_RUNTIME_ARTIFACT]
    declared_digest = str(declared.get("digest") or "")
    if (
        declared.get("kind") != "runtime_package"
        or _normalized_artifact_locator(declared.get("locator"))
        != _normalized_artifact_locator(MUJOCO_RUNTIME_LOCATOR)
        or declared.get("version") != MUJOCO_RUNTIME_VERSION
        or not _SHA256_DIGEST.fullmatch(declared_digest)
    ):
        raise UnsupportedDeploymentPlan("MuJoCo Runtime制品契约字段不合法")

    installed = next(
        (
            item
            for item in installed_artifacts
            if item.get("artifact_id") == MUJOCO_RUNTIME_ARTIFACT
            and item.get("kind") == "runtime_package"
            and _normalized_artifact_locator(item.get("locator"))
            == _normalized_artifact_locator(MUJOCO_RUNTIME_LOCATOR)
            and item.get("version") == MUJOCO_RUNTIME_VERSION
            and item.get("active") is True
        ),
        None,
    )
    if installed is None:
        raise UnsupportedDeploymentPlan("目标节点缺少已激活的MuJoCo Runtime Package")
    installed_digest = str(installed.get("digest") or "")
    if installed_digest.casefold() != declared_digest.casefold():
        raise UnsupportedDeploymentPlan("MuJoCo Runtime Package摘要不匹配")

    install_path = Path(str(installed.get("install_path") or "")).resolve()
    release_root = (runtime_package_root / "releases").resolve()
    try:
        install_path.relative_to(release_root)
    except ValueError as error:
        raise UnsupportedDeploymentPlan("MuJoCo Runtime安装路径不在受管发布目录") from error
    if not (install_path / "robot_mujoco_arm" / "__init__.py").is_file():
        raise UnsupportedDeploymentPlan("MuJoCo Runtime安装内容不完整")
    return install_path


def _resolve_windows_vision_artifacts(
    plan: Mapping[str, Any],
    *,
    installed_artifacts: list[dict[str, Any]],
    runtime_package_root: Path,
) -> Path:
    """Verify the Windows camera plugin and managed YOLOX runtime package."""

    raw_artifacts = plan.get("artifacts")
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, Mapping) for item in raw_artifacts
    ):
        raise UnsupportedDeploymentPlan("Windows视觉部署计划缺少端侧制品契约")
    by_id = {str(item.get("artifact_id", "")): item for item in raw_artifacts}
    if set(by_id) != _WINDOWS_VISION_REQUIRED_ARTIFACTS:
        raise UnsupportedDeploymentPlan("Windows视觉部署计划的制品集合不符合白名单")

    expected = {
        WINDOWS_WEBCAM_PLUGIN_ARTIFACT: (
            "edge_plugin",
            WINDOWS_WEBCAM_PLUGIN_LOCATOR,
            WINDOWS_WEBCAM_PLUGIN_VERSION,
        ),
        WINDOWS_VISION_RUNTIME_ARTIFACT: (
            "runtime_package",
            WINDOWS_VISION_RUNTIME_LOCATOR,
            WINDOWS_VISION_RUNTIME_VERSION,
        ),
    }
    matched: dict[str, Mapping[str, Any]] = {}
    for artifact_id, (kind, locator, version) in expected.items():
        declared = by_id[artifact_id]
        declared_digest = str(declared.get("digest") or "")
        if (
            declared.get("kind") != kind
            or _normalized_artifact_locator(declared.get("locator"))
            != _normalized_artifact_locator(locator)
            or declared.get("version") != version
            or not _SHA256_DIGEST.fullmatch(declared_digest)
        ):
            raise UnsupportedDeploymentPlan(f"Windows视觉制品契约字段不合法：{artifact_id}")
        installed = next(
            (
                item
                for item in installed_artifacts
                if item.get("artifact_id") == artifact_id
                and item.get("kind") == kind
                and _normalized_artifact_locator(item.get("locator"))
                == _normalized_artifact_locator(locator)
                and item.get("version") == version
                and item.get("active") is True
            ),
            None,
        )
        if installed is None:
            raise UnsupportedDeploymentPlan(f"目标节点缺少已激活制品：{artifact_id}")
        if str(installed.get("digest") or "").casefold() != declared_digest.casefold():
            raise UnsupportedDeploymentPlan(f"运行制品摘要不匹配：{artifact_id}")
        matched[artifact_id] = installed

    install_path = Path(
        str(matched[WINDOWS_VISION_RUNTIME_ARTIFACT].get("install_path") or "")
    ).resolve()
    release_root = (runtime_package_root / "releases").resolve()
    try:
        install_path.relative_to(release_root)
    except ValueError as error:
        raise UnsupportedDeploymentPlan("Windows视觉Runtime安装路径不在受管发布目录") from error
    if not (
        install_path / "robot_windows_webcam_yolox_mujoco" / "__init__.py"
    ).is_file() or not (
        install_path / "robot_windows_webcam_yolox_mujoco" / "models" / "yolox_nano.onnx"
    ).is_file():
        raise UnsupportedDeploymentPlan("Windows视觉Runtime安装内容不完整")
    return install_path


def _resolve_mujoco_calibration_profile(
    plan: Mapping[str, Any],
    *,
    installed_artifacts: list[dict[str, Any]],
    calibration_root: Path,
) -> CalibrationProfile:
    """解析部署计划锁定的已激活标定版本，并拒绝目录逃逸或设备错配。"""

    raw_artifacts = plan.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise UnsupportedDeploymentPlan("空间定位流程缺少标定制品契约")
    declared = next(
        (
            item
            for item in raw_artifacts
            if isinstance(item, Mapping)
            and item.get("artifact_id") == MUJOCO_CALIBRATION_ARTIFACT
        ),
        None,
    )
    if declared is None:
        raise UnsupportedDeploymentPlan("空间定位流程未锁定相机到机械臂标定制品")
    declared_digest = str(declared.get("digest") or "")
    if (
        declared.get("kind") != "calibration_profile"
        or str(declared.get("locator")) != MUJOCO_CALIBRATION_LOCATOR
        or declared.get("version") != MUJOCO_CALIBRATION_VERSION
        or not _SHA256_DIGEST.fullmatch(declared_digest)
    ):
        raise UnsupportedDeploymentPlan("标定制品契约字段不合法")

    installed = next(
        (
            item
            for item in installed_artifacts
            if item.get("artifact_id") == MUJOCO_CALIBRATION_ARTIFACT
            and item.get("kind") == "calibration_profile"
            and item.get("locator") == MUJOCO_CALIBRATION_LOCATOR
            and item.get("version") == MUJOCO_CALIBRATION_VERSION
            and item.get("active") is True
        ),
        None,
    )
    if installed is None:
        raise UnsupportedDeploymentPlan("目标节点缺少已激活的相机到机械臂标定配置")
    if str(installed.get("digest") or "").casefold() != declared_digest.casefold():
        raise UnsupportedDeploymentPlan("标定配置摘要与部署计划不匹配")

    install_path = Path(str(installed.get("install_path") or "")).resolve()
    release_root = (calibration_root / "releases").resolve()
    try:
        install_path.relative_to(release_root)
    except ValueError as error:
        raise UnsupportedDeploymentPlan("标定配置安装路径不在受管发布目录") from error
    profile_path = install_path / "profile.json"
    try:
        content = profile_path.read_bytes()
        actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if actual_digest.casefold() != declared_digest.casefold():
            raise UnsupportedDeploymentPlan("标定文件内容摘要与部署计划不匹配")
        profile = load_calibration_profile(profile_path)
    except OSError as error:
        raise UnsupportedDeploymentPlan(f"标定配置无法读取：{error}") from error
    except CalibrationProfileError as error:
        raise UnsupportedDeploymentPlan(f"标定配置无法加载：{error}") from error
    if (
        profile.calibration_id != MUJOCO_CALIBRATION_LOCATOR
        or profile.version != MUJOCO_CALIBRATION_VERSION
        or profile.source_transport != "simulation"
        or profile.source_device_id != "mock-rgbd-camera-01"
        or profile.target_adapter_id != "hardware.arm.mujoco"
        or profile.target_device_id != "mujoco-six-axis-01"
    ):
        raise UnsupportedDeploymentPlan("标定配置与当前相机或机械臂实例不匹配")
    return profile


def _load_mujoco_runtime_api(install_path: Path) -> tuple[type[Any], type[Any]]:
    """只从已校验的受管发布目录加载MuJoCo执行代码。"""

    existing = sys.modules.get("robot_mujoco_arm")
    if existing is not None:
        module_file = Path(str(getattr(existing, "__file__", ""))).resolve()
        try:
            module_file.relative_to(install_path)
        except ValueError as error:
            raise RuntimeCommandError("MuJoCo Runtime已从非受管路径加载") from error
        return existing.MujocoArmRuntime, existing.CartesianTarget

    sys.path.insert(0, str(install_path))
    try:
        module = importlib.import_module("robot_mujoco_arm")
    except Exception as error:
        raise RuntimeCommandError(f"无法加载受管MuJoCo Runtime：{error}") from error
    finally:
        with suppress(ValueError):
            sys.path.remove(str(install_path))

    module_file = Path(str(getattr(module, "__file__", ""))).resolve()
    try:
        module_file.relative_to(install_path)
    except ValueError as error:
        raise RuntimeCommandError("MuJoCo Runtime未从受管发布目录加载") from error
    return module.MujocoArmRuntime, module.CartesianTarget


def _load_windows_vision_runtime_api(install_path: Path) -> type[Any]:
    """Load the camera/YOLOX runtime only from the verified managed release."""

    module_name = "robot_windows_webcam_yolox_mujoco"
    existing = sys.modules.get(module_name)
    if existing is not None:
        module_file = Path(str(getattr(existing, "__file__", ""))).resolve()
        try:
            module_file.relative_to(install_path)
        except ValueError as error:
            raise RuntimeCommandError("Windows视觉Runtime已从非受管路径加载") from error
        return existing.WindowsWebcamYoloxRuntime

    sys.path.insert(0, str(install_path))
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        raise RuntimeCommandError(f"无法加载受管Windows视觉Runtime：{error}") from error
    finally:
        with suppress(ValueError):
            sys.path.remove(str(install_path))
    module_file = Path(str(getattr(module, "__file__", ""))).resolve()
    try:
        module_file.relative_to(install_path)
    except ValueError as error:
        raise RuntimeCommandError("Windows视觉Runtime未从受管发布目录加载") from error
    return module.WindowsWebcamYoloxRuntime


@dataclass
class MockSafetyGate:
    """Deterministic safety state machine used before any actuator command."""

    command_ttl_ms: int
    velocity_limit: float
    state: str = "safe_stopped"
    commands_received: int = 0
    commands_forwarded: int = 0
    commands_blocked: int = 0
    reset_count: int = 0
    emergency_stop_active: bool = False
    fault_reason: str | None = None

    def start(self, *, authorized: bool, emergency_stop: bool) -> None:
        self.emergency_stop_active = emergency_stop
        self.fault_reason = "emergency_stop" if emergency_stop else None
        if emergency_stop:
            self.state = "emergency_stopped"
        elif authorized:
            self.state = "command_enabled"
        else:
            self.state = "unauthorized"

    def forward(self, *, command_age_ms: int) -> bool:
        self.commands_received += 1
        if self.state != "command_enabled":
            self.commands_blocked += 1
            return False
        if command_age_ms > self.command_ttl_ms:
            self.fault_reason = "command_timeout"
            self.state = "fault_latched"
            self.commands_blocked += 1
            return False
        self.commands_forwarded += 1
        return True

    def emergency_stop(self) -> None:
        self.emergency_stop_active = True
        self.fault_reason = "emergency_stop"
        self.state = "emergency_stopped"

    def inject_fault(self, reason: str = "injected_fault") -> None:
        self.fault_reason = reason
        self.state = "fault_latched"

    def reset(self, *, authorized: bool, emergency_stop_active: bool = False) -> bool:
        self.reset_count += 1
        self.emergency_stop_active = emergency_stop_active
        if emergency_stop_active:
            self.fault_reason = "emergency_stop"
            self.state = "emergency_stopped"
            return False
        self.fault_reason = None
        self.state = "command_enabled" if authorized else "unauthorized"
        return authorized

    def stop(self) -> None:
        self.state = "safe_stopped"

    def metrics(self) -> dict[str, float]:
        return {
            "safety_commands_received": float(self.commands_received),
            "safety_commands_forwarded": float(self.commands_forwarded),
            "safety_commands_blocked": float(self.commands_blocked),
            "safety_gate_enabled": float(self.state == "command_enabled"),
            "safety_gate_emergency_stop": float(self.emergency_stop_active),
            "safety_gate_fault_latched": float(self.fault_reason is not None),
            "safety_gate_resets": float(self.reset_count),
            "safety_gate_command_ttl_ms": float(self.command_ttl_ms),
            "safety_gate_velocity_limit": self.velocity_limit,
        }


class MockWorkflowRuntime:
    """支持视觉与视觉引导执行器拓扑的无硬件Runtime。"""

    def __init__(self, plan: Mapping[str, Any]) -> None:
        self.plan_id, nodes, links = _workflow_parts(plan)
        by_component = {str(node.get("component_ref")): node for node in nodes}
        component_set = frozenset(by_component)
        base_components = {
            "hardware.camera.mock",
            "algorithm.vision.mock_detector",
        }
        pipeline_components = base_components | {"algorithm.vision.mock_filter"}
        actuator_components = base_components | {
            "algorithm.motion.mock_target_selector",
            "hardware.arm.mock",
        }
        safe_actuator_components = actuator_components | {
            "algorithm.motion.safety_gate",
        }
        if component_set not in {
            frozenset(base_components),
            frozenset(pipeline_components),
            frozenset(actuator_components),
            frozenset(safe_actuator_components),
        }:
            raise UnsupportedDeploymentPlan("当前模拟Runtime不支持该组件组合")
        if any(str(node.get("component_version")) != "0.1.0" for node in by_component.values()):
            raise UnsupportedDeploymentPlan("当前模拟Runtime只允许0.1.0版本的模拟组件")
        expected_links = [
            (
                by_component["hardware.camera.mock"],
                "color",
                by_component["algorithm.vision.mock_detector"],
                "image",
            )
        ]
        filter_node = by_component.get("algorithm.vision.mock_filter")
        if filter_node is not None:
            expected_links.append(
                (
                    by_component["algorithm.vision.mock_detector"],
                    "detections",
                    filter_node,
                    "detections",
                )
            )
        target_selector_node = by_component.get("algorithm.motion.mock_target_selector")
        safety_gate_node = by_component.get("algorithm.motion.safety_gate")
        arm_node = by_component.get("hardware.arm.mock")
        if target_selector_node is not None and arm_node is not None:
            expected_links.append(
                (
                    by_component["algorithm.vision.mock_detector"],
                    "detections",
                    target_selector_node,
                    "detections",
                )
            )
            if safety_gate_node is None:
                expected_links.append((target_selector_node, "command", arm_node, "command"))
            else:
                expected_links.extend(
                    [
                        (target_selector_node, "command", safety_gate_node, "command"),
                        (safety_gate_node, "safe_command", arm_node, "command"),
                    ]
                )
        _validate_links(links, expected_links)

        parameters = _node_parameters(by_component["hardware.camera.mock"])
        self._camera_config = {
            "width": parameters.get("width", 640),
            "height": parameters.get("height", 480),
            "fps": parameters.get("fps", 15),
        }
        self._adapter = MockCameraAdapter()
        self._controller = AdapterController(self._adapter)
        self._ticks = 0
        self._detections = 0
        self._motion_commands = 0
        self._motion_commands_accepted = 0
        self._safety_state = "safe_stopped"
        self._last_metrics: dict[str, float] = {}
        self._filter_threshold = (
            _float_parameter(_node_parameters(filter_node), "min_confidence", 0.5, 0.0, 1.0)
            if filter_node is not None
            else None
        )
        self._target_confidence = (
            _float_parameter(
                _node_parameters(target_selector_node),
                "minimum_confidence",
                0.6,
                0.0,
                1.0,
            )
            if target_selector_node is not None
            else None
        )
        self._command_timeout_ms = (
            _integer_parameter(
                _node_parameters(arm_node),
                "command_timeout_ms",
                500,
                100,
                5000,
            )
            if arm_node is not None
            else None
        )
        self._max_velocity_scale = (
            _float_parameter(
                _node_parameters(arm_node),
                "max_velocity_scale",
                0.2,
                0.01,
                1.0,
            )
            if arm_node is not None
            else None
        )
        safety_parameters = (
            _node_parameters(safety_gate_node) if safety_gate_node is not None else {}
        )
        self._command_authorized = bool(
            _integer_parameter(safety_parameters, "command_authorized", 1, 0, 1)
        )
        self._emergency_stop_on_start = bool(
            _integer_parameter(safety_parameters, "emergency_stop", 0, 0, 1)
        )
        self._simulated_command_age_ms = _integer_parameter(
            safety_parameters, "simulated_command_age_ms", 0, 0, 10000
        )
        self._safety_gate = (
            MockSafetyGate(
                command_ttl_ms=_integer_parameter(
                    safety_parameters, "command_ttl_ms", 300, 50, 5000
                ),
                velocity_limit=_float_parameter(
                    safety_parameters, "velocity_limit", 0.2, 0.01, 1.0
                ),
            )
            if safety_gate_node is not None
            else None
        )

    @property
    def safety_state(self) -> str:
        """暴露模拟执行器的可测试安全状态。"""

        return self._safety_gate.state if self._safety_gate is not None else self._safety_state

    def emergency_stop(self) -> None:
        if self._safety_gate is None:
            raise UnsupportedDeploymentPlan("当前工作流没有独立安全门")
        self._safety_gate.emergency_stop()

    def inject_safety_fault(self, reason: str = "injected_fault") -> None:
        if self._safety_gate is None:
            raise UnsupportedDeploymentPlan("当前工作流没有独立安全门")
        self._safety_gate.inject_fault(reason)

    def reset_safety(self, *, authorized: bool = True, emergency_stop_active: bool = False) -> bool:
        if self._safety_gate is None:
            raise UnsupportedDeploymentPlan("当前工作流没有独立安全门")
        return self._safety_gate.reset(
            authorized=authorized,
            emergency_stop_active=emergency_stop_active,
        )

    def start(self) -> RuntimeSnapshot:
        device = self._adapter.probe()[0]
        self._controller.open(device)
        self._controller.configure(self._camera_config)
        self._controller.start()
        if self._safety_gate is not None:
            self._safety_gate.start(
                authorized=self._command_authorized,
                emergency_stop=self._emergency_stop_on_start,
            )
        elif self._target_confidence is not None:
            self._safety_state = "command_enabled"
        return self.tick()

    def tick(self) -> RuntimeSnapshot:
        health = self._controller.health()
        self._ticks += 1
        self._detections += 1
        metrics = {
            "configured_fps": float(health.metrics["fps"]),
            "runtime_ticks": float(self._ticks),
            "detections_emitted": float(self._detections),
        }
        if self._filter_threshold is not None:
            metrics["filter_min_confidence"] = self._filter_threshold
            metrics["filtered_detections_emitted"] = float(self._detections)
        if self._target_confidence is not None:
            self._motion_commands += 1
            command_accepted = True
            if self._safety_gate is not None:
                command_accepted = self._safety_gate.forward(
                    command_age_ms=self._simulated_command_age_ms
                )
                metrics.update(self._safety_gate.metrics())
            if command_accepted:
                self._motion_commands_accepted += 1
            metrics.update(
                {
                    "target_minimum_confidence": self._target_confidence,
                    "motion_commands_emitted": float(self._motion_commands),
                    "motion_commands_accepted": float(self._motion_commands_accepted),
                    "command_timeout_ms": float(self._command_timeout_ms or 0),
                    "max_velocity_scale": float(self._max_velocity_scale or 0),
                    "actuator_faults": 0.0,
                    "actuator_safe_stop": 0.0,
                }
            )
        self._last_metrics = dict(metrics)
        return RuntimeSnapshot(
            status="running",
            message=(
                "视觉引导模拟机械臂运行中"
                if self._target_confidence is not None
                else (
                    "三节点模拟工作流运行中"
                    if self._filter_threshold is not None
                    else "模拟工作流运行中"
                )
            ),
            metrics=metrics,
        )

    def stop(self) -> RuntimeSnapshot:
        self._safety_state = "safe_stopped"
        if self._safety_gate is not None:
            self._safety_gate.stop()
        self._controller.close()
        metrics = dict(self._last_metrics)
        if self._target_confidence is not None:
            metrics["actuator_safe_stop"] = 1.0
        if self._safety_gate is not None:
            metrics.update(self._safety_gate.metrics())
        return RuntimeSnapshot(
            status="running",
            message=(
                "模拟机械臂已进入安全停止状态"
                if self._target_confidence is not None
                else "模拟Runtime已停止"
            ),
            metrics=metrics,
        )


class MujocoWorkflowRuntime:
    """在受管MuJoCo Package中执行视觉引导虚拟机械臂流程。"""

    _LEGACY_COMPONENTS = frozenset(
        {
            "hardware.camera.mock",
            "algorithm.vision.mock_detector",
            "algorithm.motion.mock_target_selector",
            "algorithm.motion.safety_gate",
            "hardware.arm.mujoco",
        }
    )
    _SPATIAL_COMPONENTS = frozenset(
        {
            "hardware.camera.mock",
            "algorithm.vision.mock_detector",
            "algorithm.spatial.rgbd_target_localizer",
            "algorithm.motion.cartesian_point_to_point",
            "algorithm.motion.safety_gate",
            "hardware.arm.mujoco",
        }
    )
    _SUPPORTED_COMPONENTS = {_LEGACY_COMPONENTS, _SPATIAL_COMPONENTS}
    _COMPONENTS = {
        "hardware.camera.mock",
        "algorithm.vision.mock_detector",
        "algorithm.spatial.rgbd_target_localizer",
        "algorithm.motion.cartesian_point_to_point",
        "algorithm.motion.safety_gate",
        "hardware.arm.mujoco",
    }
    preview_content_type: PreviewContentType = "image/png"

    def __init__(
        self,
        plan: Mapping[str, Any],
        *,
        installed_artifacts: list[dict[str, Any]],
        runtime_package_root: Path,
        calibration_root: Path | None = None,
        runtime_loader: MujocoRuntimeLoader = _load_mujoco_runtime_api,
    ) -> None:
        raw_workflow = plan.get("workflow")
        workflow_version = (
            str(raw_workflow.get("version", ""))
            if isinstance(raw_workflow, Mapping)
            else ""
        )
        self.plan_id, nodes, links = _workflow_parts(plan)
        by_component = {str(node.get("component_ref")): node for node in nodes}
        components = frozenset(by_component)
        if components not in self._SUPPORTED_COMPONENTS or len(nodes) != len(components):
            raise UnsupportedDeploymentPlan("MuJoCo Runtime只允许已验证的安全仿真拓扑")
        if any(str(node.get("component_version")) != "0.1.0" for node in nodes):
            raise UnsupportedDeploymentPlan("MuJoCo Runtime只允许0.1.0版本组件")

        camera = by_component["hardware.camera.mock"]
        detector = by_component["algorithm.vision.mock_detector"]
        safety_gate = by_component["algorithm.motion.safety_gate"]
        arm = by_component["hardware.arm.mujoco"]
        self._spatial_pipeline = components == self._SPATIAL_COMPONENTS
        artifact_backed_calibration = False
        if self._spatial_pipeline:
            if workflow_version == "0.3.0":
                artifact_backed_calibration = True
            elif workflow_version != "0.2.0":
                raise UnsupportedDeploymentPlan(
                    "空间定位MuJoCo流程只允许0.2.0或0.3.0版本"
                )
            localizer = by_component["algorithm.spatial.rgbd_target_localizer"]
            planner = by_component["algorithm.motion.cartesian_point_to_point"]
            _validate_links(
                links,
                [
                    (camera, "color", detector, "image"),
                    (detector, "detections", localizer, "detections"),
                    (camera, "depth", localizer, "depth"),
                    (camera, "camera_info", localizer, "camera_info"),
                    (localizer, "target_pose", planner, "target_pose"),
                    (planner, "command", safety_gate, "command"),
                    (safety_gate, "safe_command", arm, "command"),
                ],
            )
            target_parameters = _node_parameters(localizer)
            planner_parameters = _node_parameters(planner)
        else:
            selector = by_component["algorithm.motion.mock_target_selector"]
            _validate_links(
                links,
                [
                    (camera, "color", detector, "image"),
                    (detector, "detections", selector, "detections"),
                    (selector, "command", safety_gate, "command"),
                    (safety_gate, "safe_command", arm, "command"),
                ],
            )
            target_parameters = _node_parameters(selector)
            planner_parameters = {}

        expected_artifact_ids = {MUJOCO_RUNTIME_ARTIFACT}
        if artifact_backed_calibration:
            expected_artifact_ids.add(MUJOCO_CALIBRATION_ARTIFACT)
        self._install_path = _resolve_mujoco_runtime_package(
            plan,
            installed_artifacts=installed_artifacts,
            runtime_package_root=runtime_package_root,
            expected_artifact_ids=expected_artifact_ids,
        )
        calibration_profile = (
            _resolve_mujoco_calibration_profile(
                plan,
                installed_artifacts=installed_artifacts,
                calibration_root=(
                    calibration_root
                    if calibration_root is not None
                    else runtime_package_root.resolve().parent / "calibrations"
                ),
            )
            if artifact_backed_calibration
            else None
        )
        self._runtime_loader = runtime_loader
        camera_parameters = _node_parameters(camera)
        safety_parameters = _node_parameters(safety_gate)
        arm_parameters = _node_parameters(arm)
        self._camera_config = {
            "width": _integer_parameter(camera_parameters, "width", 640, 160, 1920),
            "height": _integer_parameter(camera_parameters, "height", 480, 120, 1080),
            "fps": _integer_parameter(camera_parameters, "fps", 15, 1, 60),
        }
        self._target_confidence = _float_parameter(
            target_parameters, "minimum_confidence", 0.6, 0.0, 1.0
        )
        self._command_timeout_ms = _integer_parameter(
            arm_parameters, "command_timeout_ms", 500, 100, 5000
        )
        self._max_velocity_scale = _float_parameter(
            arm_parameters, "max_velocity_scale", 0.2, 0.01, 1.0
        )
        self._target_coordinates = None
        self._intrinsics: CameraIntrinsics | None = None
        self._camera_to_base: RigidTransform | None = None
        self._workspace: CartesianWorkspace | None = None
        self._target_ttl_ms = 300
        if self._spatial_pipeline:
            try:
                if artifact_backed_calibration:
                    if calibration_profile is None:
                        raise SpatialContractError("空间定位流程缺少已解析的标定配置")
                    requested_calibration_id = _string_parameter(
                        target_parameters,
                        "calibration_id",
                        MUJOCO_CALIBRATION_LOCATOR,
                    )
                    if requested_calibration_id != calibration_profile.calibration_id:
                        raise SpatialContractError("节点选择的标定ID与部署制品不一致")
                    self._intrinsics = calibration_profile.intrinsics
                    self._camera_to_base = calibration_profile.camera_to_target
                else:
                    calibration_id = _string_parameter(
                        target_parameters,
                        "calibration_id",
                        "mock-camera-to-arm-v1",
                    )
                    self._intrinsics = CameraIntrinsics(
                        frame_id="camera_optical",
                        width_px=self._camera_config["width"],
                        height_px=self._camera_config["height"],
                        fx_px=_float_parameter(
                            target_parameters, "camera_fx_px", 600.0, 0.01, 100000.0
                        ),
                        fy_px=_float_parameter(
                            target_parameters, "camera_fy_px", 600.0, 0.01, 100000.0
                        ),
                        cx_px=_float_parameter(
                            target_parameters, "camera_cx_px", 320.0, -100000.0, 100000.0
                        ),
                        cy_px=_float_parameter(
                            target_parameters, "camera_cy_px", 240.0, -100000.0, 100000.0
                        ),
                    )
                    self._camera_to_base = RigidTransform(
                        source_frame="camera_optical",
                        target_frame="arm_base",
                        calibration_id=calibration_id,
                        translation_m=(
                            _float_parameter(
                                target_parameters,
                                "camera_to_base_x_m",
                                0.25,
                                -5.0,
                                5.0,
                            ),
                            _float_parameter(
                                target_parameters,
                                "camera_to_base_y_m",
                                0.1,
                                -5.0,
                                5.0,
                            ),
                            _float_parameter(
                                target_parameters,
                                "camera_to_base_z_m",
                                0.0,
                                -5.0,
                                5.0,
                            ),
                        ),
                        roll_rad=_float_parameter(
                            target_parameters,
                            "camera_to_base_roll_rad",
                            0.0,
                            -6.2832,
                            6.2832,
                        ),
                        pitch_rad=_float_parameter(
                            target_parameters,
                            "camera_to_base_pitch_rad",
                            0.0,
                            -6.2832,
                            6.2832,
                        ),
                        yaw_rad=_float_parameter(
                            target_parameters,
                            "camera_to_base_yaw_rad",
                            0.0,
                            -6.2832,
                            6.2832,
                        ),
                    )
                if (
                    self._intrinsics.width_px != self._camera_config["width"]
                    or self._intrinsics.height_px != self._camera_config["height"]
                ):
                    raise SpatialContractError("标定内参分辨率与相机运行配置不一致")
                self._workspace = CartesianWorkspace(
                    x_m=(
                        _float_parameter(planner_parameters, "workspace_x_min_m", 0.05, -5, 5),
                        _float_parameter(planner_parameters, "workspace_x_max_m", 0.75, -5, 5),
                    ),
                    y_m=(
                        _float_parameter(planner_parameters, "workspace_y_min_m", -0.55, -5, 5),
                        _float_parameter(planner_parameters, "workspace_y_max_m", 0.55, -5, 5),
                    ),
                    z_m=(
                        _float_parameter(planner_parameters, "workspace_z_min_m", 0.05, -5, 5),
                        _float_parameter(planner_parameters, "workspace_z_max_m", 0.85, -5, 5),
                    ),
                )
            except SpatialContractError as error:
                raise UnsupportedDeploymentPlan(str(error)) from error
            self._target_ttl_ms = _integer_parameter(
                target_parameters, "target_ttl_ms", 300, 50, 5000
            )
            self._detection_u_px = _float_parameter(
                target_parameters,
                "simulated_detection_u_px",
                320,
                0,
                self._camera_config["width"] - 1,
            )
            self._detection_v_px = _float_parameter(
                target_parameters,
                "simulated_detection_v_px",
                240,
                0,
                self._camera_config["height"] - 1,
            )
            self._detection_depth_m = _float_parameter(
                target_parameters, "simulated_depth_m", 0.45, 0.01, 5
            )
        else:
            self._target_coordinates = (
                _float_parameter(arm_parameters, "target_x_m", 0.25, 0.05, 0.75),
                _float_parameter(arm_parameters, "target_y_m", 0.1, -0.55, 0.55),
                _float_parameter(arm_parameters, "target_z_m", 0.45, 0.05, 0.85),
            )
        self._simulation_steps = _integer_parameter(
            arm_parameters, "simulation_steps", 1000, 1, 10000
        )
        self._command_authorized = bool(
            _integer_parameter(safety_parameters, "command_authorized", 1, 0, 1)
        )
        self._emergency_stop_on_start = bool(
            _integer_parameter(safety_parameters, "emergency_stop", 0, 0, 1)
        )
        self._simulated_command_age_ms = _integer_parameter(
            safety_parameters, "simulated_command_age_ms", 0, 0, 10000
        )
        self._safety_gate = MockSafetyGate(
            command_ttl_ms=_integer_parameter(safety_parameters, "command_ttl_ms", 300, 50, 5000),
            velocity_limit=_float_parameter(safety_parameters, "velocity_limit", 0.2, 0.01, 1.0),
        )
        self._adapter = MockCameraAdapter()
        self._controller = AdapterController(self._adapter)
        self._arm_runtime: Any | None = None
        self._target_type: type[Any] | None = None
        self._ticks = 0
        self._detections = 0
        self._motion_commands = 0
        self._motion_commands_accepted = 0
        self._last_metrics: dict[str, float] = {}

    @property
    def safety_state(self) -> str:
        return self._safety_gate.state

    def _require_arm(self) -> Any:
        if self._arm_runtime is None:
            raise RuntimeCommandError("MuJoCo Runtime尚未启动")
        return self._arm_runtime

    def start(self) -> RuntimeSnapshot:
        runtime_type, target_type = self._runtime_loader(self._install_path)
        self._target_type = target_type
        self._arm_runtime = runtime_type(safety_state="safe_stopped")
        device = self._adapter.probe()[0]
        self._controller.open(device)
        self._controller.configure(self._camera_config)
        self._controller.start()
        self._safety_gate.start(
            authorized=self._command_authorized,
            emergency_stop=self._emergency_stop_on_start,
        )
        if self._safety_gate.state == "command_enabled":
            self._arm_runtime.enable_commands()
        elif self._safety_gate.state == "emergency_stopped":
            self._arm_runtime.emergency_stop()
        return self.tick()

    def tick(self) -> RuntimeSnapshot:
        arm = self._require_arm()
        health = self._controller.health()
        self._ticks += 1
        self._detections += 1
        detection_confidence = 0.9
        metrics = {
            "configured_fps": float(health.metrics["fps"]),
            "runtime_ticks": float(self._ticks),
            "detections_emitted": float(self._detections),
            "detection_confidence": detection_confidence,
            "target_minimum_confidence": self._target_confidence,
            "command_timeout_ms": float(self._command_timeout_ms),
            "max_velocity_scale": self._max_velocity_scale,
            "actuator_faults": 0.0,
            "actuator_safe_stop": 0.0,
        }
        if detection_confidence >= self._target_confidence:
            if self._spatial_pipeline:
                if (
                    self._intrinsics is None
                    or self._camera_to_base is None
                    or self._workspace is None
                ):
                    raise RuntimeCommandError("空间定位契约尚未初始化")
                captured_at_ms = int(time.time() * 1000)
                target_pose = localize_detection(
                    DetectionSample(
                        sequence=self._detections,
                        captured_at_ms=captured_at_ms,
                        u_px=self._detection_u_px,
                        v_px=self._detection_v_px,
                        depth_m=self._detection_depth_m,
                        label="simulated_target",
                        confidence=detection_confidence,
                    ),
                    self._intrinsics,
                    self._camera_to_base,
                    ttl_ms=self._target_ttl_ms,
                )
                command = plan_cartesian_target(
                    target_pose,
                    self._workspace,
                    now_ms=captured_at_ms,
                )
                target_coordinates = command.position_m
                metrics["target_pose_valid"] = 1.0
                metrics["target_pose_x_m"] = target_coordinates[0]
                metrics["target_pose_y_m"] = target_coordinates[1]
                metrics["target_pose_z_m"] = target_coordinates[2]
                metrics["target_pose_ttl_ms"] = float(self._target_ttl_ms)
            else:
                if self._target_coordinates is None:
                    raise RuntimeCommandError("旧版目标坐标尚未初始化")
                target_coordinates = self._target_coordinates
            metrics["mujoco_target_x_m"] = target_coordinates[0]
            metrics["mujoco_target_y_m"] = target_coordinates[1]
            metrics["mujoco_target_z_m"] = target_coordinates[2]
            self._motion_commands += 1
            if self._safety_gate.forward(command_age_ms=self._simulated_command_age_ms):
                if self._target_type is None:
                    raise RuntimeCommandError("MuJoCo目标类型尚未加载")
                target = self._target_type(*target_coordinates)
                state = arm.command_target(
                    target,
                    simulation_steps=self._simulation_steps,
                )
                self._motion_commands_accepted += 1
                metrics.update(
                    {
                        "mujoco_position_error_m": float(state.position_error_m),
                        "mujoco_target_reached": float(state.target_reached),
                        "mujoco_simulation_steps": float(state.simulation_steps),
                        "mujoco_end_effector_x_m": float(state.end_effector[0]),
                        "mujoco_end_effector_y_m": float(state.end_effector[1]),
                        "mujoco_end_effector_z_m": float(state.end_effector[2]),
                    }
                )
            elif self._safety_gate.state in {"emergency_stopped", "fault_latched"}:
                arm.emergency_stop()
                metrics["actuator_safe_stop"] = 1.0
        metrics.update(self._safety_gate.metrics())
        metrics["motion_commands_emitted"] = float(self._motion_commands)
        metrics["motion_commands_accepted"] = float(self._motion_commands_accepted)
        self._last_metrics = dict(metrics)
        return RuntimeSnapshot(
            status="running",
            message="MuJoCo视觉引导机械臂仿真运行中",
            metrics=metrics,
        )

    def read_preview(self) -> bytes | None:
        """Render a bounded PNG preview; preview failures never stop the workflow."""

        try:
            image = self._require_arm().render_png(width=480, height=360)
        except Exception:
            return None
        if (
            len(image) < 20
            or len(image) > MAX_PREVIEW_BYTES
            or not image.startswith(b"\x89PNG\r\n\x1a\n")
            or not image.endswith(b"IEND\xaeB`\x82")
        ):
            return None
        return image

    def emergency_stop(self) -> None:
        self._safety_gate.emergency_stop()
        self._require_arm().emergency_stop()

    def inject_safety_fault(self, reason: str = "injected_fault") -> None:
        self._safety_gate.inject_fault(reason)
        self._require_arm().emergency_stop()

    def reset_safety(self, *, authorized: bool = True, emergency_stop_active: bool = False) -> bool:
        reset = self._safety_gate.reset(
            authorized=authorized,
            emergency_stop_active=emergency_stop_active,
        )
        self._require_arm().reset_safety(
            authorized=authorized,
            emergency_stop_active=emergency_stop_active,
        )
        return reset

    def stop(self) -> RuntimeSnapshot:
        self._safety_gate.stop()
        if self._arm_runtime is not None:
            self._arm_runtime.emergency_stop()
        self._controller.close()
        metrics = dict(self._last_metrics)
        metrics.update(self._safety_gate.metrics())
        metrics["actuator_safe_stop"] = 1.0
        return RuntimeSnapshot(
            status="running",
            message="MuJoCo机械臂已进入安全停止状态",
            metrics=metrics,
        )


class WindowsWebcamYoloxMujocoRuntime:
    """Run the validated Windows webcam -> YOLOX -> MuJoCo simulation loop."""

    _COMPONENTS = {
        "hardware.camera.windows-webcam",
        "algorithm.vision.yolox_onnx",
        "algorithm.motion.detection_to_sim_target",
        "algorithm.motion.safety_gate",
        "hardware.arm.mujoco",
    }
    preview_content_type: PreviewContentType = "image/jpeg"
    # Five sampled composite frames per second keeps the browser responsive
    # without turning the Platform API into the camera's primary data plane.
    preview_interval_s = 0.2

    def __init__(
        self,
        plan: Mapping[str, Any],
        *,
        installed_artifacts: list[dict[str, Any]],
        runtime_package_root: Path,
        mujoco_loader: MujocoRuntimeLoader = _load_mujoco_runtime_api,
        vision_loader: WindowsVisionRuntimeLoader = _load_windows_vision_runtime_api,
        verify_artifacts: bool = True,
    ) -> None:
        self.plan_id, nodes, links = _workflow_parts(plan)
        raw_workflow = plan.get("workflow")
        workflow_version = (
            str(raw_workflow.get("version", "")) if isinstance(raw_workflow, Mapping) else ""
        )
        if workflow_version not in {"0.1.2", "0.1.3", "0.1.4", "0.1.5"}:
            raise UnsupportedDeploymentPlan("Unsupported Windows vision workflow version")
        self._realtime_tracking = workflow_version in {"0.1.4", "0.1.5"}
        by_component = {str(node.get("component_ref")): node for node in nodes}
        if set(by_component) != self._COMPONENTS or len(nodes) != len(self._COMPONENTS):
            raise UnsupportedDeploymentPlan("Windows视觉闭环只允许已验证的五节点仿真拓扑")
        if any(str(node.get("component_version")) != "0.1.0" for node in nodes):
            raise UnsupportedDeploymentPlan("Windows视觉闭环只允许0.1.0版本组件")

        camera = by_component["hardware.camera.windows-webcam"]
        detector = by_component["algorithm.vision.yolox_onnx"]
        mapper = by_component["algorithm.motion.detection_to_sim_target"]
        safety_gate = by_component["algorithm.motion.safety_gate"]
        arm = by_component["hardware.arm.mujoco"]
        _validate_links(
            links,
            [
                (camera, "color", detector, "image"),
                (detector, "detections", mapper, "detections"),
                (mapper, "command", safety_gate, "command"),
                (safety_gate, "safe_command", arm, "command"),
            ],
        )

        if verify_artifacts:
            self._mujoco_path = _resolve_mujoco_runtime_package(
                plan,
                installed_artifacts=installed_artifacts,
                runtime_package_root=runtime_package_root,
                expected_artifact_ids=_WINDOWS_VISION_REQUIRED_ARTIFACTS,
            )
            self._vision_path = _resolve_windows_vision_artifacts(
                plan,
                installed_artifacts=installed_artifacts,
                runtime_package_root=runtime_package_root,
            )
        else:
            # A historical stop command must stay executable even when its
            # original runtime packages are no longer installed.
            self._mujoco_path = runtime_package_root
            self._vision_path = runtime_package_root
        self._mujoco_loader = mujoco_loader
        self._vision_loader = vision_loader

        camera_parameters = _node_parameters(camera)
        detector_parameters = _node_parameters(detector)
        mapper_parameters = _node_parameters(mapper)
        safety_parameters = _node_parameters(safety_gate)
        arm_parameters = _node_parameters(arm)
        model_id = _string_parameter(detector_parameters, "model_id", "yolox.nano.coco")
        if model_id != "yolox.nano.coco":
            raise UnsupportedDeploymentPlan("Windows视觉闭环只允许固定的YOLOX Nano COCO模型")
        self._camera_config = {
            "camera_index": _camera_index_parameter(camera_parameters),
            "width": _integer_parameter(camera_parameters, "width", 640, 160, 1920),
            "height": _integer_parameter(camera_parameters, "height", 480, 120, 1080),
            "fps": _integer_parameter(camera_parameters, "fps", 15, 1, 30),
        }
        self._detector_config = {
            "confidence": _float_parameter(
                detector_parameters, "confidence", 0.4, 0.01, 0.99
            ),
            "nms_threshold": _float_parameter(
                detector_parameters, "nms", 0.45, 0.01, 0.99
            ),
            "target_label": _string_parameter(
                detector_parameters, "target_label", "cell phone", maximum=64
            ),
        }
        self._minimum_confidence = _float_parameter(
            mapper_parameters, "minimum_confidence", 0.5, 0.01, 0.99
        )
        self._target_x_m = _float_parameter(
            mapper_parameters, "target_x_m", 0.35, 0.05, 0.75
        )
        self._horizontal_span_m = _float_parameter(
            mapper_parameters, "horizontal_span_m", 0.4, 0.01, 1.1
        )
        self._vertical_span_m = _float_parameter(
            mapper_parameters, "vertical_span_m", 0.3, 0.01, 0.8
        )
        self._center_z_m = _float_parameter(
            mapper_parameters, "center_z_m", 0.4, 0.05, 0.85
        )
        self._simulation_steps = _integer_parameter(
            arm_parameters, "simulation_steps", 600, 1, 10000
        )
        self._command_timeout_ms = _integer_parameter(
            arm_parameters, "command_timeout_ms", 700, 100, 5000
        )
        self._max_velocity_scale = _float_parameter(
            arm_parameters, "max_velocity_scale", 0.15, 0.01, 1.0
        )
        self._safety_gate = MockSafetyGate(
            command_ttl_ms=_integer_parameter(
                safety_parameters, "command_ttl_ms", 300, 50, 5000
            ),
            velocity_limit=_float_parameter(
                safety_parameters, "velocity_limit", 0.2, 0.01, 1.0
            ),
        )
        self._command_authorized = bool(
            _integer_parameter(safety_parameters, "command_authorized", 1, 0, 1)
        )
        self._emergency_stop_on_start = bool(
            _integer_parameter(safety_parameters, "emergency_stop", 0, 0, 1)
        )
        self._simulated_command_age_ms = _integer_parameter(
            safety_parameters, "simulated_command_age_ms", 0, 0, 10000
        )
        self._vision_runtime: Any | None = None
        self._arm_runtime: Any | None = None
        self._target_type: type[Any] | None = None
        self._ticks = 0
        self._detections_total = 0
        self._motion_commands = 0
        self._motion_commands_accepted = 0
        self._inference_total_ms = 0.0
        self._active_motion = False
        self._command_timed_out = False
        self._last_command_monotonic: float | None = None
        self._mujoco_simulation_steps = 0
        self._last_metrics: dict[str, float] = {}
        self._stage_total_ms = {
            "camera_read": 0.0,
            "preprocess": 0.0,
            "inference": 0.0,
            "postprocess": 0.0,
            "annotation": 0.0,
            "vision_total": 0.0,
            "control_path": 0.0,
            "runtime_tick": 0.0,
        }

    @property
    def execution_interval_s(self) -> float:
        """Run vision and simulation independently from the Agent heartbeat."""

        return 1.0 / float(self._camera_config["fps"])

    @property
    def safety_state(self) -> str:
        return self._safety_gate.state

    def _require_started(self) -> tuple[Any, Any]:
        if self._vision_runtime is None or self._arm_runtime is None:
            raise RuntimeCommandError("Windows视觉闭环Runtime尚未启动")
        return self._vision_runtime, self._arm_runtime

    def start(self) -> RuntimeSnapshot:
        runtime_type = self._vision_loader(self._vision_path)
        arm_type, target_type = self._mujoco_loader(self._mujoco_path)
        self._target_type = target_type
        vision = runtime_type(**self._camera_config, **self._detector_config)
        vision.start()
        try:
            arm = arm_type(safety_state="safe_stopped")
        except Exception:
            vision.stop()
            raise
        self._vision_runtime = vision
        self._arm_runtime = arm
        self._safety_gate.start(
            authorized=self._command_authorized,
            emergency_stop=self._emergency_stop_on_start,
        )
        if self._safety_gate.state == "command_enabled":
            arm.enable_commands()
        elif self._safety_gate.state == "emergency_stopped":
            arm.emergency_stop()
        return self.tick()

    def _map_target(self, detection: Any, width: int, height: int) -> tuple[float, float, float]:
        center_x, center_y = detection.center
        normalized_x = min(1.0, max(0.0, float(center_x) / max(1, width)))
        normalized_y = min(1.0, max(0.0, float(center_y) / max(1, height)))
        target_y = (0.5 - normalized_x) * self._horizontal_span_m
        target_z = self._center_z_m + (0.5 - normalized_y) * self._vertical_span_m
        return (
            self._target_x_m,
            min(0.55, max(-0.55, target_y)),
            min(0.85, max(0.05, target_z)),
        )

    @staticmethod
    def _incremental_arm(arm: Any) -> bool:
        return callable(getattr(arm, "set_target", None)) and callable(
            getattr(arm, "advance", None)
        )

    def _advance_arm(
        self,
        arm: Any,
        metrics: dict[str, float],
        *,
        advance_physics: bool = True,
    ) -> None:
        """Advance one real-time-sized simulation slice per vision tick."""

        if not self._active_motion or self._last_command_monotonic is None:
            return
        command_age_ms = (time.monotonic() - self._last_command_monotonic) * 1000.0
        metrics["motion_command_age_ms"] = command_age_ms
        if command_age_ms > self._command_timeout_ms:
            hold_position = getattr(arm, "hold_position", None)
            if callable(hold_position):
                hold_position()
            self._active_motion = False
            self._command_timed_out = True
            metrics["actuator_safe_stop"] = 1.0
            metrics["actuator_command_timed_out"] = 1.0
            return
        if not advance_physics:
            return

        timestep_s = float(getattr(arm, "timestep_s", 0.002))
        if not 0.0001 <= timestep_s <= 0.1:
            raise RuntimeCommandError("MuJoCo physics timestep is outside the safe range")
        step_count = min(
            self._simulation_steps,
            max(1, round(self.execution_interval_s / timestep_s)),
        )
        state = arm.advance(
            simulation_steps=step_count,
            velocity_scale=self._max_velocity_scale,
        )
        self._mujoco_simulation_steps += int(state.simulation_steps)
        metrics.update(
            {
                "mujoco_position_error_m": float(state.position_error_m),
                "mujoco_target_reached": float(state.target_reached),
                "mujoco_simulation_steps": float(self._mujoco_simulation_steps),
                "mujoco_steps_per_tick": float(state.simulation_steps),
                "mujoco_physics_rate_hz": 1.0 / timestep_s,
                "mujoco_simulation_time_s": self._mujoco_simulation_steps * timestep_s,
                "mujoco_end_effector_x_m": float(state.end_effector[0]),
                "mujoco_end_effector_y_m": float(state.end_effector[1]),
                "mujoco_end_effector_z_m": float(state.end_effector[2]),
                "actuator_command_timed_out": float(self._command_timed_out),
            }
        )

    def tick(self) -> RuntimeSnapshot:
        tick_started = time.perf_counter()
        vision, arm = self._require_started()
        result = vision.detect()
        control_started = time.perf_counter()
        self._ticks += 1
        self._detections_total += len(result.detections)
        self._inference_total_ms += float(result.inference_ms)
        target = result.target
        target_label = str(self._detector_config["target_label"])
        is_phone_target = target_label == "cell phone"
        target_confidence = float(target.confidence if target is not None else 0.0)
        metrics = {
            "configured_fps": float(self._camera_config["fps"]),
            "runtime_ticks": float(self._ticks),
            "camera_frames": float(result.frame_sequence),
            "detections_latest": float(len(result.detections)),
            "detections_total": float(self._detections_total),
            "target_detected": float(target is not None),
            "target_confidence": target_confidence,
            # Kept for existing dashboards, but never claim that another COCO
            # class such as "remote" is semantically a phone.
            "phone_detected": float(target is not None and is_phone_target),
            "phone_confidence": target_confidence if is_phone_target else 0.0,
            "inference_latest_ms": float(result.inference_ms),
            "inference_mean_ms": self._inference_total_ms / self._ticks,
            "target_minimum_confidence": self._minimum_confidence,
            "command_timeout_ms": float(self._command_timeout_ms),
            "max_velocity_scale": self._max_velocity_scale,
            "mujoco_realtime_tracking": float(self._realtime_tracking),
            "actuator_faults": 0.0,
            "actuator_safe_stop": float(self._command_timed_out),
            "actuator_command_timed_out": float(self._command_timed_out),
        }
        stage_values = {
            "camera_read": float(getattr(result, "camera_read_ms", 0.0)),
            "preprocess": float(getattr(result, "preprocess_ms", 0.0)),
            "inference": float(result.inference_ms),
            "postprocess": float(getattr(result, "postprocess_ms", 0.0)),
            "annotation": float(getattr(result, "annotation_ms", 0.0)),
            "vision_total": float(getattr(result, "total_ms", result.inference_ms)),
        }
        for stage, duration_ms in stage_values.items():
            self._stage_total_ms[stage] += duration_ms
            metrics[f"{stage}_latest_ms"] = duration_ms
            metrics[f"{stage}_mean_ms"] = self._stage_total_ms[stage] / self._ticks
        if target is not None and float(target.confidence) >= self._minimum_confidence:
            coordinates = self._map_target(target, result.width, result.height)
            metrics.update(
                {
                    "simulation_target_x_m": coordinates[0],
                    "simulation_target_y_m": coordinates[1],
                    "simulation_target_z_m": coordinates[2],
                }
            )
            self._motion_commands += 1
            if self._safety_gate.forward(command_age_ms=self._simulated_command_age_ms):
                if self._target_type is None:
                    raise RuntimeCommandError("MuJoCo目标类型尚未加载")
                target_command = self._target_type(*coordinates)
                track_target = getattr(arm, "track_target", None)
                if self._realtime_tracking:
                    if not callable(track_target):
                        raise RuntimeCommandError(
                            "MuJoCo Runtime does not support real-time kinematic tracking"
                        )
                    state = track_target(target_command)
                    self._last_command_monotonic = time.monotonic()
                    self._active_motion = True
                    self._command_timed_out = False
                    metrics["actuator_safe_stop"] = 0.0
                    metrics["actuator_command_timed_out"] = 0.0
                elif self._incremental_arm(arm):
                    arm.set_target(target_command)
                    self._last_command_monotonic = time.monotonic()
                    self._active_motion = True
                    self._command_timed_out = False
                    metrics["actuator_safe_stop"] = 0.0
                    metrics["actuator_command_timed_out"] = 0.0
                    state = None
                else:
                    # Backward-compatible fallback for historical packages.
                    state = arm.command_target(
                        target_command,
                        simulation_steps=self._simulation_steps,
                    )
                self._motion_commands_accepted += 1
                if state is not None:
                    metrics.update(
                        {
                            "mujoco_position_error_m": float(state.position_error_m),
                            "mujoco_target_reached": float(state.target_reached),
                            "mujoco_simulation_steps": float(state.simulation_steps),
                            "mujoco_end_effector_x_m": float(state.end_effector[0]),
                            "mujoco_end_effector_y_m": float(state.end_effector[1]),
                            "mujoco_end_effector_z_m": float(state.end_effector[2]),
                        }
                    )
            elif self._safety_gate.state in {"emergency_stopped", "fault_latched"}:
                arm.emergency_stop()
                self._active_motion = False
                metrics["actuator_safe_stop"] = 1.0
        if self._incremental_arm(arm):
            self._advance_arm(
                arm,
                metrics,
                advance_physics=not self._realtime_tracking,
            )
        metrics.update(self._safety_gate.metrics())
        metrics["motion_commands_emitted"] = float(self._motion_commands)
        metrics["motion_commands_accepted"] = float(self._motion_commands_accepted)
        control_path_ms = (time.perf_counter() - control_started) * 1000.0
        runtime_tick_ms = (time.perf_counter() - tick_started) * 1000.0
        for stage, duration_ms in (
            ("control_path", control_path_ms),
            ("runtime_tick", runtime_tick_ms),
        ):
            self._stage_total_ms[stage] += duration_ms
            metrics[f"{stage}_latest_ms"] = duration_ms
            metrics[f"{stage}_mean_ms"] = self._stage_total_ms[stage] / self._ticks
        self._last_metrics = dict(metrics)
        return RuntimeSnapshot(
            status="running",
            message=(
                (
                    "检测到手机，MuJoCo机械臂已接收仿真目标"
                    if is_phone_target
                    else f"检测到目标 {target_label}，MuJoCo机械臂已接收仿真目标"
                )
                if target is not None and float(target.confidence) >= self._minimum_confidence
                else (
                    "Windows摄像头与YOLOX运行中，等待手机进入画面"
                    if is_phone_target
                    else f"Windows摄像头与YOLOX运行中，等待目标 {target_label} 进入画面"
                )
            ),
            metrics=metrics,
        )

    def capture_preview(self) -> object | None:
        """Capture owned state quickly; rendering runs on the preview worker."""

        try:
            vision, arm = self._require_started()
            camera_snapshotter = getattr(vision, "snapshot_preview_frame", None)
            arm_snapshotter = getattr(arm, "snapshot_preview", None)
            if not callable(camera_snapshotter) or not callable(arm_snapshotter):
                return NotImplemented
            camera_frame = camera_snapshotter()
            if camera_frame is None:
                return None
            return camera_frame, arm_snapshotter()
        except Exception:
            return None

    def render_preview(self, payload: object) -> bytes | None:
        """Render a previously captured snapshot without touching live state."""

        try:
            vision, arm = self._require_started()
            camera_frame, arm_snapshot = payload  # type: ignore[misc]
            arm_pixels = arm.render_snapshot_rgb(
                arm_snapshot,
                width=480,
                height=360,
            )
            image = vision.compose_preview_rgb(camera_frame, arm_pixels)
        except Exception:
            return None
        return self._validated_preview(image)

    @staticmethod
    def _validated_preview(image: bytes | None) -> bytes | None:
        if (
            image is None
            or len(image) < 4
            or len(image) > MAX_PREVIEW_BYTES
            or not image.startswith(b"\xff\xd8")
            or not image.endswith(b"\xff\xd9")
        ):
            return None
        return image

    def close_preview(self) -> None:
        """Release resources on the preview thread that created them."""

        if self._arm_runtime is None:
            return
        closer = getattr(self._arm_runtime, "close_preview", None)
        if callable(closer):
            closer()

    def read_preview(self) -> bytes | None:
        try:
            vision, arm = self._require_started()
            image = vision.compose_preview(arm.render_png(width=480, height=360))
        except Exception:
            return None
        return self._validated_preview(image)

    def emergency_stop(self) -> None:
        self._safety_gate.emergency_stop()
        self._active_motion = False
        _, arm = self._require_started()
        arm.emergency_stop()

    def stop(self) -> RuntimeSnapshot:
        self._safety_gate.stop()
        self._active_motion = False
        if self._arm_runtime is not None:
            self._arm_runtime.emergency_stop()
        if self._vision_runtime is not None:
            self._vision_runtime.stop()
        metrics = dict(self._last_metrics)
        metrics.update(self._safety_gate.metrics())
        metrics["actuator_safe_stop"] = 1.0
        return RuntimeSnapshot(
            status="running",
            message="Windows视觉闭环与MuJoCo机械臂已安全停止",
            metrics=metrics,
        )


class DockerRealSenseYoloRuntime:
    """以受限Docker参数运行RealSense→YOLOX融合Runtime。"""

    preview_content_type: PreviewContentType = "image/jpeg"

    def __init__(
        self,
        plan: Mapping[str, Any],
        *,
        command_runner: CommandRunner = _default_command_runner,
        preview_reader: PreviewReader = _default_preview_reader,
    ) -> None:
        self.plan_id, nodes, links = _workflow_parts(plan)
        by_component = {str(node.get("component_ref")): node for node in nodes}
        expected = {"hardware.camera.realsense", "algorithm.vision.yolo"}
        if len(nodes) != 2 or set(by_component) != expected:
            raise UnsupportedDeploymentPlan("真实视觉Runtime只允许RealSense和YOLO组件")
        if any(str(node.get("component_version")) != "0.1.0" for node in by_component.values()):
            raise UnsupportedDeploymentPlan("真实视觉Runtime只允许0.1.0版本组件")
        camera = by_component["hardware.camera.realsense"]
        detector = by_component["algorithm.vision.yolo"]
        _validate_links(links, [(camera, "color", detector, "image")])

        camera_parameters = _node_parameters(camera)
        detector_parameters = _node_parameters(detector)
        serial = str(camera_parameters.get("serial", ""))
        if not serial or not _SAFE_VALUE.fullmatch(serial):
            raise UnsupportedDeploymentPlan("RealSense设备序列号/标识格式不合法")
        model_id = str(detector_parameters.get("model_id", "yolox.nano.coco"))
        if model_id != "yolox.nano.coco":
            raise UnsupportedDeploymentPlan("当前Runtime只允许yolox.nano.coco模型")

        engine_path = PurePosixPath(str(detector_parameters.get("engine_path", "")))
        if (
            not engine_path.is_absolute()
            or engine_path.suffix != ".engine"
            or ".." in engine_path.parts
            or ENGINE_CACHE_ROOT not in engine_path.parents
            or not _SAFE_VALUE.fullmatch(engine_path.name)
        ):
            raise UnsupportedDeploymentPlan("TensorRT Engine必须位于受管缓存目录")

        self._serial = serial
        self._engine_path = engine_path
        self._width = _integer_parameter(camera_parameters, "width", 640, 160, 1920)
        self._height = _integer_parameter(camera_parameters, "height", 480, 120, 1080)
        self._fps = _integer_parameter(camera_parameters, "fps", 30, 1, 60)
        self._confidence = _float_parameter(detector_parameters, "confidence", 0.3, 0.01, 0.99)
        self._nms = _float_parameter(detector_parameters, "nms", 0.45, 0.01, 0.99)
        self._duration_seconds = _integer_parameter(
            detector_parameters, "duration_seconds", 86400, 30, 604800
        )
        self._runner = command_runner
        self._preview_reader = preview_reader
        self._container_name = f"robot-{self.plan_id}"
        self._output_dir = RUN_OUTPUT_ROOT / self.plan_id

    @property
    def engine_path(self) -> PurePosixPath:
        """返回已通过受管目录校验的Engine路径。"""

        return self._engine_path

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self._runner(arguments)

    def _inspect(self) -> dict[str, Any] | None:
        result = self._run("docker", "container", "inspect", self._container_name)
        if result.returncode != 0:
            return None
        return self._decode_inspection(result.stdout)

    @staticmethod
    def _decode_inspection(output: str) -> dict[str, Any]:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeCommandError("Docker inspect返回了无效JSON") from error
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise RuntimeCommandError("Docker inspect返回结构不符合预期")
        return payload[0]

    def _is_owned_container(self, inspection: Mapping[str, Any]) -> bool:
        config = inspection.get("Config")
        if not isinstance(config, Mapping):
            return False
        labels = config.get("Labels")
        return (
            isinstance(labels, Mapping)
            and labels.get("robot.platform.managed") == "true"
            and labels.get("robot.platform.plan_id") == self.plan_id
            and config.get("Image") == REALSENSE_YOLOX_IMAGE
        )

    def _running_realsense_owner(self) -> str | None:
        """返回占用RealSense的其他受管计划；旧容器也按保守策略识别。"""

        listed = self._run(
            "docker",
            "ps",
            "--quiet",
            "--filter",
            "label=robot.platform.managed=true",
        )
        if listed.returncode != 0:
            raise RuntimeCommandError(listed.stderr.strip() or "无法检查本机受管Runtime")

        for container_id in listed.stdout.splitlines():
            container_id = container_id.strip()
            if not container_id:
                continue
            result = self._run("docker", "container", "inspect", container_id)
            if result.returncode != 0:
                # 容器可能恰好在docker ps与inspect之间退出。
                continue
            inspection = self._decode_inspection(result.stdout)
            config = inspection.get("Config")
            state = inspection.get("State")
            if not isinstance(config, Mapping) or not isinstance(state, Mapping):
                continue
            labels = config.get("Labels")
            if not isinstance(labels, Mapping):
                continue
            owner_plan = str(labels.get("robot.platform.plan_id", "")).strip()
            if owner_plan == self.plan_id or state.get("Running") is not True:
                continue

            resource = labels.get("robot.platform.resource")
            is_realsense_runtime = config.get("Image") == REALSENSE_YOLOX_IMAGE
            if resource == REALSENSE_RESOURCE or is_realsense_runtime:
                return owner_plan or container_id
        return None

    def start(self) -> RuntimeSnapshot:
        image = self._run("docker", "image", "inspect", REALSENSE_YOLOX_IMAGE)
        if image.returncode != 0:
            raise RuntimeCommandError(f"找不到融合Runtime镜像：{REALSENSE_YOLOX_IMAGE}")

        existing = self._inspect()
        if existing is not None:
            if not self._is_owned_container(existing):
                raise RuntimeCommandError(f"容器名称已被非平台任务占用：{self._container_name}")
            state = existing.get("State")
            if isinstance(state, Mapping) and state.get("Running") is True:
                return self.tick()
            removal = self._run("docker", "container", "rm", self._container_name)
            if removal.returncode != 0:
                raise RuntimeCommandError(removal.stderr.strip() or "无法清理已退出的Runtime容器")

        owner_plan = self._running_realsense_owner()
        if owner_plan is not None:
            raise RuntimeCommandError(
                f"RealSense设备正被计划{owner_plan}占用，请先停止该计划后再启动"
            )

        command = (
            "docker",
            "run",
            "--detach",
            "--name",
            self._container_name,
            "--label",
            "robot.platform.managed=true",
            "--label",
            f"robot.platform.plan_id={self.plan_id}",
            "--label",
            f"robot.platform.resource={REALSENSE_RESOURCE}",
            "--label",
            f"robot.platform.device_selector={self._serial}",
            "--runtime",
            "nvidia",
            "--network",
            "none",
            "--device-cgroup-rule",
            "c 81:* rw",
            "--device-cgroup-rule",
            "c 189:* rw",
            "--volume",
            "/dev:/dev",
            "--volume",
            f"{self._engine_path.parent}:/engines:ro",
            "--volume",
            f"{self._output_dir}:/outputs",
            REALSENSE_YOLOX_IMAGE,
            "--engine",
            f"/engines/{self._engine_path.name}",
            "--serial",
            self._serial,
            "--width",
            str(self._width),
            "--height",
            str(self._height),
            "--fps",
            str(self._fps),
            "--warmup-frames",
            "10",
            "--duration-seconds",
            str(self._duration_seconds),
            "--report-interval-seconds",
            "5",
            "--confidence",
            str(self._confidence),
            "--nms",
            str(self._nms),
            "--output-dir",
            "/outputs",
        )
        result = self._runner(command)
        if result.returncode != 0:
            raise RuntimeCommandError(result.stderr.strip() or "Docker Runtime启动失败")
        return RuntimeSnapshot(
            status="running",
            message="RealSense→YOLOX容器已启动，等待首批指标",
            metrics={"configured_fps": float(self._fps)},
        )

    def _latest_metrics(self) -> dict[str, float]:
        result = self._run("docker", "logs", "--tail", "100", self._container_name)
        if result.returncode != 0:
            return {"configured_fps": float(self._fps)}
        numeric_fields = {
            "configured_fps",
            "captured_frames",
            "inferred_frames",
            "dropped_frames",
            "latest_detection_count",
            "frames_with_detections",
            "inference_mean_ms",
            "pipeline_p95_ms",
        }
        for line in reversed(result.stdout.splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping) or event.get("state") != "running":
                continue
            metrics = {
                key: float(value)
                for key, value in event.items()
                if key in numeric_fields
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            }
            metrics.setdefault("configured_fps", float(self._fps))
            return metrics
        return {"configured_fps": float(self._fps)}

    def tick(self) -> RuntimeSnapshot:
        inspection = self._inspect()
        if inspection is None:
            return RuntimeSnapshot("failed", "融合Runtime容器不存在", {})
        if not self._is_owned_container(inspection):
            return RuntimeSnapshot("failed", "检测到同名非平台容器", {})
        state = inspection.get("State")
        if not isinstance(state, Mapping):
            return RuntimeSnapshot("failed", "Docker容器状态不可读", {})
        if state.get("Running") is True:
            return RuntimeSnapshot(
                "running",
                "RealSense→YOLOX实时检测运行中",
                self._latest_metrics(),
            )
        exit_code = state.get("ExitCode", -1)
        error = str(state.get("Error", "")).strip()
        detail = f"融合Runtime意外退出，exit_code={exit_code}"
        if error:
            detail = f"{detail}：{error}"
        return RuntimeSnapshot("failed", detail, self._latest_metrics())

    def stop(self) -> RuntimeSnapshot | None:
        inspection = self._inspect()
        if inspection is None:
            return
        if not self._is_owned_container(inspection):
            raise RuntimeCommandError(f"拒绝停止同名非平台容器：{self._container_name}")
        state = inspection.get("State")
        if isinstance(state, Mapping) and state.get("Running") is True:
            stopped = self._run("docker", "stop", "--time", "10", self._container_name)
            if stopped.returncode != 0:
                raise RuntimeCommandError(stopped.stderr.strip() or "停止Runtime容器失败")
        removed = self._run("docker", "container", "rm", self._container_name)
        if removed.returncode != 0:
            raise RuntimeCommandError(removed.stderr.strip() or "清理Runtime容器失败")

    def read_preview(self) -> bytes | None:
        """读取Runtime原子生成的JPEG预览；不存在或格式异常时跳过本轮上传。"""

        try:
            image = self._preview_reader(self._output_dir / "preview.jpg")
        except OSError:
            return None
        if (
            len(image) < 4
            or len(image) > MAX_PREVIEW_BYTES
            or not image.startswith(b"\xff\xd8")
            or not image.endswith(b"\xff\xd9")
        ):
            return None
        return image


def create_workflow_runtime(
    plan: Mapping[str, Any],
    *,
    artifact_inventory: ArtifactInventory = collect_installed_artifacts,
    runtime_package_root: Path = DEFAULT_RUNTIME_PACKAGE_ROOTS[0],
    calibration_root: Path | None = None,
    machine: str | None = None,
) -> WorkflowRuntime:
    """按严格组件组合选择本机Runtime实现。"""

    _, nodes, _ = _workflow_parts(plan)
    components = {str(node.get("component_ref")) for node in nodes}
    if components == {"hardware.camera.mock", "algorithm.vision.mock_detector"}:
        return MockWorkflowRuntime(plan)
    if components == {
        "hardware.camera.mock",
        "algorithm.vision.mock_detector",
        "algorithm.vision.mock_filter",
    }:
        return MockWorkflowRuntime(plan)
    if components == {
        "hardware.camera.mock",
        "algorithm.vision.mock_detector",
        "algorithm.motion.mock_target_selector",
        "hardware.arm.mock",
    }:
        return MockWorkflowRuntime(plan)
    if components == {
        "hardware.camera.mock",
        "algorithm.vision.mock_detector",
        "algorithm.motion.mock_target_selector",
        "algorithm.motion.safety_gate",
        "hardware.arm.mock",
    }:
        return MockWorkflowRuntime(plan)
    if frozenset(components) in MujocoWorkflowRuntime._SUPPORTED_COMPONENTS:
        return MujocoWorkflowRuntime(
            plan,
            installed_artifacts=artifact_inventory(),
            runtime_package_root=runtime_package_root,
            calibration_root=calibration_root,
        )
    if components == WindowsWebcamYoloxMujocoRuntime._COMPONENTS:
        return WindowsWebcamYoloxMujocoRuntime(
            plan,
            installed_artifacts=artifact_inventory(),
            runtime_package_root=runtime_package_root,
            verify_artifacts=str(plan.get("desired_state", "running")) == "running",
        )
    if components == {"hardware.camera.realsense", "algorithm.vision.yolo"}:
        runtime = DockerRealSenseYoloRuntime(plan)
        if str(plan.get("desired_state", "running")) == "running":
            _verify_real_artifact_contract(
                plan,
                engine_path=runtime.engine_path,
                installed_artifacts=artifact_inventory(),
                machine=machine or platform.machine(),
            )
        return runtime
    raise UnsupportedDeploymentPlan("当前Edge版本没有匹配该组件组合的Runtime")


class _ContinuousRuntimeWorker:
    """Drive control and latest-only preview on independent bounded workers."""

    def __init__(
        self,
        runtime: WorkflowRuntime,
        initial_snapshot: RuntimeSnapshot,
        *,
        interval_s: float,
        publish_preview: Callable[[object | None], _PreviewPublishResult],
        preview_interval_s: float,
    ) -> None:
        self._runtime = runtime
        self._interval_s = interval_s
        self._publish_preview = publish_preview
        self._preview_interval_s = preview_interval_s
        self._snapshot = initial_snapshot
        self._error: Exception | None = None
        self._lock = threading.Lock()
        self._preview_ready = threading.Condition(self._lock)
        self._pending_preview: _PreviewPayload | None = None
        self._stop_requested = threading.Event()
        self._control_started = time.monotonic()
        self._control_ticks = 0
        self._control_deadline_misses = 0
        self._tick_durations_ms: deque[float] = deque(maxlen=120)
        self._preview_started = self._control_started
        self._preview_frames = 0
        self._preview_errors = 0
        self._preview_dropped = 0
        self._preview_capture_ms: deque[float] = deque(maxlen=120)
        self._preview_render_ms: deque[float] = deque(maxlen=120)
        self._preview_upload_ms: deque[float] = deque(maxlen=120)
        self._preview_age_ms: deque[float] = deque(maxlen=120)
        self._preview_bytes = 0
        self._thread = threading.Thread(
            target=self._run,
            name=f"runtime-{runtime.plan_id}",
            daemon=True,
        )
        self._preview_thread = threading.Thread(
            target=self._run_preview,
            name=f"preview-{runtime.plan_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._preview_thread.start()
        self._capture_preview()
        self._thread.start()

    def observe(self) -> tuple[RuntimeSnapshot, Exception | None]:
        with self._lock:
            metrics = dict(self._snapshot.metrics)
            metrics.update(self._diagnostic_metrics_locked())
            snapshot = RuntimeSnapshot(
                self._snapshot.status,
                self._snapshot.message,
                metrics,
            )
            return snapshot, self._error

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_requested.set()
        with self._preview_ready:
            self._preview_ready.notify_all()
        deadline = time.monotonic() + timeout_s
        self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
        self._preview_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive() or self._preview_thread.is_alive():
            raise RuntimeCommandError("连续Runtime未能在限定时间内停止")

    @staticmethod
    def _mean(values: deque[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _p95(values: deque[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered) - 1)))]

    def _diagnostic_metrics_locked(self) -> dict[str, float]:
        control_elapsed = max(1e-6, time.monotonic() - self._control_started)
        preview_elapsed = max(1e-6, time.monotonic() - self._preview_started)
        return {
            "control_loop_actual_fps": self._control_ticks / control_elapsed,
            "control_tick_latest_ms": (
                self._tick_durations_ms[-1] if self._tick_durations_ms else 0.0
            ),
            "control_tick_mean_ms": self._mean(self._tick_durations_ms),
            "control_tick_p95_ms": self._p95(self._tick_durations_ms),
            "control_deadline_misses": float(self._control_deadline_misses),
            "preview_actual_fps": self._preview_frames / preview_elapsed,
            "preview_frames_published": float(self._preview_frames),
            "preview_errors": float(self._preview_errors),
            "preview_frames_dropped": float(self._preview_dropped),
            "preview_capture_latest_ms": (
                self._preview_capture_ms[-1] if self._preview_capture_ms else 0.0
            ),
            "preview_render_latest_ms": (
                self._preview_render_ms[-1] if self._preview_render_ms else 0.0
            ),
            "preview_render_mean_ms": self._mean(self._preview_render_ms),
            "preview_render_p95_ms": self._p95(self._preview_render_ms),
            "preview_upload_latest_ms": (
                self._preview_upload_ms[-1] if self._preview_upload_ms else 0.0
            ),
            "preview_upload_mean_ms": self._mean(self._preview_upload_ms),
            "preview_age_latest_ms": (
                self._preview_age_ms[-1] if self._preview_age_ms else 0.0
            ),
            "preview_age_mean_ms": self._mean(self._preview_age_ms),
            "preview_bytes_latest": float(self._preview_bytes),
        }

    def _capture_preview(self) -> None:
        capture_started = time.perf_counter()
        capture = getattr(self._runtime, "capture_preview", None)
        try:
            if callable(capture):
                value = capture()
                if value is None:
                    return
                if value is NotImplemented:
                    value = None
            else:
                # Legacy runtimes perform their safe file read in the preview worker.
                value = None
        except Exception:
            with self._lock:
                self._preview_errors += 1
            return
        item = _PreviewPayload(time.monotonic(), value)
        capture_ms = (time.perf_counter() - capture_started) * 1000.0
        with self._preview_ready:
            self._preview_capture_ms.append(capture_ms)
            if self._pending_preview is not None:
                self._preview_dropped += 1
            self._pending_preview = item
            self._preview_ready.notify()

    def _run(self) -> None:
        next_tick = time.monotonic() + self._interval_s
        next_preview = time.monotonic() + self._preview_interval_s
        while not self._stop_requested.is_set():
            delay = max(0.0, next_tick - time.monotonic())
            if self._stop_requested.wait(delay):
                return
            try:
                tick_started = time.perf_counter()
                snapshot = self._runtime.tick()
            except Exception as error:
                with self._lock:
                    self._error = error
                return
            tick_ms = (time.perf_counter() - tick_started) * 1000.0
            with self._lock:
                self._snapshot = snapshot
                self._control_ticks += 1
                self._tick_durations_ms.append(tick_ms)
                if tick_ms > self._interval_s * 1000.0:
                    self._control_deadline_misses += 1
            if snapshot.status == "failed":
                return

            now = time.monotonic()
            if now >= next_preview:
                self._capture_preview()
                next_preview = now + self._preview_interval_s
            next_tick = max(next_tick + self._interval_s, time.monotonic())

    def _run_preview(self) -> None:
        try:
            while not self._stop_requested.is_set():
                with self._preview_ready:
                    self._preview_ready.wait_for(
                        lambda: self._pending_preview is not None
                        or self._stop_requested.is_set()
                    )
                    if self._stop_requested.is_set():
                        return
                    item = self._pending_preview
                    self._pending_preview = None
                assert item is not None
                try:
                    result = self._publish_preview(item.value)
                except Exception:
                    with self._lock:
                        self._preview_errors += 1
                    continue
                age_ms = (time.monotonic() - item.captured_monotonic) * 1000.0
                with self._lock:
                    if result.image_bytes > 0:
                        self._preview_frames += 1
                    self._preview_render_ms.append(result.render_ms)
                    self._preview_upload_ms.append(result.upload_ms)
                    if result.image_bytes > 0:
                        self._preview_age_ms.append(age_ms)
                    self._preview_bytes = result.image_bytes
                    if result.requested_interval_s is not None:
                        self._preview_interval_s = min(
                            1.0,
                            max(1.0 / 15.0, result.requested_interval_s),
                        )
        finally:
            closer = getattr(self._runtime, "close_preview", None)
            if callable(closer):
                with suppress(Exception):
                    closer()


class EdgeDeploymentManager:
    """幂等应用平台下发的活动计划并回报状态。"""

    def __init__(
        self,
        *,
        runtime_factory: Callable[[Mapping[str, Any]], WorkflowRuntime] = create_workflow_runtime,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._runtimes: dict[str, WorkflowRuntime] = {}
        self._workers: dict[str, _ContinuousRuntimeWorker] = {}

    @staticmethod
    def _publish_preview(
        client: DeploymentClient,
        plan_id: str,
        runtime: WorkflowRuntime,
        preview_payload: object | None = None,
    ) -> _PreviewPublishResult:
        render_started = time.perf_counter()
        renderer = getattr(runtime, "render_preview", None)
        reader = getattr(runtime, "read_preview", None)
        uploader = getattr(client, "upload_deployment_preview", None)
        if not callable(uploader):
            return _PreviewPublishResult(None, 0.0, 0.0, 0)
        if preview_payload is not None and callable(renderer):
            image = renderer(preview_payload)
        elif callable(reader):
            image = reader()
        else:
            image = None
        render_ms = (time.perf_counter() - render_started) * 1000.0
        requested_interval: float | None = None
        upload_ms = 0.0
        if image is not None:
            content_type = getattr(runtime, "preview_content_type", "image/jpeg")
            upload_started = time.perf_counter()
            response = uploader(plan_id, image, content_type=content_type)
            upload_ms = (time.perf_counter() - upload_started) * 1000.0
            if isinstance(response, Mapping):
                requested_fps = response.get("requested_fps")
                if isinstance(requested_fps, int | float) and requested_fps > 0:
                    requested_interval = 1.0 / float(requested_fps)
        return _PreviewPublishResult(
            requested_interval,
            render_ms,
            upload_ms,
            len(image) if image is not None else 0,
        )

    def reconcile(self, client: DeploymentClient, agent_id: str) -> None:
        """领取目标计划，执行启动、状态刷新或停止指令。"""

        for plan in client.list_deployments(agent_id):
            plan_id = str(plan.get("plan_id", ""))
            if not plan_id:
                continue
            desired_state = str(plan.get("desired_state", "running"))
            runtime = self._runtimes.get(plan_id)

            if desired_state == "stopped":
                try:
                    if runtime is None:
                        runtime = self._runtime_factory(plan)
                    worker = self._workers.pop(plan_id, None)
                    worker_snapshot: RuntimeSnapshot | None = None
                    if worker is not None:
                        worker_snapshot, _ = worker.observe()
                        worker.stop()
                    stop_snapshot = runtime.stop()
                    if stop_snapshot is not None and worker_snapshot is not None:
                        metrics = dict(stop_snapshot.metrics)
                        metrics.update(worker_snapshot.metrics)
                        stop_snapshot = RuntimeSnapshot(
                            stop_snapshot.status,
                            stop_snapshot.message,
                            metrics,
                        )
                except Exception as error:
                    client.report_deployment_status(plan_id, "failed", message=str(error))
                    continue
                self._runtimes.pop(plan_id, None)
                client.report_deployment_status(
                    plan_id,
                    "stopped",
                    message=(
                        stop_snapshot.message
                        if stop_snapshot is not None
                        else "Runtime已停止并清理"
                    ),
                    metrics=(stop_snapshot.metrics if stop_snapshot is not None else None),
                )
                continue

            if desired_state != "running":
                client.report_deployment_status(
                    plan_id, "failed", message=f"不支持的目标状态：{desired_state}"
                )
                continue

            if runtime is not None:
                worker = self._workers.get(plan_id)
                try:
                    if worker is None:
                        snapshot = runtime.tick()
                        runtime_error = None
                    else:
                        snapshot, runtime_error = worker.observe()
                        if runtime_error is not None:
                            raise runtime_error
                except Exception as error:
                    failed_worker = self._workers.pop(plan_id, None)
                    if failed_worker is not None:
                        with suppress(Exception):
                            failed_worker.stop()
                    with suppress(Exception):
                        runtime.stop()
                    self._runtimes.pop(plan_id, None)
                    client.report_deployment_status(plan_id, "failed", message=str(error))
                    continue
                client.report_deployment_status(
                    plan_id,
                    snapshot.status,
                    message=snapshot.message,
                    metrics=snapshot.metrics,
                )
                if worker is None:
                    self._publish_preview(client, plan_id, runtime)
                if snapshot.status == "failed":
                    failed_worker = self._workers.pop(plan_id, None)
                    if failed_worker is not None:
                        with suppress(Exception):
                            failed_worker.stop()
                    self._runtimes.pop(plan_id, None)
                continue

            client.report_deployment_status(plan_id, "starting", message="正在启动端侧Runtime")
            try:
                runtime = self._runtime_factory(plan)
                snapshot = runtime.start()
            except Exception as error:
                client.report_deployment_status(plan_id, "failed", message=str(error))
                continue
            self._runtimes[plan_id] = runtime
            client.report_deployment_status(
                plan_id,
                snapshot.status,
                message=snapshot.message,
                metrics=snapshot.metrics,
            )
            if snapshot.status == "failed":
                self._runtimes.pop(plan_id, None)
                continue

            execution_interval = getattr(runtime, "execution_interval_s", None)
            if isinstance(execution_interval, int | float) and execution_interval > 0:
                preview_interval = getattr(runtime, "preview_interval_s", 1.0)
                if not isinstance(preview_interval, int | float) or preview_interval <= 0:
                    preview_interval = 1.0
                worker = _ContinuousRuntimeWorker(
                    runtime,
                    snapshot,
                    interval_s=float(execution_interval),
                    publish_preview=(
                        lambda payload, client=client, plan_id=plan_id, runtime=runtime: (
                            self._publish_preview(
                                client,
                                plan_id,
                                runtime,
                                preview_payload=payload,
                            )
                        )
                    ),
                    preview_interval_s=float(preview_interval),
                )
                self._workers[plan_id] = worker
                worker.start()
            else:
                self._publish_preview(client, plan_id, runtime)
