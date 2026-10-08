# ADR-0002：首个边缘目标采用JetPack 6.2.3与ROS 2 Humble

- 状态：已接受
- 日期：2026-10-02

## 背景

现有硬件为Jetson AGX Orin Developer Kit，已安装JetPack 6.2.3、Ubuntu 22.04.5和Python 3.10.12。平台需要形成可重复验证的首个ARM64边缘目标，而不是围绕Windows原型环境设计Edge Agent。

## 决策

1. 保留当前JetPack 6.2.3系统，不立即升级；
2. Edge Agent支持Python 3.10，并优先原生安装为systemd服务；
3. 后续ROS适配使用ROS 2 Humble；
4. Jetson GPU组件使用匹配L4T R36.5.2的容器基础镜像；
5. 控制面与边缘端使用独立运行时和发布包；
6. 第一个硬件场景只接入图像采集，不控制运动设备。

## 理由

- 与现有系统和Python版本直接兼容；
- 避免升级JetPack引入驱动、CUDA、TensorRT和厂商SDK迁移风险；
- Ubuntu 22.04与ROS 2 Humble形成稳定且明确的组合；
- 先验证Agent、设备描述、部署和监控链路，再引入ROS复杂度。

## 后果

- Platform API与Edge Agent不再共享同一Python最低版本；
- CI必须增加Python 3.10和Linux ARM64验证；
- JetPack升级需要新的ADR、兼容性验证和回滚方案；
- 使用JetPack 7专属能力的组件暂不进入首个发布范围。

