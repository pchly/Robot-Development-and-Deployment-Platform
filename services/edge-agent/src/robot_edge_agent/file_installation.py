"""固定摘要运行时文件的受限原子安装与回滚。"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class ManagedFileInstallationError(RuntimeError):
    """受管文件在替换目标之前或无旧文件时安装失败。"""


class ManagedFileRollbackError(ManagedFileInstallationError):
    """替换目标后校验失败，旧文件已经恢复。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact_file:
        for chunk in iter(lambda: artifact_file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class ManagedFileInstaller:
    """只允许把固定摘要Engine安装到显式受管根目录。"""

    def __init__(self, roots: Iterable[Path]) -> None:
        self._roots = tuple(Path(root) for root in roots)
        if not self._roots:
            raise ValueError("至少需要一个受管文件根目录")

    def _target(self, locator: str) -> Path:
        target = Path(locator)
        if not target.is_absolute() or target.suffix.casefold() != ".engine":
            raise ManagedFileInstallationError(
                "TensorRT Engine目标必须是受管根目录内的绝对.engine路径"
            )
        resolved_target = target.resolve(strict=False)
        for root in self._roots:
            resolved_root = root.resolve(strict=False)
            try:
                resolved_target.relative_to(resolved_root)
            except ValueError:
                continue
            return target
        raise ManagedFileInstallationError("TensorRT Engine目标不在允许的受管目录内")

    def install(
        self,
        plan: dict[str, Any],
        staged_file: Path,
        *,
        progress: Callable[[str, str], None],
    ) -> str:
        artifact = plan.get("artifact", {})
        if artifact.get("kind") != "tensorrt_engine":
            raise ManagedFileInstallationError("安装计划不是TensorRT Engine制品")
        operation_id = str(plan.get("operation_id", ""))
        if not _SAFE_OPERATION_ID.fullmatch(operation_id):
            raise ManagedFileInstallationError("安装事务ID格式不合法")
        expected_digest = str(artifact.get("digest", "")).casefold()
        distribution = artifact.get("distribution", {})
        if (
            not _DIGEST_PATTERN.fullmatch(expected_digest)
            or not isinstance(distribution, dict)
            or str(distribution.get("digest", "")).casefold() != expected_digest
        ):
            raise ManagedFileInstallationError("Engine内容摘要与分发契约不一致")
        if not staged_file.is_file() or staged_file.is_symlink():
            raise ManagedFileInstallationError("Engine暂存文件不存在或不是普通文件")
        target = self._target(str(artifact.get("locator", "")))

        progress("verifying", "正在复核TensorRT Engine内容摘要与目标路径")
        try:
            actual_digest = _sha256(staged_file)
        except OSError as error:
            raise ManagedFileInstallationError(f"无法读取Engine暂存文件：{error}") from error
        if actual_digest != expected_digest:
            raise ManagedFileInstallationError("Engine暂存文件SHA-256与Catalog不一致")

        try:
            for root in self._roots:
                root.mkdir(parents=True, exist_ok=True)
                if root.is_symlink():
                    raise ManagedFileInstallationError("受管文件根目录不能是符号链接")
            target.parent.mkdir(parents=True, exist_ok=True)
            checked_target = self._target(str(target))
            if checked_target.is_symlink():
                raise ManagedFileInstallationError("Engine目标不能是符号链接")
            if checked_target.exists() and not checked_target.is_file():
                raise ManagedFileInstallationError("Engine目标已存在但不是普通文件")
        except OSError as error:
            raise ManagedFileInstallationError(f"无法准备Engine目标目录：{error}") from error

        temporary = target.parent / f".{target.name}.{operation_id}.new"
        backup = target.parent / f".{target.name}.{operation_id}.backup"
        if temporary.exists() or backup.exists():
            raise ManagedFileInstallationError("安装事务临时路径已存在")

        progress("installing", f"正在原子安装TensorRT Engine到 {target}")
        previous_moved = False
        installed = False
        try:
            with staged_file.open("rb") as source, temporary.open("xb") as output:
                for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o640)
            if _sha256(temporary) != expected_digest:
                raise ManagedFileInstallationError("Engine目标临时副本摘要校验失败")
            if target.exists():
                os.replace(target, backup)
                previous_moved = True
            os.replace(temporary, target)
            installed = True
            if _sha256(target) != expected_digest:
                raise ManagedFileInstallationError("Engine原子替换后摘要校验失败")
            backup.unlink(missing_ok=True)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            if previous_moved:
                try:
                    if installed:
                        target.unlink(missing_ok=True)
                    os.replace(backup, target)
                except OSError as rollback_error:
                    raise ManagedFileInstallationError(
                        f"Engine安装失败且无法恢复旧文件：{rollback_error}"
                    ) from error
                raise ManagedFileRollbackError(str(error)) from error
            if installed:
                target.unlink(missing_ok=True)
            if isinstance(error, ManagedFileInstallationError):
                raise
            raise ManagedFileInstallationError(f"Engine原子安装失败：{error}") from error

        return f"TensorRT Engine已按固定摘要原子安装：{target}（{expected_digest}）"
