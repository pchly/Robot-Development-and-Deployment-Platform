#!/usr/bin/env python3
"""生成无需Docker即可构建的确定性引导归档，供Docker加载后规范化导出。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

IMAGE = "robot-platform/container-import-smoke:0.1.0"
CREATED = "2026-10-04T00:00:00Z"


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(content))


def build(output: Path) -> tuple[str, str, int]:
    marker = b"robot-platform container import smoke test\n"
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w", format=tarfile.GNU_FORMAT) as layer:
        marker_info = tarfile.TarInfo("robot-platform-smoke.txt")
        marker_info.size = len(marker)
        marker_info.mtime = 0
        marker_info.uid = 0
        marker_info.gid = 0
        marker_info.uname = "root"
        marker_info.gname = "root"
        marker_info.mode = 0o644
        layer.addfile(marker_info, io.BytesIO(marker))
    layer_content = layer_buffer.getvalue()
    layer_digest = hashlib.sha256(layer_content).hexdigest()

    config = canonical_json(
        {
            "architecture": "arm64",
            "config": {"Labels": {"robot.platform.validation": "container-import-v1"}},
            "created": CREATED,
            "history": [{"created": CREATED, "created_by": "robot-platform smoke builder"}],
            "os": "linux",
            "rootfs": {"diff_ids": [f"sha256:{layer_digest}"], "type": "layers"},
        }
    )
    bootstrap_config_digest = hashlib.sha256(config).hexdigest()
    manifest = canonical_json(
        [
            {
                "Config": f"{bootstrap_config_digest}.json",
                "Layers": [f"{layer_digest}/layer.tar"],
                "RepoTags": [IMAGE],
            }
        ]
    )
    repositories = canonical_json(
        {"robot-platform/container-import-smoke": {"0.1.0": layer_digest}}
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"拒绝覆盖已有归档：{output}")
    with tarfile.open(output, mode="w", format=tarfile.GNU_FORMAT) as archive:
        add_bytes(archive, f"{bootstrap_config_digest}.json", config)
        add_bytes(archive, f"{layer_digest}/layer.tar", layer_content)
        add_bytes(archive, "manifest.json", manifest)
        add_bytes(archive, "repositories", repositories)

    archive_digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return (
        f"sha256:{bootstrap_config_digest}",
        archive_digest,
        output.stat().st_size,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    bootstrap_config_digest, archive_digest, size = build(arguments.output)
    print(f"image={IMAGE}")
    print(f"bootstrap_config_digest={bootstrap_config_digest}")
    print(f"archive_sha256={archive_digest}")
    print(f"archive_size={size}")
    print(
        "next=在目标Docker中load此引导归档，再使用export-container-image.sh"
        "生成可登记到Catalog的正式归档与最终镜像ID"
    )


if __name__ == "__main__":
    main()
