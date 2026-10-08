# 文档导航

## 交接入口

- [项目交接与复现手册](project-handover.md)：面向新接收者的一站式系统说明、代码地图、部署步骤、完整闭环、质量验证、安全边界和交接清单。
- [Git仓库与上传指南](git-repository-guide.md)：仓库边界、Git LFS、首次私有上传、保护规则和克隆复现。
- [外部依赖与离线边界](external-dependencies.md)：说明仓库内置资源、系统依赖、容器镜像和完整离线交付还需准备的内容。

## 当前事实

- [当前平台基线](current-platform-baseline.md)：系统边界、领域对象、主流程、运行族和功能冻结门槛。
- [系统架构](architecture.md)：三个进程、四个平面和部署拓扑。
- [工程环境](engineering-environment.md)：Windows、Jetson和仿真节点的运行位置与启动方式。
- [资产包契约](asset-package-contract.md)：组件、制品、平台变体和可执行配方Manifest。
- [控制面持久化](control-plane-persistence.md)：SQLite数据边界、Schema和备份。
- [部署版本与回退](deployment-version-rollback.md)：不可变计划和安全切换状态机。
- [运行验收报告](runtime-acceptance-report.md)：终态判定、证据保存、事件时间线和工程边界。
- [当前基线验收清单](baseline-acceptance-checklist.md)：新增功能前的统一手工验收步骤。
- [Windows摄像头→YOLOX→MuJoCo闭环验收](windows-webcam-yolox-mujoco-acceptance.md)：按真实工程顺序完成项目、制品、画布、预检、运行与观测。

## 已接受的架构决策

`docs/decisions/`保存ADR。ADR解释“为什么这样设计”，不用于替代当前版本和操作说明。

## 专题设计

- [RealSense→YOLO垂直链路](realsense-yolo-vertical-slice.md)
- [视觉到MuJoCo架构](vision-to-mujoco-architecture.md)
- [安全门契约](safety-gate-contract.md)
- [模拟执行器契约](simulated-actuator-contract.md)
- [容器镜像分发](container-image-distribution.md)
- [TensorRT Engine分发](tensorrt-engine-distribution.md)

## 历史与验收记录

- `framework-implementation-plan.md`和`project-roadmap.md`是阶段推进记录；
- `validation/`保存特定日期、设备和版本的验收证据；
- 历史文档中的版本号只代表当时证据，不代表当前运行基线。

遇到冲突时按以下顺序判断：当前代码与Manifest → ADR → [当前平台基线](current-platform-baseline.md) → 专题文档 → 历史里程碑与验收记录。
