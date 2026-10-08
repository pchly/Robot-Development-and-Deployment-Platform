# 机器人开发平台交接与复现手册

> 文档状态：当前工程交接入口  
> 基线日期：2026-10-08  
> 适用目录：`robot-development-platform/`  
> 目标：接收者仅凭本工程目录和本文档，即可理解系统边界、启动平台、复现现有闭环、验证质量并继续开发。

## 1. 先说结论

这不是一个单独的视觉演示程序，而是一套面向机器人项目的轻量工程平台。它把硬件、算法、模型、运行环境、标定和仿真定义为版本化资产，通过 Web Studio 编排工作流，再由控制面生成不可变部署计划，交给目标节点上的 Agent 校验并执行。

当前已经形成三个可运行闭环：

1. **Jetson 真实感知链路**：RealSense D415 → YOLOX TensorRT → 指标与预览；
2. **Windows 仿真机械臂链路**：模拟 RGB-D → 模拟检测 → 空间定位 → 笛卡尔规划 → Safety Gate → MuJoCo；
3. **Windows 摄像头视觉闭环**：Windows 摄像头 → YOLOX ONNX CPU → 目标映射 → Safety Gate → MuJoCo 机械臂。

当前系统能验证“项目—资产—编排—预检—部署—运行—观测—停止/回退—验收”这条完整工程主线。它还**不是**生产级机器人控制系统：没有真实机械臂驱动、独立硬安全回路、身份鉴权、TLS、RBAC 和生产审计，不应直接控制真实执行器。

## 2. 交接包应该包含什么

### 2.1 推荐交接方式

最稳妥的方式是把整个 `robot-development-platform` 目录制作成压缩包，同时排除可重建的缓存和本机运行状态。这样可以保留当前 Manifest 引用的模型、wheel、TensorRT Engine 和其他二进制制品。

建议保留：

- `apps/`
- `services/`
- `hardware-adapters/`
- `algorithm-packages/`
- `workflow-runtimes/`
- `simulation-runtimes/`
- `artifact-packages/`
- `tools/`
- `scripts/`
- `docs/`
- 根目录的 `README.md`、`.python-version`、`.nvmrc`、配置与锁定文件；
- 所有被 Manifest 引用的 `dist/` 文件；
- `simulation-runtimes/windows-webcam-yolox-mujoco/models/yolox_nano.onnx`。

建议排除：

- `.venv/`
- `node_modules/`
- `.run/`
- Python `__pycache__/`
- `.pytest_cache/`、`.ruff_cache/`、覆盖率文件；
- 日志、临时目录和其他可以重新生成的缓存；
- `apps/studio/dist/`，它是前端构建输出，可以重新生成。

### 2.2 一个非常重要的例外

仓库级忽略规则会忽略一般意义上的 `dist/`，但本项目的一些 `dist/` 保存的是**受 Manifest 摘要约束的交付制品**，不是普通构建缓存。直接复制工程目录时必须保留它们；若从版本库重新拉取后发现这些文件缺失，则必须重新构建或导入制品，并同步更新 Manifest 中的 SHA-256，不能只替换文件。

交接前后都执行：

```powershell
cd <PROJECT_ROOT>
.\services\platform-api\.venv\Scripts\python.exe tools\validate_asset_packages.py
```

只有出现类似下面的成功统计，才说明 Manifest 引用和文件摘要是一致的：

```text
5 hardware, 9 algorithms, 7 recipes, 12 artifacts
```

### 2.3 是否携带现有项目和历史记录

源码与控制面状态应分开交接。默认数据库是：

```text
services/platform-api/.data/control-plane.sqlite3
```

- 只需要复现功能：不必携带数据库，系统会建立新状态；
- 需要保留现有项目、不可变修订、部署记录和验收报告：先停止平台，再单独复制该 SQLite 文件；
- 不要在 Platform API 写数据库时直接复制文件；
- 不要把数据库当作源码的一部分提交或长期分发。

数据库位置可通过环境变量覆盖：

```powershell
$env:ROBOT_PLATFORM_DB_PATH = 'D:\robot-platform-data\control-plane.sqlite3'
```

## 3. 当前已验证版本

| 模块 | 当前版本/状态 |
|---|---|
| Platform API | 0.29.0 |
| Edge Agent | 0.21.0 |
| SQLite Schema | v3 |
| Web Studio 包版本 | 0.1.0 |
| MuJoCo Arm Runtime | 0.4.0 |
| Windows Webcam + YOLOX + MuJoCo Runtime | 0.2.0 |
| MuJoCo 工作流配方 | 0.3.0 |
| Windows 摄像头闭环配方 | 0.1.5 |
| RealSense + YOLOX 配方 | 0.1.1 |
| RealSense + YOLOX Runtime 镜像 | 0.1.3 |
| Python 目标版本 | `.python-version` 为 3.12；包声明允许的版本以各自 `pyproject.toml` 为准 |
| Node 目标版本 | `.nvmrc` 为 24.18.0 |

