"""从资产包Manifest构建平台Catalog。"""

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from robot_platform_api.models import (
    AlgorithmDefinition,
    ArtifactDefinition,
    ArtifactDistribution,
    ArtifactKind,
    ArtifactScope,
    ArtifactVariant,
    AssetReadiness,
    ComponentParameter,
    ComponentPort,
    DeviceDiscoveryRule,
    HardwareDefinition,
    PortReference,
    RecipeComponent,
    RecipeParameter,
    WorkflowLink,
    WorkflowRecipe,
)


class CatalogManifestError(ValueError):
    """资产Manifest无法加载或违反Catalog契约。"""


@dataclass(frozen=True)
class CatalogSnapshot:
    """启动时构建的不可变Catalog快照。"""

    hardware: tuple[HardwareDefinition, ...]
    algorithms: tuple[AlgorithmDefinition, ...]
    recipes: tuple[WorkflowRecipe, ...]
    artifacts: tuple[ArtifactDefinition, ...]
    artifact_files: tuple["CatalogArtifactFile", ...]


@dataclass(frozen=True)
class CatalogArtifactFile:
    """Catalog内部保存的可信文件映射；宿主机路径不会出现在API响应中。"""

    artifact_id: str
    variant_id: str | None
    path: Path
    digest: str
    media_type: str


@dataclass(frozen=True)
class ArtifactContent:
    """经过读取时二次摘要校验的制品内容。"""

    file_name: str
    path: Path
    size_bytes: int
    digest: str
    media_type: str


_MOCK_HARDWARE = HardwareDefinition(
    hardware_id="hardware.camera.mock",
    version="0.1.0",
    name="模拟RGB相机",
    vendor="Robot Platform",
    category="camera.rgb",
    readiness=AssetReadiness.VALIDATED,
    discovery=DeviceDiscoveryRule(transport="simulation"),
    outputs=[
        ComponentPort(name="color", data_type="image.rgb8.v1"),
        ComponentPort(name="depth", data_type="image.depth16.v1"),
        ComponentPort(name="camera_info", data_type="camera.intrinsics.v1"),
    ],
    parameters=[
        ComponentParameter(
            key="width", label="图像宽度", value_type="integer", default=640, visible=False
        ),
        ComponentParameter(
            key="height", label="图像高度", value_type="integer", default=480, visible=False
        ),
        ComponentParameter(
            key="fps",
            label="目标帧率",
            value_type="integer",
            default=15,
            minimum=1,
            maximum=60,
        ),
    ],
)

_MOCK_ALGORITHM = AlgorithmDefinition(
    algorithm_id="algorithm.vision.mock_detector",
    version="0.1.0",
    name="模拟目标检测器",
    category="vision.object_detection",
    readiness=AssetReadiness.VALIDATED,
    inputs=[ComponentPort(name="image", data_type="image.rgb8.v1")],
    outputs=[ComponentPort(name="detections", data_type="vision.detections.v1")],
    artifact_formats=["builtin-python"],
)

_MOCK_FILTER = AlgorithmDefinition(
    algorithm_id="algorithm.vision.mock_filter",
    version="0.1.0",
    name="模拟检测结果过滤器",
    category="vision.detection_filter",
    readiness=AssetReadiness.VALIDATED,
    inputs=[ComponentPort(name="detections", data_type="vision.detections.v1")],
    outputs=[ComponentPort(name="filtered", data_type="vision.detections.v1")],
    artifact_formats=["builtin-python"],
    parameters=[
        ComponentParameter(
            key="min_confidence",
            label="最低置信度",
            value_type="number",
            default=0.5,
            minimum=0,
            maximum=1,
            step=0.05,
        )
    ],
)

_MOCK_TARGET_SELECTOR = AlgorithmDefinition(
    algorithm_id="algorithm.motion.mock_target_selector",
    version="0.1.0",
    name="模拟视觉目标选择器",
    category="motion.target_selection",
    readiness=AssetReadiness.VALIDATED,
    inputs=[ComponentPort(name="detections", data_type="vision.detections.v1")],
    outputs=[ComponentPort(name="command", data_type="robot.motion.command.v1")],
    artifact_formats=["builtin-python"],
    parameters=[
        ComponentParameter(
            key="minimum_confidence",
            label="目标最低置信度",
            value_type="number",
            default=0.6,
            minimum=0,
            maximum=1,
            step=0.05,
        )
    ],
)

_RGBD_TARGET_LOCALIZER = AlgorithmDefinition(
    algorithm_id="algorithm.spatial.rgbd_target_localizer",
    version="0.1.0",
    name="RGB-D目标空间定位器",
    category="spatial.target_localization",
    readiness=AssetReadiness.VALIDATED,
    inputs=[
        ComponentPort(name="detections", data_type="vision.detections.v1"),
        ComponentPort(name="depth", data_type="image.depth16.v1"),
        ComponentPort(name="camera_info", data_type="camera.intrinsics.v1"),
    ],
    outputs=[ComponentPort(name="target_pose", data_type="robot.target_pose.v1")],
    artifact_formats=["builtin-python"],
    parameters=[
        ComponentParameter(
            key="minimum_confidence",
            label="目标最低置信度",
            value_type="number",
            default=0.6,
            minimum=0,
            maximum=1,
            step=0.05,
        ),
        ComponentParameter(
            key="calibration_id",
            label="相机到机械臂标定ID",
            value_type="string",
            default="mock-camera-to-mujoco-arm-v1",
        ),
        ComponentParameter(
            key="simulated_detection_u_px",
            label="仿真目标像素U",
            value_type="number",
            default=320,
            minimum=0,
            maximum=1919,
            visible=False,
        ),
        ComponentParameter(
            key="simulated_detection_v_px",
            label="仿真目标像素V",
            value_type="number",
            default=240,
            minimum=0,
            maximum=1079,
            visible=False,
        ),
        ComponentParameter(
            key="simulated_depth_m",
            label="仿真对齐深度（米）",
            value_type="number",
            default=0.45,
            minimum=0.01,
            maximum=5,
            visible=False,
        ),
        ComponentParameter(
            key="target_ttl_ms",
            label="目标有效期（毫秒）",
            value_type="integer",
            default=300,
            minimum=50,
            maximum=5000,
            step=50,
        ),
    ],
)

