"""把逻辑制品确定性解析为目标节点可安装的发行变体。"""

from robot_platform_api.models import (
    AgentRecord,
    ArtifactCompatibility,
    ArtifactDefinition,
    ArtifactResolution,
    ArtifactVariant,
    ArtifactVariantRejection,
)

_ARCHITECTURE_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
}


def _normalized_architecture(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    return _ARCHITECTURE_ALIASES.get(normalized, normalized)


def _normalized_operating_system(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized.startswith("win"):
        return "windows"
    if normalized in {"gnu/linux", "linux"}:
        return "linux"
    if normalized in {"darwin", "macos", "mac os"}:
        return "macos"
    return normalized


def _agent_operating_system(agent: AgentRecord) -> str:
    return _normalized_operating_system(
        agent.metadata.get("operating_system", agent.metadata.get("os", ""))
    )


def compatibility_reasons(
    compatibility: ArtifactCompatibility,
    agent: AgentRecord,
) -> list[str]:
    """返回约束不满足的可读原因；空列表表示兼容。"""

    reasons: list[str] = []
    actual_os = _agent_operating_system(agent)
    if compatibility.operating_system:
        required_os = _normalized_operating_system(compatibility.operating_system)
        if not actual_os:
            reasons.append(f"节点未上报操作系统（要求{compatibility.operating_system}）")
        elif actual_os != required_os:
            reasons.append(
                f"要求操作系统{compatibility.operating_system}，节点为"
                f"{agent.metadata.get('operating_system', agent.metadata.get('os'))}"
            )

    actual_architecture = _normalized_architecture(agent.metadata.get("machine", ""))
    if compatibility.architecture:
        required_architecture = _normalized_architecture(compatibility.architecture)
        if not actual_architecture:
            reasons.append(f"节点未上报CPU架构（要求{compatibility.architecture}）")
        elif actual_architecture != required_architecture:
            reasons.append(
                f"要求架构{compatibility.architecture}，节点为{agent.metadata.get('machine')}"
            )

    missing_capabilities = sorted(
        set(compatibility.required_capabilities) - set(agent.capabilities)
    )
    if missing_capabilities:
        reasons.append("缺少节点能力：" + "、".join(missing_capabilities))

    for key, expected in sorted(compatibility.metadata.items()):
        actual = agent.metadata.get(key)
        if actual is None or str(actual).strip() == "":
            reasons.append(f"节点未上报环境字段{key}（要求{expected}）")
        elif str(actual).strip().casefold() != expected.strip().casefold():
            reasons.append(f"环境字段{key}要求{expected}，节点为{actual}")
    return reasons


def _variant_compatibility(variant: ArtifactVariant) -> ArtifactCompatibility:
    if variant.architecture and not variant.compatibility.architecture:
        return variant.compatibility.model_copy(update={"architecture": variant.architecture})
    return variant.compatibility


def resolve_artifact(
    artifact: ArtifactDefinition,
    target_agent: AgentRecord,
) -> ArtifactResolution:
    """选择优先级最高且约束最具体的兼容变体，并固化其定位符和摘要。"""

    if not artifact.variants:
        # 旧Agent可能没有上报machine；单发行制品沿用历史上的宽松行为。
        compatibility = ArtifactCompatibility(
            architecture=(
                artifact.architecture
                if str(target_agent.metadata.get("machine", "")).strip()
                else None
            )
        )
        reasons = compatibility_reasons(compatibility, target_agent)
        if reasons:
            return ArtifactResolution(
                artifact_id=artifact.artifact_id,
                target_agent_id=target_agent.agent_id,
                compatible=False,
                reason="；".join(reasons),
                rejected_variants=[ArtifactVariantRejection(variant_id="legacy", reasons=reasons)],
            )
        return ArtifactResolution(
            artifact_id=artifact.artifact_id,
            target_agent_id=target_agent.agent_id,
            compatible=True,
            artifact=artifact.model_copy(deep=True),
            reason="单发行制品与目标节点兼容",
        )

    compatible: list[tuple[int, int, ArtifactVariant]] = []
    rejected: list[ArtifactVariantRejection] = []
    for variant in artifact.variants:
        compatibility = _variant_compatibility(variant)
        reasons = compatibility_reasons(compatibility, target_agent)
        if reasons:
            rejected.append(
                ArtifactVariantRejection(
                    variant_id=variant.variant_id,
                    reasons=reasons,
                )
            )
            continue
        specificity = (
            int(bool(compatibility.operating_system))
            + int(bool(compatibility.architecture))
            + len(compatibility.required_capabilities)
            + len(compatibility.metadata)
        )
        compatible.append((variant.priority, specificity, variant))

    if not compatible:
        return ArtifactResolution(
            artifact_id=artifact.artifact_id,
            target_agent_id=target_agent.agent_id,
            compatible=False,
            reason="没有与目标节点环境兼容的发行变体",
            rejected_variants=rejected,
        )

    compatible.sort(key=lambda item: (-item[0], -item[1], item[2].variant_id))
    selected = compatible[0][2]
    resolved = artifact.model_copy(
        update={
            "locator": selected.locator,
            "digest": selected.digest,
            "architecture": (selected.architecture or selected.compatibility.architecture),
            "distribution": selected.distribution,
            "selected_variant_id": selected.variant_id,
        },
        deep=True,
    )
    return ArtifactResolution(
        artifact_id=artifact.artifact_id,
        target_agent_id=target_agent.agent_id,
        compatible=True,
        selected_variant_id=selected.variant_id,
        artifact=resolved,
        reason=f"已选择兼容发行变体{selected.variant_id}",
        rejected_variants=rejected,
    )
