export type AgentStatus = "online" | "offline";

export interface AgentMetrics {
  uptime_s: number | null;
  load_average_1m: number | null;
  memory_total_bytes: number | null;
  memory_available_bytes: number | null;
  root_disk_total_bytes: number | null;
  root_disk_free_bytes: number | null;
  data_disk_total_bytes: number | null;
  data_disk_free_bytes: number | null;
  max_temperature_c: number | null;
}

export interface DeviceInstance {
  device_id: string;
  transport: "usb" | "can" | "serial" | "ethernet" | "simulation";
  name: string;
  manufacturer: string | null;
  vendor_id: string | null;
  product_id: string | null;
  serial: string | null;
  device_nodes: string[];
  metadata: Record<string, string>;
}

export type ArtifactKind =
  | "edge_plugin"
  | "container_image"
  | "model"
  | "tensorrt_engine"
  | "simulation_asset"
  | "runtime_package"
  | "calibration_profile";

export type ArtifactScope = "build_source" | "edge_runtime";

export interface InstalledArtifact {
  kind: ArtifactKind;
  locator: string;
  version: string | null;
  digest: string | null;
  size_bytes: number | null;
  artifact_id: string | null;
  install_path: string | null;
  active: boolean | null;
}

export interface ArtifactDefinition {
  artifact_id: string;
  name: string;
  kind: ArtifactKind;
  scope: ArtifactScope;
  version: string;
  locator: string;
  digest: string | null;
  architecture: string | null;
  source_ref: string;
  required_by: string[];
  distribution: {
    transport: "platform_api";
    download_path: string;
    file_name: string;
    media_type: string;
    size_bytes: number;
    digest: string;
  } | null;
  variants: Array<{
    variant_id: string;
    locator: string;
    digest: string | null;
    architecture: string | null;
    compatibility: {
      operating_system: string | null;
      architecture: string | null;
      required_capabilities: string[];
      metadata: Record<string, string>;
    };
    distribution: ArtifactDefinition["distribution"];
    priority: number;
  }>;
  selected_variant_id: string | null;
}

export interface ArtifactResolution {
  artifact_id: string;
  target_agent_id: string;
  compatible: boolean;
  selected_variant_id: string | null;
  artifact: ArtifactDefinition | null;
  reason: string;
  rejected_variants: Array<{ variant_id: string; reasons: string[] }>;
}

export type ArtifactPreparationState =
  | "ready"
  | "installable"
  | "manual_required"
  | "blocked"
  | "not_required";

export type ArtifactInstallStrategy =
  | "none"
  | "docker_pull"
  | "docker_import"
  | "install_wheel"
  | "copy_file"
  | "install_config"
  | "manual";

export interface ArtifactPreparationPlan {
  plan_id: string;
  target_agent_id: string;
  ready_for_deployment: boolean;
  requires_changes: boolean;
  execution_enabled: false;
  items: Array<{
    order: number;
    artifact: ArtifactDefinition;
    state: ArtifactPreparationState;
    strategy: ArtifactInstallStrategy;
    reason: string;
    verify_digest: boolean;
    rollback_required: boolean;
    installed: InstalledArtifact | null;
  }>;
  warnings: string[];
  created_at: string;
}

export type ArtifactInstallationStatus =
  | "pending"
  | "downloading"
  | "verifying"
  | "installing"
  | "activating"
  | "succeeded"
  | "failed"
  | "rolled_back";

export interface ArtifactInstallationRecord {
  plan: {
    operation_id: string;
    target_agent_id: string;
    artifact: ArtifactDefinition;
    created_at: string;
  };
  status: ArtifactInstallationStatus;
  message: string;
  updated_at: string;
}

export interface AgentRecord {
  agent_id: string;
  name: string;
  version: string;
  capabilities: string[];
  metadata: Record<string, unknown>;
  status: AgentStatus;
  registered_at: string;
  last_seen_at: string;
  heartbeat_sequence: number;
  metrics: AgentMetrics | null;
  devices: DeviceInstance[];
  installed_artifacts: InstalledArtifact[];
}

export type ProjectStatus = "active" | "archived";

export interface ProjectRecord {
  project_id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
}

export type AssetReadiness = "definition_only" | "validated";

export interface ComponentPort {
  name: string;
  data_type: string;
  required: boolean;
}

export interface ComponentParameter {
  key: string;
  label: string;
  value_type: "string" | "integer" | "number";
  default: string | number;
  required: boolean;
  visible: boolean;
  binding_source: "manual" | "target_hardware_devices";
  minimum: number | null;
  maximum: number | null;
  step: number | null;
}

export interface HardwareDefinition {
  hardware_id: string;
  version: string;
  name: string;
  vendor: string;
  category: string;
  readiness: AssetReadiness;
  discovery: {
    transport: string;
    vendor_ids: string[];
    product_name_contains: string[];
  };
  inputs: ComponentPort[];
  outputs: ComponentPort[];
  parameters: ComponentParameter[];
}

export interface AlgorithmDefinition {
  algorithm_id: string;
  version: string;
  name: string;
  category: string;
  readiness: AssetReadiness;
  inputs: ComponentPort[];
  outputs: ComponentPort[];
  artifact_formats: string[];
  parameters: ComponentParameter[];
}

