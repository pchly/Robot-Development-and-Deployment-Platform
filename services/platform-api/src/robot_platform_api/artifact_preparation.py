"""生成只读制品准备计划，不执行远程命令或修改Edge节点。"""

import hashlib
import json
from datetime import UTC, datetime

from robot_platform_api.artifact_resolution import _normalized_architecture, resolve_artifact
from robot_platform_api.catalog import find_artifact
from robot_platform_api.models import (
    AgentRecord,
    ArtifactDefinition,
    ArtifactInstallStrategy,
    ArtifactKind,
    ArtifactPreparationItem,
    ArtifactPreparationPlan,
    ArtifactPreparationRequest,
    ArtifactPreparationState,
    ArtifactScope,
    InstalledArtifact,
)


class ArtifactNotFoundError(LookupError):
    """请求引用了Catalog中不存在的制品。"""


def _normalized_locator(value: str) -> str:
    return value.strip().replace("\\", "/").casefold()


def _matching_installation(
    artifact: ArtifactDefinition,
    target_agent: AgentRecord,
) -> InstalledArtifact | None:
    expected = _normalized_locator(artifact.locator)
    for installed in target_agent.installed_artifacts:
        if installed.kind != artifact.kind:
            continue
        actual = _normalized_locator(installed.locator)
        if artifact.kind in {ArtifactKind.EDGE_PLUGIN, ArtifactKind.RUNTIME_PACKAGE}:
            locator_matches = actual.replace("_", "-") == expected.replace("_", "-")
        elif artifact.kind is ArtifactKind.MODEL:
            locator_matches = actual == expected or actual.endswith(f"/{expected}")
        else:
            locator_matches = actual == expected
        version_matches = not installed.version or installed.version == artifact.version
        if locator_matches and version_matches:
            return installed
    return None


def _architecture_mismatch(
    artifact: ArtifactDefinition,
    target_agent: AgentRecord,
) -> bool:
    machine = str(target_agent.metadata.get("machine", "")).strip().casefold()
    return bool(
        artifact.architecture
        and machine
        and _normalized_architecture(artifact.architecture) != _normalized_architecture(machine)
    )