_CARTESIAN_POINT_TO_POINT = AlgorithmDefinition(
    algorithm_id="algorithm.motion.cartesian_point_to_point",
    version="0.1.0",
    name="笛卡尔点到点规划器",
    category="motion.cartesian_planning",
    readiness=AssetReadiness.VALIDATED,
    inputs=[ComponentPort(name="target_pose", data_type="robot.target_pose.v1")],
    outputs=[ComponentPort(name="command", data_type="robot.motion.command.v1")],
    artifact_formats=["builtin-python"],
    parameters=[
        ComponentParameter(
            key="workspace_x_min_m",
            label="工作空间X下限（米）",
            value_type="number",
            default=0.05,
            minimum=-5,
            maximum=5,
            visible=False,
        ),
        ComponentParameter(
            key="workspace_x_max_m",
            label="工作空间X上限（米）",
            value_type="number",
            default=0.75,
            minimum=-5,
            maximum=5,
            visible=False,
        ),
        ComponentParameter(
            key="workspace_y_min_m",
            label="工作空间Y下限（米）",
            value_type="number",
            default=-0.55,
            minimum=-5,
            maximum=5,
            visible=False,
        ),
        ComponentParameter(
            key="workspace_y_max_m",
            label="工作空间Y上限（米）",
            value_type="number",
            default=0.55,
            minimum=-5,
            maximum=5,
            visible=False,
        ),
        ComponentParameter(
            key="workspace_z_min_m",
            label="工作空间Z下限（米）",
            value_type="number",
            default=0.05,
            minimum=-5,
            maximum=5,
            visible=False,
        ),
        ComponentParameter(
            key="workspace_z_max_m",
            label="工作空间Z上限（米）",
            value_type="number",
            default=0.85,
            minimum=-5,
            maximum=5,
            visible=False,
        ),
    ],
)

_MOCK_SAFETY_GATE = AlgorithmDefinition(
    algorithm_id="algorithm.motion.safety_gate",
    version="0.1.0",
    name="运动安全门",
    category="motion.safety",
    readiness=AssetReadiness.VALIDATED,
    inputs=[ComponentPort(name="command", data_type="robot.motion.command.v1")],
    outputs=[
        ComponentPort(name="safe_command", data_type="robot.motion.command.v1"),
        ComponentPort(name="state", data_type="robot.safety.state.v1"),
        ComponentPort(name="fault", data_type="robot.fault.v1", required=False),
    ],
    artifact_formats=["builtin-python"],
    parameters=[
        ComponentParameter(
            key="command_ttl_ms",
            label="命令有效期（毫秒）",
            value_type="integer",
            default=300,
            minimum=50,
            maximum=5000,
            step=50,
        ),
        ComponentParameter(
            key="velocity_limit",
            label="安全速度上限",
            value_type="number",
            default=0.2,
            minimum=0.01,
            maximum=1,
            step=0.01,
        ),
        ComponentParameter(
            key="command_authorized",
            label="命令授权（0/1）",
            value_type="integer",
            default=1,
            minimum=0,
            maximum=1,
            step=1,
        ),
        ComponentParameter(
            key="emergency_stop",
            label="模拟急停（0/1）",
            value_type="integer",
            default=0,
            minimum=0,
            maximum=1,
            step=1,
        ),
        ComponentParameter(
            key="simulated_command_age_ms",
            label="模拟命令延迟（毫秒）",
            value_type="integer",
            default=0,
            minimum=0,
            maximum=10000,
            step=50,
        ),
    ],
)

_MOCK_ARM = HardwareDefinition(
    hardware_id="hardware.arm.mock",
    version="0.1.0",
    name="模拟六轴机械臂",
    vendor="Robot Platform",
    category="manipulator.arm",
    readiness=AssetReadiness.VALIDATED,
    discovery=DeviceDiscoveryRule(transport="simulation"),
    inputs=[ComponentPort(name="command", data_type="robot.motion.command.v1")],
    outputs=[
        ComponentPort(name="state", data_type="robot.motion.state.v1"),
        ComponentPort(name="fault", data_type="robot.fault.v1", required=False),
    ],
    parameters=[
        ComponentParameter(
            key="command_timeout_ms",
            label="命令超时",
            value_type="integer",
            default=500,
            minimum=100,
            maximum=5000,
            step=100,
        ),
        ComponentParameter(
            key="max_velocity_scale",
            label="最大速度比例",
            value_type="number",
            default=0.2,
            minimum=0.01,
            maximum=1,
            step=0.01,
        ),
    ],
)

_MOCK_RECIPE = WorkflowRecipe(
    recipe_id="workflow.mock_detection",
    version="0.1.0",
    name="模拟相机目标检测",
    components=[
        RecipeComponent(
            node_id="camera",
            component_ref=_MOCK_HARDWARE.hardware_id,
            component_version=_MOCK_HARDWARE.version,
        ),
        RecipeComponent(
            node_id="detector",
            component_ref=_MOCK_ALGORITHM.algorithm_id,
            component_version=_MOCK_ALGORITHM.version,
        ),
    ],
    links=[
        WorkflowLink(
            source=PortReference(node_id="camera", port="color"),
            target=PortReference(node_id="detector", port="image"),
        )
    ],
)

_MOCK_PIPELINE_RECIPE = WorkflowRecipe(
    recipe_id="workflow.mock_detection_pipeline",
    version="0.1.0",
    name="三节点模拟视觉流程",
    components=[
        RecipeComponent(
            node_id="camera",
            component_ref=_MOCK_HARDWARE.hardware_id,
            component_version=_MOCK_HARDWARE.version,
        ),
        RecipeComponent(
            node_id="detector",
            component_ref=_MOCK_ALGORITHM.algorithm_id,
            component_version=_MOCK_ALGORITHM.version,
        ),
        RecipeComponent(
            node_id="filter",
            component_ref=_MOCK_FILTER.algorithm_id,
            component_version=_MOCK_FILTER.version,
        ),
    ],
    links=[
        WorkflowLink(
            source=PortReference(node_id="camera", port="color"),
            target=PortReference(node_id="detector", port="image"),
        ),
        WorkflowLink(
            source=PortReference(node_id="detector", port="detections"),
            target=PortReference(node_id="filter", port="detections"),
        ),
    ],
)