2026-10-08 在当前 Windows 开发机完成的基线验证：

| 检查项 | 结果 |
|---|---|
| Platform API | Ruff 通过；94 项测试通过；覆盖率 91.82% |
| Edge Agent | Ruff 通过；180 项测试通过；覆盖率 90.00% |
| MuJoCo Runtime | 14 项测试通过；覆盖率 96.04% |
| Windows 摄像头闭环 Runtime | Ruff 通过；7 项测试通过 |
| Web Studio | 生产构建通过 |
| Manifest 与制品摘要 | 5 个硬件、9 个算法、7 个配方、12 个制品全部通过 |

这些结果是交接时的基线，不代表所有目标机器无需重新验收。接收者应在自己的 Windows、Jetson 和摄像头环境上重新执行第 13 节。

## 4. 系统架构与运行位置

```text
┌──────────────── Windows 开发/控制机 ────────────────┐
│ Web Studio (React/Vite, :5173)                      │
│        │ HTTP                                       │
│ Platform API (FastAPI, :8765) ── SQLite 控制面状态  │
│        │ HTTP / Tailscale                           │
│ Windows Simulation Agent                            │
│        ├─ Windows 摄像头 + YOLOX ONNX Runtime       │
│        └─ MuJoCo 机械臂 Runtime                     │
└────────┼─────────────────────────────────────────────┘
         │ 受控网络中的出站连接
┌────────▼──────── Jetson AGX Orin ───────────────────┐
│ Edge Agent (systemd)                                │
│        ├─ RealSense 插件                            │
│        ├─ TensorRT Engine                           │
│        └─ RealSense + YOLOX Docker Runtime          │
└──────────────────────────────────────────────────────┘
```

三个长期进程的职责：

| 进程 | 默认运行位置 | 负责 | 不负责 |
|---|---|---|---|
| Web Studio | Windows 浏览器 | 项目、资产、画布、预检、运行记录和监控交互 | 不直接访问硬件，不运行控制循环 |
| Platform API | Windows | Catalog、校验、制品解析、不可变计划、状态机、持久化 | 不承载原始视频、深度图、张量或高频控制 |
| Edge/Simulation Agent | Windows 或 Jetson | 节点盘点、受控制品安装、Runtime 白名单执行、心跳和状态上报 | 不执行 Manifest 中任意 Shell，不绕过部署计划 |

系统可以分成四个平面：

- **设计平面**：项目、组件、工作流画布和参数；
- **控制平面**：Catalog、预检、修订、部署计划、状态和回退；
- **执行平面**：Agent、插件、容器、仿真和设备 Runtime；
- **观测平面**：指标、低频预览、按需实时监控、事件时间线和终态验收。

核心边界是：原始视频、深度、模型张量和高频运动循环尽量留在节点本地；控制面只传递配置、不可变计划、状态、结构化指标和有限预览。

## 5. 核心领域对象

| 对象 | 作用 |
|---|---|
| Component Definition | 定义硬件或算法节点、参数、输入输出端口及数据类型 |
| Artifact Definition | 定义可安装/可分发内容、平台变体、摘要和能力要求 |
| Workflow Recipe | 定义平台认可并可执行的工作流模板 |
| Workflow Revision | 用户保存后的不可变画布、参数和连接快照 |
| DeploymentPlan | 控制面针对某一目标节点重新解析并编译出的不可变执行计划 |
| Artifact Installation | 制品准备、安装、激活与失败事务记录 |
| Agent Record | 节点身份、架构、系统、能力、心跳和盘点 |
| Runtime Snapshot | 当前状态、指标、预览、终止原因和安全状态 |
| Acceptance Report | 运行结束后根据证据生成的 passed/warning/failed/incomplete 结论 |

设计原则：

1. 画布可以编辑，但运行的永远是保存后的不可变修订；
2. DeploymentPlan 与具体目标节点、能力、平台变体和制品摘要绑定；
3. Platform API 先预检，Agent 启动前再做一次本地校验；
4. Catalog 中“存在”不等于目标节点“已安装”；
5. 同一个逻辑资产可以为 Windows x86_64、Linux aarch64 等平台发布不同制品变体；
6. 仿真资产也是正式资产，应使用同一套版本、摘要、能力和部署规则。

## 6. 目录与代码入口

