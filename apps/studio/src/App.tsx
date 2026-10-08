import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  AgentRecord,
  AlgorithmDefinition,
  ArtifactDefinition,
  ArtifactResolution,
  ArtifactInstallationRecord,
  ArtifactPreparationPlan,
  CompatibilityReport,
  compileDeploymentPlan,
  createArtifactInstallation,
  createProject,
  DeploymentAcceptanceCheck,
  DeploymentRecord,
  DeviceInstance,
  deploymentPreviewUrl,
  fetchAgents,
  fetchAlgorithmCatalog,
  fetchArtifactCatalog,
  fetchArtifactInstallations,
  fetchArtifactResolutions,
  fetchDeployments,
  fetchHardwareCatalog,
  fetchProjects,
  fetchWorkflowRevisions,
  fetchWorkflowRecipes,
  HardwareDefinition,
  ProjectRecord,
  previewArtifactPreparation,
  rollbackDeployment,
  saveWorkflowRevision,
  startDeploymentLivePreview,
  stopDeployment,
  stopDeploymentLivePreview,
  submitRevisionDeployment,
  WorkflowDefinition,
  WorkflowRecipe,
  WorkflowRevisionRecord,
} from "./api";
import { WorkflowGraphEditor } from "./WorkflowGraphEditor";

const refreshIntervalMs = 5_000;

type View = "overview" | "projects" | "components" | "artifacts" | "workflows";

type NavigationIconName = "overview" | "projects" | "components" | "artifacts" | "workflows";

const navigation: Array<{
  id: View;
  label: string;
  title: string;
  icon: NavigationIconName;
}> = [
  { id: "overview", label: "概览", title: "运行概览", icon: "overview" },
  { id: "projects", label: "项目", title: "项目管理", icon: "projects" },
  { id: "components", label: "组件", title: "组件仓库", icon: "components" },
  { id: "artifacts", label: "制品", title: "制品与版本", icon: "artifacts" },
  { id: "workflows", label: "流程", title: "工作流", icon: "workflows" },
];

function NavigationIcon({ name }: { name: NavigationIconName }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.8,
  };
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {name === "overview" && (
        <>
          <rect {...common} x="3" y="3" width="7" height="7" rx="2" />
          <rect {...common} x="14" y="3" width="7" height="7" rx="2" />
          <rect {...common} x="3" y="14" width="7" height="7" rx="2" />
          <rect {...common} x="14" y="14" width="7" height="7" rx="2" />
        </>
      )}
      {name === "projects" && (
        <path {...common} d="M3 7.5h7l2-2h9v13.5H3z" />
      )}
      {name === "components" && (
        <>
          <rect {...common} x="3" y="4" width="7" height="7" rx="1.5" />
          <rect {...common} x="14" y="4" width="7" height="7" rx="1.5" />
          <rect {...common} x="8.5" y="14" width="7" height="7" rx="1.5" />
        </>
      )}
      {name === "artifacts" && (
        <>
          <path {...common} d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5z" />
          <path {...common} d="m4 7.5 8 4.5 8-4.5M12 12v9" />
        </>
      )}
      {name === "workflows" && (
        <>
          <circle {...common} cx="5" cy="6" r="2.5" />
          <circle {...common} cx="19" cy="12" r="2.5" />
          <circle {...common} cx="5" cy="18" r="2.5" />
          <path {...common} d="M7.5 6h3.5a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3h-.5M7.5 18h3.5a3 3 0 0 0 3-3v0a3 3 0 0 1 3-3h0" />
        </>
      )}
    </svg>
  );
}

const moduleContent: Record<
  Exclude<View, "overview">,
  { description: string; items: Array<{ title: string; detail: string }> }
