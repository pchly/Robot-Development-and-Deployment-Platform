"""Platform API 的领域模型。"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AgentStatus(StrEnum):
    """边缘节点的连接状态。"""

    ONLINE = "online"
    OFFLINE = "offline"


class ArtifactKind(StrEnum):
    """平台可分发或在Edge节点上验证的制品类型。"""

    EDGE_PLUGIN = "edge_plugin"
    CONTAINER_IMAGE = "container_image"
    MODEL = "model"
    TENSORRT_ENGINE = "tensorrt_engine"
    SIMULATION_ASSET = "simulation_asset"
    RUNTIME_PACKAGE = "runtime_package"
    CALIBRATION_PROFILE = "calibration_profile"


class ArtifactScope(StrEnum):
    """制品在工程生命周期中的驻留位置。"""

    BUILD_SOURCE = "build_source"
    EDGE_RUNTIME = "edge_runtime"


class ArtifactDistribution(BaseModel):
    """由Platform API按固定Catalog ID提供的不可变文件。"""

    transport: Literal["platform_api"] = "platform_api"
    download_path: str = Field(min_length=1, max_length=512)
    file_name: str = Field(min_length=1, max_length=256)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0, le=64 * 1024 * 1024 * 1024)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ArtifactCompatibility(BaseModel):
    """制品发行变体对目标节点环境的声明式约束。"""

    operating_system: str | None = Field(default=None, max_length=64)
    architecture: str | None = Field(default=None, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, max_length=128)
    metadata: dict[str, str] = Field(default_factory=dict)


class ArtifactVariant(BaseModel):
    """一个逻辑制品面向特定平台的不可变发行变体。"""

    variant_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    locator: str = Field(min_length=1, max_length=1024)
    digest: str | None = Field(default=None, max_length=128)
    architecture: str | None = Field(default=None, max_length=64)
    compatibility: ArtifactCompatibility = Field(default_factory=ArtifactCompatibility)
    distribution: ArtifactDistribution | None = None
    priority: int = Field(default=0, ge=-1000, le=1000)


class InstalledArtifact(BaseModel):
    """Edge Agent只读盘点得到的本机制品。"""

    kind: ArtifactKind
    locator: str = Field(min_length=1, max_length=1024)
    version: str | None = Field(default=None, max_length=128)
    digest: str | None = Field(default=None, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0)
    artifact_id: str | None = Field(
        default=None,
        max_length=192,
        pattern=r"^[a-z0-9._-]+$",
    )
    install_path: str | None = Field(default=None, max_length=2048)
    active: bool | None = None


class AgentRegistration(BaseModel):
    """Edge Agent 首次注册或刷新自身信息时提交的数据。"""

    agent_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    capabilities: list[str] = Field(default_factory=list, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    installed_artifacts: list[InstalledArtifact] = Field(default_factory=list, max_length=1024)


class AgentMetrics(BaseModel):
    """Edge Agent 周期性上报的基础系统遥测。"""

    uptime_s: float | None = Field(default=None, ge=0)
    load_average_1m: float | None = Field(default=None, ge=0)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    memory_available_bytes: int | None = Field(default=None, ge=0)
    root_disk_total_bytes: int | None = Field(default=None, ge=0)
    root_disk_free_bytes: int | None = Field(default=None, ge=0)
    data_disk_total_bytes: int | None = Field(default=None, ge=0)
    data_disk_free_bytes: int | None = Field(default=None, ge=0)
    max_temperature_c: float | None = Field(default=None, ge=-50, le=250)


class DeviceTransport(StrEnum):
    """端侧设备的物理或逻辑传输类型。"""

    USB = "usb"
    CAN = "can"
    SERIAL = "serial"
    ETHERNET = "ethernet"
    SIMULATION = "simulation"


class DeviceInstance(BaseModel):
    """Edge Agent只读发现的具体设备实例。"""

    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    transport: DeviceTransport
    name: str = Field(min_length=1, max_length=256)
    manufacturer: str | None = Field(default=None, max_length=256)
    vendor_id: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{4}$")
    product_id: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{4}$")
    serial: str | None = Field(default=None, max_length=256)
    device_nodes: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentHeartbeat(BaseModel):
    """Edge Agent 心跳数据。"""

    sequence: int = Field(ge=0)
    metrics: AgentMetrics | None = None
    devices: list[DeviceInstance] | None = Field(default=None, max_length=512)


class AgentRecord(AgentRegistration):
    """平台保存并返回的边缘节点记录。"""

    status: AgentStatus
    registered_at: datetime
    last_seen_at: datetime
    heartbeat_sequence: int = Field(ge=0)
    metrics: AgentMetrics | None = None
    devices: list[DeviceInstance] = Field(default_factory=list)


class AssetReadiness(StrEnum):
    """资产当前是否具备部署条件。"""

    DEFINITION_ONLY = "definition_only"
    VALIDATED = "validated"


class ArtifactDefinition(BaseModel):
    """Catalog中声明的固定版本制品。"""

    artifact_id: str = Field(min_length=1, max_length=192, pattern=r"^[a-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=256)
    kind: ArtifactKind
    scope: ArtifactScope
    version: str = Field(min_length=1, max_length=64)
    locator: str = Field(min_length=1, max_length=1024)
    digest: str | None = Field(default=None, max_length=128)
    architecture: str | None = Field(default=None, max_length=64)
    source_ref: str = Field(min_length=1, max_length=192)
    required_by: list[str] = Field(default_factory=list, max_length=128)
    distribution: ArtifactDistribution | None = None
    variants: list[ArtifactVariant] = Field(default_factory=list, max_length=64)
    selected_variant_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_variants(self) -> "ArtifactDefinition":
        variant_ids = [variant.variant_id for variant in self.variants]
        if len(set(variant_ids)) != len(variant_ids):
            raise ValueError("同一逻辑制品的variant_id不能重复")
        if self.selected_variant_id and self.selected_variant_id not in variant_ids:
            raise ValueError("selected_variant_id必须引用已声明的制品变体")
        return self


class ArtifactVariantRejection(BaseModel):
    """一个发行变体不能部署到目标节点的原因。"""

    variant_id: str
    reasons: list[str] = Field(min_length=1, max_length=32)


class ArtifactResolution(BaseModel):
    """逻辑制品针对目标节点的确定性解析结果。"""

    artifact_id: str
    target_agent_id: str
    compatible: bool
    selected_variant_id: str | None = None
    artifact: ArtifactDefinition | None = None
    reason: str
    rejected_variants: list[ArtifactVariantRejection] = Field(default_factory=list)


class ArtifactPreparationState(StrEnum):
    """目标节点相对于Catalog制品定义的准备状态。"""

    READY = "ready"
    INSTALLABLE = "installable"
    MANUAL_REQUIRED = "manual_required"
    BLOCKED = "blocked"
    NOT_REQUIRED = "not_required"


class ArtifactInstallStrategy(StrEnum):
    """受控安装事务未来可以采用的有限策略集合。"""

    NONE = "none"
    DOCKER_PULL = "docker_pull"
    DOCKER_IMPORT = "docker_import"
    INSTALL_WHEEL = "install_wheel"
    COPY_FILE = "copy_file"
    INSTALL_CONFIG = "install_config"
    MANUAL = "manual"


class ArtifactPreparationRequest(BaseModel):
    """为指定节点预检一组固定Catalog制品。"""

    target_agent_id: str = Field(min_length=1, max_length=128)
    artifact_ids: list[str] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_unique_artifacts(self) -> "ArtifactPreparationRequest":
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("artifact_ids不能重复")
        return self


class ArtifactPreparationItem(BaseModel):
    """一个制品在目标节点上的预检结论。"""

    order: int = Field(ge=1)
    artifact: ArtifactDefinition
    state: ArtifactPreparationState
    strategy: ArtifactInstallStrategy
    reason: str
    verify_digest: bool
    rollback_required: bool
    installed: InstalledArtifact | None = None


class ArtifactPreparationPlan(BaseModel):
    """只读准备计划；当前版本不会在Edge节点执行任何修改。"""

    plan_id: str
    target_agent_id: str
    ready_for_deployment: bool
    requires_changes: bool
    execution_enabled: Literal[False] = False
    items: list[ArtifactPreparationItem]
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class ArtifactInstallationStatus(StrEnum):
    """受控制品安装事务状态。"""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLING = "installing"
    ACTIVATING = "activating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ArtifactInstallationRequest(BaseModel):
    """请求把一个Catalog制品安装到目标Edge节点。"""

    target_agent_id: str = Field(min_length=1, max_length=128)
    artifact_id: str = Field(min_length=1, max_length=192)
    reinstall: bool = False


class ArtifactInstallationPlan(BaseModel):
    """Agent可执行的固定制品安装计划。"""

    operation_id: str
    target_agent_id: str
    artifact: ArtifactDefinition
    created_at: datetime


class ArtifactInstallationUpdate(BaseModel):
    """Edge Agent回报安装事务进度。"""

    status: ArtifactInstallationStatus
    message: str = Field(default="", max_length=512)


class ArtifactInstallationRecord(BaseModel):
    """平台保存的安装计划与当前状态。"""

    plan: ArtifactInstallationPlan
    status: ArtifactInstallationStatus
    message: str = ""
    updated_at: datetime


class ProjectStatus(StrEnum):
    """项目设计态生命周期，不代表机器人运行状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ProjectCreate(BaseModel):
    """创建项目时提交的稳定字段。"""

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)