```text
robot-development-platform/
├─ apps/studio/                         # React Web Studio
├─ services/platform-api/               # FastAPI 控制面与 SQLite
├─ services/edge-agent/                 # Windows/Jetson 共用 Agent 核心
├─ hardware-adapters/                   # 硬件定义、Profile、插件和发现逻辑
├─ algorithm-packages/                  # 算法定义、模型、Engine
├─ workflow-runtimes/                   # 可执行工作流配方与运行镜像契约
├─ simulation-runtimes/                 # MuJoCo 和 Windows 视觉闭环 Python 包
├─ artifact-packages/                   # 独立制品、标定和平台发布变体
├─ tools/                               # Manifest 校验及脚手架
├─ scripts/                             # Windows 环境初始化、启停
└─ docs/                                # 架构、契约、验收和历史证据
```

### 6.1 Web Studio

- `apps/studio/src/App.tsx`：页面组织、项目/资产/流程/运行记录主界面；
- `apps/studio/src/api.ts`：Platform API 客户端；
- `apps/studio/src/WorkflowGraphEditor.tsx`：节点、端口、连线、选择、删除和参数编辑；
- `apps/studio/src/styles.css`：布局、响应式界面、运行卡片和实时监控窗口样式。

当前实时监控采用按需会话，不是控制链路：

- 运行卡片保留轻量预览；
- 点击“打开实时监控”才创建有 TTL 的较高帧率会话；
- Canvas 绘制，窗口可拖动、缩放和关闭；
- 网络或渲染变慢时丢弃旧帧，不把压力反向传入控制循环；
- 监控画面不能作为安全判断或精确测量依据。

### 6.2 Platform API

目录：`services/platform-api/src/robot_platform_api/`

重点代码按职责包括：

- `main.py`：FastAPI 应用和 HTTP 路由入口；
- `models.py`：控制面数据模型；
- `catalog.py`：Manifest Catalog 和少量 bootstrap 定义；
- 工作流编译/校验模块：画布结构、端口类型、必需输入、方向和配方检查；
- artifact resolution/preparation/installation 模块：平台变体解析、缺失制品准备和安装事务；
- deployment 模块：不可变计划、状态机、停止、回退、预览和实时会话；
- acceptance 模块：终态验收报告与证据归纳；
- `control_store.py`：SQLite Schema v3 与持久化；
- registry 模块：Agent 注册、心跳、在线状态、能力和库存。

API 的准确入口和请求模型以启动后的 Swagger 为准：

```text
http://127.0.0.1:8765/docs
```

主要路由族包括：

- `/health`
- `/api/v1/agents`
- `/api/v1/projects`
- `/api/v1/catalog/*`
- `/api/v1/workflows/*`
- `/api/v1/deployments/*`
- `/api/v1/artifacts/*`
- Agent 注册、心跳、库存和执行状态路由。

### 6.3 Edge Agent

目录：`services/edge-agent/src/robot_edge_agent/`

- `__main__.py`：CLI、配置、插件加载和启动；
- `runtime.py`：注册、心跳、轮询、重连和运行状态循环；
- `client.py`：控制面通信；
- `deployment_runtime.py`：受支持 Runtime 的白名单选择与执行；
- artifact installer 相关模块：文件、wheel、Runtime 包、容器、激活和摘要校验；
- adapter/telemetry/spatial 模块：硬件适配、遥测和空间数据处理。

Agent 不是“任意流程解释器”。新工作流只有在配方、制品、能力和本地 Runtime 实现同时齐全时才可执行。该限制是安全边界，不应为了方便改成执行任意命令。

## 7. 当前资产清单

### 7.1 硬件定义（5）

- `hardware.camera.mock@0.1.0`
- `hardware.arm.mock@0.1.0`
- `hardware.arm.mujoco@0.1.0`
- `hardware.camera.realsense@0.1.0`
- `hardware.camera.windows-webcam@0.1.0`

### 7.2 算法定义（9）

- `algorithm.vision.mock_detector@0.1.0`
- `algorithm.vision.mock_filter@0.1.0`
- `algorithm.motion.mock_target_selector@0.1.0`
- `algorithm.spatial.rgbd_target_localizer@0.1.0`
- `algorithm.motion.cartesian_point_to_point@0.1.0`
- `algorithm.motion.safety_gate@0.1.0`
- `algorithm.motion.detection_to_sim_target@0.1.0`
- `algorithm.vision.yolo@0.1.0`
- `algorithm.vision.yolox_onnx@0.1.0`

### 7.3 可执行配方（7）