_MOCK_VISION_ARM_RECIPE = WorkflowRecipe(
    recipe_id="workflow.mock_vision_arm",
    version="0.1.0",
    name="视觉引导模拟机械臂",
    components=[
        RecipeComponent(
            node_id="camera",
            component_ref=_MOCK_HARDWARE.hardware_id,
            component_version=_MOCK_HARDWARE.version,
        ),
        RecipeComponent(
            node_id="detector",
            component_ref=_MOCK_ALGORITHM.algorithm_id,
            component_version=_MOCK_ALGORITHM.version,
        ),
        RecipeComponent(
            node_id="target_selector",
            component_ref=_MOCK_TARGET_SELECTOR.algorithm_id,
            component_version=_MOCK_TARGET_SELECTOR.version,
        ),
        RecipeComponent(
            node_id="arm",
            component_ref=_MOCK_ARM.hardware_id,
            component_version=_MOCK_ARM.version,
        ),
    ],
    links=[
        WorkflowLink(
            source=PortReference(node_id="camera", port="color"),
            target=PortReference(node_id="detector", port="image"),
        ),
        WorkflowLink(
            source=PortReference(node_id="detector", port="detections"),
            target=PortReference(node_id="target_selector", port="detections"),
        ),
        WorkflowLink(
            source=PortReference(node_id="target_selector", port="command"),
            target=PortReference(node_id="arm", port="command"),
        ),
    ],
    required_capabilities=["simulation", "actuator.safety.v1"],
)

_MOCK_SAFE_VISION_ARM_RECIPE = WorkflowRecipe(
    recipe_id="workflow.mock_vision_arm_safety",
    version="0.2.0",
    name="视觉引导模拟机械臂（独立安全门）",
    components=[
        RecipeComponent(
            node_id="camera",
            component_ref=_MOCK_HARDWARE.hardware_id,
            component_version=_MOCK_HARDWARE.version,
        ),
        RecipeComponent(
            node_id="detector",
            component_ref=_MOCK_ALGORITHM.algorithm_id,
            component_version=_MOCK_ALGORITHM.version,
        ),
        RecipeComponent(
            node_id="target_selector",
            component_ref=_MOCK_TARGET_SELECTOR.algorithm_id,
            component_version=_MOCK_TARGET_SELECTOR.version,
        ),
        RecipeComponent(
            node_id="safety_gate",
            component_ref=_MOCK_SAFETY_GATE.algorithm_id,
            component_version=_MOCK_SAFETY_GATE.version,
        ),
        RecipeComponent(
            node_id="arm",
            component_ref=_MOCK_ARM.hardware_id,
            component_version=_MOCK_ARM.version,
        ),
    ],
    links=[
        WorkflowLink(
            source=PortReference(node_id="camera", port="color"),
            target=PortReference(node_id="detector", port="image"),
        ),
        WorkflowLink(
            source=PortReference(node_id="detector", port="detections"),
            target=PortReference(node_id="target_selector", port="detections"),
        ),
        WorkflowLink(
            source=PortReference(node_id="target_selector", port="command"),
            target=PortReference(node_id="safety_gate", port="command"),
        ),
        WorkflowLink(
            source=PortReference(node_id="safety_gate", port="safe_command"),
            target=PortReference(node_id="arm", port="command"),
        ),
    ],
    required_capabilities=[
        "simulation",
        "actuator.safety.v1",
        "actuator.safety-gate.v1",
    ],
)


def _default_asset_root() -> Path | None:
    configured_root = os.getenv("ROBOT_PLATFORM_ASSET_ROOT")
    if configured_root:
        root = Path(configured_root).expanduser().resolve()
        if not root.is_dir():
            raise CatalogManifestError(f"资产根目录不存在：{root}")
        return root

    repository_root = Path(__file__).resolve().parents[4]
    if (repository_root / "hardware-adapters").is_dir():
        return repository_root
    return None


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogManifestError(f"无法读取资产Manifest {path}: {error}") from error
    if not isinstance(payload, dict):
        raise CatalogManifestError(f"资产Manifest根节点必须是对象：{path}")
    return payload


def _validate_model(model_type: Any, payload: dict[str, Any], path: Path) -> Any:
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        raise CatalogManifestError(f"资产Manifest不符合契约 {path}: {error}") from error


def _load_hardware(path: Path) -> HardwareDefinition:
    manifest = _read_manifest(path)
    return _validate_model(
        HardwareDefinition,
        {
            "hardware_id": manifest.get("adapter_id"),
            "version": manifest.get("version"),
            "name": manifest.get("name"),
            "vendor": manifest.get("vendor"),
            "category": manifest.get("category"),
            "readiness": manifest.get("readiness"),
            "discovery": manifest.get("discovery"),
            "inputs": manifest.get("inputs", []),
            "outputs": manifest.get("outputs", []),
            "parameters": manifest.get("parameters", []),
        },
        path,
    )


def _load_algorithm(path: Path) -> AlgorithmDefinition:
    manifest = _read_manifest(path)
    return _validate_model(
        AlgorithmDefinition,
        {
            "algorithm_id": manifest.get("algorithm_id"),
            "version": manifest.get("version"),
            "name": manifest.get("name"),
            "category": manifest.get("category"),
            "readiness": manifest.get("readiness"),
            "inputs": manifest.get("inputs", []),
            "outputs": manifest.get("outputs", []),
            "artifact_formats": manifest.get("artifact_formats", []),
            "parameters": manifest.get("parameters", []),
        },
        path,
    )


