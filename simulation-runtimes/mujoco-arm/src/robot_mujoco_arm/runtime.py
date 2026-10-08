"""Headless-capable MuJoCo virtual arm and Cartesian target controller."""

from __future__ import annotations

import struct
import zlib
from dataclasses import asdict, dataclass
from importlib.resources import files
from typing import Literal

import mujoco
import numpy as np

SafetyState = Literal[
    "safe_stopped",
    "unauthorized",
    "command_enabled",
    "emergency_stopped",
    "fault_latched",
]

JOINT_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow",
    "wrist_roll",
    "wrist_pitch",
    "wrist_yaw",
)


@dataclass(frozen=True)
class CartesianTarget:
    """A target expressed in the simulator base coordinate system, in metres."""

    x: float
    y: float
    z: float

    def as_array(self) -> np.ndarray:
        return np.asarray((self.x, self.y, self.z), dtype=np.float64)


@dataclass(frozen=True)
class ArmState:
    """Serializable observation returned after a command is simulated."""

    joint_positions: tuple[float, ...]
    end_effector: tuple[float, float, float]
    target: tuple[float, float, float]
    position_error_m: float
    simulation_steps: int
    target_reached: bool
    safety_state: SafetyState

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ArmPreviewSnapshot:
    """Immutable simulator state handed from control to preview rendering."""

    joint_positions: tuple[float, ...]
    target: tuple[float, float, float]


