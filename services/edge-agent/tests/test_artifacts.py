"""Edge节点制品只读盘点测试。"""

import json
import subprocess
from pathlib import Path

from robot_edge_agent.artifacts import (
    collect_container_images,
    collect_file_artifacts,
    collect_managed_plugin_artifacts,
    collect_managed_runtime_packages,
)


def test_collect_container_images_uses_tags_and_digest() -> None:
    lines = [
        {
            "Repository": "robot-platform/demo",
            "Tag": "1.2.3",
            "Digest": "sha256:abc",
            "ID": "sha256:def",
        },
        {"Repository": "<none>", "Tag": "<none>", "Digest": "<none>", "ID": "x"},
    ]

    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n".join(json.dumps(item) for item in lines)
        )

    artifacts = collect_container_images(run=fake_run)

    assert artifacts == [
        {
            "kind": "container_image",
            "locator": "robot-platform/demo:1.2.3",
            "version": "1.2.3",
            "digest": "sha256:abc",
        }
    ]


def test_collect_file_artifacts_finds_models_and_engines(tmp_path: Path) -> None:
    engine = tmp_path / "engines" / "demo.engine"
    model = tmp_path / "models" / "demo.onnx"
    engine.parent.mkdir()
    model.parent.mkdir()
    engine.write_bytes(b"engine")
    model.write_bytes(b"model")

    artifacts = collect_file_artifacts([tmp_path])

    assert {item["kind"] for item in artifacts} == {"tensorrt_engine", "model"}
    assert {item["locator"] for item in artifacts} == {str(engine), str(model)}
    digests = {item["locator"]: item["digest"] for item in artifacts}
    assert digests[str(engine)] == (
        "sha256:ed9f6f25068608efd412958da4dfc19328ca3511251fa6d5f9c42baf230e32f8"
    )
    assert digests[str(model)] == (
        "sha256:9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4"
    )


def test_collect_managed_plugin_artifacts_reports_verified_active_release(
    tmp_path: Path,
) -> None:
    artifact_id = "hardware.camera.demo.edge-plugin"
    digest = "sha256:" + "a" * 64
    release = tmp_path / "releases" / artifact_id / "1.2.3-aaaaaaaaaaaa"
    dist_info = release / "robot_hardware_demo-1.2.3.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: robot-hardware-demo\nVersion: 1.2.3\n",
        encoding="utf-8",
    )
    marker = {"artifact_id": artifact_id, "version": "1.2.3", "digest": digest}
    (release / ".artifact.json").write_text(json.dumps(marker), encoding="utf-8")
    active = tmp_path / "active"
    active.mkdir()
    (active / f"{artifact_id}.json").write_text(
        json.dumps(
            {
                **marker,
                "release_path": release.relative_to(tmp_path).as_posix(),
            }
        ),
        encoding="utf-8",
    )

    artifacts = collect_managed_plugin_artifacts([tmp_path])

    assert artifacts == [
        {
            "kind": "edge_plugin",
            "locator": "robot-hardware-demo",
            "version": "1.2.3",
            "digest": digest,
            "artifact_id": artifact_id,
            "install_path": str(release.resolve()),
            "active": True,
        }
    ]

    marker["digest"] = "sha256:" + "b" * 64
    (release / ".artifact.json").write_text(json.dumps(marker), encoding="utf-8")
    assert collect_managed_plugin_artifacts([tmp_path]) == []


def test_collect_managed_runtime_packages_reports_active_release(
    tmp_path: Path,
) -> None:
    artifact_id = "simulation.demo.runtime"
    digest = "sha256:" + "c" * 64
    release = tmp_path / "releases" / artifact_id / "1.0.0-cccccccccccc"
    dist_info = release / "robot_demo_runtime-1.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: robot-demo-runtime\nVersion: 1.0.0\n",
        encoding="utf-8",
    )
    marker = {"artifact_id": artifact_id, "version": "1.0.0", "digest": digest}
    (release / ".artifact.json").write_text(json.dumps(marker), encoding="utf-8")
    active = tmp_path / "active"
    active.mkdir()
    (active / f"{artifact_id}.json").write_text(
        json.dumps(
            {
                **marker,
                "release_path": release.relative_to(tmp_path).as_posix(),
            }
        ),
        encoding="utf-8",
    )

    assert collect_managed_runtime_packages([tmp_path]) == [
        {
            "kind": "runtime_package",
            "locator": "robot-demo-runtime",
            "version": "1.0.0",
            "digest": digest,
            "artifact_id": artifact_id,
            "install_path": str(release.resolve()),
            "active": True,
        }
    ]
