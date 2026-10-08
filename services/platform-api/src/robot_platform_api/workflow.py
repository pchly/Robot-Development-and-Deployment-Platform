"""工作流兼容性解析和DeploymentPlan编译。"""

import hashlib
import json
from datetime import UTC, datetime

from robot_platform_api.artifact_resolution import resolve_artifact
from robot_platform_api.catalog import (
    find_algorithm,
    find_artifact,
    find_hardware,
    find_workflow_recipe,
)
from robot_platform_api.models import (
    AgentRecord,
    AgentStatus,
    AlgorithmDefinition,
    ArtifactDefinition,
    AssetReadiness,
    CompatibilityIssue,
    CompatibilityReport,
    DeploymentCompilation,
    DeploymentPlan,
    DeploymentPlanRequest,
    HardwareDefinition,
    InstalledArtifact,
    ResolvedArtifact,
    ResolvedComponent,
    WorkflowDefinition,
)

CatalogAsset = HardwareDefinition | AlgorithmDefinition


def _normalized_locator(value: str) -> str:
    return value.strip().replace("\\", "/").casefold()


def _installed_artifact(
    artifact: ArtifactDefinition,
    target_agent: AgentRecord,
) -> InstalledArtifact | None:
    expected = _normalized_locator(artifact.locator)
    for installed in target_agent.installed_artifacts:
        if installed.kind != artifact.kind:
            continue
        actual = _normalized_locator(installed.locator)
        if artifact.kind.value == "edge_plugin":
            locator_matches = actual.replace("_", "-") == expected.replace("_", "-")
        elif artifact.kind.value == "model":
            locator_matches = actual == expected or actual.endswith(f"/{expected}")
        else:
            locator_matches = actual == expected
        version_matches = not installed.version or installed.version == artifact.version
        if locator_matches and version_matches:
            return installed
    return None


def _resolve_asset(component_ref: str, version: str) -> tuple[str, CatalogAsset] | None:
    hardware = find_hardware(component_ref, version)
    if hardware is not None:
        return "hardware", hardware
    algorithm = find_algorithm(component_ref, version)
    if algorithm is not None:
        return "algorithm", algorithm
    return None