def _item_for(
    order: int,
    artifact: ArtifactDefinition,
    target_agent: AgentRecord,
) -> ArtifactPreparationItem:
    if artifact.scope is ArtifactScope.BUILD_SOURCE:
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.NOT_REQUIRED,
            strategy=ArtifactInstallStrategy.NONE,
            reason="构建源制品不需要驻留目标Edge节点",
            verify_digest=bool(artifact.digest),
            rollback_required=False,
        )

    if _architecture_mismatch(artifact, target_agent):
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.BLOCKED,
            strategy=ArtifactInstallStrategy.NONE,
            reason=(
                f"制品要求{artifact.architecture}架构，目标节点为"
                f"{target_agent.metadata.get('machine')}"
            ),
            verify_digest=bool(artifact.digest),
            rollback_required=False,
        )

    installed = _matching_installation(artifact, target_agent)
    if installed is not None:
        if (
            artifact.digest
            and installed.digest
            and installed.digest.casefold() != artifact.digest.casefold()
        ):
            if artifact.kind is ArtifactKind.EDGE_PLUGIN and artifact.distribution is not None:
                return ArtifactPreparationItem(
                    order=order,
                    artifact=artifact,
                    state=ArtifactPreparationState.INSTALLABLE,
                    strategy=ArtifactInstallStrategy.INSTALL_WHEEL,
                    reason="节点插件摘要与Catalog不一致，可从可信wheel重新安装并保留回滚指针",
                    verify_digest=True,
                    rollback_required=True,
                    installed=installed,
                )
            if artifact.kind is ArtifactKind.RUNTIME_PACKAGE and artifact.distribution is not None:
                return ArtifactPreparationItem(
                    order=order,
                    artifact=artifact,
                    state=ArtifactPreparationState.INSTALLABLE,
                    strategy=ArtifactInstallStrategy.INSTALL_WHEEL,
                    reason="节点Runtime Package摘要与Catalog不一致，可重新安装固定摘要wheel",
                    verify_digest=True,
                    rollback_required=True,
                    installed=installed,
                )
            if artifact.kind is ArtifactKind.CONTAINER_IMAGE:
                return ArtifactPreparationItem(
                    order=order,
                    artifact=artifact,
                    state=ArtifactPreparationState.INSTALLABLE,
                    strategy=(
                        ArtifactInstallStrategy.DOCKER_IMPORT
                        if artifact.distribution is not None
                        else ArtifactInstallStrategy.DOCKER_PULL
                    ),
                    reason=(
                        "节点镜像摘要与Catalog不一致，可从固定摘要归档重新导入"
                        if artifact.distribution is not None
                        else "节点镜像摘要与Catalog不一致，可按固定仓库摘要重新拉取"
                    ),
                    verify_digest=True,
                    rollback_required=False,
                    installed=installed,
                )
            if artifact.kind is ArtifactKind.TENSORRT_ENGINE and artifact.distribution is not None:
                return ArtifactPreparationItem(
                    order=order,
                    artifact=artifact,
                    state=ArtifactPreparationState.INSTALLABLE,
                    strategy=ArtifactInstallStrategy.COPY_FILE,
                    reason="节点Engine摘要与Catalog不一致，可从可信文件原子替换并保留回滚副本",
                    verify_digest=True,
                    rollback_required=True,
                    installed=installed,
                )
            if (
                artifact.kind is ArtifactKind.CALIBRATION_PROFILE
                and artifact.distribution is not None
            ):
                return ArtifactPreparationItem(
                    order=order,
                    artifact=artifact,
                    state=ArtifactPreparationState.INSTALLABLE,
                    strategy=ArtifactInstallStrategy.INSTALL_CONFIG,
                    reason="节点标定配置摘要与Catalog不一致，可重新安装固定摘要版本",
                    verify_digest=True,
                    rollback_required=True,
                    installed=installed,
                )
            return ArtifactPreparationItem(
                order=order,
                artifact=artifact,
                state=ArtifactPreparationState.BLOCKED,
                strategy=ArtifactInstallStrategy.NONE,
                reason="节点已有同定位符制品，但摘要与Catalog固定摘要不一致",
                verify_digest=True,
                rollback_required=False,
                installed=installed,
            )
        digest_unavailable = bool(artifact.digest and not installed.digest)
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.READY,
            strategy=ArtifactInstallStrategy.NONE,
            reason=(
                "目标节点库存已满足固定版本要求，但节点未上报可比较摘要"
                if digest_unavailable
                else "目标节点库存已满足固定版本与摘要要求"
            ),
            verify_digest=bool(artifact.digest),
            rollback_required=False,
            installed=installed,
        )

    if artifact.distribution is not None and artifact.kind is ArtifactKind.EDGE_PLUGIN:
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.INSTALLABLE,
            strategy=ArtifactInstallStrategy.INSTALL_WHEEL,
            reason="平台具有经过Catalog摘要固定的wheel分发文件",
            verify_digest=True,
            rollback_required=True,
        )

    if (
        artifact.distribution is not None
        and artifact.digest
        and artifact.kind is ArtifactKind.RUNTIME_PACKAGE
    ):
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.INSTALLABLE,
            strategy=ArtifactInstallStrategy.INSTALL_WHEEL,
            reason="平台具有经过Catalog摘要固定的Runtime wheel，可由Agent版本化安装",
            verify_digest=True,
            rollback_required=True,
        )

    if artifact.kind is ArtifactKind.CONTAINER_IMAGE and artifact.digest:
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.INSTALLABLE,
            strategy=(
                ArtifactInstallStrategy.DOCKER_IMPORT
                if artifact.distribution is not None
                else ArtifactInstallStrategy.DOCKER_PULL
            ),
            reason=(
                "平台具有经过Catalog摘要固定的Docker镜像归档，可由Agent流式下载并导入"
                if artifact.distribution is not None
                else "Catalog已固定容器仓库定位符与SHA-256摘要，可由Agent受控拉取"
            ),
            verify_digest=True,
            rollback_required=False,
        )

    if (
        artifact.kind is ArtifactKind.TENSORRT_ENGINE
        and artifact.digest
        and artifact.distribution is not None
    ):
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.INSTALLABLE,
            strategy=ArtifactInstallStrategy.COPY_FILE,
            reason="平台具有经过Catalog摘要固定的TensorRT Engine，可由Agent流式下载并原子安装",
            verify_digest=True,
            rollback_required=True,
        )

    if (
        artifact.kind is ArtifactKind.CALIBRATION_PROFILE
        and artifact.digest
        and artifact.distribution is not None
    ):
        return ArtifactPreparationItem(
            order=order,
            artifact=artifact,
            state=ArtifactPreparationState.INSTALLABLE,
            strategy=ArtifactInstallStrategy.INSTALL_CONFIG,
            reason="平台具有经过Catalog摘要固定并完成结构校验的标定配置",
            verify_digest=True,
            rollback_required=True,
        )

    # 只有目标定位符而没有可信分发文件时，不生成任何可执行命令。
    return ArtifactPreparationItem(
        order=order,
        artifact=artifact,
        state=ArtifactPreparationState.MANUAL_REQUIRED,
        strategy=ArtifactInstallStrategy.MANUAL,
        reason="Catalog尚未声明可信分发源，当前仅允许人工准备后重新盘点",
        verify_digest=bool(artifact.digest),
        rollback_required=True,
    )


