"""资产Manifest加载与契约校验。"""

import hashlib
import json
from pathlib import Path

import pytest

from robot_platform_api.catalog import CatalogManifestError, load_catalog


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def custom_asset_manifests(root: Path, *, algorithm_ref: str = "algorithm.demo") -> None:
    write_json(
        root / "hardware-adapters" / "demo-camera" / "manifest.json",
        {
            "adapter_id": "hardware.camera.demo",
            "version": "1.0.0",
            "name": "Demo Camera",
            "vendor": "Demo",
            "category": "camera.rgb",
            "readiness": "validated",
            "discovery": {"transport": "usb", "vendor_ids": ["1234"]},
            "outputs": [{"name": "color", "data_type": "image.rgb8.v1"}],
        },
    )
    write_json(
        root / "algorithm-packages" / "demo-detector" / "manifest.json",
        {
            "algorithm_id": "algorithm.demo",
            "version": "1.0.0",
            "name": "Demo Detector",
            "category": "vision.object_detection",
            "readiness": "validated",
            "artifact_formats": ["onnx"],
            "inputs": [{"name": "image", "data_type": "image.rgb8.v1"}],
            "outputs": [{"name": "detections", "data_type": "vision.detections.v1"}],
        },
    )
    write_json(
        root / "workflow-runtimes" / "demo" / "manifest.json",
        {
            "deployment_recipe": {
                "recipe_id": "workflow.demo",
                "version": "1.0.0",
                "name": "Demo Workflow",
                "components": [
                    {
                        "node_id": "camera",
                        "component_ref": "hardware.camera.demo",
                        "component_version": "1.0.0",
                    },
                    {
                        "node_id": "detector",
                        "component_ref": algorithm_ref,
                        "component_version": "1.0.0",
                    },
                ],
                "links": [
                    {
                        "source": {"node_id": "camera", "port": "color"},
                        "target": {"node_id": "detector", "port": "image"},
                    }
                ],
            }
        },
    )


def calibration_profile_payload() -> dict[str, object]:
    return {
        "schema": "robot.calibration.camera-to-arm.v1",
        "calibration_id": "demo-camera-to-arm-v1",
        "version": "1.0.0",
        "source": {
            "frame_id": "camera_optical",
            "device": {"transport": "simulation", "device_id": "camera-01"},
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
            "device": {"adapter_id": "hardware.arm.demo", "device_id": "arm-01"},
        },
        "transform": {
            "translation_m": {"x": 0.1, "y": 0.0, "z": 0.2},
            "rotation_rpy_rad": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
        },
    }


def write_calibration_package(root: Path, payload: dict[str, object]) -> Path:
    package = root / "artifact-packages" / "demo-calibration"
    profile_path = package / "dist" / "demo-camera-to-arm-v1.json"
    write_json(profile_path, payload)
    digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    write_json(
        package / "manifest.json",
        {
            "schema_version": "1.1",
            "artifact_id": "calibration.camera.demo-to-arm",
            "name": "Demo calibration",
            "kind": "calibration_profile",
            "scope": "edge_runtime",
            "version": "1.0.0",
            "locator": "demo-camera-to-arm-v1",
            "source_ref": "calibration.camera.demo-to-arm",
            "distribution": {
                "file": "dist/demo-camera-to-arm-v1.json",
                "sha256": digest,
            },
        },
    )
    return profile_path


def test_new_asset_directories_are_discovered_without_core_code_changes(
    tmp_path: Path,
) -> None:
    custom_asset_manifests(tmp_path)

    snapshot = load_catalog(tmp_path)

    assert {item.hardware_id for item in snapshot.hardware} == {
        "hardware.camera.mock",
        "hardware.arm.mock",
        "hardware.camera.demo",
    }
    assert {item.algorithm_id for item in snapshot.algorithms} == {
        "algorithm.vision.mock_detector",
        "algorithm.vision.mock_filter",
        "algorithm.motion.mock_target_selector",
        "algorithm.spatial.rgbd_target_localizer",
        "algorithm.motion.cartesian_point_to_point",
        "algorithm.motion.safety_gate",
        "algorithm.demo",
    }
    assert {item.recipe_id for item in snapshot.recipes} == {
        "workflow.mock_detection",
        "workflow.mock_detection_pipeline",
        "workflow.mock_vision_arm",
        "workflow.mock_vision_arm_safety",
        "workflow.demo",
    }


def test_catalog_loads_and_validates_calibration_profile(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path)
    write_calibration_package(tmp_path, calibration_profile_payload())

    snapshot = load_catalog(tmp_path)

    artifact = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "calibration.camera.demo-to-arm"
    )
    assert artifact.kind == "calibration_profile"
    assert artifact.distribution is not None
    assert artifact.distribution.media_type == "application/json"


def test_catalog_rejects_calibration_device_or_manifest_mismatch(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path)
    payload = calibration_profile_payload()
    payload["calibration_id"] = "wrong-id"
    write_calibration_package(tmp_path, payload)

    with pytest.raises(CatalogManifestError, match="标定ID"):
        load_catalog(tmp_path)