def validate_workflow(workflow: WorkflowDefinition) -> CompatibilityReport:
    """检查组件、版本、端口、数据类型和必需输入。"""

    issues: list[CompatibilityIssue] = []
    nodes = {node.node_id: node for node in workflow.nodes}
    if len(nodes) != len(workflow.nodes):
        issues.append(
            CompatibilityIssue(
                severity="error",
                code="duplicate_node_id",
                message="工作流节点ID必须唯一",
            )
        )

    resolved: dict[str, tuple[str, CatalogAsset]] = {}
    for node in workflow.nodes:
        asset = _resolve_asset(node.component_ref, node.component_version)
        if asset is None:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="component_not_found",
                    message=f"找不到组件{node.component_ref}@{node.component_version}",
                    node_id=node.node_id,
                )
            )
            continue
        resolved[node.node_id] = asset
        if asset[1].readiness is not AssetReadiness.VALIDATED:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="asset_not_validated",
                    message=f"组件{node.component_ref}尚未通过实机或模拟验证",
                    node_id=node.node_id,
                )
            )

    connected_inputs: set[tuple[str, str]] = set()
    graph: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for link in workflow.links:
        source_asset = resolved.get(link.source.node_id)
        target_asset = resolved.get(link.target.node_id)
        if source_asset is None or target_asset is None:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="link_node_not_found",
                    message="连接引用了不存在或无法解析的节点",
                )
            )
            continue

        graph[link.source.node_id].add(link.target.node_id)

        source_ports = {port.name: port for port in source_asset[1].outputs}
        target_ports = {port.name: port for port in target_asset[1].inputs}
        source_port = source_ports.get(link.source.port)
        target_port = target_ports.get(link.target.port)
        if source_port is None:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="source_port_not_found",
                    message=f"节点{link.source.node_id}没有输出端口{link.source.port}",
                    node_id=link.source.node_id,
                )
            )
            continue
        if target_port is None:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="target_port_not_found",
                    message=f"节点{link.target.node_id}没有输入端口{link.target.port}",
                    node_id=link.target.node_id,
                )
            )
            continue
        target_key = (link.target.node_id, link.target.port)
        if target_key in connected_inputs:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="input_already_connected",
                    message=f"输入{link.target.node_id}.{link.target.port}只能连接一个数据源",
                    node_id=link.target.node_id,
                )
            )
        connected_inputs.add(target_key)
        if source_port.data_type != target_port.data_type:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="port_type_mismatch",
                    message=(
                        f"{link.source.node_id}.{link.source.port}的{source_port.data_type}"
                        f"不能连接到{link.target.node_id}.{link.target.port}的{target_port.data_type}"
                    ),
                    node_id=link.target.node_id,
                )
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def has_cycle(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(has_cycle(target) for target in graph.get(node_id, ())):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    if any(has_cycle(node_id) for node_id in graph if node_id not in visited):
        issues.append(
            CompatibilityIssue(
                severity="error",
                code="workflow_cycle",
                message="工作流数据连接不能形成循环",
            )
        )

    for node_id, (_kind, asset) in resolved.items():
        for port in asset.inputs:
            if port.required and (node_id, port.name) not in connected_inputs:
                issues.append(
                    CompatibilityIssue(
                        severity="error",
                        code="required_input_unconnected",
                        message=f"必需输入{node_id}.{port.name}尚未连接",
                        node_id=node_id,
                    )
                )

    return CompatibilityReport(
        compatible=not any(issue.severity == "error" for issue in issues),
        issues=issues,
    )


def compile_deployment(
    request: DeploymentPlanRequest,
    target_agent: AgentRecord | None,
    *,
    now: datetime | None = None,
) -> DeploymentCompilation:
    """把兼容工作流编译为固定组件版本的端侧计划。"""

    report = validate_workflow(request.workflow)
    issues = list(report.issues)
    recipe = None
    if target_agent is None:
        issues.append(
            CompatibilityIssue(
                severity="error",
                code="target_agent_not_found",
                message=f"找不到目标节点{request.target_agent_id}",
            )
        )
    elif target_agent.status is not AgentStatus.ONLINE:
        issues.append(
            CompatibilityIssue(
                severity="error",
                code="target_agent_offline",
                message=f"目标节点{request.target_agent_id}当前离线",
            )
        )
    else:
        component_refs = [node.component_ref for node in request.workflow.nodes]
        recipe = find_workflow_recipe(component_refs)
        if recipe is None:
            issues.append(
                CompatibilityIssue(
                    severity="error",
                    code="runtime_recipe_not_found",
                    message="组件端口兼容，但当前没有经过验证的端侧Runtime配方",
                )
            )
        else:
            missing_capabilities = sorted(
                set(recipe.required_capabilities) - set(target_agent.capabilities)
            )
            if missing_capabilities:
                issues.append(
                    CompatibilityIssue(
                        severity="error",
                        code="target_capability_missing",
                        message=("目标节点缺少实机Runtime能力：" + "、".join(missing_capabilities)),
                    )
                )
            inventory_supported = "artifact.inventory.v1" in target_agent.capabilities
            for artifact_id in recipe.required_artifact_ids:
                artifact = find_artifact(artifact_id)
                if artifact is None:
                    continue
                resolution = resolve_artifact(artifact, target_agent)
                if resolution.artifact is None:
                    issues.append(
                        CompatibilityIssue(
                            severity="error",
                            code="artifact_variant_not_compatible",
                            message=(
                                f"运行制品{artifact.name}没有兼容发行变体：{resolution.reason}"
                            ),
                        )
                    )
                    continue
                artifact = resolution.artifact
                if inventory_supported:
                    installed = _installed_artifact(artifact, target_agent)
                    if installed is None:
                        issues.append(
                            CompatibilityIssue(
                                severity="error",
                                code="required_artifact_missing",
                                message=f"目标节点缺少运行制品：{artifact.name}",
                            )
                        )
                    elif (
                        artifact.digest
                        and installed.digest
                        and artifact.digest.casefold() != installed.digest.casefold()
                    ):
                        issues.append(
                            CompatibilityIssue(
                                severity="error",
                                code="artifact_digest_mismatch",
                                message=f"目标节点制品摘要不匹配：{artifact.name}",
                            )
                        )
            for component in recipe.components:
                hardware = find_hardware(
                    component.component_ref,
                    component.component_version,
                )
                if hardware is None or hardware.discovery.transport != "usb":
                    continue
                vendor_ids = {item.lower() for item in hardware.discovery.vendor_ids}
                name_parts = [item.lower() for item in hardware.discovery.product_name_contains]
                has_required_device = any(
                    (
                        not vendor_ids
                        or (device.vendor_id is not None and device.vendor_id.lower() in vendor_ids)
                    )
                    and all(part in device.name.lower() for part in name_parts)
                    for device in target_agent.devices
                )
                if not has_required_device:
                    issues.append(
                        CompatibilityIssue(
                            severity="error",
                            code="required_device_missing",
                            message=f"目标节点当前未发现{hardware.name}设备",
                        )
                    )

    report = CompatibilityReport(
        compatible=not any(issue.severity == "error" for issue in issues),
        issues=issues,
    )
    if not report.compatible:
        return DeploymentCompilation(report=report)

    components: list[ResolvedComponent] = []
    for node in request.workflow.nodes:
        resolved = _resolve_asset(node.component_ref, node.component_version)
        if resolved is None:
            continue
        components.append(
            ResolvedComponent(
                node_id=node.node_id,
                component_ref=node.component_ref,
                component_version=node.component_version,
                kind=resolved[0],
            )
        )

    artifacts: list[ResolvedArtifact] = []
    if recipe is not None:
        for artifact_id in recipe.required_artifact_ids:
            artifact = find_artifact(artifact_id)
            if artifact is None:
                continue
            if target_agent is not None:
                resolution = resolve_artifact(artifact, target_agent)
                if resolution.artifact is None:
                    continue
                artifact = resolution.artifact
            artifacts.append(
                ResolvedArtifact(
                    artifact_id=artifact.artifact_id,
                    kind=artifact.kind,
                    version=artifact.version,
                    locator=artifact.locator,
                    digest=artifact.digest,
                    architecture=artifact.architecture,
                    variant_id=artifact.selected_variant_id,
                )
            )

    canonical = json.dumps(
        {
            "request": request.model_dump(mode="json", exclude_none=True),
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    plan_id = f"plan-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"
    plan = DeploymentPlan(
        plan_id=plan_id,
        target_agent_id=request.target_agent_id,
        workflow=request.workflow,
        project_id=request.project_id,
        workflow_revision=request.workflow_revision,
        components=components,
        artifacts=artifacts,
        created_at=now or datetime.now(UTC),
    )
    return DeploymentCompilation(report=report, plan=plan)
