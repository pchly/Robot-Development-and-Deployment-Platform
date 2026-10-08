import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import {
  AlgorithmDefinition,
  CompatibilityIssue,
  ComponentParameter,
  ComponentPort,
  DeviceInstance,
  HardwareDefinition,
  WorkflowDefinition,
  WorkflowNode,
} from "./api";

type WorkflowLink = WorkflowDefinition["links"][number];

interface ComponentDescriptor {
  ref: string;
  version: string;
  name: string;
  kind: "hardware" | "algorithm";
  category: string;
  inputs: ComponentPort[];
  outputs: ComponentPort[];
  parameters: ComponentParameter[];
  discovery?: HardwareDefinition["discovery"];
}

interface DragState {
  nodeId: string;
  pointerId: number;
  offsetX: number;
  offsetY: number;
}

interface PortOption {
  nodeId: string;
  nodeName: string;
  port: ComponentPort;
  value: string;
}

interface ConnectionDragState {
  pointerId: number;
  source: PortOption;
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
}

const nodeWidth = 230;
const nodeHeight = 250;
const canvasPadding = 40;
const gridSize = 20;

function componentCatalog(
  hardware: HardwareDefinition[],
  algorithms: AlgorithmDefinition[],
): ComponentDescriptor[] {
  return [
    ...hardware.map((item) => ({
      ref: item.hardware_id,
      version: item.version,
      name: item.name,
      kind: "hardware" as const,
      category: item.category,
      inputs: item.inputs,
      outputs: item.outputs,
      parameters: item.parameters,
      discovery: item.discovery,
    })),
    ...algorithms.map((item) => ({
      ref: item.algorithm_id,
      version: item.version,
      name: item.name,
      kind: "algorithm" as const,
      category: item.category,
      inputs: item.inputs,
      outputs: item.outputs,
      parameters: item.parameters,
    })),
  ];
}

function nextNodeId(component: ComponentDescriptor, nodes: WorkflowNode[]): string {
  const base = component.kind === "hardware" ? "hardware" : "algorithm";
  let index = 1;
  while (nodes.some((node) => node.node_id === `${base}_${index}`)) index += 1;
  return `${base}_${index}`;
}

function portKey(nodeId: string, port: string): string {
  return `${nodeId}.${port}`;
}

const portLabels: Record<string, string> = {
  color: "彩色图像",
  depth: "深度图像",
  camera_info: "相机参数",
  image: "图像输入",
  detections: "检测结果",
  filtered: "过滤结果",
  command: "运动命令",
  state: "执行器状态",
  fault: "执行器故障",
};

function portLabel(portName: string): string {
  return portLabels[portName] ?? portName;
}

function connectionPath(startX: number, startY: number, endX: number, endY: number): string {
  const controlOffset = Math.max(70, Math.abs(endX - startX) / 2);
  return `M ${startX} ${startY} C ${startX + controlOffset} ${startY}, ${endX - controlOffset} ${endY}, ${endX} ${endY}`;
}

function nodePosition(node: WorkflowNode, index: number): { x: number; y: number } {
  return node.position ?? {
    x: 60 + (index % 3) * 300,
    y: 70 + Math.floor(index / 3) * 310,
  };
}

function matchingDevices(
  component: ComponentDescriptor,
  devices: DeviceInstance[],
): DeviceInstance[] {
  if (!component.discovery) return [];
  const discovery = component.discovery;
  return devices.filter((device) => {
    const vendorMatches = discovery.vendor_ids.length === 0
      || (device.vendor_id != null
        && discovery.vendor_ids.some(
          (vendorId) => vendorId.toLowerCase() === device.vendor_id?.toLowerCase(),
        ));
    const normalizedName = device.name.toLowerCase();
    return device.transport === discovery.transport
      && vendorMatches
      && discovery.product_name_contains.every(
        (part) => normalizedName.includes(part.toLowerCase()),
      );
  });
}

