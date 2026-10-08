"""校验仓库中的硬件、算法和可执行配方Manifest。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    api_source = project_root / "services" / "platform-api" / "src"
    if not api_source.is_dir():
        raise SystemExit(f"错误：找不到Platform API源码目录：{api_source}")
    sys.path.insert(0, str(api_source))

    from robot_platform_api.catalog import CatalogManifestError, load_catalog

    try:
        snapshot = load_catalog(project_root)
    except CatalogManifestError as error:
        raise SystemExit(f"资产契约校验失败：{error}") from error

    print(
        "资产契约校验通过："
        f"硬件{len(snapshot.hardware)}个，"
        f"算法{len(snapshot.algorithms)}个，"
        f"可执行配方{len(snapshot.recipes)}个，"
        f"逻辑制品{len(snapshot.artifacts)}个"
    )
    for item in snapshot.hardware:
        print(f"HARDWARE {item.hardware_id}@{item.version} {item.readiness.value}")
    for item in snapshot.algorithms:
        print(f"ALGORITHM {item.algorithm_id}@{item.version} {item.readiness.value}")
    for item in snapshot.recipes:
        print(f"RECIPE {item.recipe_id}@{item.version}")
    for item in snapshot.artifacts:
        variants = ",".join(variant.variant_id for variant in item.variants) or "single"
        print(f"ARTIFACT {item.artifact_id}@{item.version} variants={variants}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
