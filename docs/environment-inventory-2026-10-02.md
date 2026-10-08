# 环境盘点记录：2026-10-02

## Jetson边缘节点

| 项目 | 结果 |
|---|---|
| 型号 | NVIDIA Jetson AGX Orin Developer Kit |
| 架构 | ARM64 / aarch64 |
| JetPack | 6.2.3+b81 |
| L4T | R36.5.2 |
| 操作系统 | Ubuntu 22.04.5 LTS |
| 内核 | 5.15.199-tegra PREEMPT |
| Python | 3.10.12 |
| 内存 | 61 GiB，约53 GiB可用 |
| Swap | 30 GiB |
| 根分区 | 57 GiB，已使用88%，剩余6.8 GiB |
| NVMe | Samsung SSD 980 PRO 1.8 TiB，ext4，挂载到`/data` |
| Docker | 29.4.0 |
| NVIDIA Container Toolkit | 1.16.2-1 |
| 视频设备 | 未发现`/dev/video*` |
| USB图像设备 | 未发现，仅检测到USB Hub、键鼠和蓝牙设备 |
| 网络 | Wi-Fi `192.168.1.111/24`，以太网未连接 |
| SSH | 已启动 |
| CAN | `can0`、`can1`存在但处于DOWN状态，当前不启用 |
| Docker现状 | 8个镜像、2个运行中容器、119项构建缓存 |

## Windows开发节点

| 项目 | 结果 |
|---|---|
| 操作系统 | Windows 11家庭版中文版，10.0.26200，64位 |
| Git | 2.43.0.windows.1 |
| WSL | 未安装或命令不可用 |
| Docker | 未安装或命令不可用 |
| 已验证本地运行时 | Python 3.14.6、Node.js 24.18.0 |

## 风险与处理

### 已解决：工程数据落到NVMe

eMMC只剩6.8 GiB，但1.8 TiB NVMe已持久挂载到`/data`，剩余约1.6 TiB。Docker数据根目录已经是`/data/docker`，无需迁移。平台工作区、模型、数据集、制品和运行日志统一规划到`/data/robot-platform`。

已有容器`openral-openral-dev-1`和`moss-tts-server`分别使用`/data/robotics`与`/data/inspur`，属于现有工作负载。本项目不得清理其镜像、缓存、卷或挂载目录。

当前用户目录占用约19 GiB，其中Miniconda约8.5 GiB，编辑器和远程开发工具也占用数GiB。这些属于用户已有环境，不纳入本阶段自动清理范围。

### P1：Edge Agent Python基线不一致

原型曾要求Python 3.12，Jetson系统Python为3.10.12。Edge Agent现调整为支持Python 3.10—3.14，Platform API继续使用Python 3.12—3.14，二者独立发布。

### P1：尚无相机

第一条硬件链路需要标准USB UVC相机或兼容CSI相机。相机到位前使用固定视频文件构建算法和工作流，不阻塞平台协议开发。

### P2：Windows缺少Linux容器环境

当前阶段不强制安装WSL和Docker Desktop。Windows继续承担Web Studio和Platform API开发，Linux/ARM64验证放在Jetson；后续建立远程CI后再决定是否为Windows配置WSL 2。

### P1：开发链路目前使用Wi-Fi

Jetson当前通过Wi-Fi使用`192.168.1.111`，SSH已启动。Phase 1可以先用Wi-Fi验证Agent通信；进入硬件在环、视频流或长期稳定性测试前应切换到有线以太网，并在路由器中设置DHCP地址保留。

Windows开发机Wi-Fi地址为`192.168.1.109`，到Jetson的SSH端口验证通过。

## 已作出的技术决策

- 保留JetPack 6.2.3，不为追求新版本立即升级JetPack 7；
- ROS适配基线采用ROS 2 Humble；
- Edge Agent优先作为Jetson原生systemd服务运行；
- GPU算法和模型组件使用与L4T R36.5.2匹配的Jetson容器；
- 第一条真实业务链路采用“相机/视频—算法—结果上报—Web展示”；
- 平台运行数据根目录规划为`/data/robot-platform`；
- 运动控制在完成安全架构、仿真和硬件在环测试前不接入。