- `workflow.mock_detection@0.1.0`
- `workflow.mock_detection_pipeline@0.1.0`
- `workflow.mock_vision_arm@0.1.0`
- `workflow.mock_vision_arm_safety@0.2.0`
- `workflow.mujoco_vision_arm_safety@0.3.0`
- `workflow.realsense_yolox@0.1.1`
- `workflow.windows_webcam_yolox_mujoco@0.1.5`

### 7.4 逻辑制品（12）

- RealSense Edge 插件 0.1.0；
- RealSense Runtime 镜像 0.1.0；
- Windows Webcam Edge 插件发行版 0.1.2；
- YOLOX Runtime 镜像 0.1.0；
- YOLOX-Nano COCO 模型 0.1.1rc0；
- YOLOX TensorRT Engine 0.1.0；
- RealSense + YOLOX Runtime 镜像 0.1.3；
- Container Import Smoke 镜像 0.1.0；
- 模拟相机到 MuJoCo 机械臂标定 1.0.0；
- MuJoCo 六轴机械臂 Runtime 包 0.4.0；
- MuJoCo 模型资产 0.1.0（构建源）；
- Windows Webcam + YOLOX + MuJoCo Runtime 包 0.2.0。

MuJoCo Runtime 同一逻辑制品已有 Windows x86_64 和 Linux aarch64 变体。目标平台由 Manifest 的系统、架构和能力约束选择，不在业务代码里靠文件名猜测。

## 8. 从零启动 Windows 开发环境

### 8.1 前置条件

- Windows 10/11 x86_64；
- Python，推荐与 `.python-version` 一致；
- Node.js，与 `.nvmrc` 一致；
- npm；
- 摄像头驱动可用；
- 若连接 Jetson：Windows 与 Jetson 已加入同一个 Tailscale 网络，或处于可信局域网；
- 若安装/导入容器制品：Docker 可用。

首次初始化：

```powershell
cd <PROJECT_ROOT>
.\scripts\setup.cmd
```

该脚本会分别建立 API、Agent 和 MuJoCo Runtime 环境，安装 Windows 视觉闭环依赖，并安装前端依赖。不要把某个服务的 `.venv` 当作全仓库唯一 Python 环境。

### 8.2 启动 Windows 仿真节点

```powershell
cd <PROJECT_ROOT>
.\scripts\dev.cmd -Lan -SimulationAgent
```

它会启动：

- Platform API：`http://127.0.0.1:8765`
- API 文档：`http://127.0.0.1:8765/docs`
- Web Studio：`http://127.0.0.1:5173`
- Windows Simulation Agent：
  - 节点 ID：`simulation-windows-01`
  - 名称：`WindowsMuJoCoSimulationNode`

运行日志和进程记录位于：

```text
<PROJECT_ROOT>/.run/
```

Simulation Agent 本地数据位于：

```text
<PROJECT_ROOT>/.run/simulation-agent/
```

其中包括 Runtime 包、标定、staging 和运行目录。

如果提示开发环境已经运行：

```powershell
.\scripts\stop-dev.cmd
.\scripts\dev.cmd -Lan -SimulationAgent
```

不要重复打开多套 API/Studio/Agent 进程争用端口、数据库和摄像头。

### 8.3 只启动控制面、连接远端 Jetson

```powershell
cd <PROJECT_ROOT>
.\scripts\dev.cmd -Lan -WithoutAgent
```

### 8.4 停止

```powershell
cd <PROJECT_ROOT>
.\scripts\stop-dev.cmd
```

## 9. Windows 摄像头→YOLOX→MuJoCo 完整复现流程

这是最适合新接收者验收平台主线的闭环，因为它只需要一台 Windows 电脑、摄像头和当前工程目录。

### 9.1 准备

1. 执行 `scripts/setup.cmd`；
2. 执行 `scripts/dev.cmd -Lan -SimulationAgent`；
3. 打开 `http://127.0.0.1:5173`；
4. 在“边缘节点”确认 `simulation-windows-01` 在线；
5. 确认节点能力至少包含 `simulation.mujoco` 和 `inference.onnxruntime.cpu`；
6. 关闭 Teams、相机应用或其他独占摄像头的程序。

### 9.2 按真实工程顺序操作