export interface RecipeComponent {
  node_id: string;
  component_ref: string;
  component_version: string;
}

export interface RecipeParameter extends ComponentParameter {
  node_id: string;
}

export interface WorkflowRecipe {
  recipe_id: string;
  version: string;
  name: string;
  components: RecipeComponent[];
  links: WorkflowDefinition["links"];
  parameters: RecipeParameter[];
  required_capabilities: string[];
  required_artifact_ids: string[];
}

export type DeploymentStatus =
  | "queued"
  | "pending"
  | "starting"
  | "running"
  | "stopping"
  | "stopped"
  | "failed";

export interface WorkflowNode {
  node_id: string;
  component_ref: string;
  component_version: string;
  parameters: Record<string, unknown>;
  position?: { x: number; y: number } | null;
}

export interface WorkflowDefinition {
  workflow_id: string;
  version: string;
  name: string;
  nodes: WorkflowNode[];
  links: Array<{
    source: { node_id: string; port: string };
    target: { node_id: string; port: string };
  }>;
}

export interface WorkflowRevisionRecord {
  project_id: string;
  workflow_id: string;
  revision: number;
  workflow: WorkflowDefinition;
  created_at: string;
}

export interface DeploymentRequest {
  target_agent_id: string;
  workflow: WorkflowDefinition;
}

export interface CompatibilityIssue {
  severity: "error" | "warning";
  code: string;
  message: string;
  node_id: string | null;
}

export interface CompatibilityReport {
  compatible: boolean;
  issues: CompatibilityIssue[];
}

export interface DeploymentPlan {
  plan_id: string;
  target_agent_id: string;
  workflow: WorkflowDefinition;
  project_id: string | null;
  workflow_revision: number | null;
  components: Array<{
    node_id: string;
    component_ref: string;
    component_version: string;
    kind: "hardware" | "algorithm";
  }>;
  artifacts: Array<{
    artifact_id: string;
    kind: ArtifactKind;
    version: string;
    locator: string;
    digest: string | null;
    architecture: string | null;
  }>;
  rollback_of_plan_id: string | null;
  supersedes_plan_ids: string[];
  desired_state: "running" | "stopped";
  created_at: string;
}

export interface DeploymentCompilation {
  report: {
    compatible: boolean;
    issues: CompatibilityIssue[];
  };
  plan: DeploymentPlan | null;
}

export type AcceptanceVerdict = "passed" | "warning" | "failed" | "incomplete";

export interface DeploymentAcceptanceCheck {
  check_id: string;
  label: string;
  status: "passed" | "warning" | "failed" | "not_applicable";
  actual: number | string | null;
  expected: string;
  unit: string | null;
  detail: string;
}

export interface DeploymentAcceptanceReport {
  schema_version: "runtime-acceptance.v1";
  attempt: number;
  verdict: AcceptanceVerdict;
  summary: string;
  generated_at: string;
  checks: DeploymentAcceptanceCheck[];
  evidence_metrics: Record<string, number>;
}

export interface DeploymentEvent {
  attempt: number;
  event_type: "submitted" | "restarted" | "rollback_created" | "status_changed" | "stop_requested";
  status: DeploymentStatus;
  message: string;
  occurred_at: string;
}

export interface DeploymentRecord {
  plan: DeploymentPlan;
  status: DeploymentStatus;
  message: string;
  metrics: Record<string, number>;
  attempt: number;
  events: DeploymentEvent[];
  acceptance_reports: DeploymentAcceptanceReport[];
  updated_at: string;
  preview_updated_at: string | null;
  preview_content_type: "image/jpeg" | "image/png" | null;
}

export interface DeploymentLivePreviewSession {
  plan_id: string;
  active: boolean;
  requested_fps: number;
  expires_at: string | null;
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchAgents(signal?: AbortSignal): Promise<AgentRecord[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/agents`, { signal });
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<AgentRecord[]>;
}

async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, { signal });
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function apiError(response: Response): Promise<Error> {
  try {
    const payload = await response.json() as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return new Error(payload.detail);
    }
  } catch {
    // 非JSON错误响应回退到状态码，避免遮蔽原始失败。
  }
  return new Error(`Platform API 返回 ${response.status}`);
}

export function fetchHardwareCatalog(signal?: AbortSignal): Promise<HardwareDefinition[]> {
  return fetchJson<HardwareDefinition[]>("/api/v1/catalog/hardware", signal);
}

export function fetchAlgorithmCatalog(signal?: AbortSignal): Promise<AlgorithmDefinition[]> {
  return fetchJson<AlgorithmDefinition[]>("/api/v1/catalog/algorithms", signal);
}

export function fetchWorkflowRecipes(signal?: AbortSignal): Promise<WorkflowRecipe[]> {
  return fetchJson<WorkflowRecipe[]>("/api/v1/catalog/workflow-recipes", signal);
}

export function fetchArtifactCatalog(signal?: AbortSignal): Promise<ArtifactDefinition[]> {
  return fetchJson<ArtifactDefinition[]>("/api/v1/catalog/artifacts", signal);
}

export function fetchArtifactResolutions(
  agentId: string,
  signal?: AbortSignal,
): Promise<ArtifactResolution[]> {
  return fetchJson<ArtifactResolution[]>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/artifact-resolutions`,
    signal,
  );
}