def prepare_artifacts(
    request: ArtifactPreparationRequest,
    target_agent: AgentRecord,
    *,
    now: datetime | None = None,
) -> ArtifactPreparationPlan:
    """解析Catalog与节点库存，返回不可执行的安全预检结果。"""

    artifacts: list[ArtifactDefinition] = []
    blocked_items: dict[int, ArtifactPreparationItem] = {}
    for artifact_id in request.artifact_ids:
        artifact = find_artifact(artifact_id)
        if artifact is None:
            raise ArtifactNotFoundError(artifact_id)
        resolution = resolve_artifact(artifact, target_agent)
        order = len(artifacts) + 1
        if resolution.artifact is None:
            blocked_items[order] = ArtifactPreparationItem(
                order=order,
                artifact=artifact,
                state=ArtifactPreparationState.BLOCKED,
                strategy=ArtifactInstallStrategy.NONE,
                reason=resolution.reason
                + "："
                + "；".join(
                    f"{item.variant_id}（{'，'.join(item.reasons)}）"
                    for item in resolution.rejected_variants
                ),
                verify_digest=bool(artifact.digest),
                rollback_required=False,
            )
            artifacts.append(artifact)
        else:
            artifacts.append(resolution.artifact)

    items = [
        blocked_items.get(index) or _item_for(index, artifact, target_agent)
        for index, artifact in enumerate(artifacts, start=1)
    ]
    ready_states = {
        ArtifactPreparationState.READY,
        ArtifactPreparationState.NOT_REQUIRED,
    }
    ready = all(item.state in ready_states for item in items)
    canonical = json.dumps(
        {
            "target_agent_id": request.target_agent_id,
            "artifact_ids": request.artifact_ids,
            "selected_variants": [artifact.selected_variant_id for artifact in artifacts],
            "inventory": [
                item.model_dump(mode="json") for item in target_agent.installed_artifacts
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    plan_id = f"prepare-{hashlib.sha256(canonical.encode()).hexdigest()[:20]}"
    warnings = []
    unverified_count = sum(
        1
        for item in items
        if item.state is ArtifactPreparationState.READY
        and item.artifact.digest
        and item.installed is not None
        and not item.installed.digest
    )
    if unverified_count:
        warnings.append(f"{unverified_count}项库存未上报可比较摘要；当前按定位符与版本判定已就绪。")
    if not ready:
        warnings.append("此接口只生成预检计划；不会下载、覆盖、删除制品或执行远程命令。")
    return ArtifactPreparationPlan(
        plan_id=plan_id,
        target_agent_id=request.target_agent_id,
        ready_for_deployment=ready,
        requires_changes=any(
            item.state
            not in {
                ArtifactPreparationState.READY,
                ArtifactPreparationState.NOT_REQUIRED,
            }
            for item in items
        ),
        items=items,
        warnings=warnings,
        created_at=now or datetime.now(UTC),
    )