1. **新建项目**：建立一个独立测试项目；
2. **选择目标环境**：目标节点选 `WindowsMuJoCoSimulationNode / simulation-windows-01`；
3. **选择配方**：选 `Windows 摄像头识别手机→MuJoCo 机械臂` 对应的可执行配方；
4. **检查制品**：进入制品页，确认目标节点所需 Runtime、模型和插件可解析；
5. **准备制品**：若预检提示缺失，点击准备/安装，等待节点盘点更新；
6. **编辑画布**：检查摄像头、YOLOX、目标映射、Safety Gate、MuJoCo 机械臂节点及其类型化连线；
7. **配置参数**：设置摄像头、目标类别/阈值、运动限制、超时和预览参数；
8. **检查工作流与部署条件**：必须同时通过接口检查和部署预检；
9. **保存为新修订**：保存后生成不可变 Revision；
10. **保存新修订并启动**：控制面编译不可变 DeploymentPlan，Agent 复核后运行；
11. **观察运行记录**：检查运行状态、摄像头检测框、MuJoCo 机械臂、控制频率、推理延迟和安全状态；
12. **按需实时监控**：点击运行卡片中的实时监控按钮；监控窗口只是观察通道；
13. **停止 Runtime**：停止后检查安全停机和终态验收报告；
14. **回退验证**：从历史计划选择“回退到此版本”，系统应生成替换计划，而不是修改旧计划。

### 9.3 正常现象与判定

- 摄像头画面有检测框；
- 目标类别与配方参数一致时，目标标记随画面位置变化；
- MuJoCo 机械臂跟随受限的仿真目标运动；
- 未检测到目标时，系统不应继续发出陈旧运动命令；
- 预览变慢时允许丢帧，但控制频率不应被预览拖慢；
- 关闭实时监控不会停止 Runtime；
- 停止 Runtime 后 Safety Gate/执行器进入安全状态并生成验收报告。

二维检测框到 MuJoCo 的映射只是闭环演示用的受限映射，不是手眼标定结果，不能迁移为真实机械臂坐标。

### 9.4 摄像头读取失败排查顺序

1. 关闭所有可能占用摄像头的应用；
2. 确认 Windows 隐私设置允许桌面应用访问摄像头；
3. 在设备管理器确认设备和驱动正常；
4. 停止所有平台进程后重新启动；
5. 查看 `.run/` 下 Simulation Agent 日志；
6. 检查配方中的摄像头索引/设备参数；
7. 若连续读取失败达到阈值，Runtime 会失败退出，这是预期的故障保护，不应改成无限吞错。

## 10. Jetson + RealSense + YOLOX 复现

### 10.1 已验证目标环境

- NVIDIA Jetson AGX Orin Developer Kit；
- Ubuntu 22.04；
- ARM64/aarch64；
- JetPack 6.2.3 / L4T 36.5.2；
- Python 3.10；
- TensorRT 10.3；
- RealSense D415；
- Docker；
- Tailscale 或可信局域网。

具体设备序列号不应写死在交接文档中，应在目标节点盘点和工作流参数中选择。

### 10.2 网络

推荐使用 Tailscale 的稳定节点名和私有网络，不把历史 IP 当成固定配置。Windows 启动：

```powershell
.\scripts\dev.cmd -Lan -WithoutAgent
```

Jetson 应能访问：

```text
http://<WINDOWS_TAILSCALE_IP>:8765/health
```

当前 LAN 模式没有认证和 TLS，只能在可信开发网络或受控 Tailscale 网络中使用，禁止直接映射到公网。

### 10.3 安装 Agent

把 `services/edge-agent` 的完整源码/发行包放到 Jetson 后，在该目录执行：

```bash
sudo bash deploy/jetson/install.sh \
  --server-url http://<WINDOWS_TAILSCALE_IP>:8765 \
  --agent-id jetson-agx-orin-01 \
  --name JetsonAGXOrin \
  --user nvidia
```

安装器会：

- 把 Python 源码复制到 `/data/robot-platform/edge-agent/releases/<UTC>`；
- 原子更新 `/data/robot-platform/edge-agent/current`；
- 写入 `/etc/robot-platform/edge-agent.env`；
- 安装并启用 `/etc/systemd/system/robot-edge-agent.service`；
- 重启 Agent，使其向 Platform API 注册。

常用运维命令：

```bash
sudo systemctl status robot-edge-agent.service --no-pager
sudo journalctl -u robot-edge-agent.service -f
sudo systemctl restart robot-edge-agent.service

PYTHONPATH=/data/robot-platform/edge-agent/current/src \
  python3 -c 'import robot_edge_agent; print(robot_edge_agent.__version__)'
```

关键目录：

```text
/data/robot-platform/edge-agent/releases/
/data/robot-platform/edge-agent/current
/data/robot-platform/edge-agent/plugins/
/data/robot-platform/edge-agent/staging/
/data/robot-platform/edge-agent/calibrations/
/data/robot-platform/edge-agent/runtime-packages/
/data/robot-platform/cache/tensorrt/
/data/robot-platform/runs/
```