export async function previewArtifactPreparation(request: {
  target_agent_id: string;
  artifact_ids: string[];
}): Promise<ArtifactPreparationPlan> {
  const response = await fetch(`${apiBaseUrl}/api/v1/artifact-preparation-plans/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return response.json() as Promise<ArtifactPreparationPlan>;
}

export async function createArtifactInstallation(request: {
  target_agent_id: string;
  artifact_id: string;
  reinstall?: boolean;
}): Promise<ArtifactInstallationRecord> {
  const response = await fetch(`${apiBaseUrl}/api/v1/artifact-installations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return response.json() as Promise<ArtifactInstallationRecord>;
}

export function fetchArtifactInstallations(
  signal?: AbortSignal,
): Promise<ArtifactInstallationRecord[]> {
  return fetchJson<ArtifactInstallationRecord[]>("/api/v1/artifact-installations", signal);
}

export function fetchDeployments(signal?: AbortSignal): Promise<DeploymentRecord[]> {
  return fetchJson<DeploymentRecord[]>("/api/v1/deployments", signal);
}

export function fetchProjects(signal?: AbortSignal): Promise<ProjectRecord[]> {
  return fetchJson<ProjectRecord[]>("/api/v1/projects", signal);
}

export function fetchWorkflowRevisions(
  projectId: string,
  signal?: AbortSignal,
): Promise<WorkflowRevisionRecord[]> {
  return fetchJson<WorkflowRevisionRecord[]>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/workflows`,
    signal,
  );
}

export async function createProject(project: {
  name: string;
  description: string;
}): Promise<ProjectRecord> {
  const response = await fetch(`${apiBaseUrl}/api/v1/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(project),
  });
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<ProjectRecord>;
}

export async function saveWorkflowRevision(
  projectId: string,
  workflow: WorkflowDefinition,
): Promise<WorkflowRevisionRecord> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/projects/${encodeURIComponent(projectId)}/workflows`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(workflow),
    },
  );
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<WorkflowRevisionRecord>;
}

export async function validateWorkflow(
  workflow: WorkflowDefinition,
): Promise<CompatibilityReport> {
  const response = await fetch(`${apiBaseUrl}/api/v1/workflows/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(workflow),
  });
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<CompatibilityReport>;
}

export async function compileDeploymentPlan(
  deployment: DeploymentRequest,
): Promise<DeploymentCompilation> {
  const response = await fetch(`${apiBaseUrl}/api/v1/deployment-plans/compile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(deployment),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return response.json() as Promise<DeploymentCompilation>;
}

export async function submitRevisionDeployment(
  revision: WorkflowRevisionRecord,
  targetAgentId: string,
): Promise<DeploymentCompilation> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/projects/${encodeURIComponent(revision.project_id)}`
      + `/workflows/${encodeURIComponent(revision.workflow_id)}`
      + `/revisions/${revision.revision}/deployments`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_agent_id: targetAgentId }),
    },
  );
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<DeploymentCompilation>;
}

export function deploymentPreviewUrl(planId: string, updatedAt: string): string {
  const query = new URLSearchParams({ updated_at: updatedAt });
  return `${apiBaseUrl}/api/v1/deployments/${encodeURIComponent(planId)}/preview?${query}`;
}

export async function startDeploymentLivePreview(
  planId: string,
): Promise<DeploymentLivePreviewSession> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/deployments/${encodeURIComponent(planId)}/live-preview-session`,
    { method: "POST" },
  );
  if (!response.ok) {
    throw new Error(`实时监控会话启动失败：Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<DeploymentLivePreviewSession>;
}

export async function stopDeploymentLivePreview(
  planId: string,
): Promise<DeploymentLivePreviewSession> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/deployments/${encodeURIComponent(planId)}/live-preview-session`,
    { method: "DELETE" },
  );
  if (response.status === 404) {
    return { plan_id: planId, active: false, requested_fps: 5, expires_at: null };
  }
  if (!response.ok) {
    throw new Error(`实时监控会话关闭失败：Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<DeploymentLivePreviewSession>;
}

export async function submitDeployment(
  deployment: DeploymentRequest,
): Promise<DeploymentCompilation> {
  const response = await fetch(`${apiBaseUrl}/api/v1/deployments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(deployment),
  });
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<DeploymentCompilation>;
}

export async function stopDeployment(planId: string): Promise<DeploymentRecord> {
  const response = await fetch(`${apiBaseUrl}/api/v1/deployments/${planId}/stop`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Platform API 返回 ${response.status}`);
  }
  return response.json() as Promise<DeploymentRecord>;
}

export async function rollbackDeployment(planId: string): Promise<DeploymentRecord> {
  const response = await fetch(`${apiBaseUrl}/api/v1/deployments/${planId}/rollback`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return response.json() as Promise<DeploymentRecord>;
}

