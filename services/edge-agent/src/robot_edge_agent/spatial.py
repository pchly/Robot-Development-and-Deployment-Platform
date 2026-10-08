"""视觉观测到机器人坐标系目标位姿的通用空间契约。"""

from __future__ import annotations

import math
from dataclasses import dataclass


class SpatialContractError(ValueError):
    """空间数据缺失、过期或不满足工作空间约束。"""


def _finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise SpatialContractError(f"{name}必须是有限数值")
    return result


@dataclass(frozen=True)
class CameraIntrinsics:
    """针孔相机内参与对应图像坐标系。"""

    frame_id: str
    width_px: int
    height_px: int
    fx_px: float
    fy_px: float
    cx_px: float
    cy_px: float

    def __post_init__(self) -> None:
        if not self.frame_id:
            raise SpatialContractError("相机坐标系不能为空")
        if self.width_px <= 0 or self.height_px <= 0:
            raise SpatialContractError("图像尺寸必须为正整数")
        for name in ("fx_px", "fy_px"):
            if _finite(getattr(self, name), name) <= 0:
                raise SpatialContractError(f"{name}必须大于0")
        _finite(self.cx_px, "cx_px")
        _finite(self.cy_px, "cy_px")


@dataclass(frozen=True)
class RigidTransform:
    """从源坐标系到目标坐标系的刚体变换，旋转采用 ZYX 欧拉角。"""

    source_frame: str
    target_frame: str
    calibration_id: str
    translation_m: tuple[float, float, float]
    roll_rad: float = 0.0
    pitch_rad: float = 0.0
    yaw_rad: float = 0.0

    def __post_init__(self) -> None:
        if not self.source_frame or not self.target_frame:
            raise SpatialContractError("变换的源坐标系和目标坐标系不能为空")
        if not self.calibration_id:
            raise SpatialContractError("calibration_id不能为空")
        if len(self.translation_m) != 3:
            raise SpatialContractError("平移向量必须包含3个分量")
        for index, value in enumerate(self.translation_m):
            _finite(value, f"translation_m[{index}]")
        for name in ("roll_rad", "pitch_rad", "yaw_rad"):
            _finite(getattr(self, name), name)


@dataclass(frozen=True)
class DetectionSample:
    """与深度对齐的单个二维检测中心点。"""

    sequence: int
    captured_at_ms: int
    u_px: float
    v_px: float
    depth_m: float
    label: str
    confidence: float


@dataclass(frozen=True)
class TargetPose:
    """可跨算法与执行器传递的机器人基座坐标系目标位姿。"""

    sequence: int
    captured_at_ms: int
    expires_at_ms: int
    frame_id: str
    calibration_id: str
    source_label: str
    source_confidence: float
    position_m: tuple[float, float, float]


@dataclass(frozen=True)
class CartesianWorkspace:
    """笛卡尔点到点规划器允许的轴对齐工作空间。"""

    x_m: tuple[float, float]
    y_m: tuple[float, float]
    z_m: tuple[float, float]

    def __post_init__(self) -> None:
        for axis, limits in (("x", self.x_m), ("y", self.y_m), ("z", self.z_m)):
            if len(limits) != 2 or not limits[0] < limits[1]:
                raise SpatialContractError(f"{axis}轴工作空间上下界无效")


@dataclass(frozen=True)
class CartesianCommand:
    """经时效与工作空间检查后的笛卡尔运动命令。"""

    sequence: int
    frame_id: str
    position_m: tuple[float, float, float]
    source_expires_at_ms: int


def _rotate_zyx(
    point: tuple[float, float, float],
    *,
    roll_rad: float,
    pitch_rad: float,
    yaw_rad: float,
) -> tuple[float, float, float]:
    """按 Rz(yaw) * Ry(pitch) * Rx(roll) 旋转三维点。"""

    x, y, z = point
    cr, sr = math.cos(roll_rad), math.sin(roll_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    return (
        (cy * cp) * x + (cy * sp * sr - sy * cr) * y + (cy * sp * cr + sy * sr) * z,
        (sy * cp) * x + (sy * sp * sr + cy * cr) * y + (sy * sp * cr - cy * sr) * z,
        (-sp) * x + (cp * sr) * y + (cp * cr) * z,
    )


def localize_detection(
    detection: DetectionSample,
    intrinsics: CameraIntrinsics,
    camera_to_base: RigidTransform,
    *,
    ttl_ms: int,
) -> TargetPose:
    """将像素、对齐深度和标定变换转换为机器人基座坐标系目标。"""

    if camera_to_base.source_frame != intrinsics.frame_id:
        raise SpatialContractError("标定源坐标系与相机内参坐标系不一致")
    if detection.sequence < 0 or detection.captured_at_ms < 0:
        raise SpatialContractError("序号和采集时间不能为负数")
    if not detection.label:
        raise SpatialContractError("检测标签不能为空")
    confidence = _finite(detection.confidence, "confidence")
    if not 0.0 <= confidence <= 1.0:
        raise SpatialContractError("confidence必须位于[0, 1]")
    u_px = _finite(detection.u_px, "u_px")
    v_px = _finite(detection.v_px, "v_px")
    if not 0.0 <= u_px < intrinsics.width_px or not 0.0 <= v_px < intrinsics.height_px:
        raise SpatialContractError("检测中心点位于图像范围之外")
    depth_m = _finite(detection.depth_m, "depth_m")
    if depth_m <= 0:
        raise SpatialContractError("对齐深度必须大于0米")
    if ttl_ms <= 0:
        raise SpatialContractError("目标有效期必须大于0毫秒")

    camera_point = (
        (u_px - intrinsics.cx_px) * depth_m / intrinsics.fx_px,
        (v_px - intrinsics.cy_px) * depth_m / intrinsics.fy_px,
        depth_m,
    )
    rotated = _rotate_zyx(
        camera_point,
        roll_rad=camera_to_base.roll_rad,
        pitch_rad=camera_to_base.pitch_rad,
        yaw_rad=camera_to_base.yaw_rad,
    )
    position = tuple(rotated[index] + camera_to_base.translation_m[index] for index in range(3))
    return TargetPose(
        sequence=detection.sequence,
        captured_at_ms=detection.captured_at_ms,
        expires_at_ms=detection.captured_at_ms + ttl_ms,
        frame_id=camera_to_base.target_frame,
        calibration_id=camera_to_base.calibration_id,
        source_label=detection.label,
        source_confidence=confidence,
        position_m=position,
    )


def plan_cartesian_target(
    target: TargetPose,
    workspace: CartesianWorkspace,
    *,
    now_ms: int,
    required_frame: str = "arm_base",
) -> CartesianCommand:
    """检查坐标系、时效和工作空间后生成笛卡尔运动命令。"""

    if target.frame_id != required_frame:
        raise SpatialContractError(f"目标坐标系必须是{required_frame}，实际为{target.frame_id}")
    if now_ms > target.expires_at_ms:
        raise SpatialContractError("目标位姿已过期")
    for axis, value, limits in zip(
        ("x", "y", "z"),
        target.position_m,
        (workspace.x_m, workspace.y_m, workspace.z_m),
        strict=True,
    ):
        if not limits[0] <= value <= limits[1]:
            raise SpatialContractError(f"目标{axis}={value:.4f}m超出工作空间")
    return CartesianCommand(
        sequence=target.sequence,
        frame_id=target.frame_id,
        position_m=target.position_m,
        source_expires_at_ms=target.expires_at_ms,
    )
