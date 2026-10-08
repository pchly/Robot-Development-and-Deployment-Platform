# 机器人开发平台

> 需要把整套工程交给其他人时，从[项目交接与复现手册](docs/project-handover.md)开始。

仓库协作、Git LFS 和首次上传步骤见[Git仓库与上传指南](docs/git-repository-guide.md)，系统与镜像等非内嵌内容见[外部依赖与离线边界](docs/external-dependencies.md)。参与开发前请阅读[贡献指南](CONTRIBUTING.md)和[安全说明](SECURITY.md)。当前尚未完成仓库级开源许可证审查，建议使用私有远端仓库。

这是一个面向机器人项目的工程化开发平台。它把硬件、算法、仿真、标定和运行环境沉淀为版本化资产，在Web中形成不可变工作流修订，并把固定版本与摘要的部署计划交给目标Edge节点执行。

当前首先收敛已有框架，不继续扩展硬件品类。系统边界、领域对象、唯一主流程和功能冻结门槛见[当前平台基线](docs/current-platform-baseline.md)，完整文档入口见[文档导航](docs/README.md)。

## 当前版本

| 模块 | 版本 |
|---|---|
| Platform API | 0.29.0 |
| Edge Agent | 0.21.0 |
| SQLite Schema | 3 |
| MuJoCo空间工作流 | 0.3.0 |

## 稳定架构

```text
Web Studio ──设计与操作──▶ Platform API ◀──计划与状态── Edge Agent
                                  │                         │
                                  ▼                         ▼
                         SQLite控制面数据             本地受管Runtime
```

- Web Studio：项目、组件、制品、画布、修订和运行记录；
- Platform API：Catalog、校验、制品变体解析、不可变计划和持久化；
- Edge Agent：节点盘点、受控制品安装、Runtime白名单执行和状态回报；
- Runtime：在目标节点本地处理视频、深度、张量、仿真和设备数据。

Studio不直接访问硬件，Platform API不承载高频数据面，Edge Agent不执行Manifest中的任意Shell命令。

## 当前运行链路

### Jetson真实感知

`RealSense D415 → YOLOX TensorRT → 检测指标与低频预览`

该链路只产生感知结果，不产生机器人运动输出。

### Windows机械臂仿真

`模拟RGB-D → 模拟检测 → 空间定位 → 笛卡尔规划 → Safety Gate → MuJoCo机械臂`

该链路使用独立的标定配置制品，只允许仿真运动。

### Windows真实摄像头视觉闭环

`Windows摄像头 → YOLOX ONNX CPU → 目标框仿真映射 → Safety Gate → MuJoCo机械臂`

该链路使用真实摄像头和真实YOLOX推理；Runtime以配置帧率为目标节拍、与Agent心跳解耦地连续执行。运行卡片保持上限约5 FPS轻量预览，点击“打开实时监控”后创建有TTL的按需会话并提升到上限约10 FPS；关闭或失联后自动回落。Windows闭环工作流0.1.5使用MuJoCo Runtime 0.4.0的实时运动学跟随，并将控制循环与预览渲染、编码和上传解耦：控制侧只发布不可变快照，预览侧只保留最新帧，变慢时丢弃旧帧而不反压控制。运行指标同时给出采集、预处理、推理、后处理、控制、渲染、上传和帧龄耗时。二维检测框只映射到受限的MuJoCo仿真工作空间，不作为真实机械臂标定结果。完整手工流程见[Windows摄像头闭环验收](docs/windows-webcam-yolox-mujoco-acceptance.md)。

## 唯一使用流程

1. Catalog加载组件、可执行配方和制品；
2. 在Studio选择项目、工作流模板和目标Edge节点；
3. 在画布中配置节点、参数和类型化连线；
4. 执行统一部署预检，由Platform API无副作用地检查画布、配方、节点、设备、能力和制品；
5. 如有缺失，执行受控制品准备并重新预检；
6. 保存不可变工作流修订；
7. Platform API再次编译不可变DeploymentPlan；
8. Edge Agent二次校验并启动本地Runtime；
9. 在Studio观察状态、指标和预览，执行停止或版本回退；
10. 运行进入终态后检查自动生成的验收结论、分项证据和事件时间线。

任意画布可以保存；只有当前画布与当前目标节点通过统一预检后才开放启动。最终结论仍以启动时Platform API编译和Edge Agent复核为准。终态运行的结构化验收规则见[运行验收报告](docs/runtime-acceptance-report.md)。

## 目录结构

```text
robot-development-platform/
├── apps/studio/                 # React Web Studio
├── services/platform-api/       # FastAPI控制面
├── services/edge-agent/         # Windows/Jetson共用Agent核心
├── hardware-adapters/           # 硬件定义、Profile与发现插件
├── algorithm-packages/          # 算法定义、模型和Engine
├── workflow-runtimes/           # 可执行工作流配方
├── artifact-packages/           # 独立制品与平台发行变体
├── simulation-runtimes/         # MuJoCo等仿真执行包
├── scripts/                     # 开发环境启停
├── tools/                       # Manifest校验与脚手架
└── docs/                        # 当前基线、ADR、专题和验收记录
```

## 开发环境

首次准备：

```powershell
cd <PROJECT_ROOT>
.\scripts\setup.cmd
```

Windows控制面并连接远端Jetson：

```powershell
.\scripts\dev.cmd -Lan -WithoutAgent
```

Windows控制面并启动MuJoCo仿真节点：

```powershell
.\scripts\dev.cmd -Lan -SimulationAgent
```

访问地址：

- Web Studio：`http://127.0.0.1:5173`
- Platform API：`http://127.0.0.1:8765`
- API文档：`http://127.0.0.1:8765/docs`
- 后台日志：`.run/`

停止开发环境：

```powershell
.\scripts\stop-dev.cmd
```

局域网模式当前没有身份认证，只能在可信开发网络或受控Tailscale网络中使用，不能暴露到公网。

## 自动检查

```powershell
cd services/platform-api
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider

cd ../edge-agent
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider

cd ../../apps/studio
npm.cmd run build

cd ../..
.\services\platform-api\.venv\Scripts\python.exe tools\validate_asset_packages.py
```

## 当前开发纪律

在已有Windows仿真闭环、Jetson感知闭环、历史回退、控制面恢复和Agent重连完成一轮统一验收前，只处理框架边界、契约一致性、可靠性和界面可用性问题。SR101、其他机械臂、移动底盘和新的算法Runtime在此之后按同一资产与部署主流程接入。
