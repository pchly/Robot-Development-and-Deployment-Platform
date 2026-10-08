"""相机到执行器标定配置的校验、安装和运行时加载。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from robot_edge_agent.spatial import CameraIntrinsics, RigidTransform, SpatialContractError

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SCHEMA = "robot.calibration.camera-to-arm.v1"


class CalibrationProfileError(ValueError):
    """标定配置不满足稳定契约。"""


class CalibrationInstallationError(RuntimeError):
    """标定配置无法安全安装或激活。"""


@dataclass(frozen=True)
class CalibrationProfile:
    """完成设备绑定并可直接交给空间定位器的标定快照。"""

    calibration_id: str
    version: str
    source_transport: str
    source_device_id: str
    target_adapter_id: str
    target_device_id: str
    intrinsics: CameraIntrinsics
    camera_to_target: RigidTransform


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibrationProfileError(f"标定字段{field}必须是对象")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationProfileError(f"标定字段{field}必须是非空字符串")
    return value.strip()


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationProfileError(f"标定字段{field}必须是有限数值")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise CalibrationProfileError(f"标定字段{field}数值不合法")
    return result


def parse_calibration_profile(content: bytes | str) -> CalibrationProfile:
    """解析并完整校验版本化的相机到执行器标定JSON。"""

    try:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        raw = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CalibrationProfileError("标定文件不是合法UTF-8 JSON") from error
    root = _mapping(raw, "root")
    if root.get("schema") != _SCHEMA:
        raise CalibrationProfileError("标定schema不受支持")

    calibration_id = _string(root.get("calibration_id"), "calibration_id")
    version = _string(root.get("version"), "version")
    source = _mapping(root.get("source"), "source")
    target = _mapping(root.get("target"), "target")
    source_device = _mapping(source.get("device"), "source.device")
    target_device = _mapping(target.get("device"), "target.device")
    intrinsics = _mapping(source.get("intrinsics"), "source.intrinsics")
    transform = _mapping(root.get("transform"), "transform")
    translation = _mapping(transform.get("translation_m"), "transform.translation_m")
    rotation = _mapping(transform.get("rotation_rpy_rad"), "transform.rotation_rpy_rad")
    source_frame = _string(source.get("frame_id"), "source.frame_id")
    target_frame = _string(target.get("frame_id"), "target.frame_id")

    try:
        camera_intrinsics = CameraIntrinsics(
            frame_id=source_frame,
            width_px=int(
                _number(intrinsics.get("width_px"), "source.intrinsics.width_px", positive=True)
            ),
            height_px=int(
                _number(
                    intrinsics.get("height_px"),
                    "source.intrinsics.height_px",
                    positive=True,
                )
            ),
            fx_px=_number(intrinsics.get("fx_px"), "source.intrinsics.fx_px", positive=True),
            fy_px=_number(intrinsics.get("fy_px"), "source.intrinsics.fy_px", positive=True),
            cx_px=_number(intrinsics.get("cx_px"), "source.intrinsics.cx_px"),
            cy_px=_number(intrinsics.get("cy_px"), "source.intrinsics.cy_px"),
        )
        rigid_transform = RigidTransform(
            source_frame=source_frame,
            target_frame=target_frame,
            calibration_id=calibration_id,
            translation_m=(
                _number(translation.get("x"), "transform.translation_m.x"),
                _number(translation.get("y"), "transform.translation_m.y"),
                _number(translation.get("z"), "transform.translation_m.z"),
            ),
            roll_rad=_number(rotation.get("roll"), "transform.rotation_rpy_rad.roll"),
            pitch_rad=_number(rotation.get("pitch"), "transform.rotation_rpy_rad.pitch"),
            yaw_rad=_number(rotation.get("yaw"), "transform.rotation_rpy_rad.yaw"),
        )
    except SpatialContractError as error:
        raise CalibrationProfileError(str(error)) from error

    return CalibrationProfile(
        calibration_id=calibration_id,
        version=version,
        source_transport=_string(source_device.get("transport"), "source.device.transport"),
        source_device_id=_string(source_device.get("device_id"), "source.device.device_id"),
        target_adapter_id=_string(target_device.get("adapter_id"), "target.device.adapter_id"),
        target_device_id=_string(target_device.get("device_id"), "target.device.device_id"),
        intrinsics=camera_intrinsics,
        camera_to_target=rigid_transform,
    )


def load_calibration_profile(path: Path) -> CalibrationProfile:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise CalibrationProfileError(f"无法读取标定文件：{error}") from error
    return parse_calibration_profile(content)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as profile_file:
        for chunk in iter(lambda: profile_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class CalibrationProfileInstaller:
    """把摘要固定的标定配置安装到版本目录并原子切换激活指针。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def install(
        self,
        plan: dict[str, Any],
        staged_file: Path,
        *,
        progress: Callable[[str, str], None],
    ) -> str:
        artifact = plan.get("artifact")
        operation_id = str(plan.get("operation_id", ""))
        if not isinstance(artifact, dict) or artifact.get("kind") != "calibration_profile":
            raise CalibrationInstallationError("安装计划不是标定配置制品")
        artifact_id = str(artifact.get("artifact_id", ""))
        version = str(artifact.get("version", ""))
        locator = str(artifact.get("locator", ""))
        identifiers = (operation_id, artifact_id, version, locator)
        if not all(_SAFE_NAME.fullmatch(value) for value in identifiers):
            raise CalibrationInstallationError("标定安装标识符格式不合法")
        distribution = artifact.get("distribution")
        expected_digest = str(artifact.get("digest", "")).casefold()
        if (
            not isinstance(distribution, dict)
            or distribution.get("transport") != "platform_api"
            or str(distribution.get("digest", "")).casefold() != expected_digest
            or not _DIGEST_PATTERN.fullmatch(expected_digest)
        ):
            raise CalibrationInstallationError("标定内容摘要与分发契约不一致")
        if not staged_file.is_file() or staged_file.is_symlink():
            raise CalibrationInstallationError("标定暂存文件不存在或不是普通文件")

        progress("verifying", "正在校验标定JSON、设备绑定和SHA-256摘要")
        try:
            if _sha256(staged_file) != expected_digest:
                raise CalibrationInstallationError("标定暂存文件摘要与Catalog不一致")
            profile = load_calibration_profile(staged_file)
        except OSError as error:
            raise CalibrationInstallationError(f"无法读取标定暂存文件：{error}") from error
        if profile.calibration_id != locator or profile.version != version:
            raise CalibrationInstallationError("标定文件ID或版本与安装计划不一致")

        release = self._root / "releases" / artifact_id / f"{version}-{expected_digest[7:19]}"
        stage = self._root / ".staging" / operation_id
        active_root = self._root / "active"
        pointer = active_root / f"{artifact_id}.json"
        pointer_tmp = active_root / f".{artifact_id}.{operation_id}.tmp"
        release_root = (self._root / "releases").resolve()
        for candidate in (release, stage, pointer, pointer_tmp):
            candidate.resolve(strict=False).relative_to(self._root)
        release.resolve(strict=False).relative_to(release_root)

        marker = {
            "artifact_id": artifact_id,
            "version": version,
            "digest": expected_digest,
            "locator": locator,
        }
        previous_pointer = pointer.read_bytes() if pointer.is_file() else None
        try:
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(parents=True, mode=0o750)
            shutil.copyfile(staged_file, stage / "profile.json")
            (stage / "profile.json").chmod(0o640)
            (stage / ".artifact.json").write_text(
                json.dumps(marker, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            if _sha256(stage / "profile.json") != expected_digest:
                raise CalibrationInstallationError("标定副本写入后摘要不一致")
            load_calibration_profile(stage / "profile.json")

            progress("installing", "正在写入标定版本目录并原子切换激活指针")
            release.parent.mkdir(parents=True, exist_ok=True)
            if release.exists():
                existing_marker = json.loads(
                    (release / ".artifact.json").read_text(encoding="utf-8")
                )
                if (
                    existing_marker != marker
                    or _sha256(release / "profile.json") != expected_digest
                ):
                    raise CalibrationInstallationError("既有标定版本目录与安装契约不一致")
                shutil.rmtree(stage)
            else:
                os.replace(stage, release)
            active_root.mkdir(parents=True, exist_ok=True)
            pointer_payload = {
                **marker,
                "operation_id": operation_id,
                "release_path": release.relative_to(self._root).as_posix(),
            }
            pointer_tmp.write_text(
                json.dumps(pointer_payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.replace(pointer_tmp, pointer)
        except Exception as error:
            pointer_tmp.unlink(missing_ok=True)
            if stage.exists():
                shutil.rmtree(stage)
            if previous_pointer is None:
                pointer.unlink(missing_ok=True)
            else:
                restore = active_root / f".{artifact_id}.{operation_id}.rollback.tmp"
                restore.write_bytes(previous_pointer)
                os.replace(restore, pointer)
            if isinstance(error, CalibrationInstallationError):
                raise
            raise CalibrationInstallationError(str(error)) from error

        return f"标定配置已安装并激活：{locator}@{version}（{expected_digest}）"