Agent 建议使用普通用户运行。该用户若加入 `docker` 组，实质上拥有接近主机 root 的权限，因此只能执行白名单计划。Runtime 容器应保持无网络、非 privileged，并采用最小挂载和最小设备权限。

### 10.4 在 Studio 中运行

1. 确认 `jetson-agx-orin-01` 在线且盘点包含 RealSense/TensorRT/Docker 能力；
2. 新建项目或选择测试项目；
3. 选择 RealSense + YOLOX 可执行配方；
4. 目标节点选 Jetson；
5. 检查制品，准备缺失的插件、镜像、模型和 Engine；
6. 选择真实 RealSense 设备；
7. 检查工作流、保存新修订并启动；
8. 检查检测指标和低频预览；
9. 停止并查看验收报告。

该链路目前只做感知，不发送真实机械臂运动命令。

### 10.5 手工文件投递仅用于当前开发阶段

若尚未通过平台自动分发发行包，可用 Tailscale 文件投递：

Windows：

```powershell
tailscale file cp <FILE> jetson-agx-orin-01:
```

Jetson：

```bash
sudo tailscale set --operator=nvidia
mkdir -p /data/robot-platform/manual/incoming
tailscale file get /data/robot-platform/manual/incoming
sha256sum /data/robot-platform/manual/incoming/<FILE>
```

每次都先核对发布方给出的 SHA-256。该方法只是发行包运输通道，不代替平台制品解析、安装记录和激活状态。

## 11. 如何增加新的硬件、算法、平台制品或工作流

新增功能应沿现有资产主线完成，不应直接在前端写死一个按钮绕过 Catalog 和预检。

### 11.1 新硬件

1. 在 `hardware-adapters/` 增加硬件定义 Manifest；
2. 定义稳定的能力名、设备属性、参数和类型化端口；
3. 若需要发现或访问设备，实现 Agent 插件；
4. 把插件打包成带摘要的制品；
5. 为不同 OS/架构声明不同 variant；
6. 增加单元测试、故障路径和资源释放测试；
7. 运行 Manifest 校验和目标机盘点。

### 11.2 新算法

1. 在 `algorithm-packages/` 增加算法定义；
2. 明确输入输出 Schema，避免使用没有版本的松散字典；
3. 模型、Engine 和 Runtime 分开定义版本与摘要；
4. CUDA/TensorRT、ONNX CPU 等平台实现作为同一逻辑算法的不同制品变体；
5. 增加确定性测试、性能指标和失败语义。

### 11.3 新仿真或 Runtime

1. 在 `simulation-runtimes/` 或合适目录实现独立 Python 包/容器；
2. 保证生命周期明确：prepare/start/tick/stop/cleanup；
3. 把控制循环与预览渲染、编码、上传解耦；
4. 提供结构化指标、故障码和安全停止证据；
5. 打包 wheel/镜像并登记 platform variants；
6. 在 Agent `deployment_runtime.py` 中显式加入白名单适配；
7. 增加配方和端到端验收用例。

### 11.4 新工作流配方

1. 在 `workflow-runtimes/` 增加 recipe Manifest；
2. 列出允许节点、默认画布、必需连线、参数和所需制品；
3. 明确所需 Agent capabilities；
4. 让预检能判断架构不兼容、制品缺失、设备缺失和类型错误；
5. 保存不可变修订后再编译 DeploymentPlan；
6. 不允许用“Catalog 中存在”替代“目标节点可执行”。

### 11.5 Manifest 与摘要纪律

- 文件内容变化必须改变版本或构建标识，并更新 SHA-256；
- 平台特有的 wheel、镜像、Engine、标定文件都应有独立摘要；
- 不同 OS 无法共用的制品应使用同一逻辑资产下的不同 variant；
- Engine 必须绑定 GPU 架构、TensorRT 版本、精度和模型摘要；
- 标定文件必须绑定设备/坐标系/版本，不能使用模糊的“latest”；
- 所有 Manifest 修改后都运行 `tools/validate_asset_packages.py`。

## 12. 状态、日志、备份与恢复

### 12.1 持久化内容

- 项目；
- 不可变 Workflow Revision；
- DeploymentPlan 与状态历史；
- 制品安装事务；
- 终态 Acceptance Report；
- 低频结构化事件时间线。

### 12.2 仅在线/临时内容

- Agent 在线状态与最近心跳；
- 高频 Runtime 指标；
- 最新预览图和预览元数据；
- 原始视频、深度图和张量；
- Runtime 临时工作目录。

### 12.3 备份

开发阶段的安全做法：

