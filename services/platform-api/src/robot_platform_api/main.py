"""Platform API 应用入口。"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from robot_platform_api import __version__
from robot_platform_api.artifact_installations import (
    ArtifactInstallationConflict,
    ArtifactInstallationNotFoundError,
    ArtifactInstallationRegistry,
    InvalidArtifactInstallationTransition,
)
from robot_platform_api.artifact_preparation import (
    ArtifactNotFoundError,
    prepare_artifacts,
)
from robot_platform_api.artifact_resolution import resolve_artifact
from robot_platform_api.catalog import (
    CatalogManifestError,
    find_artifact,
    list_algorithms,
    list_artifacts,
    list_hardware,
    list_workflow_recipes,
    read_artifact_content,
)
from robot_platform_api.control_store import (
    ControlPlaneStore,
    ProjectNotFoundError,
    WorkflowRevisionNotFoundError,
    default_control_store_path,
)
from robot_platform_api.deployments import (
    DeploymentNotFoundError,
    DeploymentRegistry,
    DeploymentResourceConflict,
    InvalidDeploymentTransition,
)
from robot_platform_api.models import (
    AgentHeartbeat,
    AgentRecord,
    AgentRegistration,
    AlgorithmDefinition,
    ArtifactDefinition,
    ArtifactInstallationRecord,
    ArtifactInstallationRequest,
    ArtifactInstallationUpdate,
    ArtifactKind,
    ArtifactPreparationPlan,
    ArtifactPreparationRequest,
    ArtifactResolution,
    CompatibilityReport,
    DeploymentCompilation,
    DeploymentLivePreviewSession,
    DeploymentPlan,
    DeploymentPlanRequest,
    DeploymentPreviewMetadata,
    DeploymentRecord,
    DeploymentStatusUpdate,
    HardwareDefinition,
    HealthResponse,
    ProjectCreate,
    ProjectRecord,
    ProjectUpdate,
    RevisionDeploymentRequest,
    WorkflowDefinition,
    WorkflowRecipe,
    WorkflowRevisionRecord,
)
from robot_platform_api.registry import AgentNotFoundError, AgentRegistry
from robot_platform_api.workflow import compile_deployment, validate_workflow


def create_app(
    registry: AgentRegistry | None = None,
    deployment_registry: DeploymentRegistry | None = None,
    artifact_installation_registry: ArtifactInstallationRegistry | None = None,
    control_store: ControlPlaneStore | None = None,
) -> FastAPI:
    """创建 Platform API 应用，允许测试注入独立注册表。"""

    selected_store = control_store or ControlPlaneStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        selected_store.close()

    application = FastAPI(
        title="机器人开发平台 API",
        version=__version__,
        description="Web Studio 与 Edge Agent 之间的控制面 API。",
        lifespan=lifespan,
    )
    application.state.agent_registry = registry or AgentRegistry()
    application.state.deployment_registry = deployment_registry or DeploymentRegistry(
        initial_records=selected_store.list_deployment_records(),
        record_writer=selected_store.save_deployment_record,
    )
    application.state.artifact_installation_registry = (
        artifact_installation_registry
        or ArtifactInstallationRegistry(
            initial_records=selected_store.list_artifact_installation_records(),
            record_writer=selected_store.save_artifact_installation_record,
        )
    )
    application.state.control_store = selected_store
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @application.get("/api/v1/agents", response_model=list[AgentRecord], tags=["agents"])
    def list_agents(request: Request) -> list[AgentRecord]:
        return request.app.state.agent_registry.list_agents()

    @application.post(
        "/api/v1/projects",
        response_model=ProjectRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["projects"],
    )
    def create_project(project: ProjectCreate, request: Request) -> ProjectRecord:
        return request.app.state.control_store.create_project(project)

    @application.get(
        "/api/v1/projects",
        response_model=list[ProjectRecord],
        tags=["projects"],
    )
    def list_projects(request: Request) -> list[ProjectRecord]:
        return request.app.state.control_store.list_projects()

    @application.get(
        "/api/v1/projects/{project_id}",
        response_model=ProjectRecord,
        tags=["projects"],
    )
    def get_project(project_id: str, request: Request) -> ProjectRecord:
        try:
            return request.app.state.control_store.get_project(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            ) from error

    @application.patch(
        "/api/v1/projects/{project_id}",
        response_model=ProjectRecord,
        tags=["projects"],
    )
    def update_project(project_id: str, update: ProjectUpdate, request: Request) -> ProjectRecord:
        try:
            return request.app.state.control_store.update_project(project_id, update)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            ) from error

    @application.post(
        "/api/v1/projects/{project_id}/workflows",
        response_model=WorkflowRevisionRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["projects", "workflows"],
    )
    def save_project_workflow(
        project_id: str, workflow: WorkflowDefinition, request: Request
    ) -> WorkflowRevisionRecord:
        try:
            return request.app.state.control_store.save_workflow(project_id, workflow)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            ) from error

    @application.get(
        "/api/v1/projects/{project_id}/workflows",
        response_model=list[WorkflowRevisionRecord],
        tags=["projects", "workflows"],
    )
    def list_project_workflows(project_id: str, request: Request) -> list[WorkflowRevisionRecord]:
        try:
            return request.app.state.control_store.list_workflow_revisions(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            ) from error

    @application.get(
        "/api/v1/projects/{project_id}/workflows/{workflow_id}/revisions/{revision}",
        response_model=WorkflowRevisionRecord,
        tags=["projects", "workflows"],
    )
    def get_project_workflow_revision(
        project_id: str,
        workflow_id: str,
        revision: int,
        request: Request,
    ) -> WorkflowRevisionRecord:
        try:
            return request.app.state.control_store.get_workflow_revision(
                project_id, workflow_id, revision
            )
        except WorkflowRevisionNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="工作流修订不存在",
            ) from error

    @application.post(
        "/api/v1/projects/{project_id}/workflows/{workflow_id}/revisions/{revision}/deployments",
        response_model=DeploymentCompilation,
        tags=["projects", "workflows", "deployments"],
    )
    def deploy_project_workflow_revision(
        project_id: str,
        workflow_id: str,
        revision: int,
        deployment_request: RevisionDeploymentRequest,
        request: Request,
    ) -> DeploymentCompilation:
        """按不可变修订内容编译并提交部署，确保运行来源可追溯。"""

        try:
            saved = request.app.state.control_store.get_workflow_revision(
                project_id, workflow_id, revision
            )
        except WorkflowRevisionNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="工作流修订不存在",
            ) from error

        compile_request = DeploymentPlanRequest(
            target_agent_id=deployment_request.target_agent_id,
            workflow=saved.workflow,
            project_id=project_id,
            workflow_revision=revision,
        )
        try:
            target_agent = request.app.state.agent_registry.get_agent(
                deployment_request.target_agent_id
            )
        except AgentNotFoundError:
            target_agent = None
        compilation = compile_deployment(compile_request, target_agent)
        if compilation.plan is not None:
            try:
                request.app.state.deployment_registry.submit(compilation.plan)
            except DeploymentResourceConflict as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(error),
                ) from error
        return compilation

    @application.get(
        "/api/v1/projects/{project_id}/deployments",
        response_model=list[DeploymentRecord],
        tags=["projects", "deployments"],
    )
    def list_project_deployments(project_id: str, request: Request) -> list[DeploymentRecord]:
        try:
            request.app.state.control_store.get_project(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            ) from error
        return request.app.state.deployment_registry.list_for_project(project_id)

    @application.get(
        "/api/v1/catalog/hardware",
        response_model=list[HardwareDefinition],
        tags=["catalog"],
    )
    def hardware_catalog() -> list[HardwareDefinition]:
        return list_hardware()

    @application.get(
        "/api/v1/catalog/algorithms",
        response_model=list[AlgorithmDefinition],
        tags=["catalog"],
    )
    def algorithm_catalog() -> list[AlgorithmDefinition]:
        return list_algorithms()

    @application.get(
        "/api/v1/catalog/workflow-recipes",
        response_model=list[WorkflowRecipe],
        tags=["catalog"],
    )
    def workflow_recipe_catalog() -> list[WorkflowRecipe]:
        return list_workflow_recipes()

    @application.get(
        "/api/v1/catalog/artifacts",
        response_model=list[ArtifactDefinition],
        tags=["catalog"],
    )
    def artifact_catalog() -> list[ArtifactDefinition]:
        return list_artifacts()

    @application.get(
        "/api/v1/agents/{agent_id}/artifact-resolutions",
        response_model=list[ArtifactResolution],
        tags=["agents", "artifacts"],
    )
    def resolve_agent_artifacts(agent_id: str, request: Request) -> list[ArtifactResolution]:
        """预览每个逻辑制品在指定节点上会选择的发行变体。"""

        try:
            target_agent = request.app.state.agent_registry.get_agent(agent_id)
        except AgentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标Edge节点不存在",
            ) from error
        return [resolve_artifact(artifact, target_agent) for artifact in list_artifacts()]

    @application.get(
        "/api/v1/artifacts/{artifact_id}/content",
        response_class=Response,
        tags=["artifacts"],
    )
    def download_artifact(artifact_id: str, variant_id: str | None = None) -> Response:
        """只分发Catalog明确登记且读取时摘要仍一致的文件。"""

        try:
            artifact_content = read_artifact_content(artifact_id, variant_id)
        except CatalogManifestError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        if artifact_content is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="制品没有可分发文件",
            )
        return FileResponse(
            path=artifact_content.path,
            media_type=artifact_content.media_type,
            filename=artifact_content.file_name,
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "X-Artifact-Digest": artifact_content.digest,
                "X-Artifact-Size": str(artifact_content.size_bytes),
            },
        )

    @application.post(
        "/api/v1/artifact-preparation-plans/preview",
        response_model=ArtifactPreparationPlan,
        tags=["artifacts"],
    )
    def preview_artifact_preparation(
        preparation_request: ArtifactPreparationRequest,
        request: Request,
    ) -> ArtifactPreparationPlan:
        """只读比较Catalog与节点库存，不执行任何端侧修改。"""

        try:
            target_agent = request.app.state.agent_registry.get_agent(
                preparation_request.target_agent_id
            )
        except AgentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标Edge节点不存在",
            ) from error
        try:
            return prepare_artifacts(preparation_request, target_agent)
        except ArtifactNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"制品不存在：{error}",
            ) from error

    @application.post(
        "/api/v1/artifact-installations",
        response_model=ArtifactInstallationRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["artifacts"],
    )
    def create_artifact_installation(
        installation_request: ArtifactInstallationRequest,
        request: Request,
    ) -> ArtifactInstallationRecord:
        try:
            target_agent = request.app.state.agent_registry.get_agent(
                installation_request.target_agent_id
            )
        except AgentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标Edge节点不存在",
            ) from error
        artifact = find_artifact(installation_request.artifact_id)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="制品不存在",
            )
        resolution = resolve_artifact(artifact, target_agent)
        if resolution.artifact is None:
            details = "；".join(
                f"{item.variant_id}（{'，'.join(item.reasons)}）"
                for item in resolution.rejected_variants
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{resolution.reason}：{details}",
            )
        artifact = resolution.artifact
        if artifact.kind is ArtifactKind.EDGE_PLUGIN:
            if artifact.distribution is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Edge插件缺少可信Catalog分发文件",
                )
        elif artifact.kind is ArtifactKind.CONTAINER_IMAGE:
            if not artifact.digest or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", artifact.digest.casefold()
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="容器镜像必须同时固定仓库定位符与SHA-256摘要",
                )
            required_capability = (
                "artifact.container-import.v1"
                if artifact.distribution is not None
                else "artifact.container-pull.v1"
            )
            if required_capability not in target_agent.capabilities:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "目标Edge Agent不支持受控容器镜像归档导入"
                        if artifact.distribution is not None
                        else "目标Edge Agent不支持受控容器镜像拉取"
                    ),
                )
        elif artifact.kind is ArtifactKind.TENSORRT_ENGINE:
            if (
                artifact.distribution is None
                or not artifact.digest
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.digest.casefold())
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="TensorRT Engine必须同时固定目标路径、SHA-256和可信分发文件",
                )
            if "artifact.file-install.v1" not in target_agent.capabilities:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="目标Edge Agent不支持受管文件原子安装",
                )
        elif artifact.kind is ArtifactKind.RUNTIME_PACKAGE:
            if (
                artifact.distribution is None
                or not artifact.digest
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.digest.casefold())
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Runtime Package必须同时固定版本、SHA-256和可信wheel",
                )
            if "artifact.runtime-package-install.v1" not in target_agent.capabilities:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="目标Edge Agent不支持受控Runtime Package安装",
                )
        elif artifact.kind is ArtifactKind.CALIBRATION_PROFILE:
            if (
                artifact.distribution is None
                or artifact.distribution.media_type != "application/json"
                or not artifact.digest
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.digest.casefold())
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="标定配置必须同时固定版本、SHA-256和可信JSON分发文件",
                )
            if "artifact.calibration-profile-install.v1" not in target_agent.capabilities:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="目标Edge Agent不支持受控标定配置安装",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "当前只支持Edge插件、固定摘要容器镜像、TensorRT Engine、"
                    "Runtime Package和标定配置"
                ),
            )
        if "artifact.installation.v1" not in target_agent.capabilities:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="目标Edge Agent不支持受控制品安装",
            )
        if (
            artifact.kind is ArtifactKind.EDGE_PLUGIN
            and "artifact.activation-health.v1" not in target_agent.capabilities
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="目标Edge Agent尚不支持插件重启后健康确认",
            )
        preparation = prepare_artifacts(
            ArtifactPreparationRequest(
                target_agent_id=installation_request.target_agent_id,
                artifact_ids=[installation_request.artifact_id],
            ),
            target_agent,
        )
        if preparation.ready_for_deployment and not installation_request.reinstall:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="目标节点已具备该制品；如需重装必须显式设置reinstall=true",
            )
        try:
            return request.app.state.artifact_installation_registry.submit(
                installation_request.target_agent_id,
                artifact,
            )
        except ArtifactInstallationConflict as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @application.get(
        "/api/v1/artifact-installations",
        response_model=list[ArtifactInstallationRecord],
        tags=["artifacts"],
    )
    def list_artifact_installations(request: Request) -> list[ArtifactInstallationRecord]:
        return request.app.state.artifact_installation_registry.list_records()

    @application.get(
        "/api/v1/agents/{agent_id}/artifact-installations",
        response_model=list[ArtifactInstallationRecord],
        tags=["agents", "artifacts"],
    )
    def list_agent_artifact_installations(
        agent_id: str,
        request: Request,
    ) -> list[ArtifactInstallationRecord]:
        try:
            request.app.state.agent_registry.get_agent(agent_id)
        except AgentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标Edge节点不存在",
            ) from error
        return request.app.state.artifact_installation_registry.list_active_for_agent(agent_id)

    @application.post(
        "/api/v1/artifact-installations/{operation_id}/status",
        response_model=ArtifactInstallationRecord,
        tags=["artifacts"],
    )
    def update_artifact_installation(
        operation_id: str,
        installation_update: ArtifactInstallationUpdate,
        request: Request,
    ) -> ArtifactInstallationRecord:
        try:
            return request.app.state.artifact_installation_registry.update(
                operation_id,
                installation_update,
            )
        except ArtifactInstallationNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="制品安装事务不存在",
            ) from error
        except InvalidArtifactInstallationTransition as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @application.post(
        "/api/v1/workflows/validate",
        tags=["workflows"],
    )
    def validate_workflow_definition(workflow: WorkflowDefinition) -> CompatibilityReport:
        return validate_workflow(workflow)

    @application.post(
        "/api/v1/deployment-plans/compile",
        response_model=DeploymentCompilation,
        tags=["deployments"],
    )
    def compile_deployment_plan(
        deployment_request: DeploymentPlanRequest,
        request: Request,
    ) -> DeploymentCompilation:
        try:
            target_agent = request.app.state.agent_registry.get_agent(
                deployment_request.target_agent_id
            )
        except AgentNotFoundError:
            target_agent = None
        return compile_deployment(deployment_request, target_agent)

    @application.post(
        "/api/v1/deployments",
        response_model=DeploymentCompilation,
        tags=["deployments"],
    )
    def submit_deployment(
        deployment_request: DeploymentPlanRequest,
        request: Request,
    ) -> DeploymentCompilation:
        try:
            target_agent = request.app.state.agent_registry.get_agent(
                deployment_request.target_agent_id
            )
        except AgentNotFoundError:
            target_agent = None
        compilation = compile_deployment(deployment_request, target_agent)
        if compilation.plan is not None:
            try:
                request.app.state.deployment_registry.submit(compilation.plan)
            except DeploymentResourceConflict as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(error),
                ) from error
        return compilation

    @application.get(
        "/api/v1/deployments",
        response_model=list[DeploymentRecord],
        tags=["deployments"],
    )
    def list_deployments(request: Request) -> list[DeploymentRecord]:
        return request.app.state.deployment_registry.list_records()

    @application.post(
        "/api/v1/deployments/{plan_id}/preview",
        response_model=DeploymentPreviewMetadata,
        tags=["deployments"],
    )
    async def upload_deployment_preview(
        plan_id: str, request: Request
    ) -> DeploymentPreviewMetadata:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type not in {"image/jpeg", "image/png"}:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="预览只允许image/jpeg或image/png",
            )
        image = await request.body()
        valid_jpeg = (
            content_type == "image/jpeg"
            and image.startswith(b"\xff\xd8")
            and image.endswith(b"\xff\xd9")
        )
        valid_png = (
            content_type == "image/png"
            and image.startswith(b"\x89PNG\r\n\x1a\n")
            and image.endswith(b"IEND\xaeB`\x82")
        )
        if len(image) < 4 or len(image) > 256 * 1024 or not (valid_jpeg or valid_png):
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="预览必须是4到262144字节的完整JPEG或PNG",
            )
        try:
            return request.app.state.deployment_registry.update_preview(
                plan_id, image, content_type
            )
        except DeploymentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="部署计划不存在",
            ) from error
        except InvalidDeploymentTransition as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @application.get(
        "/api/v1/deployments/{plan_id}/preview",
        response_class=Response,
        tags=["deployments"],
    )
    def get_deployment_preview(plan_id: str, request: Request) -> Response:
        try:
            image, content_type = request.app.state.deployment_registry.get_preview(plan_id)
        except DeploymentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="部署预览不存在",
            ) from error
        return Response(
            content=image,
            media_type=content_type,
            headers={"Cache-Control": "no-store"},
        )

    @application.post(
        "/api/v1/deployments/{plan_id}/live-preview-session",
        response_model=DeploymentLivePreviewSession,
        tags=["deployments"],
    )
    def start_live_preview_session(
        plan_id: str, request: Request
    ) -> DeploymentLivePreviewSession:
        try:
            return request.app.state.deployment_registry.start_live_preview(plan_id)
        except DeploymentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="部署计划不存在",
            ) from error
        except InvalidDeploymentTransition as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @application.delete(
        "/api/v1/deployments/{plan_id}/live-preview-session",
        response_model=DeploymentLivePreviewSession,
        tags=["deployments"],
    )
    def stop_live_preview_session(
        plan_id: str, request: Request
    ) -> DeploymentLivePreviewSession:
        try:
            return request.app.state.deployment_registry.stop_live_preview(plan_id)
        except DeploymentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="部署计划不存在",
            ) from error

    @application.post(
        "/api/v1/deployments/{plan_id}/stop",
        response_model=DeploymentRecord,
        tags=["deployments"],
    )
    def stop_deployment(plan_id: str, request: Request) -> DeploymentRecord:
        try:
            return request.app.state.deployment_registry.request_stop(plan_id)
        except DeploymentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="部署计划不存在",
            ) from error
        except InvalidDeploymentTransition as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @application.post(
        "/api/v1/deployments/{plan_id}/rollback",
        response_model=DeploymentRecord,
        tags=["deployments"],
    )
    def rollback_deployment(plan_id: str, request: Request) -> DeploymentRecord:
        """复制历史部署快照，并在冲突Runtime安全停止后自动启动。"""

        try:
            source = request.app.state.deployment_registry.get_record(plan_id)
            request.app.state.agent_registry.get_agent(source.plan.target_agent_id)
            return request.app.state.deployment_registry.rollback(plan_id)
        except (DeploymentNotFoundError, AgentNotFoundError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="部署计划或目标Edge节点不存在",
            ) from error
        except InvalidDeploymentTransition as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @application.get(
        "/api/v1/agents/{agent_id}/deployments",
        response_model=list[DeploymentPlan],
        tags=["deployments"],
    )
    def list_agent_deployments(agent_id: str, request: Request) -> list[DeploymentPlan]:
        return request.app.state.deployment_registry.list_active_for_agent(agent_id)

    @application.post(
        "/api/v1/deployments/{plan_id}/status",
        response_model=DeploymentRecord,
        tags=["deployments"],
    )
    def update_deployment_status(
        plan_id: str,
        update: DeploymentStatusUpdate,
        request: Request,
    ) -> DeploymentRecord:
        try:
            return request.app.state.deployment_registry.update(plan_id, update)
        except DeploymentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="部署计划不存在",
            ) from error
        except InvalidDeploymentTransition as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

    @application.post(
        "/api/v1/agents/register",
        response_model=AgentRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["agents"],
    )
    def register_agent(registration: AgentRegistration, request: Request) -> AgentRecord:
        return request.app.state.agent_registry.register(registration)

    @application.post(
        "/api/v1/agents/{agent_id}/heartbeat",
        response_model=AgentRecord,
        tags=["agents"],
    )
    def heartbeat_agent(
        agent_id: str,
        heartbeat: AgentHeartbeat,
        request: Request,
    ) -> AgentRecord:
        try:
            return request.app.state.agent_registry.heartbeat(agent_id, heartbeat)
        except AgentNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Edge Agent 尚未注册",
            ) from error

    return application


app = create_app(control_store=ControlPlaneStore(default_control_store_path()))
