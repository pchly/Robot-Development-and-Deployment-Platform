from __future__ import annotations

import struct

import numpy as np
import pytest

from robot_mujoco_arm import CartesianTarget, MujocoArmRuntime
from robot_mujoco_arm.runtime import JOINT_NAMES


def test_model_exposes_six_joints_and_actuators() -> None:
    runtime = MujocoArmRuntime()

    assert runtime.model.nu == 6
    assert len(JOINT_NAMES) == 6
    assert len(runtime.observe().joint_positions) == 6


def test_runtime_requires_safety_authorization() -> None:
    runtime = MujocoArmRuntime()

    with pytest.raises(RuntimeError, match="Safety Gate"):
        runtime.command_target(CartesianTarget(0.25, 0.0, 0.45), simulation_steps=10)


def test_cartesian_target_moves_end_effector() -> None:
    runtime = MujocoArmRuntime()
    before = runtime.observe()
    runtime.enable_commands()

    after = runtime.command_target(
        CartesianTarget(0.20, 0.10, 0.48),
        simulation_steps=1200,
        tolerance_m=0.03,
    )

    assert after.safety_state == "command_enabled"
    assert after.position_error_m < before.position_error_m
    assert after.position_error_m < 0.08


def test_incremental_target_does_not_teleport_live_arm() -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")
    before = runtime.observe()

    accepted = runtime.set_target(CartesianTarget(0.20, 0.12, 0.48))

    assert accepted.joint_positions == pytest.approx(before.joint_positions)
    assert accepted.end_effector == pytest.approx(before.end_effector)

    first_slice = runtime.advance(simulation_steps=34, velocity_scale=0.15)
    later_slice = runtime.advance(simulation_steps=34, velocity_scale=0.15)

    assert first_slice.joint_positions != pytest.approx(before.joint_positions)
    assert later_slice.position_error_m < first_slice.position_error_m
    assert first_slice.simulation_steps == 34
    assert runtime.timestep_s == pytest.approx(0.002)


def test_realtime_tracking_applies_current_ik_solution_without_physics_batch() -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")

    state = runtime.track_target(CartesianTarget(0.20, 0.12, 0.48))

    assert state.simulation_steps == 0
    assert state.position_error_m < 0.03
    assert state.target_reached is True
    assert tuple(runtime.data.ctrl) == pytest.approx(state.joint_positions)


def test_hold_position_cancels_stale_target_without_latching_estop() -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")
    runtime.set_target(CartesianTarget(0.20, 0.12, 0.48))
    runtime.advance(simulation_steps=34, velocity_scale=0.15)

    runtime.hold_position()
    held = runtime.observe()
    runtime.advance(simulation_steps=34, velocity_scale=0.15)
    after = runtime.observe()

    assert runtime.safety_state == "command_enabled"
    assert after.joint_positions == pytest.approx(held.joint_positions, abs=0.02)


def test_emergency_stop_requires_explicit_reset() -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")

    runtime.emergency_stop()

    with pytest.raises(RuntimeError, match="安全故障"):
        runtime.enable_commands()
    assert runtime.reset_safety(authorized=True, emergency_stop_active=True) is False
    assert runtime.reset_safety(authorized=True, emergency_stop_active=False) is True


@pytest.mark.parametrize(
    "target",
    [
        CartesianTarget(0.0, 0.0, 0.4),
        CartesianTarget(0.2, 0.8, 0.4),
        CartesianTarget(0.2, 0.0, 1.0),
    ],
)
def test_workspace_limits_reject_invalid_targets(target: CartesianTarget) -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")

    with pytest.raises(ValueError, match="目标"):
        runtime.command_target(target, simulation_steps=10)


def test_step_limit_is_bounded() -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")

    with pytest.raises(ValueError, match="simulation_steps"):
        runtime.command_target(CartesianTarget(0.2, 0.0, 0.4), simulation_steps=0)


def test_render_png_returns_bounded_rgb_preview() -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")
    runtime.command_target(CartesianTarget(0.25, 0.10, 0.45), simulation_steps=100)

    image = runtime.render_png(width=320, height=240)

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.endswith(b"IEND\xaeB`\x82")
    assert struct.unpack(">II", image[16:24]) == (320, 240)
    assert len(image) < 256 * 1024


def test_preview_snapshot_is_rendered_with_reused_isolated_resources() -> None:
    runtime = MujocoArmRuntime(safety_state="command_enabled")
    runtime.track_target(CartesianTarget(0.22, 0.10, 0.48))
    snapshot = runtime.snapshot_preview()

    first = runtime.render_snapshot_rgb(snapshot, width=320, height=240)
    renderer = runtime._preview_renderer
    runtime.track_target(CartesianTarget(0.22, -0.10, 0.42))
    second = runtime.render_snapshot_rgb(snapshot, width=320, height=240)

    assert first.shape == (240, 320, 3)
    assert second.shape == (240, 320, 3)
    assert np.array_equal(first, second)
    assert runtime._preview_renderer is renderer
    runtime.close_preview()
    assert runtime._preview_renderer is None


def test_png_encoder_rejects_non_rgb_input() -> None:
    with pytest.raises(ValueError, match="RGB"):
        MujocoArmRuntime._encode_png(np.zeros((10, 10), dtype=np.uint8))