1. 执行 `scripts/stop-dev.cmd`；
2. 确认 API 进程已退出；
3. 复制 `services/platform-api/.data/control-plane.sqlite3`；
4. 记录代码版本、Schema v3 和备份时间；
5. 恢复到新机器后先跑 API 测试，再启动 Studio/Agent。

生产化后应改用 SQLite 在线备份 API 或迁移到具备备份、迁移和并发策略的数据库，不应长期依赖手工文件复制。

## 13. 质量检查与验收

以下命令应在交接、升级或修改核心契约后全部通过。

### 13.1 Platform API

```powershell
cd <PROJECT_ROOT>\services\platform-api
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider
```

### 13.2 Edge Agent

```powershell
cd <PROJECT_ROOT>\services\edge-agent
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider
```

### 13.3 MuJoCo Runtime

```powershell
cd <PROJECT_ROOT>\simulation-runtimes\mujoco-arm
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider
```

### 13.4 Windows 视觉闭环 Runtime

该包由 MuJoCo 环境安装和运行：

```powershell
cd <PROJECT_ROOT>\simulation-runtimes\windows-webcam-yolox-mujoco
..\mujoco-arm\.venv\Scripts\python -m ruff check src tests
..\mujoco-arm\.venv\Scripts\python -m pytest -p no:cacheprovider
```

### 13.5 Web Studio

```powershell
cd <PROJECT_ROOT>\apps\studio
npm.cmd run build
```

### 13.6 Manifest 和制品

```powershell
cd <PROJECT_ROOT>
.\services\platform-api\.venv\Scripts\python.exe tools\validate_asset_packages.py
```

### 13.7 手工基线验收

执行 [当前基线验收清单](baseline-acceptance-checklist.md)，至少覆盖：

- Windows 仿真 Agent 注册/断线/重连；
- Windows 摄像头闭环启动、检测、机械臂跟随、实时监控和安全停止；
- Jetson Agent 注册和 RealSense 感知链路；
- 缺失制品、架构不兼容和能力不足时预检必须阻止启动；
- 停止、失败、替换和回退状态机；
- API/Agent 重启后的控制面恢复；
- 终态验收报告及事件时间线。

## 14. 终态验收报告的含义

Runtime 进入 stopped 或 failed 后，系统根据结构化证据生成报告。重要规则包括：

- 控制频率：达到目标 90% 以上为通过，75%～90% 为警告，低于 75% 为失败；
- P95 周期：不超过一个帧预算为通过，不超过 1.5 倍为警告，再高为失败；
- deadline miss：不超过 5 为通过，不超过 20 为警告，更多为失败；
- 执行器故障必须为 0；
- 有运动命令时，运动接受率达到 98% 为通过，90%～98% 为警告；
- 安全停止必须有证据；
- 最后预览年龄超过 500 ms 会产生观测警告；
- 没有运动命令的感知任务，运动项为 `not_applicable`，不是失败。

最终结论：

- `passed`：关键条件满足；
- `warning`：功能完成但存在性能或观测警告；
- `failed`：关键安全/执行/时序条件失败；
- `incomplete`：证据不足，不能宣称通过。

详细规则见 [运行验收报告](runtime-acceptance-report.md)。

## 15. 安全边界

1. 当前所有运动输出仅允许进入 MuJoCo 或 mock 执行器；
2. 接真实机械臂前必须增加硬件急停、独立安全控制器、软限位、速度/力矩限制、watchdog、使能钥匙和人工监督；
3. Safety Gate 软件节点不是硬安全认证设备；
4. 实时监控画面不是安全通道；
5. LAN 模式没有认证/TLS/RBAC，只能用于可信开发网络；
6. 不允许 Agent 执行 Manifest 中的任意 Shell；
7. Docker 运行不得默认使用 `--privileged`、host network 或无限制目录挂载；
8. 制品必须校验摘要，不能用同名文件或 `latest` 标签替代不可变身份；
9. 数据采集涉及人员画面时，应取得授权并避免把预览截图写入长期文档或版本库；
10. 对外演示前清理数据库、日志和图片中的个人信息。

## 16. 已知限制与技术债

- `catalog.py` 中仍有少量 bootstrap mock 定义；新资产应全部走 Manifest；
- Runtime 选择目前是 Agent 内显式白名单，不是任意图执行器；这是当前安全策略，也限制了扩展速度；
- Windows Simulation Agent 尚未安装为 Windows Service，重启后需要开发脚本启动；
- Platform API 仍是开发模式，没有反向代理、TLS、身份认证、RBAC 和完整审计；
- SQLite 适合当前单机控制面，不适合多实例高并发；
- 实时监控是有限帧率观察通道，运行卡片约 5 FPS、按需窗口上限约 10 FPS，不能替代本地显示或专业视频流；
- 没有保存完整原始视频回放，当前只保留结构化指标和终态证据；
- Windows 摄像头闭环的二维映射不是实际空间标定；
- RealSense 链路还没有接入真实执行器；
- 尚未完成跨平台 CI、长时间稳定性测试、断电恢复演练和灾难恢复；
- 尚未形成正式的源码/制品发布流水线，部分发行包仍可能依赖人工 Tailscale 传输；
- 当前 Docker 用户权限边界需要在生产化前重新设计。