def _load_recipe(path: Path) -> WorkflowRecipe:
    manifest = _read_manifest(path)
    recipe = manifest.get("deployment_recipe")
    if not isinstance(recipe, dict):
        raise CatalogManifestError(f"Runtime Manifest缺少deployment_recipe：{path}")
    return _validate_model(WorkflowRecipe, recipe, path)


def _load_distribution(
    manifest_path: Path,
    artifact_id: str,
    plugin: dict[str, Any],
    *,
    variant_id: str | None = None,
) -> tuple[ArtifactDistribution | None, CatalogArtifactFile | None]:
    relative_file = plugin.get("wheel_file")
    if relative_file is None:
        return None, None
    if not isinstance(relative_file, str) or not relative_file.strip():
        raise CatalogManifestError(f"wheel_file必须是非空相对路径：{manifest_path}")

    package_root = manifest_path.parent.resolve()
    candidate = (package_root / relative_file).resolve()
    try:
        candidate.relative_to(package_root)
    except ValueError as error:
        raise CatalogManifestError(f"制品分发文件不能越过组件目录：{manifest_path}") from error
    if candidate.suffix.casefold() != ".whl" or not candidate.is_file():
        raise CatalogManifestError(f"wheel_file不存在或不是wheel文件：{candidate}")

    content = candidate.read_bytes()
    if not content:
        raise CatalogManifestError(f"制品分发文件不能为空：{candidate}")
    if len(content) > 256 * 1024 * 1024:
        raise CatalogManifestError(f"制品分发文件超过256 MiB限制：{candidate}")
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    declared_digest = plugin.get("wheel_sha256")
    if not isinstance(declared_digest, str) or digest != f"sha256:{declared_digest.lower()}":
        raise CatalogManifestError(f"wheel_file摘要与Manifest不一致：{candidate}")

    media_type = "application/zip"
    distribution = ArtifactDistribution(
        download_path=(
            f"/api/v1/artifacts/{artifact_id}/content?variant_id={variant_id}"
            if variant_id
            else f"/api/v1/artifacts/{artifact_id}/content"
        ),
        file_name=candidate.name,
        media_type=media_type,
        size_bytes=len(content),
        digest=digest,
    )
    return distribution, CatalogArtifactFile(
        artifact_id=artifact_id,
        variant_id=variant_id,
        path=candidate,
        digest=digest,
        media_type=media_type,
    )


def _load_container_distribution(
    manifest_path: Path,
    artifact_id: str,
    specification: Any,
    *,
    variant_id: str | None = None,
) -> tuple[ArtifactDistribution | None, CatalogArtifactFile | None]:
    """加载固定摘要的Docker镜像归档；未声明时保留Registry拉取路径。"""

    if specification is None:
        return None, None
    if not isinstance(specification, dict):
        raise CatalogManifestError(f"容器镜像distribution必须是对象：{manifest_path}")
    relative_file = specification.get("file")
    declared_digest = specification.get("sha256")
    if not isinstance(relative_file, str) or not relative_file.strip():
        raise CatalogManifestError(f"容器镜像distribution.file必须是非空相对路径：{manifest_path}")
    if not isinstance(declared_digest, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", declared_digest
    ):
        raise CatalogManifestError(f"容器镜像distribution.sha256格式不合法：{manifest_path}")

    package_root = manifest_path.parent.resolve()
    candidate = (package_root / relative_file).resolve()
    try:
        candidate.relative_to(package_root)
    except ValueError as error:
        raise CatalogManifestError(f"制品分发文件不能越过组件目录：{manifest_path}") from error
    lowered_name = candidate.name.casefold()
    if not lowered_name.endswith((".tar", ".tar.gz", ".tgz")) or not candidate.is_file():
        raise CatalogManifestError(f"容器镜像distribution文件不存在或不是tar归档：{candidate}")
    try:
        size = candidate.stat().st_size
        digest_builder = hashlib.sha256()
        with candidate.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(4 * 1024 * 1024), b""):
                digest_builder.update(chunk)
    except OSError as error:
        raise CatalogManifestError(f"无法读取制品分发文件：{candidate}") from error
    if size <= 0 or size > 64 * 1024 * 1024 * 1024:
        raise CatalogManifestError(f"容器镜像distribution大小不合法：{candidate}")
    digest = f"sha256:{digest_builder.hexdigest()}"
    if digest != f"sha256:{declared_digest.casefold()}":
        raise CatalogManifestError(f"容器镜像distribution摘要与Manifest不一致：{candidate}")
    media_type = (
        "application/gzip" if lowered_name.endswith((".tar.gz", ".tgz")) else "application/x-tar"
    )
    distribution = ArtifactDistribution(
        download_path=(
            f"/api/v1/artifacts/{artifact_id}/content?variant_id={variant_id}"
            if variant_id
            else f"/api/v1/artifacts/{artifact_id}/content"
        ),
        file_name=candidate.name,
        media_type=media_type,
        size_bytes=size,
        digest=digest,
    )
    return distribution, CatalogArtifactFile(
        artifact_id=artifact_id,
        variant_id=variant_id,
        path=candidate,
        digest=digest,
        media_type=media_type,
    )