def test_catalog_loads_digest_pinned_runtime_image(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path)
    manifest_path = tmp_path / "workflow-runtimes" / "demo" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "workflow_runtime_id": "runtime.demo",
            "version": "1.2.3",
            "name": "Demo Runtime",
            "architecture": "aarch64",
            "runtime_image": "registry.example/robot/demo:1.2.3",
            "runtime_image_digest": "sha256:" + "a" * 64,
        }
    )
    write_json(manifest_path, manifest)

    snapshot = load_catalog(tmp_path)

    artifact = next(
        item for item in snapshot.artifacts if item.artifact_id == "runtime.demo.runtime-image"
    )
    assert artifact.digest == "sha256:" + "a" * 64
    assert artifact.locator == "registry.example/robot/demo:1.2.3"


def test_catalog_loads_digest_pinned_container_archive(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path)
    manifest_path = tmp_path / "workflow-runtimes" / "demo" / "manifest.json"
    archive_path = manifest_path.parent / "dist" / "demo.tar.gz"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(b"fixed docker archive")
    archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "workflow_runtime_id": "runtime.demo",
            "version": "1.2.3",
            "name": "Demo Runtime",
            "architecture": "aarch64",
            "runtime_image": "robot/demo:1.2.3",
            "runtime_image_digest": "sha256:" + "a" * 64,
            "runtime_image_distribution": {
                "file": "dist/demo.tar.gz",
                "sha256": archive_digest,
            },
        }
    )
    write_json(manifest_path, manifest)

    snapshot = load_catalog(tmp_path)

    artifact = next(
        item for item in snapshot.artifacts if item.artifact_id == "runtime.demo.runtime-image"
    )
    assert artifact.distribution is not None
    assert artifact.distribution.digest == f"sha256:{archive_digest}"
    assert artifact.distribution.media_type == "application/gzip"
    assert snapshot.artifact_files[0].path == archive_path


def test_catalog_rejects_container_archive_digest_mismatch(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path)
    manifest_path = tmp_path / "workflow-runtimes" / "demo" / "manifest.json"
    archive_path = manifest_path.parent / "demo.tar"
    archive_path.write_bytes(b"archive")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "workflow_runtime_id": "runtime.demo",
            "version": "1.2.3",
            "name": "Demo Runtime",
            "runtime_image": "robot/demo:1.2.3",
            "runtime_image_digest": "sha256:" + "a" * 64,
            "runtime_image_distribution": {
                "file": "demo.tar",
                "sha256": "b" * 64,
            },
        }
    )
    write_json(manifest_path, manifest)

    with pytest.raises(CatalogManifestError, match="摘要与Manifest不一致"):
        load_catalog(tmp_path)


def test_catalog_loads_standalone_container_artifact_package(tmp_path: Path) -> None:
    archive_path = tmp_path / "artifact-packages" / "smoke" / "dist" / "smoke.tar"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(b"standalone archive")
    archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    write_json(
        archive_path.parents[1] / "manifest.json",
        {
            "artifact_id": "validation.container-smoke.runtime-image",
            "name": "Container Smoke",
            "kind": "container_image",
            "scope": "edge_runtime",
            "version": "0.1.0",
            "locator": "robot/container-smoke:0.1.0",
            "digest": "sha256:" + "a" * 64,
            "architecture": "aarch64",
            "source_ref": "validation.container-smoke",
            "distribution": {
                "file": "dist/smoke.tar",
                "sha256": archive_digest,
            },
        },
    )

    snapshot = load_catalog(tmp_path)

    artifact = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "validation.container-smoke.runtime-image"
    )
    assert artifact.distribution is not None
    assert artifact.distribution.size_bytes == len(b"standalone archive")