## 17. 推荐的后续顺序

先收敛平台，不继续堆功能：

1. 冻结当前版本，完成 Windows 与 Jetson 的统一基线验收；
2. 生成一个可重复发布的源码包、制品包和 SHA-256 清单；
3. 增加一键诊断包：版本、节点能力、计划、最近日志、指标、验收报告，不包含原始个人画面；
4. 增加结构化运行回放和关键帧，而不是先保存全量视频；
5. 将 Windows Agent 服务化，并补充自动恢复与健康检查；
6. 为控制面增加身份、TLS、RBAC、审计和正式迁移/备份；
7. 再接 ROS 2 或真实机械臂适配器；
8. 真实硬件阶段必须先在仿真验证同一 Workflow Revision，再由独立安全链路限权运行。

## 18. 常见问题

### 启动时报“already running”

```powershell
.\scripts\stop-dev.cmd
.\scripts\dev.cmd -Lan -SimulationAgent
```

### Web 打不开

检查 `.run/` 日志，确认 5173/8765 未被其他进程占用，再访问 `127.0.0.1`，不要先用 LAN 地址排查本机问题。

### Jetson 在线但无法执行

在线只代表心跳正常。依次检查：节点能力、系统/架构、设备盘点、制品 variant、摘要、安装状态、配方和 Agent 白名单 Runtime。

### “Catalog 有制品”但启动按钮不可用

Catalog 只说明平台知道该资产。部署预检还会检查目标节点是否具备正确平台变体、能力、设备、安装/激活状态以及工作流端口完整性。

### 画面流畅但机械臂不动

检查目标类别、置信度、目标映射输出、Safety Gate 是否使能/急停/故障锁存、命令 TTL、执行器接受计数以及 MuJoCo Runtime 状态。不要把预览帧率当作控制频率。

### 预览卡顿

优先看分段指标：采集、预处理、推理、后处理、控制、渲染、编码、上传和帧龄。预览应允许降帧和丢旧帧，不能降低控制实时性来追求视觉平滑。

## 19. 接收者验收清单

交接完成前，双方应共同确认：

- [ ] 能解释 Studio、API、Agent 和 Runtime 分别运行在哪里；
- [ ] 能执行 `setup.cmd`、启动和停止平台；
- [ ] 能看到 Windows 与 Jetson 节点的在线状态和能力；
- [ ] Manifest 校验通过，二进制制品没有遗漏；
- [ ] Platform API、Edge Agent、两套 Runtime 测试和 Studio 构建通过；
- [ ] 能按第 9 节从新项目完成 Windows 视觉闭环；
- [ ] 能停止 Runtime 并读懂验收报告；
- [ ] 能在 Jetson 上查看 Agent 版本、systemd 状态和日志；
- [ ] 知道如何备份/恢复 SQLite，以及哪些数据不会持久化；
- [ ] 知道修改制品后必须更新版本与 SHA-256；
- [ ] 知道当前系统不能直接控制真实机械臂；
- [ ] 已记录目标机器的 Python/Node/Docker/JetPack/TensorRT/摄像头环境；
- [ ] 已把所有本机 IP、用户名和目录替换为目标环境配置，不依赖历史机器的偶然状态。

## 20. 文档阅读顺序

接收者建议按以下顺序阅读：

1. 本文档；
2. [当前平台基线](current-platform-baseline.md)；
3. [系统架构](architecture.md)；
4. [工程环境](engineering-environment.md)；
5. [资产包契约](asset-package-contract.md)；
6. [控制面持久化](control-plane-persistence.md)；
7. [部署版本与回退](deployment-version-rollback.md)；
8. [Windows 摄像头闭环验收](windows-webcam-yolox-mujoco-acceptance.md)；
9. [当前基线验收清单](baseline-acceptance-checklist.md)；
10. [安全门契约](safety-gate-contract.md) 和 [模拟执行器契约](simulated-actuator-contract.md)。

历史实施计划和 `docs/validation/` 只用于追溯当时证据。发生冲突时，判断优先级是：**当前代码与 Manifest → 已接受 ADR → 当前平台基线 → 专题文档 → 历史记录**。

