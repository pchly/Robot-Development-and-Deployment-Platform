"""空间定位与笛卡尔规划契约测试。"""

import math

import pytest

from robot_edge_agent.spatial import (
    CameraIntrinsics,
    CartesianWorkspace,
    DetectionSample,
    RigidTransform,
    SpatialContractError,
    localize_detection,
    plan_cartesian_target,
)


def intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        frame_id="camera_optical",
        width_px=640,
        height_px=480,
        fx_px=600,
        fy_px=600,
        cx_px=320,
        cy_px=240,
    )


def detection(**changes: object) -> DetectionSample:
    values = {
        "sequence": 7,
        "captured_at_ms": 1000,
        "u_px": 320.0,
        "v_px": 240.0,
        "depth_m": 0.45,
        "label": "target",
        "confidence": 0.9,
    }
    values.update(changes)
    return DetectionSample(**values)  # type: ignore[arg-type]


def transform(**changes: object) -> RigidTransform:
    values = {
        "source_frame": "camera_optical",
        "target_frame": "arm_base",
        "calibration_id": "mock-camera-to-arm-v1",
        "translation_m": (0.25, 0.1, 0.0),
    }
    values.update(changes)
    return RigidTransform(**values)  # type: ignore[arg-type]


def workspace() -> CartesianWorkspace:
    return CartesianWorkspace(
        x_m=(0.05, 0.75),
        y_m=(-0.55, 0.55),
        z_m=(0.05, 0.85),
    )


def test_localize_center_pixel_and_plan_target() -> None:
    target = localize_detection(detection(), intrinsics(), transform(), ttl_ms=300)

    assert target.position_m == pytest.approx((0.25, 0.1, 0.45))
    assert target.frame_id == "arm_base"
    assert target.calibration_id == "mock-camera-to-arm-v1"
    assert target.expires_at_ms == 1300

    command = plan_cartesian_target(target, workspace(), now_ms=1100)
    assert command.position_m == pytest.approx((0.25, 0.1, 0.45))
    assert command.sequence == 7


def test_localize_applies_camera_projection_and_extrinsic_rotation() -> None:
    target = localize_detection(
        detection(u_px=420.0, v_px=240.0, depth_m=0.6),
        intrinsics(),
        transform(translation_m=(0.0, 0.0, 0.0), yaw_rad=math.pi / 2),
        ttl_ms=100,
    )

    assert target.position_m == pytest.approx((0.0, 0.1, 0.6), abs=1e-9)


@pytest.mark.parametrize(
    ("sample", "message"),
    [
        (detection(depth_m=0.0), "深度"),
        (detection(u_px=640.0), "图像范围"),
        (detection(confidence=1.1), "confidence"),
    ],
)
def test_localize_rejects_invalid_observations(sample: DetectionSample, message: str) -> None:
    with pytest.raises(SpatialContractError, match=message):
        localize_detection(sample, intrinsics(), transform(), ttl_ms=300)


def test_localize_rejects_mismatched_calibration_frame() -> None:
    with pytest.raises(SpatialContractError, match="坐标系"):
        localize_detection(
            detection(),
            intrinsics(),
            transform(source_frame="other_camera"),
            ttl_ms=300,
        )


def test_planner_rejects_expired_or_out_of_workspace_target() -> None:
    target = localize_detection(detection(), intrinsics(), transform(), ttl_ms=100)
    with pytest.raises(SpatialContractError, match="过期"):
        plan_cartesian_target(target, workspace(), now_ms=1101)

    outside = localize_detection(
        detection(depth_m=1.0),
        intrinsics(),
        transform(),
        ttl_ms=300,
    )
    with pytest.raises(SpatialContractError, match="工作空间"):
        plan_cartesian_target(outside, workspace(), now_ms=1000)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: CameraIntrinsics("", 640, 480, 600, 600, 320, 240),
            "相机坐标系",
        ),
        (
            lambda: CameraIntrinsics("camera", 0, 480, 600, 600, 320, 240),
            "图像尺寸",
        ),
        (
            lambda: CameraIntrinsics("camera", 640, 480, 0, 600, 320, 240),
            "fx_px",
        ),
        (
            lambda: CameraIntrinsics("camera", 640, 480, 600, 600, math.inf, 240),
            "有限数值",
        ),
        (
            lambda: RigidTransform("", "arm_base", "cal", (0, 0, 0)),
            "源坐标系",
        ),
        (
            lambda: RigidTransform("camera", "arm_base", "", (0, 0, 0)),
            "calibration_id",
        ),
        (
            lambda: RigidTransform("camera", "arm_base", "cal", (0, 0)),
            "3个分量",
        ),
        (
            lambda: CartesianWorkspace((1, 0), (-1, 1), (0, 1)),
            "工作空间上下界",
        ),
    ],
)
def test_contract_value_objects_reject_invalid_configuration(factory: object, message: str) -> None:
    with pytest.raises(SpatialContractError, match=message):
        factory()  # type: ignore[operator]


def test_localizer_and_planner_reject_incomplete_target_contract() -> None:
    with pytest.raises(SpatialContractError, match="序号"):
        localize_detection(detection(sequence=-1), intrinsics(), transform(), ttl_ms=300)
    with pytest.raises(SpatialContractError, match="标签"):
        localize_detection(detection(label=""), intrinsics(), transform(), ttl_ms=300)
    with pytest.raises(SpatialContractError, match="有效期"):
        localize_detection(detection(), intrinsics(), transform(), ttl_ms=0)

    target = localize_detection(detection(), intrinsics(), transform(), ttl_ms=300)
    with pytest.raises(SpatialContractError, match="目标坐标系"):
        plan_cartesian_target(target, workspace(), now_ms=1000, required_frame="world")