class ProjectUpdate(BaseModel):
    """项目元数据的部分更新。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    status: ProjectStatus | None = None


class ProjectRecord(ProjectCreate):
    """持久化项目记录。"""

    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$")
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ComponentPort(BaseModel):
    """组件的类型化输入或输出端口。"""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    data_type: str = Field(min_length=1, max_length=128)
    required: bool = True


class DeviceDiscoveryRule(BaseModel):
    """硬件定义用于匹配设备实例的声明式规则。"""

    transport: str = Field(min_length=1, max_length=32)
    vendor_ids: list[str] = Field(default_factory=list, max_length=32)
    product_name_contains: list[str] = Field(default_factory=list, max_length=32)


class ComponentParameter(BaseModel):
    """组件Manifest声明的可配置参数。"""

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=128)
    value_type: Literal["string", "integer", "number"]
    default: str | int | float
    required: bool = True
    visible: bool = True
    binding_source: Literal["manual", "target_hardware_devices"] = "manual"
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = Field(default=None, gt=0)


class HardwareDefinition(BaseModel):
    """硬件库中的产品族定义，而不是当前在线设备。"""

    hardware_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=256)
    vendor: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=128)
    readiness: AssetReadiness
    discovery: DeviceDiscoveryRule
    inputs: list[ComponentPort] = Field(default_factory=list, max_length=64)
    outputs: list[ComponentPort] = Field(default_factory=list, max_length=64)
    parameters: list[ComponentParameter] = Field(default_factory=list, max_length=128)


class AlgorithmDefinition(BaseModel):
    """算法库中的逻辑算法定义。"""

    algorithm_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    readiness: AssetReadiness
    inputs: list[ComponentPort] = Field(default_factory=list, max_length=64)
    outputs: list[ComponentPort] = Field(default_factory=list, max_length=64)
    artifact_formats: list[str] = Field(default_factory=list, max_length=32)
    parameters: list[ComponentParameter] = Field(default_factory=list, max_length=128)


class RecipeComponent(BaseModel):
    """可执行配方中的组件节点。"""

    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    component_ref: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    component_version: str = Field(min_length=1, max_length=32)


class RecipeParameter(ComponentParameter):
    """由Studio动态生成表单的配方参数。"""

    node_id: str = Field(min_length=1, max_length=64)


class WorkflowRecipe(BaseModel):
    """已经具备端侧Runtime实现、允许一键部署的组件组合。"""

    recipe_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=256)
    components: list[RecipeComponent] = Field(min_length=2, max_length=32)
    links: list["WorkflowLink"] = Field(min_length=1, max_length=32)
    parameters: list[RecipeParameter] = Field(default_factory=list, max_length=128)
    required_capabilities: list[str] = Field(default_factory=list, max_length=128)
    required_artifact_ids: list[str] = Field(default_factory=list, max_length=128)


class WorkflowNodePosition(BaseModel):
    """Studio画布中的节点位置；不参与端侧执行语义。"""

    x: float = Field(ge=0, le=10000)
    y: float = Field(ge=0, le=10000)


class WorkflowNode(BaseModel):
    """工作流中的一个逻辑组件实例。"""

    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    component_ref: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    component_version: str = Field(min_length=1, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)
    position: WorkflowNodePosition | None = None


class PortReference(BaseModel):
    """指向某个节点端口的引用。"""

    node_id: str = Field(min_length=1, max_length=64)
    port: str = Field(min_length=1, max_length=64)


class WorkflowLink(BaseModel):
    """工作流中的类型化单向数据连接。"""

    source: PortReference
    target: PortReference


class WorkflowDefinition(BaseModel):
    """可版本化、尚未绑定目标节点的逻辑工作流。"""

    workflow_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=256)
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=128)
    links: list[WorkflowLink] = Field(default_factory=list, max_length=256)


class WorkflowRevisionRecord(BaseModel):
    """项目中一次不可变的工作流保存记录。"""

    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$")
    workflow_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    revision: int = Field(ge=1)
    workflow: WorkflowDefinition
    created_at: datetime


class CompatibilityIssue(BaseModel):
    """工作流或目标环境中的一个兼容性问题。"""

    severity: Literal["error", "warning"]
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=512)
    node_id: str | None = None


class CompatibilityReport(BaseModel):
    """兼容性解析结果。"""

    compatible: bool
    issues: list[CompatibilityIssue] = Field(default_factory=list)


class DeploymentPlanRequest(BaseModel):
    """把逻辑工作流编译到目标Edge节点的请求。"""

    target_agent_id: str = Field(min_length=1, max_length=128)
    workflow: WorkflowDefinition
    project_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$"
    )
    workflow_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_revision_provenance(self) -> "DeploymentPlanRequest":
        """项目与修订号必须成对出现，避免产生半截来源信息。"""

        if (self.project_id is None) != (self.workflow_revision is None):
            raise ValueError("project_id与workflow_revision必须同时提供")
        return self


class RevisionDeploymentRequest(BaseModel):
    """从项目中已保存的不可变工作流修订发起部署。"""

    target_agent_id: str = Field(min_length=1, max_length=128)


class ResolvedComponent(BaseModel):
    """DeploymentPlan中已固定版本的组件。"""

    node_id: str
    component_ref: str
    component_version: str
    kind: Literal["hardware", "algorithm"]


class ResolvedArtifact(BaseModel):
    """DeploymentPlan中已经固定定位符与摘要的端侧制品。"""

    artifact_id: str
    kind: ArtifactKind
    version: str
    locator: str
    digest: str | None = None
    architecture: str | None = None
    variant_id: str | None = None


class DeploymentPlan(BaseModel):
    """通过兼容性校验后生成的不可变端侧部署计划。"""

    plan_id: str
    target_agent_id: str
    workflow: WorkflowDefinition
    project_id: str | None = None
    workflow_revision: int | None = Field(default=None, ge=1)
    components: list[ResolvedComponent]
    artifacts: list[ResolvedArtifact] = Field(default_factory=list)
    rollback_of_plan_id: str | None = None
    supersedes_plan_ids: list[str] = Field(default_factory=list)
    desired_state: Literal["running", "stopped"] = "running"
    created_at: datetime


class DeploymentCompilation(BaseModel):
    """部署编译结果；失败时plan为空并返回完整问题。"""

    report: CompatibilityReport
    plan: DeploymentPlan | None = None


class DeploymentStatus(StrEnum):
    """端侧部署的实际运行状态。"""

    QUEUED = "queued"
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class AcceptanceVerdict(StrEnum):
    """一次Runtime运行的工程验收结论。"""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class AcceptanceCheckStatus(StrEnum):
    """单项验收检查状态。"""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class DeploymentAcceptanceCheck(BaseModel):
    """带实测值和判定依据的单项验收证据。"""

    check_id: str
    label: str
    status: AcceptanceCheckStatus
    actual: float | str | None = None
    expected: str
    unit: str | None = None
    detail: str = ""


class DeploymentAcceptanceReport(BaseModel):
    """一次部署运行结束后生成的不可变验收报告。"""

    schema_version: Literal["runtime-acceptance.v1"] = "runtime-acceptance.v1"
    attempt: int = Field(ge=1)
    verdict: AcceptanceVerdict
    summary: str
    generated_at: datetime
    checks: list[DeploymentAcceptanceCheck]
    evidence_metrics: dict[str, float] = Field(default_factory=dict)


class DeploymentEvent(BaseModel):
    """部署生命周期中的低频关键事件。"""

    attempt: int = Field(ge=1)
    event_type: Literal[
        "submitted",
        "restarted",
        "rollback_created",
        "status_changed",
        "stop_requested",
    ]
    status: DeploymentStatus
    message: str = Field(default="", max_length=512)
    occurred_at: datetime


class DeploymentStatusUpdate(BaseModel):
    """Edge Agent上报的部署状态与轻量指标。"""

    status: DeploymentStatus
    message: str = Field(default="", max_length=512)
    metrics: dict[str, float] = Field(default_factory=dict)


class DeploymentRecord(BaseModel):
    """Platform API保存的部署计划与当前实际状态。"""

    plan: DeploymentPlan
    status: DeploymentStatus
    message: str = ""
    metrics: dict[str, float] = Field(default_factory=dict)
    attempt: int = Field(default=1, ge=1)
    events: list[DeploymentEvent] = Field(default_factory=list)
    acceptance_reports: list[DeploymentAcceptanceReport] = Field(default_factory=list)
    updated_at: datetime
    preview_updated_at: datetime | None = None
    preview_content_type: Literal["image/jpeg", "image/png"] | None = None


class DeploymentPreviewMetadata(BaseModel):
    """最近一张端侧预览的元数据与下一次采样策略。"""

    plan_id: str
    content_type: Literal["image/jpeg", "image/png"]
    size_bytes: int = Field(gt=0, le=256 * 1024)
    updated_at: datetime
    live_session_active: bool = False
    requested_fps: int = Field(default=5, ge=1, le=15)


class DeploymentLivePreviewSession(BaseModel):
    """有时限的按需实时监控会话。"""

    plan_id: str
    active: bool
    requested_fps: int = Field(ge=1, le=15)
    expires_at: datetime | None = None


class HealthResponse(BaseModel):
    """服务健康检查响应。"""

    status: str
    version: str