class MujocoArmRuntime:
    """Small deterministic arm runtime with no network or physical outputs."""

    def __init__(self, *, safety_state: SafetyState = "safe_stopped") -> None:
        model_path = files("robot_mujoco_arm").joinpath("models/virtual_arm.xml")
        self.model = mujoco.MjModel.from_xml_path(str(model_path))
        self.data = mujoco.MjData(self.model)
        self.safety_state = safety_state
        self._site_id = self._named_id(mujoco.mjtObj.mjOBJ_SITE, "tool_center")
        self._target_body_id = self._named_id(mujoco.mjtObj.mjOBJ_BODY, "target")
        self._joint_ids = tuple(
            self._named_id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in JOINT_NAMES
        )
        self._qpos_addresses = np.asarray(
            [self.model.jnt_qposadr[joint_id] for joint_id in self._joint_ids], dtype=np.int32
        )
        self._dof_addresses = np.asarray(
            [self.model.jnt_dofadr[joint_id] for joint_id in self._joint_ids], dtype=np.int32
        )
        self._joint_ranges = self.model.jnt_range[np.asarray(self._joint_ids)]
        self._last_target = CartesianTarget(0.25, 0.0, 0.45)
        mujoco.mj_forward(self.model, self.data)
        self._joint_target = self.data.qpos[self._qpos_addresses].copy()
        self._preview_data: mujoco.MjData | None = None
        self._preview_renderer: mujoco.Renderer | None = None
        self._preview_size: tuple[int, int] | None = None

    @property
    def timestep_s(self) -> float:
        """Return the deterministic physics step used by this model."""

        return float(self.model.opt.timestep)

    def _named_id(self, object_type: mujoco.mjtObj, name: str) -> int:
        object_id = mujoco.mj_name2id(self.model, object_type, name)
        if object_id < 0:
            raise RuntimeError(f"MuJoCo模型缺少对象：{name}")
        return object_id

    def enable_commands(self) -> None:
        if self.safety_state in {"emergency_stopped", "fault_latched"}:
            raise RuntimeError("安全故障尚未显式复位")
        self.safety_state = "command_enabled"

    def emergency_stop(self) -> None:
        self.safety_state = "emergency_stopped"
        self.hold_position()

    def reset_safety(self, *, authorized: bool, emergency_stop_active: bool = False) -> bool:
        if emergency_stop_active:
            self.safety_state = "emergency_stopped"
            return False
        self.safety_state = "command_enabled" if authorized else "unauthorized"
        return authorized

    @staticmethod
    def _validate_target(target: CartesianTarget) -> None:
        if not (0.05 <= target.x <= 0.75):
            raise ValueError("目标x必须位于[0.05, 0.75]米")
        if not (-0.55 <= target.y <= 0.55):
            raise ValueError("目标y必须位于[-0.55, 0.55]米")
        if not (0.05 <= target.z <= 0.85):
            raise ValueError("目标z必须位于[0.05, 0.85]米")

    def _solve_ik(
        self,
        target: CartesianTarget,
        *,
        iterations: int = 200,
        tolerance_m: float = 0.005,
    ) -> np.ndarray:
        # IK must never mutate the live state.  The previous implementation
        # solved directly against ``self.data`` and therefore teleported the
        # rendered arm to the solution before the dynamics were stepped.
        working_data = mujoco.MjData(self.model)
        working_data.qpos[:] = self.data.qpos
        working_data.qvel[:] = self.data.qvel
        desired = target.as_array()
        jacobian_position = np.zeros((3, self.model.nv), dtype=np.float64)
        jacobian_rotation = np.zeros((3, self.model.nv), dtype=np.float64)
        damping = 1e-3

        for _ in range(iterations):
            mujoco.mj_forward(self.model, working_data)
            error = desired - working_data.site_xpos[self._site_id]
            if float(np.linalg.norm(error)) <= tolerance_m:
                break
            mujoco.mj_jacSite(
                self.model,
                working_data,
                jacobian_position,
                jacobian_rotation,
                self._site_id,
            )
            jacobian = jacobian_position[:, self._dof_addresses]
            regularized = jacobian @ jacobian.T + damping * np.eye(3)
            delta = jacobian.T @ np.linalg.solve(regularized, error)
            positions = working_data.qpos[self._qpos_addresses] + np.clip(
                delta, -0.08, 0.08
            )
            working_data.qpos[self._qpos_addresses] = np.clip(
                positions,
                self._joint_ranges[:, 0],
                self._joint_ranges[:, 1],
            )
        return working_data.qpos[self._qpos_addresses].copy()

    def set_target(
        self,
        target: CartesianTarget,
        *,
        tolerance_m: float = 0.015,
    ) -> ArmState:
        """Accept a target without advancing or teleporting the live simulation."""

        if self.safety_state != "command_enabled":
            raise RuntimeError(f"Safety Gate rejected command: {self.safety_state}")
        self._validate_target(target)
        target_position = target.as_array()
        self.data.mocap_pos[self.model.body_mocapid[self._target_body_id]] = target_position
        self._joint_target = self._solve_ik(target, tolerance_m=tolerance_m / 3)
        self._last_target = target
        return self._state(simulation_steps=0, tolerance_m=tolerance_m)

    def track_target(
        self,
        target: CartesianTarget,
        *,
        tolerance_m: float = 0.015,
    ) -> ArmState:
        """Apply the current IK solution immediately for low-latency visual tracking.

        This mode is intentionally kinematic: it is suitable for validating a
        perception-to-workspace mapping, but it is not a dynamics or trajectory
        controller and must never be used as evidence for physical-arm safety.
        """

        self.set_target(target, tolerance_m=tolerance_m)
        self.data.qpos[self._qpos_addresses] = self._joint_target
        self.data.qvel[self._dof_addresses] = 0.0
        self.data.ctrl[:] = self._joint_target
        mujoco.mj_forward(self.model, self.data)
        return self._state(simulation_steps=0, tolerance_m=tolerance_m)

    def advance(
        self,
        *,
        simulation_steps: int = 1,
        tolerance_m: float = 0.015,
        velocity_scale: float = 1.0,
    ) -> ArmState:
        """Advance a bounded slice of dynamics toward the accepted target."""

        if self.safety_state != "command_enabled":
            raise RuntimeError(f"Safety Gate rejected command: {self.safety_state}")
        if simulation_steps < 1 or simulation_steps > 100_000:
            raise ValueError("simulation_steps must be within [1, 100000]")
        if not 0.01 <= velocity_scale <= 1.0:
            raise ValueError("velocity_scale must be within [0.01, 1.0]")

        # Rate-limit actuator setpoints as well as relying on the MuJoCo
        # position servos.  This gives max_velocity_scale an observable and
        # deterministic effect and prevents a new perception target from
        # creating a one-frame joint jump.
        maximum_delta = 2.5 * velocity_scale * self.timestep_s
        for _ in range(simulation_steps):
            delta = np.clip(self._joint_target - self.data.ctrl, -maximum_delta, maximum_delta)
            self.data.ctrl[:] = self.data.ctrl + delta
            mujoco.mj_step(self.model, self.data)
        return self._state(simulation_steps=simulation_steps, tolerance_m=tolerance_m)

    def hold_position(self) -> None:
        """Stop progressing toward a stale command without latching an E-stop."""

        current = self.data.qpos[self._qpos_addresses].copy()
        self._joint_target = current
        self.data.ctrl[:] = current

    def command_target(
        self,
        target: CartesianTarget,
        *,
        simulation_steps: int = 1000,
        tolerance_m: float = 0.015,
    ) -> ArmState:
        if self.safety_state != "command_enabled":
            raise RuntimeError(f"Safety Gate未放行命令：{self.safety_state}")
        self._validate_target(target)
        if simulation_steps < 1 or simulation_steps > 100_000:
            raise ValueError("simulation_steps必须位于[1, 100000]")

        self.set_target(target, tolerance_m=tolerance_m)
        return self.advance(
            simulation_steps=simulation_steps,
            tolerance_m=tolerance_m,
            velocity_scale=1.0,
        )

    def _state(self, *, simulation_steps: int, tolerance_m: float) -> ArmState:
        mujoco.mj_forward(self.model, self.data)
        end_effector = self.data.site_xpos[self._site_id].copy()
        target_position = self._last_target.as_array()
        error = float(np.linalg.norm(target_position - end_effector))
        return ArmState(
            joint_positions=tuple(float(value) for value in self.data.qpos[self._qpos_addresses]),
            end_effector=tuple(float(value) for value in end_effector),
            target=(self._last_target.x, self._last_target.y, self._last_target.z),
            position_error_m=error,
            simulation_steps=simulation_steps,
            target_reached=error <= tolerance_m,
            safety_state=self.safety_state,
        )

    def observe(self) -> ArmState:
        return self._state(simulation_steps=0, tolerance_m=0.015)

    def snapshot_preview(self) -> ArmPreviewSnapshot:
        """Copy only the state required by the asynchronous preview path."""

        return ArmPreviewSnapshot(
            joint_positions=tuple(
                float(value) for value in self.data.qpos[self._qpos_addresses]
            ),
            target=(self._last_target.x, self._last_target.y, self._last_target.z),
        )

    @staticmethod
    def _png_chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    @classmethod
    def _encode_png(cls, pixels: np.ndarray) -> bytes:
        """Encode an RGB array without adding an image-library dependency."""

        if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.dtype != np.uint8:
            raise ValueError("PNG input must be an uint8 RGB image")
        height, width, _ = pixels.shape
        scanlines = b"".join(b"\x00" + row.tobytes() for row in pixels)
        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + cls._png_chunk(b"IHDR", header)
            + cls._png_chunk(b"IDAT", zlib.compress(scanlines, level=6))
            + cls._png_chunk(b"IEND", b"")
        )

    @staticmethod
    def _preview_camera() -> mujoco.MjvCamera:
        camera = mujoco.MjvCamera()
        mujoco.mjv_defaultCamera(camera)
        camera.lookat[:] = (0.12, 0.0, 0.32)
        camera.distance = 1.25
        camera.azimuth = 135.0
        camera.elevation = -24.0
        return camera

    def render_snapshot_rgb(
        self,
        snapshot: ArmPreviewSnapshot,
        *,
        width: int = 480,
        height: int = 360,
    ) -> np.ndarray:
        """Render an immutable state with preview-owned MuJoCo resources."""

        if not 160 <= width <= 1280 or not 120 <= height <= 720:
            raise ValueError("preview size is outside the supported range")
        if len(snapshot.joint_positions) != len(self._qpos_addresses):
            raise ValueError("preview snapshot has an invalid joint count")
        if self._preview_renderer is None or self._preview_size != (width, height):
            self.close_preview()
            self._preview_data = mujoco.MjData(self.model)
            self._preview_renderer = mujoco.Renderer(
                self.model,
                height=height,
                width=width,
            )
            self._preview_size = (width, height)
        assert self._preview_data is not None
        assert self._preview_renderer is not None
        preview_data = self._preview_data
        preview_data.qpos[self._qpos_addresses] = np.asarray(
            snapshot.joint_positions,
            dtype=np.float64,
        )
        mocap_id = self.model.body_mocapid[self._target_body_id]
        preview_data.mocap_pos[mocap_id] = np.asarray(snapshot.target, dtype=np.float64)
        mujoco.mj_forward(self.model, preview_data)
        self._preview_renderer.update_scene(preview_data, camera=self._preview_camera())
        return np.asarray(self._preview_renderer.render(), dtype=np.uint8).copy()

    def close_preview(self) -> None:
        """Release preview resources on the preview worker that created them."""

        if self._preview_renderer is not None:
            self._preview_renderer.close()
        self._preview_renderer = None
        self._preview_data = None
        self._preview_size = None

    def render_png(self, *, width: int = 480, height: int = 360) -> bytes:
        """Render the current simulation state as a headless-friendly PNG preview."""

        pixels = self.render_snapshot_rgb(
            self.snapshot_preview(),
            width=width,
            height=height,
        )
        return self._encode_png(pixels)