> = {
  projects: {
    description: "以项目为单位组织需求、设备、算法、工作流和部署记录。",
    items: [
      { title: "需求与方案", detail: "记录目标场景、能力拆解和验收标准。" },
      { title: "系统配置", detail: "关联硬件、算法组件及运行参数。" },
      { title: "部署记录", detail: "追踪版本、目标节点和验证结果。" },
    ],
  },
  components: {
    description: "将硬件、算法和适配器沉淀为带版本、接口及约束的可复用资产。",
    items: [
      { title: "硬件组件", detail: "相机、雷达、机械臂、底盘及控制器。" },
      { title: "算法组件", detail: "感知、定位、规划、控制和业务算法。" },
      { title: "运行适配器", detail: "驱动、ROS 2节点、容器和本地进程。" },
    ],
  },
  artifacts: {
    description: "固定镜像、插件、模型和Engine版本，并核对Edge节点安装状态。",
    items: [
      { title: "制品清单", detail: "记录类型、版本、定位符、摘要和目标架构。" },
      { title: "节点盘点", detail: "由Edge Agent只读上报本机已有制品。" },
      { title: "安装与回滚", detail: "显式授权后执行受控安装、重启健康确认与版本回退。" },
    ],
  },
  workflows: {
    description: "组合组件、配置数据流与任务逻辑，并部署到边缘节点运行。",
    items: [
      { title: "流程设计", detail: "搭建节点连接、条件分支和任务步骤。" },
      { title: "兼容检查", detail: "检查接口、版本、资源和部署目标。" },
      { title: "运行验证", detail: "观察执行状态、日志和故障信息。" },
    ],
  },
};

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number | null | undefined): string {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(unitIndex >= 3 ? 1 : 0)} ${units[unitIndex]}`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  return days > 0 ? `${days}天 ${hours}小时` : `${hours}小时`;
}

function compatibilityIssueText(
  issue: CompatibilityReport["issues"][number],
): string {
  const location = issue.node_id ? `${issue.node_id} · ` : "";
  return `[${issue.code}] ${location}${issue.message}`;
}

type PreviewDiagnostic = {
  brightness: number;
  darkPixelRatio: number;
  state: "dark" | "dim" | "normal";
};

function analyzePreviewImage(image: HTMLImageElement): PreviewDiagnostic | null {
  try {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 48;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let totalBrightness = 0;
    let darkPixels = 0;
    const pixelCount = pixels.length / 4;
    for (let index = 0; index < pixels.length; index += 4) {
      const brightness = (
        0.2126 * pixels[index]
        + 0.7152 * pixels[index + 1]
        + 0.0722 * pixels[index + 2]
      ) / 255;
      totalBrightness += brightness;
      if (brightness < 0.08) darkPixels += 1;
    }
    const brightness = totalBrightness / pixelCount;
    const darkPixelRatio = darkPixels / pixelCount;
    return {
      brightness,
      darkPixelRatio,
      state: brightness < 0.08 || darkPixelRatio > 0.92
        ? "dark"
        : brightness < 0.18
          ? "dim"
          : "normal",
    };
  } catch {
    return null;
  }
}

function DeploymentLiveMonitor({
  deployment,
  onClose,
}: {
  deployment: DeploymentRecord;
  onClose: () => void;
}) {
  const [requestedFps, setRequestedFps] = useState(5);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<{
    left: number;
    top: number;
    width: number;
    height: number;
  } | null>(null);
  const frameTimer = useRef<number | null>(null);
  const frameFailureCount = useRef(0);
  const initialFrameFitted = useRef(false);
  const panelRef = useRef<HTMLElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    offsetX: number;
    offsetY: number;
  } | null>(null);
  const resizeRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    width: number;
    height: number;
  } | null>(null);
  const planId = deployment.plan.plan_id;

  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const bounds = panel.getBoundingClientRect();
    setGeometry({
      left: Math.max(12, (window.innerWidth - bounds.width) / 2),
      top: Math.max(12, (window.innerHeight - bounds.height) / 2),
      width: bounds.width,
      height: bounds.height,
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const renew = async () => {
      try {
        const session = await startDeploymentLivePreview(planId);
        if (!cancelled) {
          setRequestedFps(session.requested_fps);
          setSessionError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setSessionError(reason instanceof Error ? reason.message : "无法开启实时监控");
        }
      }
    };
    void renew();
    const renewal = window.setInterval(() => void renew(), 5_000);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      cancelled = true;
      document.body.style.overflow = previousOverflow;
      window.clearInterval(renewal);
      window.removeEventListener("keydown", closeOnEscape);
      if (frameTimer.current !== null) window.clearTimeout(frameTimer.current);
      void stopDeploymentLivePreview(planId).catch(() => undefined);
    };
  }, [onClose, planId]);

  useEffect(() => {
    let cancelled = false;
    let sequence = 0;
    const requestNextFrame = (delay: number) => {
      if (frameTimer.current !== null) window.clearTimeout(frameTimer.current);
      frameTimer.current = window.setTimeout(() => {
        frameTimer.current = null;
        const nextUrl = deploymentPreviewUrl(
          planId,
          `${Date.now()}-live-${sequence += 1}`,
        );
        const nextFrame = new Image();
        nextFrame.decoding = "async";
        nextFrame.onload = () => {
          if (cancelled) return;
          const canvas = canvasRef.current;
          const context = canvas?.getContext("2d", { alpha: false });
          if (canvas && context) {
            if (
              canvas.width !== nextFrame.naturalWidth
              || canvas.height !== nextFrame.naturalHeight
            ) {
              canvas.width = nextFrame.naturalWidth;
              canvas.height = nextFrame.naturalHeight;
            }
            context.drawImage(nextFrame, 0, 0, canvas.width, canvas.height);
            if (!initialFrameFitted.current && panelRef.current && canvas.parentElement) {
              initialFrameFitted.current = true;
              const panelBounds = panelRef.current.getBoundingClientRect();
              const stageBounds = canvas.parentElement.getBoundingClientRect();
              const stageStyle = window.getComputedStyle(canvas.parentElement);
              const horizontalPadding = (
                Number.parseFloat(stageStyle.paddingLeft)
                + Number.parseFloat(stageStyle.paddingRight)
              );
              const verticalPadding = (
                Number.parseFloat(stageStyle.paddingTop)
                + Number.parseFloat(stageStyle.paddingBottom)
              );
              const chromeHeight = panelBounds.height - stageBounds.height;
              const frameAspect = nextFrame.naturalWidth / nextFrame.naturalHeight;
              const maximumPanelHeight = window.innerHeight - 16;
              const maximumFrameHeight = Math.max(
                1,
                maximumPanelHeight - chromeHeight - verticalPadding,
              );
              let frameWidth = Math.max(1, stageBounds.width - horizontalPadding);
              let frameHeight = frameWidth / frameAspect;
              if (frameHeight > maximumFrameHeight) {
                frameHeight = maximumFrameHeight;
                frameWidth = frameHeight * frameAspect;
              }
              const fittedWidth = Math.min(
                panelBounds.width,
                frameWidth + horizontalPadding,
              );
              const fittedHeight = Math.min(
                maximumPanelHeight,
                chromeHeight + frameHeight + verticalPadding,
              );
              setGeometry({
                left: Math.max(8, (window.innerWidth - fittedWidth) / 2),
                top: Math.max(8, (window.innerHeight - fittedHeight) / 2),
                width: fittedWidth,
                height: fittedHeight,
              });
            }
          }
          if (frameFailureCount.current >= 10) setFrameError(null);
          frameFailureCount.current = 0;
          requestNextFrame(100);
        };
        nextFrame.onerror = () => {
          if (cancelled) return;
          frameFailureCount.current += 1;
          if (frameFailureCount.current === 10) {
            setFrameError("实时画面连续获取失败，保留最后一帧并继续重试");
          }
          requestNextFrame(300);
        };
        nextFrame.src = nextUrl;
      }, delay);
    };
    requestNextFrame(100);
    return () => {
      cancelled = true;
      if (frameTimer.current !== null) {
        window.clearTimeout(frameTimer.current);
        frameTimer.current = null;
      }
    };
  }, [planId]);

  const startDragging = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest("button")) return;
    const panel = panelRef.current;
    if (!panel) return;
    const bounds = panel.getBoundingClientRect();
    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - bounds.left,
      offsetY: event.clientY - bounds.top,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const dragWindow = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    const panel = panelRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !panel) return;
    const bounds = panel.getBoundingClientRect();
    const left = Math.min(
      Math.max(8, event.clientX - drag.offsetX),
      Math.max(8, window.innerWidth - bounds.width - 8),
    );
    const top = Math.min(
      Math.max(8, event.clientY - drag.offsetY),
      Math.max(8, window.innerHeight - bounds.height - 8),
    );
    setGeometry((current) => current ? { ...current, left, top } : current);
  };

  const stopDragging = (event: ReactPointerEvent<HTMLElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const startResizing = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    const panel = panelRef.current;
    if (!panel) return;
    const bounds = panel.getBoundingClientRect();
    resizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      width: bounds.width,
      height: bounds.height,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const resizeWindow = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const resize = resizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) return;
    setGeometry((current) => {
      if (!current) return current;
      const minimumWidth = Math.min(560, window.innerWidth - 16);
      const minimumHeight = Math.min(380, window.innerHeight - 16);
      return {
        ...current,
        width: Math.min(
          Math.max(minimumWidth, resize.width + event.clientX - resize.startX),
          window.innerWidth - current.left - 8,
        ),
        height: Math.min(
          Math.max(minimumHeight, resize.height + event.clientY - resize.startY),
          window.innerHeight - current.top - 8,
        ),
      };
    });
  };

  const stopResizing = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (resizeRef.current?.pointerId !== event.pointerId) return;
    resizeRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return createPortal(
    <div
      className="live-monitor-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`${deployment.plan.workflow.name}实时监控`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={panelRef}
        className="live-monitor-panel"
        style={geometry ?? undefined}
      >
        <header
          onPointerDown={startDragging}
          onPointerMove={dragWindow}
          onPointerUp={stopDragging}
          onPointerCancel={stopDragging}
        >
          <div>
            <h2>{deployment.plan.workflow.name}</h2>
            <span>实时监控 · 上限约 {requestedFps} FPS · Esc关闭</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭实时监控">关闭</button>
        </header>
        {(sessionError || frameError) && (
          <div className="error-banner">{sessionError ?? frameError}</div>
        )}
        <div className="live-monitor-stage">
          <canvas
            ref={canvasRef}
            role="img"
            aria-label={`${deployment.plan.workflow.name}实时组合画面`}
          />
        </div>
        <footer>
          <span>目标节点：{deployment.plan.target_agent_id}</span>
          <span>网络或渲染变慢时自动降速，不积压旧帧</span>
        </footer>
        <button
          className="live-monitor-resize-handle"
          type="button"
          aria-label="调整实时监控窗口大小"
          onPointerDown={startResizing}
          onPointerMove={resizeWindow}
          onPointerUp={stopResizing}
          onPointerCancel={stopResizing}
        />
      </section>
    </div>,
    document.body,
  );
}

function DeploymentPreview({ deployment }: { deployment: DeploymentRecord }) {
  const [diagnostic, setDiagnostic] = useState<PreviewDiagnostic | null>(null);
  const [liveFrame, setLiveFrame] = useState(0);
  const [monitorOpen, setMonitorOpen] = useState(false);
  const liveFrameTimer = useRef<number | null>(null);
  const closeMonitor = useCallback(() => setMonitorOpen(false), []);
  const isSimulation = deployment.plan.workflow.nodes.some(
    (node) => node.component_ref === "hardware.arm.mujoco",
  );
  const isWindowsLivePreview = isSimulation && deployment.plan.workflow.nodes.some(
    (node) => node.component_ref === "hardware.camera.windows-webcam",
  );
  const updatedAt = deployment.preview_updated_at;
  const livePreviewActive = isWindowsLivePreview
    && deployment.status === "running"
    && Boolean(updatedAt);
  const previewUrl = updatedAt
    ? deploymentPreviewUrl(
        deployment.plan.plan_id,
        isWindowsLivePreview ? `${updatedAt}-${liveFrame}` : updatedAt,
      )
    : "";

  const scheduleLiveFrame = useCallback(() => {
    if (liveFrameTimer.current !== null) {
      window.clearTimeout(liveFrameTimer.current);
    }
    liveFrameTimer.current = window.setTimeout(() => {
      liveFrameTimer.current = null;
      setLiveFrame((frame) => frame + 1);
    }, 200);
  }, []);

  useEffect(() => setDiagnostic(null), [updatedAt]);
  useEffect(() => {
    if (!livePreviewActive) return;
    scheduleLiveFrame();
    return () => {
      if (liveFrameTimer.current !== null) {
        window.clearTimeout(liveFrameTimer.current);
        liveFrameTimer.current = null;
      }
    };
  }, [livePreviewActive, scheduleLiveFrame]);

  if (!updatedAt) return null;
  const isUnusable = diagnostic?.state === "dark";
  const isDim = diagnostic?.state === "dim";

  return (
    <>
      <figure className="deployment-preview">
        <img
          src={previewUrl}
          crossOrigin="anonymous"
          alt={`${deployment.plan.workflow.name}${isSimulation ? "仿真" : "实时检测"}预览`}
          onLoad={(event) => {
            setDiagnostic(isSimulation ? null : analyzePreviewImage(event.currentTarget));
            if (livePreviewActive) scheduleLiveFrame();
          }}
          onError={() => {
            if (livePreviewActive) scheduleLiveFrame();
          }}
        />
        <div className="deployment-preview-caption">
          <figcaption>
            {isWindowsLivePreview
              ? "Windows摄像头 + MuJoCo准实时组合预览 · 上限约 5 FPS"
              : `${isSimulation ? "MuJoCo端低频仿真预览" : "Jetson端低频标注预览"} · ${formatTime(updatedAt)}`}
          </figcaption>
          {livePreviewActive && (
            <button type="button" onClick={() => setMonitorOpen(true)}>打开实时监控</button>
          )}
        </div>
        {(isUnusable || isDim) && (
          <div className={`preview-diagnostic ${isUnusable ? "blocked" : "warning"}`}>
            <strong>{isUnusable ? "输入画面几乎全黑" : "输入画面偏暗"}</strong>
            <span>
              Runtime仍在采集和推理；请检查彩色镜头是否被遮挡、相机朝向与环境照明。
              {diagnostic && ` 平均亮度 ${Math.round(diagnostic.brightness * 255)}/255。`}
            </span>
          </div>
        )}
      </figure>
      {monitorOpen && (
        <DeploymentLiveMonitor deployment={deployment} onClose={closeMonitor} />
      )}
    </>
  );
}

const primaryDeploymentMetrics: Array<[string, string]> = [
  ["control_loop_actual_fps", "实际控制频率"],
  ["control_tick_p95_ms", "闭环 P95 耗时"],
  ["inference_mean_ms", "推理平均耗时"],
  ["preview_actual_fps", "实际预览帧率"],
  ["preview_age_latest_ms", "最新预览帧龄"],
  ["actuator_safe_stop", "执行器安全停止"],
];

function formatDeploymentMetric(name: string, value: number): string {
  if (name.endsWith("_ms")) return `${value.toFixed(1)} ms`;
  if (name.endsWith("_fps")) return `${value.toFixed(1)} FPS`;
  if (
    name.endsWith("_frames")
    || name.endsWith("_ticks")
    || name.endsWith("_misses")
    || name.endsWith("_dropped")
    || name.endsWith("_published")
  ) return Math.round(value).toString();
  return Number.isInteger(value) ? value.toString() : value.toFixed(3);
}

function DeploymentMetrics({ metrics }: { metrics: Record<string, number> }) {
  const entries = Object.entries(metrics);
  if (entries.length === 0) return null;
  const primaryNames = new Set(primaryDeploymentMetrics.map(([name]) => name));
  const primary = primaryDeploymentMetrics.flatMap(([name, label]) => (
    metrics[name] !== undefined ? [[name, label, metrics[name]] as const] : []
  ));
  const diagnostics = entries.filter(([name]) => !primaryNames.has(name));

  return (
    <section className="deployment-metrics" aria-label="运行指标">
      {primary.length > 0 && (
        <dl className="deployment-metrics-summary">
          {primary.map(([name, label, value]) => (
            <div key={name}><dt>{label}</dt><dd>{formatDeploymentMetric(name, value)}</dd></div>
          ))}
        </dl>
      )}
      {diagnostics.length > 0 && (
        <details className="deployment-diagnostics">
          <summary>查看完整运行指标（{entries.length}）</summary>
          <dl>
            {diagnostics.map(([name, value]) => (
              <div key={name}><dt>{name}</dt><dd>{formatDeploymentMetric(name, value)}</dd></div>
            ))}
          </dl>
        </details>
      )}
    </section>
  );
}

const acceptanceVerdictText = {
  passed: "通过",
  warning: "有告警",
  failed: "未通过",
  incomplete: "证据不足",
} as const;

const acceptanceCheckStatusText = {
  passed: "通过",
  warning: "告警",
  failed: "失败",
  not_applicable: "未触发",
} as const;

function formatAcceptanceActual(check: DeploymentAcceptanceCheck): string {
  if (check.actual == null) return "无数据";
  const value = typeof check.actual === "number"
    ? Number.isInteger(check.actual) ? check.actual.toString() : check.actual.toFixed(1)
    : check.actual;
  return check.unit ? `${value} ${check.unit}` : value;
}

function DeploymentEvidence({ deployment }: { deployment: DeploymentRecord }) {
  const reports = deployment.acceptance_reports ?? [];
  const events = deployment.events ?? [];
  const report = reports[reports.length - 1];
  if (!report && events.length === 0) return null;

  return (
    <section className="deployment-evidence" aria-label="运行验收与时间线">
      {report && (
        <div className={`acceptance-report ${report.verdict}`}>
          <div className="acceptance-report-heading">
            <div>
              <span className={`acceptance-verdict ${report.verdict}`}>
                {acceptanceVerdictText[report.verdict]}
              </span>
              <strong>第 {report.attempt} 次运行验收</strong>
            </div>
            <time dateTime={report.generated_at}>{formatTime(report.generated_at)}</time>
          </div>
          <p>{report.summary}</p>
          <details className="acceptance-checks">
            <summary>查看 {report.checks.length} 项验收证据</summary>
            <div className="acceptance-check-list">
              {report.checks.map((check) => (
                <article className={`acceptance-check ${check.status}`} key={check.check_id}>
                  <div>
                    <strong>{check.label}</strong>
                    <span>{acceptanceCheckStatusText[check.status]}</span>
                  </div>
                  <dl>
                    <div><dt>实测</dt><dd>{formatAcceptanceActual(check)}</dd></div>
                    <div><dt>要求</dt><dd>{check.expected}{check.unit ? ` ${check.unit}` : ""}</dd></div>
                  </dl>
                  {check.detail && <p>{check.detail}</p>}
                </article>
              ))}
            </div>
          </details>
        </div>
      )}
      {events.length > 0 && (
        <details className="deployment-timeline">
          <summary>查看运行时间线（{events.length}）</summary>
          <ol>
            {events.map((event, index) => (
              <li key={`${event.attempt}:${event.occurred_at}:${index}`}>
                <time dateTime={event.occurred_at}>{formatTime(event.occurred_at)}</time>
                <div>
                  <strong>第 {event.attempt} 次 · {deploymentStatusText[event.status]}</strong>
                  <span>{event.message || "状态已更新"}</span>
                </div>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

function metadataText(agent: AgentRecord, key: string): string {
  const value = agent.metadata[key];
  return typeof value === "string" && value ? value : "—";
}

function ModulePage({ view }: { view: Exclude<View, "overview"> }) {
  const content = moduleContent[view];

  return (
    <section className="module-page">
      <div className="module-intro">
        <h2>{content.description}</h2>
        <p>当前已完成页面与导航骨架，后续会按端到端切片逐步接入真实数据和操作能力。</p>
      </div>
      <div className="module-grid">
        {content.items.map((item, index) => (
          <article className="module-card" key={item.title}>
            <span className="module-index">0{index + 1}</span>
            <h3>{item.title}</h3>
            <p>{item.detail}</p>
            <span className="module-status">待接入</span>
          </article>
        ))}
      </div>
    </section>
  );
}

function ProjectsPage({
  projects,
  onCreated,
}: {
  projects: ProjectRecord[];
  onCreated: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function submitProject() {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setSubmitError("请输入项目名称");
      return;
    }
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      await createProject({ name: trimmedName, description: description.trim() });
      setName("");
      setDescription("");
      await onCreated();
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "创建项目失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="module-page">
      <div className="module-intro">
        <h2>从需求到部署的持久化工程空间</h2>
        <p>项目保存需求背景和版本化工作流；Agent在线状态与实时预览仍作为易失运行态独立管理。</p>
      </div>

      <div className="project-layout">
        <section className="project-create-card">
          <h2>创建项目</h2>
          <label className="field-label" htmlFor="project-name">项目名称</label>
          <input
            id="project-name"
            value={name}
            maxLength={128}
            placeholder="例如：SR101机械臂视觉抓取"
            onChange={(event) => setName(event.target.value)}
          />
          <label className="field-label" htmlFor="project-description">目标与范围</label>
          <textarea
            id="project-description"
            value={description}
            maxLength={2048}
            rows={5}
            placeholder="记录使用场景、硬件边界和验收目标"
            onChange={(event) => setDescription(event.target.value)}
          />
          {submitError && <div className="error-banner workflow-error">{submitError}</div>}
          <button
            className="primary-button"
            type="button"
            disabled={isSubmitting}
            onClick={() => void submitProject()}
          >
            {isSubmitting ? "正在创建…" : "创建项目"}
          </button>
        </section>

        <section className="project-list-panel">
          <div className="section-heading">
            <div><h2>项目列表</h2></div>
            <span className="device-count">{projects.length} 个</span>
          </div>
          {projects.length === 0 ? (
            <div className="empty-state compact-empty">
              <h3>还没有项目</h3>
              <p>先创建一个工程空间，下一步把工作流草稿保存到项目中。</p>
            </div>
          ) : (
            <div className="project-list">
              {projects.map((project) => (
                <article className="project-card" key={project.project_id}>
                  <div>
                    <span className={`status-pill ${project.status === "active" ? "online" : "offline"}`}>
                      {project.status === "active" ? "进行中" : "已归档"}
                    </span>
                    <h3>{project.name}</h3>
                    <p>{project.description || "尚未填写项目目标"}</p>
                  </div>
                  <details className="card-details">
                    <summary>查看项目信息</summary>
                    <dl>
                      <div><dt>项目 ID</dt><dd>{project.project_id}</dd></div>
                      <div><dt>更新时间</dt><dd>{formatTime(project.updated_at)}</dd></div>
                    </dl>
                  </details>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

function readinessText(readiness: "definition_only" | "validated"): string {
  return readiness === "validated" ? "已验证" : "已定义 · 待实机验证";
}

function deviceRecognitionText(device: DeviceInstance): string {
  return device.metadata.adapter_id ? "硬件插件识别" : "通用只读发现";
}

function ComponentsPage({
  hardware,
  algorithms,
  agents,
}: {
  hardware: HardwareDefinition[];
  algorithms: AlgorithmDefinition[];
  agents: AgentRecord[];
}) {
  const discoveredDevices = agents.flatMap((agent) =>
    agent.devices.map((device) => ({ agent, device })),
  );

  return (
    <section className="module-page">
      <div className="module-intro">
        <h2>资产定义与端侧实例分开管理</h2>
        <p>
          硬件库描述可复用产品能力，算法库描述类型化输入输出；实际运行前再绑定Jetson发现的具体设备和适配制品。
        </p>
      </div>

      <section className="library-section">
        <div className="section-heading">
          <div><h2>硬件库</h2></div>
        </div>
        <div className="asset-grid">
          {hardware.map((item) => (
            <article className="asset-card" key={item.hardware_id}>
              <span className="asset-kind">{item.category}</span>
              <h3>{item.name}</h3>
              <p>{item.vendor} · v{item.version}</p>
              <span className="module-status">{readinessText(item.readiness)}</span>
              <details className="card-details">
                <summary>查看接口与发现规则</summary>
                <dl>
                  <div><dt>输入</dt><dd>{item.inputs.map((port) => port.name).join(" · ") || "无"}</dd></div>
                  <div><dt>输出</dt><dd>{item.outputs.map((port) => port.name).join(" · ") || "无"}</dd></div>
                  <div><dt>设备匹配</dt><dd>USB {item.discovery.vendor_ids.join(", ")}</dd></div>
                </dl>
              </details>
            </article>
          ))}
        </div>
      </section>

      <section className="library-section">
        <div className="section-heading">
          <div><h2>算法库</h2></div>
        </div>
        <div className="asset-grid">
          {algorithms.map((item) => (
            <article className="asset-card" key={item.algorithm_id}>
              <span className="asset-kind">{item.category}</span>
              <h3>{item.name}</h3>
              <p>v{item.version} · {item.artifact_formats.join(" / ")}</p>
              <span className="module-status">{readinessText(item.readiness)}</span>
              <details className="card-details">
                <summary>查看接口定义</summary>
                <dl>
                  <div><dt>输入</dt><dd>{item.inputs.map((port) => port.data_type).join(" · ")}</dd></div>
                  <div><dt>输出</dt><dd>{item.outputs.map((port) => port.data_type).join(" · ")}</dd></div>
                </dl>
              </details>
            </article>
          ))}
        </div>
      </section>

      <section className="library-section">
        <div className="section-heading">
          <div><h2>端侧已发现设备</h2></div>
          <span className="device-count">{discoveredDevices.length} 个</span>
        </div>
        {discoveredDevices.length === 0 ? (
          <div className="empty-state">
            <h3>尚未收到设备清单</h3>
            <p>升级并重启Jetson Edge Agent后，USB设备会以只读方式显示在这里。</p>
          </div>
        ) : (
          <div className="asset-grid">
            {discoveredDevices.map(({ agent, device }) => (
              <article className="asset-card" key={`${agent.agent_id}-${device.device_id}`}>
                <span className="asset-kind">{device.transport.toUpperCase()} · {agent.name}</span>
                <h3>{device.name}</h3>
                <p>{device.manufacturer || "未知厂商"}</p>
                <span
                  className={`module-status${device.metadata.adapter_id ? " plugin-status" : ""}`}
                >
                  {deviceRecognitionText(device)}
                </span>
                <details className="card-details">
                  <summary>查看设备详情</summary>
                  <dl>
                    <div><dt>VID:PID</dt><dd>{device.vendor_id || "—"}:{device.product_id || "—"}</dd></div>
                    <div><dt>序列号</dt><dd>{device.serial || "—"}</dd></div>
                    <div><dt>速率</dt><dd>{device.metadata.speed_mbps ? `${device.metadata.speed_mbps} Mbps` : "—"}</dd></div>
                    {device.metadata.adapter_id ? (
                      <div><dt>适配器</dt><dd>{device.metadata.adapter_id}</dd></div>
                    ) : null}
                    {device.metadata.profile_id ? (
                      <div><dt>配置</dt><dd>{device.metadata.profile_id}</dd></div>
                    ) : null}
                  </dl>
                </details>
              </article>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}

const artifactKindText: Record<ArtifactDefinition["kind"], string> = {
  edge_plugin: "Edge插件",
  container_image: "容器镜像",
  model: "模型",
  tensorrt_engine: "TensorRT Engine",
  simulation_asset: "仿真资产",
  runtime_package: "Runtime包",
  calibration_profile: "标定配置",
};

function normalizedArtifactLocator(value: string): string {
  return value.trim().replaceAll("\\", "/").toLowerCase();
}

function isArtifactInstalled(artifact: ArtifactDefinition, agent: AgentRecord): boolean {
  const expected = normalizedArtifactLocator(artifact.locator);
  return agent.installed_artifacts.some((installed) => {
    if (installed.kind !== artifact.kind) return false;
    const actual = normalizedArtifactLocator(installed.locator);
    if (artifact.kind === "edge_plugin") {
      return actual.replaceAll("_", "-") === expected.replaceAll("_", "-")
        && (!installed.version || installed.version === artifact.version);
    }
    if (artifact.kind === "model") {
      if (actual !== expected && !actual.endsWith(`/${expected}`)) return false;
    } else if (actual !== expected) {
      return false;
    }
    return !artifact.digest || !installed.digest
      || artifact.digest.toLowerCase() === installed.digest.toLowerCase();
  });
}

function findInstalledArtifact(
  artifact: ArtifactDefinition,
  agent: AgentRecord,
) {
  const expected = normalizedArtifactLocator(artifact.locator);
  return agent.installed_artifacts.find((installed) => {
    if (installed.kind !== artifact.kind) return false;
    const actual = normalizedArtifactLocator(installed.locator);
    const locatorMatches = artifact.kind === "edge_plugin"
      ? actual.replaceAll("_", "-") === expected.replaceAll("_", "-")
      : actual === expected || (artifact.kind === "model" && actual.endsWith(`/${expected}`));
    return locatorMatches && (!installed.version || installed.version === artifact.version);
  });
}

function hasArtifactDigestMismatch(artifact: ArtifactDefinition, agent: AgentRecord): boolean {
  if (!artifact.digest) return false;
  const installed = findInstalledArtifact(artifact, agent);
  return Boolean(
    installed?.digest
    && installed.digest.toLowerCase() !== artifact.digest.toLowerCase(),
  );
}

type ArtifactReadinessState =
  | "ready"
  | "missing"
  | "mismatch"
  | "incompatible"
  | "unsupported"
  | "unresolved";

function normalizedArchitecture(value: string): string {
  const normalized = value.trim().toLowerCase();
  return {
    amd64: "x86_64",
    x64: "x86_64",
    arm64: "aarch64",
  }[normalized] ?? normalized;
}

function useArtifactResolutions(
  agentId: string,
  artifacts: ArtifactDefinition[],
): {
  resolutions: ArtifactResolution[];
  ready: boolean;
  error: string | null;
} {
  const [resolutions, setResolutions] = useState<ArtifactResolution[]>([]);
  const [resolvedAgentId, setResolvedAgentId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const artifactCatalogSignature = artifacts.map((artifact) => JSON.stringify({
    artifact_id: artifact.artifact_id,
    version: artifact.version,
    digest: artifact.digest,
    variants: artifact.variants.map((variant) => ({
      variant_id: variant.variant_id,
      digest: variant.digest,
      architecture: variant.architecture,
      compatibility: variant.compatibility,
    })),
  })).join("|");

  useEffect(() => {
    setResolutions([]);
    setResolvedAgentId("");
    setError(null);
    if (!agentId) return;
    const controller = new AbortController();
    void fetchArtifactResolutions(agentId, controller.signal)
      .then((items) => {
        setResolutions(items);
        setResolvedAgentId(agentId);
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "无法解析目标节点制品变体");
      });
    return () => controller.abort();
  }, [agentId, artifactCatalogSignature]);

  return {
    resolutions,
    ready: Boolean(agentId) && resolvedAgentId === agentId,
    error,
  };
}

function artifactReadinessState(
  artifact: ArtifactDefinition,
  agent: AgentRecord,
): ArtifactReadinessState {
  if (!agent.capabilities.includes("artifact.inventory.v1")) return "unsupported";
  const machine = typeof agent.metadata.machine === "string" ? agent.metadata.machine : "";
  if (artifact.architecture && machine
    && normalizedArchitecture(artifact.architecture) !== normalizedArchitecture(machine)) {
    return "incompatible";
  }
  if (hasArtifactDigestMismatch(artifact, agent)) return "mismatch";
  return isArtifactInstalled(artifact, agent) ? "ready" : "missing";
}

function ArtifactsPage({
  artifacts,
  installations,
  agents,
  onRefresh,
}: {
  artifacts: ArtifactDefinition[];
  installations: ArtifactInstallationRecord[];
  agents: AgentRecord[];
  onRefresh: () => Promise<void>;
}) {
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [submittingArtifactId, setSubmittingArtifactId] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const onlineAgents = agents.filter((agent) => agent.status === "online");
  const effectiveAgentId = agents.some((agent) => agent.agent_id === selectedAgentId)
    ? selectedAgentId
    : onlineAgents[0]?.agent_id ?? agents[0]?.agent_id ?? "";
  const selectedAgent = agents.find((agent) => agent.agent_id === effectiveAgentId);
  const {
    resolutions,
    error: artifactResolutionError,
  } = useArtifactResolutions(effectiveAgentId, artifacts);
  const resolvedArtifacts = new Map(
    resolutions
      .filter((item) => item.artifact)
      .map((item) => [item.artifact_id, item.artifact as ArtifactDefinition]),
  );
  const inventorySupported = selectedAgent?.capabilities.includes("artifact.inventory.v1") ?? false;
  const installedCount = selectedAgent && inventorySupported
    ? artifacts.filter((artifact) => {
      const resolved = resolvedArtifacts.get(artifact.artifact_id);
      return resolved ? isArtifactInstalled(resolved, selectedAgent) : false;
    }).length
    : 0;
  const runtimeArtifacts = artifacts.filter((artifact) => artifact.scope === "edge_runtime");
  const runtimeReadyCount = selectedAgent && inventorySupported
    ? runtimeArtifacts.filter((artifact) => {
      const resolved = resolvedArtifacts.get(artifact.artifact_id);
      return resolved ? isArtifactInstalled(resolved, selectedAgent) : false;
    }).length
    : 0;

  const installArtifact = async (artifact: ArtifactDefinition, reinstall: boolean) => {
    if (!selectedAgent) return;
    if (reinstall && !window.confirm(
      `确定要在 ${selectedAgent.name} 上重新准备 ${artifact.name} 吗？完成后 Edge Agent 会自动刷新制品盘点。`,
    )) return;
    setSubmittingArtifactId(artifact.artifact_id);
    setActionError(null);
    try {
      await createArtifactInstallation({
        target_agent_id: selectedAgent.agent_id,
        artifact_id: artifact.artifact_id,
        reinstall,
      });
      await onRefresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "制品安装请求失败");
    } finally {
      setSubmittingArtifactId("");
    }
  };

  return (
    <section className="module-page">
      <div className="module-intro artifact-intro">
        <h2>部署前先确认目标节点真正具备所需制品</h2>
        <p>构建源保留在开发环境；只有标记为端侧运行的插件、镜像和Engine需要部署到目标节点。</p>
      </div>

      <section className="artifact-toolbar">
        <div>
          <label className="field-label" htmlFor="artifact-target-agent">目标节点</label>
          <select
            id="artifact-target-agent"
            value={effectiveAgentId}
            disabled={agents.length === 0}
            onChange={(event) => setSelectedAgentId(event.target.value)}
          >
            {agents.length === 0 ? (
              <option value="">暂无已注册节点</option>
            ) : agents.map((agent) => (
              <option key={agent.agent_id} value={agent.agent_id}>
                {agent.name} · {agent.status === "online" ? "在线" : "离线"}
              </option>
            ))}
          </select>
        </div>
        <div className="artifact-summary">
          <span><strong>{artifacts.length}</strong> 平台制品</span>
          <span><strong>{installedCount}</strong> 已安装</span>
          <span><strong>{runtimeReadyCount}/{runtimeArtifacts.length}</strong> 运行就绪</span>
        </div>
      </section>

      {selectedAgent && !inventorySupported && (
        <div className="artifact-notice">
          当前节点运行的是 Edge Agent v{selectedAgent.version}，尚未支持制品盘点；升级后会自动上报。
        </div>
      )}
      {(actionError ?? artifactResolutionError) && (
        <div className="error-banner artifact-action-error">
          {actionError ?? artifactResolutionError}
        </div>
      )}

      <section className="library-section">
        <div className="section-heading">
          <div><h2>平台制品清单</h2></div>
        </div>
        <div className="artifact-grid">
          {artifacts.map((artifact) => {
            const resolution = resolutions.find(
              (item) => item.artifact_id === artifact.artifact_id,
            );
            const effectiveArtifact = resolution?.artifact ?? artifact;
            const installedArtifact = selectedAgent
              ? findInstalledArtifact(effectiveArtifact, selectedAgent)
              : undefined;
            const installed = selectedAgent && inventorySupported
              ? isArtifactInstalled(effectiveArtifact, selectedAgent)
              : false;
            const state = resolution && !resolution.compatible
              ? "incompatible"
              : artifact.scope === "build_source" && !installed
              ? "build-source"
              : selectedAgent && hasArtifactDigestMismatch(effectiveArtifact, selectedAgent)
                ? "mismatch"
              : !selectedAgent || !inventorySupported
              ? "unknown"
              : installed ? "installed" : "missing";
            const latestInstallation = installations
              .filter((record) => (
                record.plan.target_agent_id === effectiveAgentId
                && record.plan.artifact.artifact_id === artifact.artifact_id
              ))
              .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0];
            const installationActive = latestInstallation
              ? ["pending", "downloading", "verifying", "installing", "activating"].includes(
                latestInstallation.status,
              )
              : false;
            const installationSupported = Boolean(
              selectedAgent
              && selectedAgent.status === "online"
              && selectedAgent.capabilities.includes("artifact.installation.v1")
              && (
                (
                  effectiveArtifact.kind === "edge_plugin"
                  && selectedAgent.capabilities.includes("artifact.activation-health.v1")
                  && effectiveArtifact.distribution
                )
                || (
                  effectiveArtifact.kind === "container_image"
                  && (
                    (
                      effectiveArtifact.distribution
                      && selectedAgent.capabilities.includes("artifact.container-import.v1")
                    )
                    || (
                      !effectiveArtifact.distribution
                      && selectedAgent.capabilities.includes("artifact.container-pull.v1")
                    )
                  )
                  && effectiveArtifact.digest?.startsWith("sha256:")
                )
                || (
                  effectiveArtifact.kind === "tensorrt_engine"
                  && Boolean(effectiveArtifact.distribution)
                  && effectiveArtifact.digest?.startsWith("sha256:")
                  && selectedAgent.capabilities.includes("artifact.file-install.v1")
                )
                || (
                  effectiveArtifact.kind === "runtime_package"
                  && Boolean(effectiveArtifact.distribution)
                  && effectiveArtifact.digest?.startsWith("sha256:")
                  && selectedAgent.capabilities.includes("artifact.runtime-package-install.v1")
                )
                || (
                  effectiveArtifact.kind === "calibration_profile"
                  && Boolean(effectiveArtifact.distribution)
                  && effectiveArtifact.digest?.startsWith("sha256:")
                  && selectedAgent.capabilities.includes(
                    "artifact.calibration-profile-install.v1",
                  )
                )
              ),
            );
            const reinstall = Boolean(installedArtifact);
            return (
              <article className="artifact-card" key={artifact.artifact_id}>
                <div className="artifact-card-heading">
                  <span className="asset-kind">{artifactKindText[artifact.kind]}</span>
                  <span className={`artifact-state ${state}`}>
                    {state === "installed"
                      ? "节点已有"
                      : state === "missing"
                        ? "节点缺失"
                        : state === "mismatch"
                          ? "摘要不匹配"
                          : state === "incompatible"
                            ? "节点不兼容"
                          : state === "build-source" ? "构建源" : "待上报"}
                  </span>
                </div>
                <h3>{artifact.name}</h3>
                <dl className="artifact-card-summary">
                  <div><dt>版本</dt><dd>{artifact.version}</dd></div>
                  <div><dt>用途</dt><dd>{artifact.scope === "edge_runtime" ? "端侧运行" : "开发/构建"}</dd></div>
                  <div>
                    <dt>目标变体</dt>
                    <dd>
                      {resolution?.selected_variant_id
                        ?? (resolution?.compatible ? "单发行" : resolution ? "无兼容变体" : "解析中")}
                    </dd>
                  </div>
                </dl>
                {resolution && !resolution.compatible && (
                  <p className="artifact-resolution-reason">{resolution.reason}</p>
                )}
                {latestInstallation && (
                  <div className={`artifact-operation ${latestInstallation.status}`}>
                    <strong>{artifactInstallationStatusText[latestInstallation.status]}</strong>
                    <span>{latestInstallation.message || `更新于 ${formatTime(latestInstallation.updated_at)}`}</span>
                  </div>
                )}
                <details className="card-details artifact-technical-details">
                  <summary>查看制品技术信息</summary>
                  <dl>
                    <div><dt>制品 ID</dt><dd>{artifact.artifact_id}</dd></div>
                    {artifact.variants.length > 0 && (
                      <div><dt>发行变体</dt><dd>{artifact.variants.length} 个</dd></div>
                    )}
                    <div><dt>定位符</dt><dd>{effectiveArtifact.locator}</dd></div>
                    <div><dt>架构</dt><dd>{effectiveArtifact.architecture ?? "通用/未限制"}</dd></div>
                    <div><dt>摘要</dt><dd>{effectiveArtifact.digest ?? "清单尚未固定摘要"}</dd></div>
                    <div><dt>来源</dt><dd>{artifact.source_ref}</dd></div>
                    <div><dt>运行契约</dt><dd>{artifact.required_by.length > 0 ? artifact.required_by.join("、") : "未被运行配方直接要求"}</dd></div>
                    {installedArtifact?.digest && (
                      <div><dt>节点摘要</dt><dd>{installedArtifact.digest}</dd></div>
                    )}
                    {installedArtifact?.install_path && (
                      <div><dt>安装路径</dt><dd>{installedArtifact.install_path}</dd></div>
                    )}
                    {installedArtifact?.active != null && (
                      <div><dt>激活状态</dt><dd>{installedArtifact.active ? "已激活" : "未激活"}</dd></div>
                    )}
                  </dl>
                </details>
                {installationSupported || installationActive ? (
                  <button
                    className="artifact-install-button"
                    type="button"
                    disabled={
                      !installationSupported
                      || installationActive
                      || submittingArtifactId === artifact.artifact_id
                    }
                    onClick={() => void installArtifact(artifact, reinstall)}
                  >
                    {installationActive
                      ? artifactInstallationStatusText[latestInstallation!.status]
                      : submittingArtifactId === artifact.artifact_id
                        ? "正在提交…"
                        : reinstall ? "重新准备" : "准备到节点"}
                  </button>
                ) : (
                  <p className="artifact-manual-note">
                    {artifact.kind === "container_image" && !artifact.digest
                      ? "Catalog尚未固定镜像摘要，出于安全原因保持只读盘点。"
                      : "当前类型或节点能力尚未开放自动准备。"}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      </section>
    </section>
  );
}

const artifactInstallationStatusText: Record<ArtifactInstallationRecord["status"], string> = {
  pending: "等待节点领取",
  downloading: "正在下载",
  verifying: "正在校验",
  installing: "正在原子安装",
  activating: "等待重启健康确认",
  succeeded: "安装成功",
  failed: "安装失败",
  rolled_back: "失败并已回滚",
};

const deploymentStatusText: Record<DeploymentRecord["status"], string> = {
  queued: "等待安全回退",
  pending: "等待端侧领取",
  starting: "正在启动",
  running: "运行中",
  stopping: "正在停止",
  stopped: "已停止",
  failed: "运行失败",
};

function displayParameterValue(value: unknown): string {
  if (value == null || value === "") return "未设置";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function revisionDifferences(
  current: WorkflowRevisionRecord,
  previous: WorkflowRevisionRecord | undefined,
  recipes: WorkflowRecipe[],
): string[] {
  if (!previous) return [];
  const recipe = recipes.find((item) => item.recipe_id === current.workflow_id);
  const labels = new Map(
    (recipe?.parameters ?? []).map((parameter) => [
      `${parameter.node_id}.${parameter.key}`,
      parameter.label,
    ]),
  );
  const flatten = (workflow: WorkflowDefinition) => new Map(
    workflow.nodes.flatMap((node) => Object.entries(node.parameters).map(([key, value]) => [
      `${node.node_id}.${key}`,
      value,
    ] as const)),
  );
  const currentValues = flatten(current.workflow);
  const previousValues = flatten(previous.workflow);
  const keys = new Set([...currentValues.keys(), ...previousValues.keys()]);
  const changes: string[] = [];
  for (const key of [...keys].sort()) {
    const before = previousValues.get(key);
    const after = currentValues.get(key);
    if (JSON.stringify(before) === JSON.stringify(after)) continue;
    changes.push(
      `${labels.get(key) ?? key}：${displayParameterValue(before)} → ${displayParameterValue(after)}`,
    );
  }
  return changes;
}

function WorkflowsPage({
  agents,
  hardware,
  algorithms,
  artifacts,
  installations,
  recipes,
  deployments,
  projects,
  onRefresh,
}: {
  agents: AgentRecord[];
  hardware: HardwareDefinition[];
  algorithms: AlgorithmDefinition[];
  artifacts: ArtifactDefinition[];
  installations: ArtifactInstallationRecord[];
  recipes: WorkflowRecipe[];
  deployments: DeploymentRecord[];
  projects: ProjectRecord[];
  onRefresh: () => Promise<void>;
}) {
  const onlineAgents = agents.filter((agent) => agent.status === "online");
  const [selectedRecipeId, setSelectedRecipeId] = useState("workflow.realsense_yolox");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [targetAgentId, setTargetAgentId] = useState("");
  const [parameterValues, setParameterValues] = useState<Record<string, string | number>>({});
  const [graphNodes, setGraphNodes] = useState<WorkflowDefinition["nodes"]>([]);
  const [graphLinks, setGraphLinks] = useState<WorkflowDefinition["links"]>([]);
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState("");
  const [validationReport, setValidationReport] = useState<CompatibilityReport | null>(null);
  const [checkedPreflightKey, setCheckedPreflightKey] = useState("");
  const [isValidating, setIsValidating] = useState(false);
  const [revisions, setRevisions] = useState<WorkflowRevisionRecord[]>([]);
  const pendingRevisionRef = useRef<WorkflowRevisionRecord | null>(null);
  const [loadedRevision, setLoadedRevision] = useState<WorkflowRevisionRecord | null>(null);
  const [deploymentScope, setDeploymentScope] = useState<"project" | "all">("project");
  const [isLoadingRevisions, setIsLoadingRevisions] = useState(false);
  const [isSavingRevision, setIsSavingRevision] = useState(false);
  const [deployingRevision, setDeployingRevision] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isPreparingArtifacts, setIsPreparingArtifacts] = useState(false);
  const [artifactPreparationPlan, setArtifactPreparationPlan] = useState<ArtifactPreparationPlan | null>(null);
  const [artifactPreparationNotice, setArtifactPreparationNotice] = useState<string | null>(null);
  const [stoppingPlanId, setStoppingPlanId] = useState<string | null>(null);
  const [rollingBackPlanId, setRollingBackPlanId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const selectedRecipe = recipes.find((recipe) => recipe.recipe_id === selectedRecipeId)
    ?? recipes[0];
  const effectiveRecipeId = selectedRecipe?.recipe_id ?? "";
  const effectiveProjectId = projects.some(
    (project) => project.project_id === selectedProjectId,
  ) ? selectedProjectId : projects[0]?.project_id ?? "";

  useEffect(() => {
    setLoadedRevision(null);
    pendingRevisionRef.current = null;
    if (!effectiveProjectId) {
      setRevisions([]);
      return;
    }
    const controller = new AbortController();
    setIsLoadingRevisions(true);
    void fetchWorkflowRevisions(effectiveProjectId, controller.signal)
      .then(setRevisions)
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setSubmitError(reason instanceof Error ? reason.message : "无法读取工作流修订");
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoadingRevisions(false);
      });
    return () => controller.abort();
  }, [effectiveProjectId]);

  const componentParameters = (componentRef: string, componentVersion: string) => (
    hardware.find(
      (item) => item.hardware_id === componentRef && item.version === componentVersion,
    )?.parameters
    ?? algorithms.find(
      (item) => item.algorithm_id === componentRef && item.version === componentVersion,
    )?.parameters
    ?? []
  );

  const valuesFromWorkflow = (
    recipe: WorkflowRecipe,
    workflow?: WorkflowDefinition,
  ): Record<string, string | number> => {
    const nodes: WorkflowDefinition["nodes"] = workflow?.nodes ?? recipe.components.map((component) => ({
      ...component,
      parameters: {},
    }));
    return Object.fromEntries(nodes.flatMap((node) => (
      componentParameters(node.component_ref, node.component_version).map((parameter) => {
        const saved = node.parameters[parameter.key];
        return [
          `${node.node_id}.${parameter.key}`,
          typeof saved === "string" || typeof saved === "number"
            ? saved
            : parameter.default,
        ];
      })
    )));
  };

  const positionNodes = (nodes: WorkflowDefinition["nodes"]): WorkflowDefinition["nodes"] =>
    nodes.map((node, index) => ({
      ...node,
      position: node.position ?? { x: 80 + index * 260, y: 90 },
    }));

  useEffect(() => {
    if (!selectedRecipe) return;
    const pending = pendingRevisionRef.current;
    if (pending?.workflow_id === selectedRecipe.recipe_id) {
      setParameterValues(valuesFromWorkflow(selectedRecipe, pending.workflow));
      const restoredNodes = positionNodes(pending.workflow.nodes);
      setGraphNodes(restoredNodes);
      setGraphLinks(pending.workflow.links);
      setSelectedGraphNodeId(restoredNodes[0]?.node_id ?? "");
      setValidationReport(null);
      pendingRevisionRef.current = null;
      return;
    }
    setParameterValues(valuesFromWorkflow(selectedRecipe));
    const templateNodes = positionNodes(selectedRecipe.components.map((component) => ({
      node_id: component.node_id,
      component_ref: component.component_ref,
      component_version: component.component_version,
      parameters: {},
    })));
    setGraphNodes(templateNodes);
    setGraphLinks(selectedRecipe.links);
    setSelectedGraphNodeId(templateNodes[0]?.node_id ?? "");
    setValidationReport(null);
  }, [selectedRecipe?.recipe_id]);

  const graphComponentRefs = graphNodes
    .map((node) => `${node.component_ref}@${node.component_version}`)
    .sort();
  const matchedRecipe = recipes.find((recipe) => (
    recipe.components
      .map((component) => `${component.component_ref}@${component.component_version}`)
      .sort()
      .join("|")
      === graphComponentRefs.join("|")
  ));
  const deploymentRecipe = matchedRecipe;
  const requiredArtifacts = (deploymentRecipe?.required_artifact_ids ?? [])
    .map((artifactId) => artifacts.find((artifact) => artifact.artifact_id === artifactId))
    .filter((artifact): artifact is ArtifactDefinition => artifact != null);
  const agentSupportsDeployment = (agent: AgentRecord) => Boolean(deploymentRecipe)
    && (deploymentRecipe?.required_capabilities ?? []).every(
      (capability) => agent.capabilities.includes(capability),
    );
  const compatibleAgent = onlineAgents.find(agentSupportsDeployment);
  const requestedAgent = onlineAgents.find((agent) => agent.agent_id === targetAgentId);
  const effectiveAgentId = requestedAgent?.agent_id
    ?? compatibleAgent?.agent_id
    ?? onlineAgents[0]?.agent_id
    ?? "";
  const effectiveAgent = onlineAgents.find((agent) => agent.agent_id === effectiveAgentId);
  const {
    resolutions: deploymentArtifactResolutions,
    ready: artifactResolutionsReady,
    error: artifactResolutionError,
  } = useArtifactResolutions(effectiveAgentId, artifacts);

  useEffect(() => {
    if (!deploymentRecipe || !compatibleAgent) return;
    if (requestedAgent && agentSupportsDeployment(requestedAgent)) return;
    setTargetAgentId(compatibleAgent.agent_id);
  }, [deploymentRecipe?.recipe_id, compatibleAgent?.agent_id]);

  const artifactResolutionById = new Map(
    deploymentArtifactResolutions.map((item) => [item.artifact_id, item]),
  );
  const requiredArtifactReadiness = effectiveAgent && artifactResolutionsReady
    ? requiredArtifacts.map((catalogArtifact) => {
        const resolution = artifactResolutionById.get(catalogArtifact.artifact_id);
        if (!resolution) {
          return { artifact: catalogArtifact, state: "unresolved" as const };
        }
        if (!resolution.compatible || !resolution.artifact) {
          return { artifact: catalogArtifact, state: "incompatible" as const };
        }
        return {
          artifact: resolution.artifact,
          state: artifactReadinessState(resolution.artifact, effectiveAgent),
        };
      })
    : [];
  const missingCapabilities = deploymentRecipe && effectiveAgent
    ? deploymentRecipe.required_capabilities.filter(
        (capability) => !effectiveAgent.capabilities.includes(capability),
      )
    : [];
  const targetReadinessReady = Boolean(effectiveAgent && deploymentRecipe)
    && artifactResolutionsReady
    && missingCapabilities.length === 0
    && requiredArtifactReadiness.every((item) => item.state === "ready")
    && requiredArtifacts.length === (deploymentRecipe?.required_artifact_ids.length ?? 0);
  const preflightKey = JSON.stringify({
    target_agent_id: effectiveAgentId,
    recipe_id: deploymentRecipe?.recipe_id ?? null,
    nodes: graphNodes.map((node) => ({
      node_id: node.node_id,
      component_ref: node.component_ref,
      component_version: node.component_version,
      parameters: node.parameters,
    })),
    links: graphLinks,
    parameters: parameterValues,
    target_capabilities: [...(effectiveAgent?.capabilities ?? [])].sort(),
    target_devices: [...(effectiveAgent?.devices ?? [])]
      .sort((left, right) => left.device_id.localeCompare(right.device_id))
      .map((device) => ({
      device_id: device.device_id,
      serial: device.serial,
      metadata: device.metadata,
    })),
    installed_artifacts: [...(effectiveAgent?.installed_artifacts ?? [])]
      .sort((left, right) => (
        (left.artifact_id ?? left.locator).localeCompare(right.artifact_id ?? right.locator)
      ))
      .map((artifact) => ({
        artifact_id: artifact.artifact_id,
        locator: artifact.locator,
        digest: artifact.digest,
        active: artifact.active,
      })),
  });
  const preflightIsCurrent = checkedPreflightKey === preflightKey;
  const currentValidationReport = preflightIsCurrent ? validationReport : null;
  const deploymentReady = targetReadinessReady
    && currentValidationReport?.compatible === true;
  const artifactsNeedPreparation = requiredArtifactReadiness.some(
    (item) => item.state !== "ready",
  );
  const workflowGate = (() => {
    if (isValidating) {
      return {
        stage: "checking",
        title: "正在执行统一部署预检",
        detail: "Platform API正在检查画布、目标节点、设备、能力和制品。",
      };
    }
    if (!matchedRecipe) {
      return {
        stage: "blocked",
        title: "当前画布没有可执行Runtime配方",
        detail: "可以保存设计修订，但需要恢复为Catalog中已验证的组件组合后才能部署。",
      };
    }
    if (!effectiveAgent) {
      return {
        stage: "blocked",
        title: "尚未选择在线目标节点",
        detail: "选择一个在线Edge节点后再执行部署预检。",
      };
    }
    if (artifactResolutionError) {
      return {
        stage: "blocked",
        title: "目标节点制品解析失败",
        detail: "无法取得目标平台的制品变体，请检查Platform API后重试。",
      };
    }
    if (!artifactResolutionsReady) {
      return {
        stage: "resolving",
        title: "正在解析目标节点的制品变体",
        detail: "Platform API正在根据操作系统、架构和能力选择发行变体。",
      };
    }
    if (!preflightIsCurrent) {
      return {
        stage: "pending",
        title: "当前配置尚未执行统一预检",
        detail: "点击“检查工作流与部署条件”，检查通过后才可保存并启动。",
      };
    }
    if (!currentValidationReport?.compatible || !targetReadinessReady) {
      const errorCount = currentValidationReport?.issues.filter(
        (issue) => issue.severity === "error",
      ).length ?? 0;
      return {
        stage: "blocked",
        title: "部署预检未通过",
        detail: errorCount > 0
          ? `发现 ${errorCount} 个阻止部署的问题，请按下方提示处理后重新检查。`
          : "目标节点的能力、设备或制品尚未就绪。",
      };
    }
    return {
      stage: "ready",
      title: "当前修订可以部署",
      detail: "画布与目标环境均已通过检查；启动时Platform API和Edge Agent仍会再次复核。",
    };
  })();
  const activeArtifactPreparation = installations.some((record) => (
    record.plan.target_agent_id === effectiveAgentId
    && requiredArtifacts.some(
      (artifact) => artifact.artifact_id === record.plan.artifact.artifact_id,
    )
    && ["pending", "downloading", "verifying", "installing", "activating"].includes(
      record.status,
    )
  ));

  useEffect(() => {
    setArtifactPreparationPlan(null);
    setArtifactPreparationNotice(null);
  }, [effectiveAgentId, deploymentRecipe?.recipe_id]);

  useEffect(() => {
    if (!deploymentReady) return;
    setArtifactPreparationPlan(null);
    setArtifactPreparationNotice(null);
  }, [deploymentReady]);

  const deviceSelector = (device: DeviceInstance) =>
    device.metadata.runtime_selector
      ?? device.metadata.sdk_serial
      ?? device.serial
      ?? device.device_id;
  const graphNodeSignature = graphNodes
    .map((node) => `${node.node_id}:${node.component_ref}:${node.component_version}`)
    .join("|");
  const targetDeviceSignature = (effectiveAgent?.devices ?? [])
    .map((device) => `${device.device_id}:${deviceSelector(device)}`)
    .join("|");

  useEffect(() => {
    const bindings = graphNodes.flatMap((node) => {
      const definition = hardware.find(
        (item) => item.hardware_id === node.component_ref
          && item.version === node.component_version,
      );
      if (!definition) return [];
      const discovery = definition.discovery;
      const devices = (effectiveAgent?.devices ?? []).filter((device) => {
        const vendorMatches = discovery.vendor_ids.length === 0
          || (device.vendor_id != null && discovery.vendor_ids.some(
            (vendorId) => vendorId.toLowerCase() === device.vendor_id?.toLowerCase(),
          ));
        return device.transport === discovery.transport
          && vendorMatches
          && discovery.product_name_contains.every(
            (part) => device.name.toLowerCase().includes(part.toLowerCase()),
          );
      });
      const selectors = devices.map(deviceSelector);
      return definition.parameters
        .filter((parameter) => parameter.binding_source === "target_hardware_devices")
        .map((parameter) => ({
          nodeId: node.node_id,
          key: parameter.key,
          selectors,
        }));
    });
    if (bindings.length === 0) return;
    setParameterValues((current) => {
      const next = { ...current };
      for (const binding of bindings) {
        const key = `${binding.nodeId}.${binding.key}`;
        if (!binding.selectors.includes(String(current[key] ?? ""))) {
          next[key] = binding.selectors[0] ?? "";
        }
      }
      return next;
    });
    setGraphNodes((current) => current.map((node) => {
      const nextParameters = { ...node.parameters };
      for (const binding of bindings.filter((item) => item.nodeId === node.node_id)) {
        if (!binding.selectors.includes(String(nextParameters[binding.key] ?? ""))) {
          nextParameters[binding.key] = binding.selectors[0] ?? "";
        }
      }
      return { ...node, parameters: nextParameters };
    }));
  }, [effectiveAgentId, graphNodeSignature, targetDeviceSignature]);

  const updateGraphNodeParameter = (
    nodeId: string,
    key: string,
    value: string | number,
  ) => {
    setParameterValues((current) => ({
      ...current,
      [`${nodeId}.${key}`]: value,
    }));
    setGraphNodes((current) => current.map((node) => (
      node.node_id === nodeId
        ? { ...node, parameters: { ...node.parameters, [key]: value } }
        : node
    )));
    setValidationReport(null);
  };

  const buildWorkflow = (): WorkflowDefinition | null => {
    if (!selectedRecipe) {
      setSubmitError("当前组件组合没有可执行Runtime配方。");
      return null;
    }
    if (graphNodes.length === 0) {
      setSubmitError("工作流画布至少需要一个组件节点。");
      return null;
    }
    const nodeParameters: Record<string, Record<string, unknown>> = Object.fromEntries(
      graphNodes.map((node) => [node.node_id, { ...node.parameters }]),
    );
    for (const node of graphNodes) {
      for (const parameter of componentParameters(
        node.component_ref,
        node.component_version,
      )) {
        const value = parameterValues[`${node.node_id}.${parameter.key}`]
          ?? node.parameters[parameter.key]
          ?? parameter.default;
        if (parameter.required && (value === undefined || String(value).trim() === "")) {
          setSubmitError(`请填写${node.node_id}节点的${parameter.label}。`);
          return null;
        }
        nodeParameters[node.node_id][parameter.key] = value;
      }
    }
    const workflowRecipe = matchedRecipe ?? selectedRecipe;
    return {
      workflow_id: workflowRecipe.recipe_id,
      version: workflowRecipe.version,
      name: workflowRecipe.name,
      nodes: graphNodes.map((node) => ({
        ...node,
        parameters: nodeParameters[node.node_id],
      })),
      links: graphLinks,
    };
  };

  const checkWorkflow = async () => {
    const workflow = buildWorkflow();
    if (!workflow) return;
    setIsValidating(true);
    setCheckedPreflightKey("");
    setSubmitError(null);
    try {
      if (!effectiveAgentId) {
        setSubmitError("请选择在线目标节点后再执行统一部署预检。");
        return;
      }
      const compilation = await compileDeploymentPlan({
        target_agent_id: effectiveAgentId,
        workflow,
      });
      setValidationReport(compilation.report);
      setCheckedPreflightKey(preflightKey);
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "工作流检查失败");
    } finally {
      setIsValidating(false);
    }
  };

  const prepareRequiredArtifacts = async () => {
    if (!effectiveAgent || !deploymentRecipe || requiredArtifacts.length === 0) {
      setSubmitError("当前流程没有可检查的端侧运行制品。");
      return;
    }
    setIsPreparingArtifacts(true);
    setSubmitError(null);
    setArtifactPreparationNotice(null);
    try {
      const plan = await previewArtifactPreparation({
        target_agent_id: effectiveAgent.agent_id,
        artifact_ids: deploymentRecipe.required_artifact_ids,
      });
      setArtifactPreparationPlan(plan);
      const installable = plan.items.filter((item) => item.state === "installable");
      if (installable.length === 0) {
        const blockers = plan.items.filter(
          (item) => !["ready", "not_required"].includes(item.state),
        );
        setSubmitError(
          blockers.length > 0
            ? blockers.map((item) => `${item.artifact.name}：${item.reason}`).join("；")
            : "目标节点已经具备全部运行制品。",
        );
        return;
      }
      for (const item of installable) {
        await createArtifactInstallation({
          target_agent_id: effectiveAgent.agent_id,
          artifact_id: item.artifact.artifact_id,
          reinstall: item.installed != null,
        });
      }
      setArtifactPreparationNotice(
        `已提交 ${installable.length} 项受控制品准备任务；完成后节点会自动刷新盘点。`,
      );
      await onRefresh();
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "制品准备请求失败");
    } finally {
      setIsPreparingArtifacts(false);
    }
  };

  const startWorkflow = async () => {
    if (!effectiveProjectId) {
      setSubmitError("请先选择项目，再保存并启动工作流。");
      return;
    }
    if (!effectiveAgentId) {
      setSubmitError("当前没有在线Edge节点，无法下发工作流。");
      return;
    }
    if (!deploymentReady) {
      setSubmitError("当前配置尚未通过统一部署预检，请先完成检查并处理阻止项。");
      return;
    }
    const workflow = buildWorkflow();
    if (!workflow) return;
    setIsSubmitting(true);
    setSubmitError(null);
    let savedRevision: WorkflowRevisionRecord | null = null;
    try {
      const saved = await saveWorkflowRevision(effectiveProjectId, workflow);
      savedRevision = saved;
      setRevisions((current) => [saved, ...current]);
      setLoadedRevision(saved);
      const result = await submitRevisionDeployment(saved, effectiveAgentId);
      if (!result.report.compatible || !result.plan) {
        const detail = result.report.issues.map(compatibilityIssueText).join("；")
          || "兼容检查失败";
        setSubmitError(`R${savedRevision.revision}已保存，但未能启动：${detail}`);
        return;
      }
      await onRefresh();
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "工作流下发失败";
      setSubmitError(
        savedRevision
          ? `R${savedRevision.revision}已保存，但启动失败：${detail}`
          : detail,
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const saveRevision = async () => {
    if (!effectiveProjectId) {
      setSubmitError("请先在项目页面创建项目。");
      return;
    }
    const workflow = buildWorkflow();
    if (!workflow) return;
    setIsSavingRevision(true);
    setSubmitError(null);
    try {
      const saved = await saveWorkflowRevision(effectiveProjectId, workflow);
      setRevisions((current) => [saved, ...current]);
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "保存工作流修订失败");
    } finally {
      setIsSavingRevision(false);
    }
  };

  const deployRevision = async (revision: WorkflowRevisionRecord) => {
    if (!effectiveAgentId) {
      setSubmitError("当前没有在线Edge节点，无法部署修订。");
      return;
    }
    const revisionKey = `${revision.workflow_id}:${revision.revision}`;
    setDeployingRevision(revisionKey);
    setSubmitError(null);
    try {
      const result = await submitRevisionDeployment(revision, effectiveAgentId);
      if (!result.report.compatible || !result.plan) {
        setSubmitError(result.report.issues.map(compatibilityIssueText).join("；") || "兼容检查失败");
        return;
      }
      await onRefresh();
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "工作流修订部署失败");
    } finally {
      setDeployingRevision(null);
    }
  };

  const loadRevision = (revision: WorkflowRevisionRecord) => {
    const recipe = recipes.find((item) => item.recipe_id === revision.workflow_id);
    if (!recipe) {
      setSubmitError("该修订引用的可执行配方已不在当前组件仓库中。");
      return;
    }
    pendingRevisionRef.current = revision;
    setLoadedRevision(revision);
    setSelectedRecipeId(recipe.recipe_id);
    if (selectedRecipe?.recipe_id === recipe.recipe_id) {
      setParameterValues(valuesFromWorkflow(recipe, revision.workflow));
      const restoredNodes = positionNodes(revision.workflow.nodes);
      setGraphNodes(restoredNodes);
      setGraphLinks(revision.workflow.links);
      setSelectedGraphNodeId(restoredNodes[0]?.node_id ?? "");
      setValidationReport(null);
      pendingRevisionRef.current = null;
    }
    setSubmitError(null);
  };

  const stopRuntime = async (planId: string) => {
    setStoppingPlanId(planId);
    setSubmitError(null);
    try {
      await stopDeployment(planId);
      await onRefresh();
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "停止指令下发失败");
    } finally {
      setStoppingPlanId(null);
    }
  };

  const rollbackRuntime = async (planId: string) => {
    setRollingBackPlanId(planId);
    setSubmitError(null);
    try {
      await rollbackDeployment(planId);
      await onRefresh();
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "部署回退失败");
    } finally {
      setRollingBackPlanId(null);
    }
  };

  const visibleDeployments = (deploymentScope === "project" && effectiveProjectId
    ? deployments.filter((deployment) => deployment.plan.project_id === effectiveProjectId)
    : deployments
  ).slice().sort((left, right) => (
    new Date(right.plan.created_at).getTime() - new Date(left.plan.created_at).getTime()
  ));

  return (
    <section className="module-page">
      <div className="module-intro workflow-intro">
        <h2>从项目、目标环境到端侧Runtime</h2>
        <p>流程只在通过组件、节点能力、设备与固定摘要制品检查后，才会下发到所选Edge节点运行。</p>
      </div>

      <div className="workflow-layout">
        <section className="workflow-builder">
          <div className="section-heading">
            <div><h2>流程配置</h2></div>
            <span className="module-status">
              {loadedRevision ? `基于 R${loadedRevision.revision}` : "项目化闭环"}
            </span>
          </div>

          <div className="workflow-context" aria-label="步骤一：选择项目、组件模板与运行目标">
            <div className="workflow-context-heading">
              <span>1</span>
              <div><strong>选择运行上下文</strong><small>确定项目、初始组件模板和目标Edge节点</small></div>
            </div>
            <div className="workflow-context-grid">
              <div>
                <label className="field-label" htmlFor="workflow-project">所属项目</label>
                <select
                  id="workflow-project"
                  value={effectiveProjectId}
                  onChange={(event) => setSelectedProjectId(event.target.value)}
                  disabled={projects.length === 0}
                >
                  {projects.length === 0 ? (
                    <option value="">请先创建项目</option>
                  ) : projects.map((project) => (
                    <option key={project.project_id} value={project.project_id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="field-label" htmlFor="workflow-recipe">可执行流程模板</label>
                <select
                  id="workflow-recipe"
                  value={effectiveRecipeId}
                  onChange={(event) => setSelectedRecipeId(event.target.value)}
                  disabled={recipes.length === 0}
                >
                  {recipes.map((recipe) => (
                    <option key={recipe.recipe_id} value={recipe.recipe_id}>
                      {recipe.name} · {recipe.components.length}节点 · v{recipe.version}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="field-label" htmlFor="target-agent">目标节点</label>
                <select
                  id="target-agent"
                  value={effectiveAgentId}
                  onChange={(event) => setTargetAgentId(event.target.value)}
                  disabled={onlineAgents.length === 0}
                >
                  {onlineAgents.length === 0 ? (
                    <option value="">暂无在线节点</option>
                  ) : onlineAgents.map((agent) => (
                    <option key={agent.agent_id} value={agent.agent_id}>{agent.name}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <WorkflowGraphEditor
            hardware={hardware}
            algorithms={algorithms}
            nodes={graphNodes}
            links={graphLinks}
            selectedNodeId={selectedGraphNodeId}
            targetDevices={effectiveAgent?.devices ?? []}
            parameterValues={parameterValues}
            validationIssues={currentValidationReport?.issues ?? []}
            onNodesChange={(next) => {
              setGraphNodes(next);
              setValidationReport(null);
            }}
            onLinksChange={(next) => {
              setGraphLinks(next);
              setValidationReport(null);
            }}
            onSelectedNodeChange={setSelectedGraphNodeId}
            onNodeParameterChange={updateGraphNodeParameter}
          />

          <div className="workflow-operation-grid">
            <section className="workflow-parameters-panel" aria-label="步骤三：配置与运行">
              <div className="workflow-context-heading compact">
                <span>3</span>
                <div><strong>配置、检查与运行</strong><small>设置运行参数，通过检查后保存或启动</small></div>
              </div>

              <details
                className={`deployment-readiness ${workflowGate.stage}`}
                open={workflowGate.stage !== "ready"}
              >
                <summary className="deployment-readiness-heading">
                  <div>
                    <strong>{workflowGate.title}</strong>
                    <small>{workflowGate.detail}</small>
                  </div>
                  <span>{requiredArtifacts.length} 项制品 · 查看详情</span>
                </summary>
                {!matchedRecipe ? (
                  null
                ) : !effectiveAgent ? (
                  null
                ) : (
                  <>
                    <div className="readiness-row ready">
                      <div>
                        <strong>已匹配运行配方</strong>
                        <small>{matchedRecipe.name} · v{matchedRecipe.version}</small>
                      </div>
                      <span>可执行</span>
                    </div>
                    {missingCapabilities.length > 0 && (
                      <div className="readiness-row blocked">
                        <span>节点能力</span>
                        <strong>缺少 {missingCapabilities.join("、")}</strong>
                      </div>
                    )}
                    {!artifactResolutionsReady && effectiveAgent && (
                      <small>正在由Platform API解析目标节点的制品发行变体…</small>
                    )}
                    {requiredArtifactReadiness.map(({ artifact, state }) => (
                      <div className={`readiness-row ${state}`} key={artifact.artifact_id}>
                        <div>
                          <strong>{artifact.name}</strong>
                          <small>{artifact.version} · {artifact.locator}</small>
                        </div>
                        <span>
                          {state === "ready" ? "已就绪"
                            : state === "missing" ? "缺失"
                              : state === "mismatch" ? "摘要异常"
                                : state === "incompatible" ? "节点不兼容"
                                  : state === "unresolved" ? "解析失败" : "节点不支持盘点"}
                        </span>
                      </div>
                    ))}
                    {matchedRecipe && requiredArtifacts.length === 0
                      && missingCapabilities.length === 0 && (
                      <small>该流程不依赖额外端侧制品。</small>
                    )}
                    {artifactsNeedPreparation && requiredArtifacts.length > 0 && (
                      <div className="preflight-artifact-actions">
                        <button
                          className="secondary-button"
                          type="button"
                          disabled={isPreparingArtifacts || activeArtifactPreparation}
                          onClick={() => void prepareRequiredArtifacts()}
                        >
                          {activeArtifactPreparation
                            ? "制品准备进行中…"
                            : isPreparingArtifacts ? "正在生成准备计划…" : "检查并准备缺失制品"}
                        </button>
                        <small>仅执行Catalog允许的固定摘要策略；无法自动准备的项目会保留阻止原因。</small>
                      </div>
                    )}
                    {artifactPreparationPlan && (
                      <div className="preflight-plan-summary">
                        {artifactPreparationPlan.items.map((item) => (
                          <small key={item.artifact.artifact_id}>
                            {item.artifact.name} · {item.strategy} · {item.reason}
                          </small>
                        ))}
                      </div>
                    )}
                    {artifactPreparationNotice && (
                      <div className="preflight-notice">{artifactPreparationNotice}</div>
                    )}
                  </>
                )}
              </details>

              {(submitError ?? artifactResolutionError) && (
                <div className="error-banner workflow-error">
                  {submitError ?? artifactResolutionError}
                </div>
              )}
              {currentValidationReport && (
                <div className={`graph-validation ${currentValidationReport.compatible ? "valid" : "invalid"}`}>
                  <strong>{currentValidationReport.compatible ? "统一部署预检通过" : "统一部署预检发现问题"}</strong>
                  {currentValidationReport.issues.length === 0 ? (
                    <small>画布、Runtime配方、目标节点、设备能力与制品均有效。</small>
                  ) : currentValidationReport.issues.map((issue) => (
                    <small key={`${issue.code}:${issue.node_id ?? "workflow"}:${issue.message}`}>
                      {compatibilityIssueText(issue)}
                    </small>
                  ))}
                </div>
              )}
              <div className="workflow-actions">
                <button
                  className="secondary-button"
                  type="button"
                  disabled={!selectedRecipe || !effectiveAgentId
                    || !artifactResolutionsReady || isValidating}
                  onClick={() => void checkWorkflow()}
                >
                  {isValidating ? "正在统一预检…" : "检查工作流与部署条件"}
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={!effectiveProjectId || !selectedRecipe || isSavingRevision}
                  onClick={() => void saveRevision()}
                >
                  {isSavingRevision ? "正在保存…" : "保存为新修订"}
                </button>
                <button
                  className="primary-button"
                  type="button"
                  disabled={!effectiveProjectId || !deploymentReady || isValidating || isSubmitting}
                  onClick={() => void startWorkflow()}
                >
                  {isSubmitting ? "正在保存并启动…" : "保存新修订并启动"}
                </button>
              </div>
            </section>

            <details className="revision-panel" aria-label="工作流修订历史">
            <summary className="revision-heading">
              <div>
                <h3>不可变修订</h3>
              </div>
              <span>{revisions.length} 个 · 点击查看历史</span>
            </summary>
            {isLoadingRevisions ? (
              <p className="revision-empty">正在读取修订…</p>
            ) : revisions.length === 0 ? (
              <p className="revision-empty">尚未保存修订。保存后可从任意版本重新部署。</p>
            ) : (
              <div className="revision-list">
                {revisions.map((revision) => {
                  const revisionKey = `${revision.workflow_id}:${revision.revision}`;
                  const previous = revisions.find(
                    (item) => item.workflow_id === revision.workflow_id
                      && item.revision === revision.revision - 1,
                  );
                  const changes = revisionDifferences(revision, previous, recipes);
                  return (
                    <article className="revision-card" key={revisionKey}>
                      <div className="revision-summary">
                        <strong>R{revision.revision} · {revision.workflow.name}</strong>
                        <small>{formatTime(revision.created_at)} · 配方 v{revision.workflow.version}</small>
                        {previous && (
                          <div className="revision-diff">
                            <span>相对 R{previous.revision}</span>
                            {changes.length === 0 ? (
                              <small>配置内容无变化</small>
                            ) : changes.slice(0, 3).map((change) => (
                              <small key={change}>{change}</small>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="revision-actions">
                        <button type="button" onClick={() => loadRevision(revision)}>
                          载入编辑
                        </button>
                        <button
                          type="button"
                          disabled={!effectiveAgentId || deployingRevision === revisionKey}
                          onClick={() => void deployRevision(revision)}
                        >
                          {deployingRevision === revisionKey ? "部署中…" : "部署此修订"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
            </details>
          </div>
        </section>

        <section className="deployment-panel">
          <div className="section-heading">
            <div><h2>运行记录</h2></div>
            <div className="deployment-controls">
              <select
                aria-label="运行记录范围"
                value={deploymentScope}
                onChange={(event) => setDeploymentScope(event.target.value as "project" | "all")}
              >
                <option value="project">当前项目</option>
                <option value="all">全部记录</option>
              </select>
              <button className="refresh-button" onClick={() => void onRefresh()}>刷新</button>
            </div>
          </div>
          {visibleDeployments.length === 0 ? (
            <div className="empty-state compact-empty">
              <h3>尚无部署记录</h3>
              <p>{deploymentScope === "project" ? "当前项目尚无运行记录。" : "选择在线节点并启动第一条端侧流程。"}</p>
            </div>
          ) : (
            <div className="deployment-list">
              {visibleDeployments.map((deployment) => (
                <article className="deployment-card" key={deployment.plan.plan_id}>
                  <div className="deployment-title">
                    <div>
                      <span className={`status-pill ${deployment.status}`}>{deploymentStatusText[deployment.status]}</span>
                      <h3>{deployment.plan.workflow.name}</h3>
                    </div>
                  </div>
                  <p>{deployment.message || "等待状态回报"}</p>
                  <DeploymentPreview deployment={deployment} />
                  <dl className="deployment-overview">
                    <div><dt>目标节点</dt><dd>{deployment.plan.target_agent_id}</dd></div>
                    {deployment.plan.project_id && (
                      <div>
                        <dt>项目来源</dt>
                        <dd>
                          {projects.find(
                            (project) => project.project_id === deployment.plan.project_id,
                          )?.name ?? deployment.plan.project_id}
                          {deployment.plan.workflow_revision != null
                            ? ` · R${deployment.plan.workflow_revision}`
                            : ""}
                        </dd>
                      </div>
                    )}
                    <div><dt>更新时间</dt><dd>{formatTime(deployment.updated_at)}</dd></div>
                  </dl>
                  <DeploymentMetrics metrics={deployment.metrics} />
                  <DeploymentEvidence deployment={deployment} />
                  <details className="card-details deployment-technical-details">
                    <summary>查看部署技术信息</summary>
                    <dl>
                      <div><dt>计划 ID</dt><dd>{deployment.plan.plan_id}</dd></div>
                      <div>
                        <dt>版本快照</dt>
                        <dd>
                          {deployment.plan.components.length} 个组件 · {deployment.plan.artifacts.length} 个制品
                        </dd>
                      </div>
                      {deployment.plan.rollback_of_plan_id && (
                        <div>
                          <dt>回退来源</dt>
                          <dd>{deployment.plan.rollback_of_plan_id}</dd>
                        </div>
                      )}
                      {deployment.plan.supersedes_plan_ids.length > 0 && (
                        <div>
                          <dt>替换计划</dt>
                          <dd>{deployment.plan.supersedes_plan_ids.join("、")}</dd>
                        </div>
                      )}
                    </dl>
                  </details>
                  {!["stopped", "failed", "stopping"].includes(deployment.status) && (
                    <button
                      className="stop-button"
                      type="button"
                      disabled={stoppingPlanId === deployment.plan.plan_id}
                      onClick={() => void stopRuntime(deployment.plan.plan_id)}
                    >
                      {stoppingPlanId === deployment.plan.plan_id
                        ? "正在下发停止指令…"
                        : deployment.status === "queued" ? "取消排队回退" : "停止Runtime"}
                    </button>
                  )}
                  {["stopped", "failed"].includes(deployment.status) && (
                    <button
                      className="rollback-button"
                      type="button"
                      disabled={rollingBackPlanId === deployment.plan.plan_id}
                      onClick={() => void rollbackRuntime(deployment.plan.plan_id)}
                    >
                      {rollingBackPlanId === deployment.plan.plan_id
                        ? "正在创建回退部署…"
                        : "回退到此版本"}
                    </button>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

function App() {
  const [activeView, setActiveView] = useState<View>("overview");
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [hardware, setHardware] = useState<HardwareDefinition[]>([]);
  const [algorithms, setAlgorithms] = useState<AlgorithmDefinition[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactDefinition[]>([]);
  const [artifactInstallations, setArtifactInstallations] = useState<ArtifactInstallationRecord[]>([]);
  const [recipes, setRecipes] = useState<WorkflowRecipe[]>([]);
  const [deployments, setDeployments] = useState<DeploymentRecord[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const [
        nextAgents,
        nextHardware,
        nextAlgorithms,
        nextArtifacts,
        nextArtifactInstallations,
        nextRecipes,
        nextDeployments,
        nextProjects,
      ] = await Promise.all([
        fetchAgents(signal),
        fetchHardwareCatalog(signal),
        fetchAlgorithmCatalog(signal),
        fetchArtifactCatalog(signal),
        fetchArtifactInstallations(signal),
        fetchWorkflowRecipes(signal),
        fetchDeployments(signal),
        fetchProjects(signal),
      ]);
      setAgents(nextAgents);
      setHardware(nextHardware);
      setAlgorithms(nextAlgorithms);
      setArtifacts(nextArtifacts);
      setArtifactInstallations(nextArtifactInstallations);
      setRecipes(nextRecipes);
      setDeployments(nextDeployments);
      setProjects(nextProjects);
      setIsConnected(true);
      setError(null);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setIsConnected(false);
      setError(reason instanceof Error ? reason.message : "无法连接 Platform API");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const interval = window.setInterval(() => void refresh(), refreshIntervalMs);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [refresh]);

  const onlineCount = agents.filter((agent) => agent.status === "online").length;
  const activeNavigation = navigation.find((item) => item.id === activeView)!;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup" aria-label="机器人开发平台">
          <div className="brand-mark">R</div>
          <span>机器人平台</span>
        </div>
        <nav aria-label="主要导航">
          {navigation.map((item) => (
            <button
              className={`nav-item ${activeView === item.id ? "active" : ""}`}
              key={item.id}
              onClick={() => setActiveView(item.id)}
              aria-current={activeView === item.id ? "page" : undefined}
              title={item.title}
            >
              <span className="nav-icon"><NavigationIcon name={item.icon} /></span>
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <h1>{activeNavigation.title}</h1>
          </div>
          <span className={`connection ${isConnected ? "connected" : "disconnected"}`}>
            <span className="connection-dot" />
            {isConnected ? "平台服务已连接" : "平台服务未连接"}
          </span>
        </header>

        {activeView === "overview" ? (
          <>
            <section className="hero-card">
            <div>
                <p className="section-label">第一条端到端基线</p>
                <h2>从这里连接机器人世界</h2>
                <p>
                  当前版本已建立 Web Studio、Platform API 与 Edge Agent
                  的工程闭环；摄像头等输入可来自真实设备，执行器默认保持模拟或安全停止。
                </p>
            </div>
              <div className="hero-visual" aria-hidden="true">
                <span className="orbit orbit-one" />
                <span className="orbit orbit-two" />
                <span className="core">节点</span>
              </div>
            </section>

            <section className="metrics" aria-label="节点统计">
              <article className="metric-card">
                <span>节点总数</span>
                <strong>{agents.length}</strong>
              </article>
              <article className="metric-card">
                <span>在线节点</span>
                <strong>{onlineCount}</strong>
              </article>
              <article className="metric-card">
                <span>离线节点</span>
                <strong>{agents.length - onlineCount}</strong>
              </article>
            </section>

            <section className="agents-section">
              <div className="section-heading">
                <div>
                  <h2>边缘节点</h2>
                </div>
                <button className="refresh-button" onClick={() => void refresh()}>
                  刷新
                </button>
              </div>

              {error && <div className="error-banner">{error}。请先启动 Platform API。</div>}

              {!error && agents.length === 0 && (
                <div className="empty-state">
                  <div className="empty-icon">+</div>
                  <h3>还没有边缘节点</h3>
                  <p>启动 Edge Agent 后，节点会自动出现在这里。</p>
                </div>
              )}

              <div className="agent-grid">
                {agents.map((agent) => (
                  <article className="agent-card" key={agent.agent_id}>
                    <div className="agent-header">
                      <div>
                        <span className={`status-pill ${agent.status}`}>
                          {agent.status === "online" ? "在线" : "离线"}
                        </span>
                        <h3>{agent.name}</h3>
                      </div>
                      <span className="agent-version">v{agent.version}</span>
                    </div>
                    <dl>
                      <div>
                        <dt>设备</dt>
                        <dd>{metadataText(agent, "jetson_model") !== "—" ? metadataText(agent, "jetson_model") : metadataText(agent, "operating_system")}</dd>
                      </div>
                      <div><dt>最后心跳</dt><dd>{formatTime(agent.last_seen_at)}</dd></div>
                      {agent.metrics && (
                        <>
                          <div>
                            <dt>可用内存</dt>
                            <dd>{formatBytes(agent.metrics.memory_available_bytes)} / {formatBytes(agent.metrics.memory_total_bytes)}</dd>
                          </div>
                          <div>
                            <dt>最高温度</dt>
                            <dd>{agent.metrics.max_temperature_c == null ? "—" : `${agent.metrics.max_temperature_c.toFixed(1)} °C`}</dd>
                          </div>
                        </>
                      )}
                    </dl>
                    <details className="card-details agent-technical-details">
                      <summary>查看节点技术信息</summary>
                      <dl>
                        <div><dt>节点 ID</dt><dd>{agent.agent_id}</dd></div>
                        <div><dt>系统</dt><dd>{metadataText(agent, "os_pretty_name")}</dd></div>
                        <div><dt>架构</dt><dd>{metadataText(agent, "machine")}</dd></div>
                        <div><dt>Python</dt><dd>{metadataText(agent, "python_version")}</dd></div>
                        <div><dt>能力</dt><dd>{agent.capabilities.join(" · ") || "未声明"}</dd></div>
                        {agent.metrics?.data_disk_total_bytes != null && (
                          <div>
                            <dt>数据盘</dt>
                            <dd>{formatBytes(agent.metrics.data_disk_free_bytes)} 可用</dd>
                          </div>
                        )}
                        {agent.metrics && (
                          <div><dt>运行时间</dt><dd>{formatDuration(agent.metrics.uptime_s)}</dd></div>
                        )}
                      </dl>
                    </details>
                  </article>
                ))}
              </div>
            </section>
          </>
        ) : activeView === "projects" ? (
          <ProjectsPage projects={projects} onCreated={() => refresh()} />
        ) : activeView === "components" ? (
          <ComponentsPage hardware={hardware} algorithms={algorithms} agents={agents} />
        ) : activeView === "artifacts" ? (
          <ArtifactsPage
            artifacts={artifacts}
            installations={artifactInstallations}
            agents={agents}
            onRefresh={() => refresh()}
          />
        ) : activeView === "workflows" ? (
          <WorkflowsPage
            agents={agents}
            hardware={hardware}
            algorithms={algorithms}
            artifacts={artifacts}
            installations={artifactInstallations}
            recipes={recipes}
            deployments={deployments}
            projects={projects}
            onRefresh={() => refresh()}
          />
        ) : (
          <ModulePage view={activeView} />
        )}
      </main>
    </div>
  );
}

export default App;