def test_catalog_loads_multi_platform_artifact_variants(tmp_path: Path) -> None:
    package_root = tmp_path / "artifact-packages" / "multi-runtime"
    distribution_root = package_root / "dist"
    distribution_root.mkdir(parents=True)
    windows_archive = distribution_root / "runtime-windows.tar"
    arm_archive = distribution_root / "runtime-arm64.tar"
    windows_archive.write_bytes(b"windows runtime")
    arm_archive.write_bytes(b"arm64 runtime")
    windows_digest = hashlib.sha256(windows_archive.read_bytes()).hexdigest()
    arm_digest = hashlib.sha256(arm_archive.read_bytes()).hexdigest()
    (package_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "artifact_id": "runtime.demo.multi-platform",
                "name": "Multi Platform Runtime",
                "kind": "container_image",
                "scope": "edge_runtime",
                "version": "1.0.0",
                "source_ref": "runtime.demo",
                "variants": [
                    {
                        "variant_id": "windows-amd64",
                        "locator": "robot/demo:1.0.0-windows-amd64",
                        "digest": "sha256:" + "a" * 64,
                        "compatibility": {
                            "operating_system": "Windows",
                            "architecture": "amd64",
                        },
                        "distribution": {
                            "file": "dist/runtime-windows.tar",
                            "sha256": windows_digest,
                        },
                    },
                    {
                        "variant_id": "linux-arm64",
                        "locator": "robot/demo:1.0.0-linux-arm64",
                        "digest": "sha256:" + "b" * 64,
                        "compatibility": {
                            "operating_system": "Linux",
                            "architecture": "arm64",
                        },
                        "distribution": {
                            "file": "dist/runtime-arm64.tar",
                            "sha256": arm_digest,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_catalog(tmp_path)

    artifact = next(
        item for item in snapshot.artifacts if item.artifact_id == "runtime.demo.multi-platform"
    )
    assert [variant.variant_id for variant in artifact.variants] == [
        "windows-amd64",
        "linux-arm64",
    ]
    assert artifact.locator == "robot/demo:1.0.0-windows-amd64"
    assert {(item.variant_id, item.path.name) for item in snapshot.artifact_files} == {
        ("windows-amd64", "runtime-windows.tar"),
        ("linux-arm64", "runtime-arm64.tar"),
    }
    assert artifact.variants[1].distribution is not None
    assert artifact.variants[1].distribution.download_path.endswith("?variant_id=linux-arm64")


def test_catalog_rejects_runtime_recipe_with_missing_algorithm(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path, algorithm_ref="algorithm.missing")

    with pytest.raises(CatalogManifestError, match="引用了不存在的组件"):
        load_catalog(tmp_path)


def test_catalog_rejects_non_object_manifest(tmp_path: Path) -> None:
    write_json(tmp_path / "hardware-adapters" / "broken" / "manifest.json", [])

    with pytest.raises(CatalogManifestError, match="根节点必须是对象"):
        load_catalog(tmp_path)


def test_catalog_rejects_distribution_path_escape(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path)
    wheel = tmp_path / "outside.whl"
    wheel.write_bytes(b"untrusted")
    manifest_path = tmp_path / "hardware-adapters" / "demo-camera" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["edge_plugin"] = {
        "distribution": "demo-camera",
        "version": "1.0.0",
        "wheel_file": "../../../outside.whl",
        "wheel_sha256": "00" * 32,
    }
    write_json(manifest_path, manifest)

    with pytest.raises(CatalogManifestError, match="不能越过组件目录"):
        load_catalog(tmp_path)


def test_catalog_loads_tensorrt_engine_distribution(tmp_path: Path) -> None:
    custom_asset_manifests(tmp_path)
    manifest_path = tmp_path / "algorithm-packages" / "demo-detector" / "manifest.json"
    engine_path = manifest_path.parent / "dist" / "demo.engine"
    engine_path.parent.mkdir(parents=True)
    engine_path.write_bytes(b"fixed TensorRT engine")
    engine_digest = hashlib.sha256(engine_path.read_bytes()).hexdigest()
    target_path = "/data/robot-platform/cache/tensorrt/demo/demo.engine"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["parameters"] = [
        {
            "key": "engine_path",
            "label": "TensorRT Engine",
            "value_type": "string",
            "default": target_path,
        }
    ]
    manifest["runtime"] = {
        "architecture": "aarch64",
        "engine_artifact": {
            "path": target_path,
            "sha256": engine_digest,
            "distribution": {
                "file": "dist/demo.engine",
                "sha256": engine_digest,
            },
        },
    }
    write_json(manifest_path, manifest)

    snapshot = load_catalog(tmp_path)

    artifact = next(
        item for item in snapshot.artifacts if item.artifact_id == "algorithm.demo.tensorrt-engine"
    )
    assert artifact.digest == f"sha256:{engine_digest}"
    assert artifact.locator == target_path
    assert artifact.distribution is not None
    assert artifact.distribution.media_type == "application/octet-stream"
    assert artifact.distribution.size_bytes == len(b"fixed TensorRT engine")
    artifact_file = next(
        item for item in snapshot.artifact_files if item.artifact_id == artifact.artifact_id
    )
    assert artifact_file.path == engine_path


def test_catalog_rejects_tensorrt_engine_distribution_digest_mismatch(
    tmp_path: Path,
) -> None:
    custom_asset_manifests(tmp_path)
    manifest_path = tmp_path / "algorithm-packages" / "demo-detector" / "manifest.json"
    engine_path = manifest_path.parent / "demo.engine"
    engine_path.write_bytes(b"engine")
    distribution_digest = hashlib.sha256(engine_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["parameters"] = [
        {
            "key": "engine_path",
            "label": "TensorRT Engine",
            "value_type": "string",
            "default": "/data/robot-platform/cache/tensorrt/demo.engine",
        }
    ]
    manifest["runtime"] = {
        "engine_artifact": {
            "sha256": "a" * 64,
            "distribution": {
                "file": "demo.engine",
                "sha256": distribution_digest,
            },
        }
    }
    write_json(manifest_path, manifest)

    with pytest.raises(CatalogManifestError, match="内容摘要与distribution不一致"):
        load_catalog(tmp_path)