def _load_managed_file_distribution(
    manifest_path: Path,
    artifact_id: str,
    specification: Any,
    *,
    suffix: str,
    label: str,
    media_type: str = "application/octet-stream",
    variant_id: str | None = None,
) -> tuple[ArtifactDistribution | None, CatalogArtifactFile | None]:
    """加载需要原子复制到Edge受管目录的固定摘要文件。"""

    if specification is None:
        return None, None
    if not isinstance(specification, dict):
        raise CatalogManifestError(f"{label} distribution必须是对象：{manifest_path}")
    relative_file = specification.get("file")
    declared_digest = specification.get("sha256")
    if not isinstance(relative_file, str) or not relative_file.strip():
        raise CatalogManifestError(f"{label} distribution.file必须是非空相对路径：{manifest_path}")
    if not isinstance(declared_digest, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", declared_digest
    ):
        raise CatalogManifestError(f"{label} distribution.sha256格式不合法：{manifest_path}")

    package_root = manifest_path.parent.resolve()
    candidate = (package_root / relative_file).resolve()
    try:
        candidate.relative_to(package_root)
    except ValueError as error:
        raise CatalogManifestError(f"制品分发文件不能越过组件目录：{manifest_path}") from error
    if candidate.suffix.casefold() != suffix or not candidate.is_file():
        raise CatalogManifestError(f"{label} distribution文件不存在或扩展名不合法：{candidate}")
    try:
        size = candidate.stat().st_size
        digest_builder = hashlib.sha256()
        with candidate.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(4 * 1024 * 1024), b""):
                digest_builder.update(chunk)
    except OSError as error:
        raise CatalogManifestError(f"无法读取制品分发文件：{candidate}") from error
    if size <= 0 or size > 64 * 1024 * 1024 * 1024:
        raise CatalogManifestError(f"{label} distribution大小不合法：{candidate}")
    digest = f"sha256:{digest_builder.hexdigest()}"
    if digest != f"sha256:{declared_digest.casefold()}":
        raise CatalogManifestError(f"{label} distribution摘要与Manifest不一致：{candidate}")
    distribution = ArtifactDistribution(
        download_path=(
            f"/api/v1/artifacts/{artifact_id}/content?variant_id={variant_id}"
            if variant_id
            else f"/api/v1/artifacts/{artifact_id}/content"
        ),
        file_name=candidate.name,
        media_type=media_type,
        size_bytes=size,
        digest=digest,
    )
    return distribution, CatalogArtifactFile(
        artifact_id=artifact_id,
        variant_id=variant_id,
        path=candidate,
        digest=digest,
        media_type=media_type,
    )