function deviceSelector(device: DeviceInstance): string {
  return device.metadata.runtime_selector
    ?? device.metadata.sdk_serial
    ?? device.serial
    ?? device.device_id;
}

export function WorkflowGraphEditor({
  hardware,
  algorithms,
  nodes,
  links,
  selectedNodeId,
  targetDevices,
  parameterValues,
  validationIssues,
  onNodesChange,
  onLinksChange,
  onSelectedNodeChange,
  onNodeParameterChange,
}: {
  hardware: HardwareDefinition[];
  algorithms: AlgorithmDefinition[];
  nodes: WorkflowNode[];
  links: WorkflowDefinition["links"];
  selectedNodeId: string;
  targetDevices: DeviceInstance[];
  parameterValues: Record<string, string | number>;
  validationIssues: CompatibilityIssue[];
  onNodesChange: (nodes: WorkflowNode[]) => void;
  onLinksChange: (links: WorkflowDefinition["links"]) => void;
  onSelectedNodeChange: (nodeId: string) => void;
  onNodeParameterChange: (nodeId: string, key: string, value: string | number) => void;
}) {
  const catalog = useMemo(
    () => componentCatalog(hardware, algorithms),
    [hardware, algorithms],
  );
  const [componentToAdd, setComponentToAdd] = useState(catalog[0]?.ref ?? "");
  const [draggingNodeId, setDraggingNodeId] = useState("");
  const [connectionDrag, setConnectionDrag] = useState<ConnectionDragState | null>(null);
  const [selectedLinkIndex, setSelectedLinkIndex] = useState<number | null>(null);
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const connectionDragRef = useRef<ConnectionDragState | null>(null);

  useEffect(() => {
    if (!catalog.some((component) => component.ref === componentToAdd)) {
      setComponentToAdd(catalog[0]?.ref ?? "");
    }
  }, [catalog, componentToAdd]);

  const sourceOptions: PortOption[] = nodes.flatMap((node) => {
    const component = catalog.find((item) => item.ref === node.component_ref);
    return (component?.outputs ?? []).map((port) => ({
      nodeId: node.node_id,
      nodeName: component?.name ?? node.node_id,
      port,
      value: portKey(node.node_id, port.name),
    }));
  });
  const [sourceValue, setSourceValue] = useState("");
  const selectedSource = sourceOptions.find((option) => option.value === sourceValue)
    ?? sourceOptions[0];
  const occupiedInputs = new Set(
    links.map((link) => portKey(link.target.node_id, link.target.port)),
  );
  const targetOptions: PortOption[] = nodes.flatMap((node) => {
    const component = catalog.find((item) => item.ref === node.component_ref);
    return (component?.inputs ?? [])
      .filter((port) => !selectedSource || port.data_type === selectedSource.port.data_type)
      .filter(() => !selectedSource || node.node_id !== selectedSource.nodeId)
      .filter((port) => !occupiedInputs.has(portKey(node.node_id, port.name)))
      .map((port) => ({
        nodeId: node.node_id,
        nodeName: component?.name ?? node.node_id,
        port,
        value: portKey(node.node_id, port.name),
      }));
  });
  const [targetValue, setTargetValue] = useState("");
  const selectedTarget = targetOptions.find((option) => option.value === targetValue)
    ?? targetOptions[0];
  const selectedNode = nodes.find((node) => node.node_id === selectedNodeId) ?? nodes[0];
  const selectedComponent = catalog.find((item) => item.ref === selectedNode?.component_ref);
  const selectedDeviceOptions = selectedComponent
    ? matchingDevices(selectedComponent, targetDevices)
    : [];
  const selectedIssues = validationIssues.filter(
    (issue) => issue.node_id === selectedNode?.node_id,
  );
  const nodeDisplayName = (nodeId: string) => {
    const node = nodes.find((item) => item.node_id === nodeId);
    return catalog.find((item) => item.ref === node?.component_ref)?.name ?? nodeId;
  };

  useEffect(() => {
    if (selectedLinkIndex != null && !links[selectedLinkIndex]) {
      setSelectedLinkIndex(null);
    }
  }, [links, selectedLinkIndex]);

  const surfaceSize = useMemo(() => {
    const positioned = nodes.map(nodePosition);
    return {
      width: Math.max(900, ...positioned.map((position) => position.x + nodeWidth + canvasPadding)),
      height: Math.max(700, ...positioned.map((position) => position.y + nodeHeight + canvasPadding)),
    };
  }, [nodes]);

  const addNode = () => {
    const component = catalog.find((item) => item.ref === componentToAdd) ?? catalog[0];
    if (!component) return;
    const nodeId = nextNodeId(component, nodes);
    const row = Math.floor(nodes.length / 3);
    const column = nodes.length % 3;
    const next = [
      ...nodes,
      {
        node_id: nodeId,
        component_ref: component.ref,
        component_version: component.version,
        parameters: Object.fromEntries(
          component.parameters.map((parameter) => [parameter.key, parameter.default]),
        ),
        position: { x: 60 + column * 300, y: 70 + row * 310 },
      },
    ];
    onNodesChange(next);
    onSelectedNodeChange(nodeId);
  };

  const removeNode = (nodeId: string) => {
    onNodesChange(nodes.filter((node) => node.node_id !== nodeId));
    onLinksChange(links.filter(
      (link) => link.source.node_id !== nodeId && link.target.node_id !== nodeId,
    ));
    onSelectedNodeChange(nodes.find((node) => node.node_id !== nodeId)?.node_id ?? "");
  };

  const connectPorts = (source: PortOption, target: PortOption) => {
    if (
      source.nodeId === target.nodeId
      || source.port.data_type !== target.port.data_type
      || occupiedInputs.has(portKey(target.nodeId, target.port.name))
    ) return;
    const link: WorkflowLink = {
      source: { node_id: source.nodeId, port: source.port.name },
      target: { node_id: target.nodeId, port: target.port.name },
    };
    onLinksChange([...links, link]);
    setTargetValue("");
    setSelectedLinkIndex(links.length);
  };

  const connect = () => {
    if (!selectedSource || !selectedTarget) return;
    connectPorts(selectedSource, selectedTarget);
  };

  const connectionPoint = (clientX: number, clientY: number) => {
    const surface = surfaceRef.current;
    if (!surface) return null;
    const bounds = surface.getBoundingClientRect();
    return { x: clientX - bounds.left, y: clientY - bounds.top };
  };

  const startConnectionDrag = (
    event: ReactPointerEvent<HTMLButtonElement>,
    source: PortOption,
  ) => {
    const surface = surfaceRef.current;
    if (!surface) return;
    event.stopPropagation();
    const surfaceBounds = surface.getBoundingClientRect();
    const handleBounds = event.currentTarget.getBoundingClientRect();
    const startX = handleBounds.right - surfaceBounds.left;
    const startY = handleBounds.top + handleBounds.height / 2 - surfaceBounds.top;
    const next: ConnectionDragState = {
      pointerId: event.pointerId,
      source,
      startX,
      startY,
      currentX: startX,
      currentY: startY,
    };
    connectionDragRef.current = next;
    setConnectionDrag(next);
    setSourceValue(source.value);
    setTargetValue("");
    setSelectedLinkIndex(null);
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveConnectionDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const current = connectionDragRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const point = connectionPoint(event.clientX, event.clientY);
    if (!point) return;
    const next = { ...current, currentX: point.x, currentY: point.y };
    connectionDragRef.current = next;
    setConnectionDrag(next);
  };

  const finishConnectionDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const current = connectionDragRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const targetElement = document.elementFromPoint(event.clientX, event.clientY)
      ?.closest<HTMLButtonElement>("[data-input-node][data-input-port]");
    const targetNode = targetElement
      ? nodes.find((node) => node.node_id === targetElement.dataset.inputNode)
      : undefined;
    const targetComponent = catalog.find((item) => item.ref === targetNode?.component_ref);
    const targetPort = targetComponent?.inputs.find(
      (port) => port.name === targetElement?.dataset.inputPort,
    );
    const target: PortOption | undefined = targetNode && targetComponent && targetPort
      ? {
          nodeId: targetNode.node_id,
          nodeName: targetComponent.name,
          port: targetPort,
          value: portKey(targetNode.node_id, targetPort.name),
        }
      : undefined;
    if (target) connectPorts(current.source, target);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    connectionDragRef.current = null;
    setConnectionDrag(null);
  };

  const cancelConnectionDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    connectionDragRef.current = null;
    setConnectionDrag(null);
  };

  const removeLink = (index: number) => {
    onLinksChange(links.filter((_, itemIndex) => itemIndex !== index));
    setSelectedLinkIndex(null);
  };

  const startDrag = (
    event: ReactPointerEvent<HTMLDivElement>,
    node: WorkflowNode,
    index: number,
  ) => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const position = nodePosition(node, index);
    const bounds = surface.getBoundingClientRect();
    dragRef.current = {
      nodeId: node.node_id,
      pointerId: event.pointerId,
      offsetX: event.clientX - bounds.left - position.x,
      offsetY: event.clientY - bounds.top - position.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraggingNodeId(node.node_id);
    onSelectedNodeChange(node.node_id);
  };

  const dragNode = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const surface = surfaceRef.current;
    if (!drag || !surface || drag.pointerId !== event.pointerId) return;
    const bounds = surface.getBoundingClientRect();
    const x = Math.max(
      canvasPadding,
      Math.round((event.clientX - bounds.left - drag.offsetX) / gridSize) * gridSize,
    );
    const y = Math.max(
      canvasPadding,
      Math.round((event.clientY - bounds.top - drag.offsetY) / gridSize) * gridSize,
    );
    onNodesChange(nodes.map((node) => (
      node.node_id === drag.nodeId ? { ...node, position: { x, y } } : node
    )));
  };

  const stopDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setDraggingNodeId("");
  };

  const parameterValue = (parameter: ComponentParameter): string | number => {
    if (!selectedNode) return parameter.default;
    const fieldKey = `${selectedNode.node_id}.${parameter.key}`;
    const configured = parameterValues[fieldKey];
    if (configured !== undefined) return configured;
    const nodeValue = selectedNode.parameters[parameter.key];
    return typeof nodeValue === "string" || typeof nodeValue === "number"
      ? nodeValue
      : parameter.default;
  };

  return (
    <section className="graph-editor" aria-label="通用工作流画布">
      <header className="graph-editor-header">
        <div>
          <h3>可视化编排</h3>
          <p>从左侧添加组件，在画布中拖动节点，在右侧配置参数并连接兼容端口。</p>
        </div>
        <ol className="graph-steps" aria-label="画布使用步骤">
          <li><span>1</span>添加组件</li>
          <li><span>2</span>拖动与配置</li>
          <li><span>3</span>连接端口</li>
        </ol>
      </header>

      <div className="graph-workspace">
        <aside className="graph-library">
          <div className="graph-panel-heading">
            <strong>组件库</strong>
            <small>硬件与算法均以标准组件加入流程。</small>
          </div>
          <label htmlFor="graph-component">选择组件</label>
          <select
            id="graph-component"
            value={componentToAdd}
            onChange={(event) => setComponentToAdd(event.target.value)}
          >
            {catalog.map((component) => (
              <option key={`${component.kind}:${component.ref}`} value={component.ref}>
                {component.kind === "hardware" ? "硬件" : "算法"} · {component.name}
              </option>
            ))}
          </select>
          <button className="graph-add-button" type="button" onClick={addNode}>
            ＋ 添加到画布
          </button>
          <div className="graph-library-summary">
            <div><strong>{nodes.length}</strong><span>画布节点</span></div>
            <div><strong>{links.length}</strong><span>数据连接</span></div>
          </div>
          <p className="graph-library-tip">
            拖动节点顶部可调整位置；选择节点后，在右侧配置该节点的参数和数据连接。
          </p>
        </aside>

        <main className="graph-stage">
          <div className="graph-stage-heading">
            <div>
              <strong>工作流画布</strong>
            </div>
            <span>{nodes.length === 0 ? "等待添加节点" : `${nodes.length} 个节点 · ${links.length} 条连接`}</span>
          </div>
          <div className="graph-canvas">
            {nodes.length === 0 ? (
              <div className="graph-empty">
                <strong>画布还是空的</strong>
                <span>从左侧组件库添加第一个硬件或算法组件。</span>
              </div>
            ) : (
              <div
                className="graph-canvas-surface"
                ref={surfaceRef}
                style={{ width: surfaceSize.width, height: surfaceSize.height }}
                onClick={(event) => {
                  if (event.target === event.currentTarget) setSelectedLinkIndex(null);
                }}
              >
                <svg
                  className="graph-edge-layer"
                  width={surfaceSize.width}
                  height={surfaceSize.height}
                  aria-hidden="true"
                >
                  <defs>
                    <marker
                      id="workflow-arrowhead"
                      markerWidth="8"
                      markerHeight="8"
                      refX="7"
                      refY="4"
                      orient="auto"
                    >
                      <path d="M0,0 L8,4 L0,8 Z" fill="currentColor" />
                    </marker>
                  </defs>
                  {links.map((link, index) => {
                    const sourceIndex = nodes.findIndex(
                      (node) => node.node_id === link.source.node_id,
                    );
                    const targetIndex = nodes.findIndex(
                      (node) => node.node_id === link.target.node_id,
                    );
                    if (sourceIndex < 0 || targetIndex < 0) return null;
                    const source = nodePosition(nodes[sourceIndex], sourceIndex);
                    const target = nodePosition(nodes[targetIndex], targetIndex);
                    const startX = source.x + nodeWidth;
                    const startY = source.y + nodeHeight / 2;
                    const endX = target.x;
                    const endY = target.y + nodeHeight / 2;
                    const path = connectionPath(startX, startY, endX, endY);
                    return (
                      <g key={`${portKey(link.source.node_id, link.source.port)}-${index}`}>
                        <path
                          className="graph-edge-hit-area"
                          d={path}
                          onClick={(event) => {
                            event.stopPropagation();
                            setSelectedLinkIndex(index);
                          }}
                        />
                        <path
                          className={`graph-edge-path ${selectedLinkIndex === index ? "selected" : ""}`}
                          d={path}
                          markerEnd="url(#workflow-arrowhead)"
                        />
                      </g>
                    );
                  })}
                  {connectionDrag && (
                    <path
                      className="graph-edge-draft"
                      d={connectionPath(
                        connectionDrag.startX,
                        connectionDrag.startY,
                        connectionDrag.currentX,
                        connectionDrag.currentY,
                      )}
                      markerEnd="url(#workflow-arrowhead)"
                    />
                  )}
                </svg>

                {selectedLinkIndex != null && links[selectedLinkIndex] && (
                  <div className="graph-edge-selection" role="status">
                    <div>
                      <strong>已选择数据连接</strong>
                      <small>
                        {nodeDisplayName(links[selectedLinkIndex].source.node_id)} → {nodeDisplayName(links[selectedLinkIndex].target.node_id)}
                      </small>
                    </div>
                    <button type="button" onClick={() => removeLink(selectedLinkIndex)}>
                      删除连接
                    </button>
                  </div>
                )}

                {nodes.map((node, index) => {
                  const component = catalog.find((item) => item.ref === node.component_ref);
                  const incoming = links.filter((link) => link.target.node_id === node.node_id);
                  const outgoing = links.filter((link) => link.source.node_id === node.node_id);
                  const issues = validationIssues.filter((issue) => issue.node_id === node.node_id);
                  const missingInputs = (component?.inputs ?? []).filter(
                    (port) => port.required
                      && !incoming.some((link) => link.target.port === port.name),
                  );
                  const position = nodePosition(node, index);
                  return (
                    <article
                      className={[
                        "graph-node-card",
                        selectedNode?.node_id === node.node_id ? "selected" : "",
                        draggingNodeId === node.node_id ? "dragging" : "",
                        issues.length > 0 || missingInputs.length > 0 ? "invalid" : "",
                      ].filter(Boolean).join(" ")}
                      key={node.node_id}
                      style={{ left: position.x, top: position.y }}
                      onClick={() => onSelectedNodeChange(node.node_id)}
                    >
                      <div
                        className="graph-node-topline"
                        title="拖动节点"
                        onPointerDown={(event) => startDrag(event, node, index)}
                        onPointerMove={dragNode}
                        onPointerUp={stopDrag}
                        onPointerCancel={stopDrag}
                      >
                        <span>{component?.kind === "hardware" ? "硬件" : "算法"}</span>
                      </div>
                      <strong>{component?.name ?? node.component_ref}</strong>
                      <small>{component?.category ?? "未登记组件"} · v{node.component_version}</small>
                      {(issues.length > 0 || missingInputs.length > 0) && (
                        <span className="graph-node-warning">
                          {issues.length + missingInputs.length} 项待处理
                        </span>
                      )}
                      <div className="graph-node-ports">
                        <div>
                          <span>输入</span>
                          {(component?.inputs ?? []).length === 0 ? <small>无</small> : component?.inputs.map((port) => {
                            const isConnected = incoming.some((link) => link.target.port === port.name);
                            const compatibleTarget = targetOptions.find((option) => (
                              option.nodeId === node.node_id && option.port.name === port.name
                            ));
                            return (
                              <button
                                type="button"
                                className={[
                                  "graph-port-handle",
                                  "input",
                                  isConnected ? "connected" : "",
                                  !isConnected && port.required ? "missing" : "",
                                  connectionDrag && compatibleTarget ? "compatible" : "",
                                  connectionDrag && !compatibleTarget ? "incompatible" : "",
                                ].filter(Boolean).join(" ")}
                                key={port.name}
                                data-input-node={node.node_id}
                                data-input-port={port.name}
                                title={!isConnected && port.required ? "必需输入尚未连接" : port.data_type}
                                aria-label={`${component?.name ?? node.node_id}的${portLabel(port.name)}输入端口`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  if (compatibleTarget && selectedSource) {
                                    connectPorts(selectedSource, compatibleTarget);
                                  }
                                }}
                              >
                                {portLabel(port.name)}
                              </button>
                            );
                          })}
                        </div>
                        <div>
                          <span>输出</span>
                          {(component?.outputs ?? []).length === 0 ? <small>无</small> : component?.outputs.map((port) => {
                            const sourceOption = sourceOptions.find((option) => (
                              option.nodeId === node.node_id && option.port.name === port.name
                            ));
                            if (!sourceOption) return null;
                            return (
                              <button
                                type="button"
                                className={[
                                  "graph-port-handle",
                                  "output",
                                  outgoing.some((link) => link.source.port === port.name) ? "connected" : "",
                                  selectedSource?.value === sourceOption.value ? "active" : "",
                                ].filter(Boolean).join(" ")}
                                key={port.name}
                                title={`拖动以连接 · ${port.data_type}`}
                                aria-label={`从${component?.name ?? node.node_id}的${portLabel(port.name)}输出端口开始连接`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setSourceValue(sourceOption.value);
                                  setTargetValue("");
                                }}
                                onPointerDown={(event) => startConnectionDrag(event, sourceOption)}
                                onPointerMove={moveConnectionDrag}
                                onPointerUp={finishConnectionDrag}
                                onPointerCancel={cancelConnectionDrag}
                              >
                                {portLabel(port.name)}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                      <div className="graph-node-actions">
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            removeNode(node.node_id);
                          }}
                        >删除</button>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </main>

        <aside className="graph-inspector">
          <div className="graph-panel-heading">
            <strong>{selectedComponent?.name ?? "节点属性"}</strong>
            <small>{selectedNode ? `当前选择：${selectedNode.node_id}` : "请先选择一个节点"}</small>
          </div>

          {selectedNode && selectedComponent && (
            <>
              <details className="card-details graph-node-technical-details">
                <summary>查看节点技术信息</summary>
                <dl className="graph-node-details">
                  <div><dt>组件引用</dt><dd>{selectedNode.component_ref}</dd></div>
                  <div><dt>输入端口</dt><dd>{selectedComponent.inputs.map((port) => `${port.name} · ${port.data_type}`).join("；") || "无"}</dd></div>
                  <div><dt>输出端口</dt><dd>{selectedComponent.outputs.map((port) => `${port.name} · ${port.data_type}`).join("；") || "无"}</dd></div>
                  <div><dt>画布坐标</dt><dd>{Math.round(selectedNode.position?.x ?? 0)}, {Math.round(selectedNode.position?.y ?? 0)}</dd></div>
                </dl>
              </details>

              <div className="graph-parameter-editor">
                <div className="graph-panel-heading compact">
                  <strong>节点参数</strong>
                  <small>字段来自组件清单，保存修订时归属当前节点。</small>
                </div>
                {selectedComponent.parameters.filter((parameter) => parameter.visible).length === 0 ? (
                  <p>该节点没有需要手动配置的参数。</p>
                ) : selectedComponent.parameters.filter((parameter) => parameter.visible).map((parameter) => {
                  const inputId = `node-parameter-${selectedNode.node_id}-${parameter.key}`;
                  if (parameter.binding_source === "target_hardware_devices") {
                    return (
                      <label className="graph-parameter-field" htmlFor={inputId} key={parameter.key}>
                        <span>{parameter.label}</span>
                        <select
                          id={inputId}
                          value={parameterValue(parameter)}
                          disabled={selectedDeviceOptions.length === 0}
                          onChange={(event) => onNodeParameterChange(
                            selectedNode.node_id,
                            parameter.key,
                            event.target.value,
                          )}
                        >
                          {selectedDeviceOptions.length === 0 ? (
                            <option value="">目标节点未发现匹配设备</option>
                          ) : selectedDeviceOptions.map((device) => (
                            <option key={device.device_id} value={deviceSelector(device)}>
                              {device.name} · {device.serial ?? device.device_id}
                            </option>
                          ))}
                        </select>
                        <small>{selectedDeviceOptions.length === 1 ? "已自动绑定唯一匹配设备" : "来自目标Edge节点的实时发现结果"}</small>
                      </label>
                    );
                  }
                  const isNumber = parameter.value_type !== "string";
                  return (
                    <label className="graph-parameter-field" htmlFor={inputId} key={parameter.key}>
                      <span>{parameter.label}</span>
                      <input
                        id={inputId}
                        type={isNumber ? "number" : "text"}
                        min={parameter.minimum ?? undefined}
                        max={parameter.maximum ?? undefined}
                        step={parameter.step ?? (parameter.value_type === "integer" ? 1 : undefined)}
                        value={parameterValue(parameter)}
                        onChange={(event) => {
                          const rawValue = event.target.value;
                          onNodeParameterChange(
                            selectedNode.node_id,
                            parameter.key,
                            isNumber && rawValue !== "" ? Number(rawValue) : rawValue,
                          );
                        }}
                      />
                      {(parameter.minimum != null || parameter.maximum != null) && (
                        <small>范围：{parameter.minimum ?? "不限"} – {parameter.maximum ?? "不限"}</small>
                      )}
                    </label>
                  );
                })}
              </div>
            </>
          )}

          {selectedIssues.length > 0 && (
            <div className="graph-node-issue-list">
              <strong>节点校验问题</strong>
              {selectedIssues.map((issue) => (
                <small key={`${issue.code}:${issue.message}`}>{issue.message}</small>
              ))}
            </div>
          )}

          <details className="graph-connection-builder">
            <summary>手动选择端口连接</summary>
            <p>也可以直接从节点输出端拖动到兼容的输入端。</p>
            <label htmlFor="graph-source">1. 来源节点与输出</label>
            <select
              id="graph-source"
              value={selectedSource?.value ?? ""}
              onChange={(event) => {
                setSourceValue(event.target.value);
                setTargetValue("");
              }}
              disabled={sourceOptions.length === 0}
            >
              {nodes.map((node) => {
                const options = sourceOptions.filter((option) => option.nodeId === node.node_id);
                if (options.length === 0) return null;
                return (
                  <optgroup
                    key={node.node_id}
                    label={`${options[0].nodeName} · ${node.node_id}`}
                  >
                    {options.map((option) => (
                      <option key={option.value} value={option.value}>
                        {portLabel(option.port.name)}（{option.port.name}）
                      </option>
                    ))}
                  </optgroup>
                );
              })}
            </select>
            {selectedSource && (
              <div className="graph-port-selection">
                <strong>{selectedSource.nodeName}</strong>
                <span>输出：{portLabel(selectedSource.port.name)}</span>
                <code>{selectedSource.port.data_type}</code>
              </div>
            )}
            <label htmlFor="graph-target">2. 目标节点与输入</label>
            <select
              id="graph-target"
              value={selectedTarget?.value ?? ""}
              onChange={(event) => setTargetValue(event.target.value)}
              disabled={targetOptions.length === 0}
            >
              {targetOptions.length === 0 && <option value="">暂无可用的兼容输入端口</option>}
              {nodes.map((node) => {
                const options = targetOptions.filter((option) => option.nodeId === node.node_id);
                if (options.length === 0) return null;
                return (
                  <optgroup
                    key={node.node_id}
                    label={`${options[0].nodeName} · ${node.node_id}`}
                  >
                    {options.map((option) => (
                      <option key={option.value} value={option.value}>
                        {portLabel(option.port.name)}（{option.port.name}）
                      </option>
                    ))}
                  </optgroup>
                );
              })}
            </select>
            {selectedTarget ? (
              <div className="graph-port-selection target">
                <strong>{selectedTarget.nodeName}</strong>
                <span>输入：{portLabel(selectedTarget.port.name)}</span>
                <code>{selectedTarget.port.data_type}</code>
              </div>
            ) : (
              <p className="graph-port-empty">
                {selectedSource
                  ? "兼容输入可能已被连接占用；可在下方移除原连接后重新选择。"
                  : "当前画布中没有可作为来源的输出端口。"}
              </p>
            )}
            <button type="button" disabled={!selectedSource || !selectedTarget} onClick={connect}>
              连接这两个端口
            </button>
          </details>

          <details className="graph-link-list">
            <summary className="graph-link-heading">
              <strong>当前连接</strong><span>{links.length} 条</span>
            </summary>
            {links.length === 0 ? (
              <p>尚未建立端口连接。</p>
            ) : links.map((link, index) => (
              <div
                className={`graph-link-row ${selectedLinkIndex === index ? "selected" : ""}`}
                key={`${portKey(link.source.node_id, link.source.port)}-${index}`}
                onClick={() => setSelectedLinkIndex(index)}
              >
                <div>
                  <strong>{nodeDisplayName(link.source.node_id)}</strong>
                  <small>{portLabel(link.source.port)} · {link.source.port}</small>
                </div>
                <span>→</span>
                <div>
                  <strong>{nodeDisplayName(link.target.node_id)}</strong>
                  <small>{portLabel(link.target.port)} · {link.target.port}</small>
                </div>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    removeLink(index);
                  }}
                >移除</button>
              </div>
            ))}
          </details>
        </aside>
      </div>
    </section>
  );
}
