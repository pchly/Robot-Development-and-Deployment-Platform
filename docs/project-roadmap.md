# 项目路线图

> 本文保留早期阶段划分。当前实现已经跨越多个Phase，实际系统边界和新增功能前的冻结门槛见[机器人开发平台当前基线](current-platform-baseline.md)。

## 总体目标

建立一个能够登记硬件与算法、验证兼容性、编排工作流、部署到边缘节点并观察运行状态的机器人开发平台。

当前按[框架优先实施计划](framework-implementation-plan.md)推进：先完成模拟工作流闭环，再接入RealSense和YOLO实机链路。

## Phase 0：工程基线

交付物：

- 独立Git仓库和版本策略；
- 产品范围、架构边界和ADR；
- Windows、Linux x86_64、Linux ARM64支持矩阵；
- 自动化测试、CI和发布产物定义；
- Jetson存储与网络准备完成。

退出条件：

- `/data/robot-platform`位于NVMe且至少有100 GiB可用空间；
- Docker数据根目录和现有容器用途完成盘点，迁移方案不得破坏已有工作负载；
- Edge Agent能够在Python 3.10干净环境安装；
- Windows与Jetson可以通过固定局域网地址互相访问；
- 第一条业务链路和验收指标获得确认。

## Phase 1：Jetson Edge Agent

交付物：systemd服务、设备身份、重连、基础遥测、日志、安装与卸载脚本。

退出条件：重启自启动、断网恢复、API重启恢复、连续运行24小时。

当前进度（2026-10-02）：

- 已通过Jetson AGX Orin实机注册、心跳、系统遥测和Web展示；
- 已通过Platform API重启后的指数退避与自动重新注册验证；
- systemd单元和版本化安装器已完成本地语法检查，等待Jetson安装验收；
- 重启自启动、断网恢复和连续运行24小时尚未验收。

## Phase 2：设备清单

交付物：`DeviceManifest`规范、模拟设备、Jetson系统资源适配器、USB UVC相机适配器。

退出条件：设备插拔可检测，Web Studio可查看设备且默认只读。

首要垂直链路：区分硬件库中的RealSense产品定义和Jetson实际发现的设备实例，先完成只读发现与RGB流能力描述。

当前进度（2026-10-02）：

- 已定义RealSense硬件资产、YOLO算法资产及类型化端口；
- Edge Agent的USB只读发现与心跳上报已完成本地测试；
- Studio组件页已接入资产目录和端侧设备实例；
- Jetson上的隔离Runtime镜像已完成D415实机枚举及RGB/Depth有限帧采集；待完成稳态指标、拔插恢复、长稳测试和Studio部署闭环。

## Phase 3：组件资产

交付物：`ComponentManifest`、版本管理、输入输出与资源约束、ARM64制品。

退出条件：视频采集和示例算法能够分别打包、登记和验证。

首要垂直链路：RealSense采集组件、YOLO检测组件、ARM64 Runtime镜像和目标Jetson生成的TensorRT引擎。

## Phase 4：工作流

交付物：版本化工作流DSL、兼容检查、执行状态机和可视化编辑器。

退出条件：视频文件或相机经过算法处理，结果稳定上报Web Studio。

首要垂直链路：Studio选择RealSense与YOLO后生成部署计划，在Jetson融合Runtime中本地执行，Platform API只接收状态、指标、检测元数据和低频预览。

## Phase 5：部署与运维

交付物：远程部署、健康检查、日志、指标、失败回滚和审计记录。

退出条件：能够从平台升级和回退Jetson上的非安全关键组件。

## Phase 6：ROS与运动设备

交付物：ROS 2 Humble适配器、仿真、硬件在环测试、安全状态机和控制器协议。

退出条件：离线、仿真和安全验收通过后，才允许进入真实运动实验。