def _calibration_mapping(value: Any, field: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogManifestError(f"标定文件字段{field}必须是对象：{path}")
    return value


def _calibration_number(value: Any, field: str, path: Path, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogManifestError(f"标定文件字段{field}必须是有限数值：{path}")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise CatalogManifestError(f"标定文件字段{field}数值不合法：{path}")
    return number


def _validate_calibration_profile(
    path: Path,
    *,
    expected_locator: str,
    expected_version: str,
) -> None:
    """在Catalog启动时验证相机到执行器标定文件的稳定契约。"""

    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogManifestError(f"无法解析标定JSON：{path}") from error
    root = _calibration_mapping(profile, "root", path)
    if root.get("schema") != "robot.calibration.camera-to-arm.v1":
        raise CatalogManifestError(f"标定文件schema不受支持：{path}")
    if root.get("calibration_id") != expected_locator:
        raise CatalogManifestError(f"标定ID与Manifest locator不一致：{path}")
    if root.get("version") != expected_version:
        raise CatalogManifestError(f"标定版本与Manifest version不一致：{path}")

    source = _calibration_mapping(root.get("source"), "source", path)
    target = _calibration_mapping(root.get("target"), "target", path)
    transform = _calibration_mapping(root.get("transform"), "transform", path)
    source_device = _calibration_mapping(source.get("device"), "source.device", path)
    target_device = _calibration_mapping(target.get("device"), "target.device", path)
    intrinsics = _calibration_mapping(source.get("intrinsics"), "source.intrinsics", path)
    translation = _calibration_mapping(
        transform.get("translation_m"), "transform.translation_m", path
    )
    rotation = _calibration_mapping(
        transform.get("rotation_rpy_rad"), "transform.rotation_rpy_rad", path
    )

    required_strings = {
        "source.frame_id": source.get("frame_id"),
        "source.device.transport": source_device.get("transport"),
        "source.device.device_id": source_device.get("device_id"),
        "target.frame_id": target.get("frame_id"),
        "target.device.adapter_id": target_device.get("adapter_id"),
        "target.device.device_id": target_device.get("device_id"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in required_strings.values()):
        invalid = next(
            key
            for key, value in required_strings.items()
            if not isinstance(value, str) or not value.strip()
        )
        raise CatalogManifestError(f"标定文件字段{invalid}必须是非空字符串：{path}")

    for key in ("width_px", "height_px", "fx_px", "fy_px"):
        _calibration_number(intrinsics.get(key), f"source.intrinsics.{key}", path, positive=True)
    for key in ("cx_px", "cy_px"):
        _calibration_number(intrinsics.get(key), f"source.intrinsics.{key}", path)
    for key in ("x", "y", "z"):
        _calibration_number(translation.get(key), f"transform.translation_m.{key}", path)
    for key in ("roll", "pitch", "yaw"):
        _calibration_number(rotation.get(key), f"transform.rotation_rpy_rad.{key}", path)


def _load_calibration_distribution(
    manifest_path: Path,
    artifact_id: str,
    specification: Any,
    *,
    locator: str,
    version: str,
    variant_id: str | None = None,
) -> tuple[ArtifactDistribution | None, CatalogArtifactFile | None]:
    distribution, artifact_file = _load_managed_file_distribution(
        manifest_path,
        artifact_id,
        specification,
        suffix=".json",
        label="标定配置",
        media_type="application/json",
        variant_id=variant_id,
    )
    if artifact_file is not None:
        _validate_calibration_profile(
            artifact_file.path,
            expected_locator=locator,
            expected_version=version,
        )
    return distribution, artifact_file


def _load_artifacts(
    root: Path,
) -> tuple[list[ArtifactDefinition], list[CatalogArtifactFile]]:
    """从现有组件Manifest派生统一制品清单。"""

    artifacts: list[ArtifactDefinition] = []
    artifact_files: list[CatalogArtifactFile] = []
    for path in sorted((root / "hardware-adapters").glob("*/manifest.json")):
        manifest = _read_manifest(path)
        source_ref = str(manifest.get("adapter_id", ""))
        version = str(manifest.get("version", ""))
        plugin = manifest.get("edge_plugin")
        if isinstance(plugin, dict) and plugin.get("distribution"):
            wheel_digest = plugin.get("wheel_sha256")
            artifact_id = f"{source_ref}.edge-plugin"
            distribution, artifact_file = _load_distribution(
                path,
                artifact_id,
                plugin,
            )
            if artifact_file is not None:
                artifact_files.append(artifact_file)
            artifacts.append(
                _validate_model(
                    ArtifactDefinition,
                    {
                        "artifact_id": artifact_id,
                        "name": f"{manifest.get('name')} Edge插件",
                        "kind": ArtifactKind.EDGE_PLUGIN,
                        "scope": ArtifactScope.EDGE_RUNTIME,
                        "version": plugin.get("version", version),
                        "locator": plugin["distribution"],
                        "digest": f"sha256:{wheel_digest}" if wheel_digest else None,
                        "source_ref": source_ref,
                        "distribution": distribution,
                    },
                    path,
                )
            )
        runtime = manifest.get("runtime")
        if isinstance(runtime, dict) and runtime.get("image"):
            artifact_id = f"{source_ref}.runtime-image"
            distribution, artifact_file = _load_container_distribution(
                path,
                artifact_id,
                runtime.get("image_distribution"),
            )
            if artifact_file is not None:
                artifact_files.append(artifact_file)
            artifacts.append(
                _validate_model(
                    ArtifactDefinition,
                    {
                        "artifact_id": artifact_id,
                        "name": f"{manifest.get('name')} Runtime镜像",
                        "kind": ArtifactKind.CONTAINER_IMAGE,
                        "scope": ArtifactScope.EDGE_RUNTIME,
                        "version": version,
                        "locator": runtime["image"],
                        "digest": runtime.get("image_digest"),
                        "source_ref": source_ref,
                        "distribution": distribution,
                    },
                    path,
                )
            )

    for path in sorted((root / "algorithm-packages").glob("*/manifest.json")):
        manifest = _read_manifest(path)
        source_ref = str(manifest.get("algorithm_id", ""))
        version = str(manifest.get("version", ""))
        runtime = manifest.get("runtime")
        if isinstance(runtime, dict) and runtime.get("runtime_image"):
            artifact_id = f"{source_ref}.runtime-image"
            distribution, artifact_file = _load_container_distribution(
                path,
                artifact_id,
                runtime.get("runtime_image_distribution"),
            )
            if artifact_file is not None:
                artifact_files.append(artifact_file)
            artifacts.append(
                _validate_model(
                    ArtifactDefinition,
                    {
                        "artifact_id": artifact_id,
                        "name": f"{manifest.get('name')} Runtime镜像",
                        "kind": ArtifactKind.CONTAINER_IMAGE,
                        "scope": ArtifactScope.EDGE_RUNTIME,
                        "version": version,
                        "locator": runtime["runtime_image"],
                        "digest": runtime.get("runtime_image_digest"),
                        "architecture": runtime.get("architecture"),
                        "source_ref": source_ref,
                        "distribution": distribution,
                    },
                    path,
                )
            )
        for relative_model_path in manifest.get("models", []):
            model_path = path.parent / str(relative_model_path)
            model = _read_manifest(model_path)
            model_artifact = model.get("artifact")
            if not isinstance(model_artifact, dict) or not model_artifact.get("filename"):
                continue
            digest = model_artifact.get("sha256")
            artifacts.append(
                _validate_model(
                    ArtifactDefinition,
                    {
                        "artifact_id": f"{source_ref}.model.{model.get('model_id')}",
                        "name": str(model.get("name", model.get("model_id"))),
                        "kind": ArtifactKind.MODEL,
                        "scope": ArtifactScope.BUILD_SOURCE,
                        "version": model.get("version", version),
                        "locator": model_artifact["filename"],
                        "digest": f"sha256:{digest}" if digest else None,
                        "source_ref": source_ref,
                    },
                    model_path,
                )
            )
        engine_parameter = next(
            (
                parameter
                for parameter in manifest.get("parameters", [])
                if isinstance(parameter, dict) and parameter.get("key") == "engine_path"
            ),
            None,
        )
        if isinstance(engine_parameter, dict) and engine_parameter.get("default"):
            engine_artifact = (
                runtime.get("engine_artifact", {}) if isinstance(runtime, dict) else {}
            )
            engine_digest = (
                engine_artifact.get("sha256") if isinstance(engine_artifact, dict) else None
            )
            engine_artifact_id = f"{source_ref}.tensorrt-engine"
            engine_distribution, engine_file = _load_managed_file_distribution(
                path,
                engine_artifact_id,
                engine_artifact.get("distribution") if isinstance(engine_artifact, dict) else None,
                suffix=".engine",
                label="TensorRT Engine",
            )
            if engine_file is not None:
                artifact_files.append(engine_file)
            if (
                engine_distribution is not None
                and engine_digest
                and engine_distribution.digest != f"sha256:{str(engine_digest).casefold()}"
            ):
                raise CatalogManifestError(f"TensorRT Engine内容摘要与distribution不一致：{path}")
            artifacts.append(
                _validate_model(
                    ArtifactDefinition,
                    {
                        "artifact_id": engine_artifact_id,
                        "name": f"{manifest.get('name')} TensorRT Engine",
                        "kind": ArtifactKind.TENSORRT_ENGINE,
                        "scope": ArtifactScope.EDGE_RUNTIME,
                        "version": version,
                        "locator": engine_parameter["default"],
                        "digest": f"sha256:{engine_digest}" if engine_digest else None,
                        "architecture": runtime.get("architecture")
                        if isinstance(runtime, dict)
                        else None,
                        "source_ref": source_ref,
                        "distribution": engine_distribution,
                    },
                    path,
                )
            )

    for path in sorted((root / "workflow-runtimes").glob("*/manifest.json")):
        manifest = _read_manifest(path)
        source_ref = str(manifest.get("workflow_runtime_id", ""))
        if not manifest.get("runtime_image"):
            continue
        artifact_id = f"{source_ref}.runtime-image"
        distribution, artifact_file = _load_container_distribution(
            path,
            artifact_id,
            manifest.get("runtime_image_distribution"),
        )
        if artifact_file is not None:
            artifact_files.append(artifact_file)
        artifacts.append(
            _validate_model(
                ArtifactDefinition,
                {
                    "artifact_id": artifact_id,
                    "name": str(manifest.get("name", source_ref)),
                    "kind": ArtifactKind.CONTAINER_IMAGE,
                    "scope": ArtifactScope.EDGE_RUNTIME,
                    "version": manifest.get("version"),
                    "locator": manifest["runtime_image"],
                    "digest": manifest.get("runtime_image_digest"),
                    "architecture": manifest.get("architecture"),
                    "source_ref": source_ref,
                    "distribution": distribution,
                },
                path,
            )
        )

    for path in sorted((root / "artifact-packages").glob("*/manifest.json")):
        manifest = _read_manifest(path)
        artifact_id = str(manifest.get("artifact_id", ""))
        kind = manifest.get("kind")
        raw_variants = manifest.get("variants", [])
        if not isinstance(raw_variants, list):
            raise CatalogManifestError(f"variants必须是数组：{path}")
        variants: list[ArtifactVariant] = []
        for raw_variant in raw_variants:
            if not isinstance(raw_variant, dict):
                raise CatalogManifestError(f"variants中的每一项必须是对象：{path}")
            variant_id = str(raw_variant.get("variant_id", ""))
            variant_distribution = None
            variant_file = None
            if kind == ArtifactKind.CONTAINER_IMAGE:
                variant_distribution, variant_file = _load_container_distribution(
                    path,
                    artifact_id,
                    raw_variant.get("distribution"),
                    variant_id=variant_id,
                )
            elif kind == ArtifactKind.RUNTIME_PACKAGE:
                variant_distribution, variant_file = _load_distribution(
                    path,
                    artifact_id,
                    raw_variant.get("distribution", {}),
                    variant_id=variant_id,
                )
            elif kind == ArtifactKind.CALIBRATION_PROFILE:
                variant_distribution, variant_file = _load_calibration_distribution(
                    path,
                    artifact_id,
                    raw_variant.get("distribution"),
                    locator=str(raw_variant.get("locator", "")),
                    version=str(manifest.get("version", "")),
                    variant_id=variant_id,
                )
            if variant_file is not None:
                artifact_files.append(variant_file)
            variants.append(
                _validate_model(
                    ArtifactVariant,
                    {
                        "variant_id": variant_id,
                        "locator": raw_variant.get("locator"),
                        "digest": raw_variant.get("digest")
                        or (
                            variant_distribution.digest
                            if variant_distribution is not None
                            else None
                        ),
                        "architecture": raw_variant.get("architecture"),
                        "compatibility": raw_variant.get("compatibility", {}),
                        "distribution": variant_distribution,
                        "priority": raw_variant.get("priority", 0),
                    },
                    path,
                )
            )
        distribution = None
        artifact_file = None
        if not variants and kind == ArtifactKind.CONTAINER_IMAGE:
            distribution, artifact_file = _load_container_distribution(
                path,
                artifact_id,
                manifest.get("distribution"),
            )
        elif not variants and kind == ArtifactKind.RUNTIME_PACKAGE:
            distribution, artifact_file = _load_distribution(
                path,
                artifact_id,
                manifest.get("distribution", {}),
            )
        elif not variants and kind == ArtifactKind.CALIBRATION_PROFILE:
            distribution, artifact_file = _load_calibration_distribution(
                path,
                artifact_id,
                manifest.get("distribution"),
                locator=str(manifest.get("locator", "")),
                version=str(manifest.get("version", "")),
            )
        if artifact_file is not None:
            artifact_files.append(artifact_file)
        default_variant = variants[0] if variants else None
        artifacts.append(
            _validate_model(
                ArtifactDefinition,
                {
                    "artifact_id": artifact_id,
                    "name": manifest.get("name"),
                    "kind": kind,
                    "scope": manifest.get("scope"),
                    "version": manifest.get("version"),
                    "locator": manifest.get("locator")
                    or (default_variant.locator if default_variant else None),
                    "digest": manifest.get("digest")
                    or (default_variant.digest if default_variant else None)
                    or (distribution.digest if distribution else None),
                    "architecture": manifest.get("architecture")
                    or (default_variant.architecture if default_variant else None),
                    "source_ref": manifest.get("source_ref"),
                    "distribution": distribution
                    or (default_variant.distribution if default_variant else None),
                    "variants": variants,
                },
                path,
            )
        )
    return artifacts, artifact_files


def _assert_unique(values: list[str], kind: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise CatalogManifestError(f"{kind} ID重复：{', '.join(duplicates)}")


def _validate_recipe_references(snapshot: CatalogSnapshot) -> None:
    hardware_versions = {(item.hardware_id, item.version) for item in snapshot.hardware}
    algorithm_versions = {(item.algorithm_id, item.version) for item in snapshot.algorithms}
    for recipe in snapshot.recipes:
        for component in recipe.components:
            reference = (component.component_ref, component.component_version)
            if reference not in hardware_versions and reference not in algorithm_versions:
                raise CatalogManifestError(
                    f"配方{recipe.recipe_id}引用了不存在的组件{reference[0]}@{reference[1]}"
                )
        artifact_ids = {item.artifact_id for item in snapshot.artifacts}
        missing_artifacts = sorted(set(recipe.required_artifact_ids) - artifact_ids)
        if missing_artifacts:
            raise CatalogManifestError(
                f"配方{recipe.recipe_id}引用了不存在的制品" + "、".join(missing_artifacts)
            )


def _hydrate_recipe_parameters(
    recipes: list[WorkflowRecipe],
    hardware: list[HardwareDefinition],
    algorithms: list[AlgorithmDefinition],
) -> list[WorkflowRecipe]:
    """把组件参数Schema绑定到配方节点；配方不再重复声明字段。"""

    assets = {(item.hardware_id, item.version): item for item in hardware} | {
        (item.algorithm_id, item.version): item for item in algorithms
    }
    hydrated: list[WorkflowRecipe] = []
    for recipe in recipes:
        parameters: list[RecipeParameter] = []
        for component in recipe.components:
            asset = assets.get((component.component_ref, component.component_version))
            if asset is None:
                continue
            parameters.extend(
                RecipeParameter(node_id=component.node_id, **parameter.model_dump())
                for parameter in asset.parameters
            )
        hydrated.append(recipe.model_copy(update={"parameters": parameters}))
    return hydrated


def load_catalog(asset_root: Path | None = None) -> CatalogSnapshot:
    """加载内置模拟资产和资产根目录中的真实包Manifest。"""

    root = asset_root if asset_root is not None else _default_asset_root()
    hardware = [_MOCK_HARDWARE, _MOCK_ARM]
    algorithms = [
        _MOCK_ALGORITHM,
        _MOCK_FILTER,
        _MOCK_TARGET_SELECTOR,
        _RGBD_TARGET_LOCALIZER,
        _CARTESIAN_POINT_TO_POINT,
        _MOCK_SAFETY_GATE,
    ]
    recipes = [
        _MOCK_RECIPE,
        _MOCK_PIPELINE_RECIPE,
        _MOCK_VISION_ARM_RECIPE,
        _MOCK_SAFE_VISION_ARM_RECIPE,
    ]
    artifacts: list[ArtifactDefinition] = []
    artifact_files: list[CatalogArtifactFile] = []

    if root is not None:
        hardware.extend(
            _load_hardware(path)
            for path in sorted((root / "hardware-adapters").glob("*/manifest.json"))
        )
        algorithms.extend(
            _load_algorithm(path)
            for path in sorted((root / "algorithm-packages").glob("*/manifest.json"))
        )
        recipes.extend(
            _load_recipe(path)
            for path in sorted((root / "workflow-runtimes").glob("*/manifest.json"))
        )
        loaded_artifacts, loaded_files = _load_artifacts(root)
        artifacts.extend(loaded_artifacts)
        artifact_files.extend(loaded_files)

    recipes = _hydrate_recipe_parameters(recipes, hardware, algorithms)
    required_by: dict[str, list[str]] = {}
    for recipe in recipes:
        for artifact_id in recipe.required_artifact_ids:
            required_by.setdefault(artifact_id, []).append(recipe.recipe_id)
    artifacts = [
        artifact.model_copy(
            update={"required_by": sorted(required_by.get(artifact.artifact_id, []))}
        )
        for artifact in artifacts
    ]

    _assert_unique([item.hardware_id for item in hardware], "硬件")
    _assert_unique([item.algorithm_id for item in algorithms], "算法")
    _assert_unique([item.recipe_id for item in recipes], "配方")
    _assert_unique([item.artifact_id for item in artifacts], "制品")
    snapshot = CatalogSnapshot(
        hardware=tuple(hardware),
        algorithms=tuple(algorithms),
        recipes=tuple(recipes),
        artifacts=tuple(artifacts),
        artifact_files=tuple(artifact_files),
    )
    _validate_recipe_references(snapshot)
    return snapshot


_CATALOG = load_catalog()


def list_hardware() -> list[HardwareDefinition]:
    """返回硬件定义快照。"""

    return [item.model_copy(deep=True) for item in _CATALOG.hardware]


def list_algorithms() -> list[AlgorithmDefinition]:
    """返回算法定义快照。"""

    return [item.model_copy(deep=True) for item in _CATALOG.algorithms]


def list_workflow_recipes() -> list[WorkflowRecipe]:
    """返回已经具有端侧Runtime实现的工作流配方。"""

    return [item.model_copy(deep=True) for item in _CATALOG.recipes]


def list_artifacts() -> list[ArtifactDefinition]:
    """返回统一制品定义快照。"""

    return [item.model_copy(deep=True) for item in _CATALOG.artifacts]


def find_artifact(artifact_id: str) -> ArtifactDefinition | None:
    """按稳定ID查找制品定义。"""

    return next(
        (
            item.model_copy(deep=True)
            for item in _CATALOG.artifacts
            if item.artifact_id == artifact_id
        ),
        None,
    )


def read_artifact_content(
    artifact_id: str,
    variant_id: str | None = None,
) -> ArtifactContent | None:
    """按Catalog ID读取并复核可信文件；不接受调用方提供的文件路径。"""

    artifact_file = next(
        (
            item
            for item in _CATALOG.artifact_files
            if item.artifact_id == artifact_id and item.variant_id == variant_id
        ),
        None,
    )
    if artifact_file is None:
        return None
    try:
        size = artifact_file.path.stat().st_size
        digest_builder = hashlib.sha256()
        with artifact_file.path.open("rb") as content_file:
            for chunk in iter(lambda: content_file.read(4 * 1024 * 1024), b""):
                digest_builder.update(chunk)
    except OSError as error:
        raise CatalogManifestError(f"无法读取制品分发文件：{artifact_file.artifact_id}") from error
    digest = f"sha256:{digest_builder.hexdigest()}"
    if digest != artifact_file.digest:
        raise CatalogManifestError(
            f"制品分发文件在Catalog加载后发生变化：{artifact_file.artifact_id}"
        )
    return ArtifactContent(
        file_name=artifact_file.path.name,
        path=artifact_file.path,
        size_bytes=size,
        digest=digest,
        media_type=artifact_file.media_type,
    )


def find_workflow_recipe(component_refs: list[str]) -> WorkflowRecipe | None:
    """按完整组件集合查找唯一可执行配方。"""

    return next(
        (
            item.model_copy(deep=True)
            for item in _CATALOG.recipes
            if sorted(component.component_ref for component in item.components)
            == sorted(component_refs)
        ),
        None,
    )


def find_hardware(hardware_id: str, version: str) -> HardwareDefinition | None:
    """按ID和版本查找硬件定义。"""

    return next(
        (
            item.model_copy(deep=True)
            for item in _CATALOG.hardware
            if item.hardware_id == hardware_id and item.version == version
        ),
        None,
    )


def find_algorithm(algorithm_id: str, version: str) -> AlgorithmDefinition | None:
    """按ID和版本查找算法定义。"""

    return next(
        (
            item.model_copy(deep=True)
            for item in _CATALOG.algorithms
            if item.algorithm_id == algorithm_id and item.version == version
        ),
        None,
    )
