# ADR-0006：以OCI容器隔离端侧Runtime依赖

- 状态：已接受
- 日期：2026-10-02

## 背景

机器人端同时包含厂商SDK、相机驱动、CUDA、TensorRT、模型框架和普通Python包。若把这些依赖全部安装进Jetson宿主机或Edge Agent环境，会产生版本冲突、升级不可回滚、设备间难以复现等问题。另一方面，内核驱动、设备节点和NVIDIA容器运行时无法被普通Python虚拟环境替代。

## 决策

采用三层环境边界：

1. **宿主机层**：只维护JetPack/L4T、内核驱动、udev、Docker、NVIDIA Container Toolkit和systemd Edge Agent；
2. **Runtime容器层**：每个硬件族或算法运行包使用独立OCI镜像，封装Librealsense、CUDA用户态依赖、TensorRT插件和Python运行依赖；
3. **研发环境层**：Windows和Jetson上的开发工具可使用venv、uv或Conda，但不得成为生产部署的隐式前提。

Edge Agent保持小型、稳定，不在其Python环境中安装`pyrealsense2`、Ultralytics、PyTorch等供应商或算法依赖。Edge Executor后续只允许从受信任清单启动固定版本与摘要的Runtime镜像，并按计划开放最小设备权限。

## Python依赖策略

- 普通服务和Runtime默认使用`pyproject.toml`与锁文件；依赖在镜像构建阶段安装，运行时不联网安装；
- Conda用于需要复杂二进制求解的研发、训练、模型导出环境，不作为轻量生产Runtime的默认层；
- 如果某个算法确实依赖Conda生态，则其环境被封装进自己的镜像，而不是共享Jetson全局Conda环境；
- Python版本、CPU架构、JetPack/L4T、CUDA/TensorRT和供应商SDK版本都属于制品兼容性元数据。

## 硬件权限策略

- 内核驱动仍由宿主机提供，容器化不能消除内核兼容性；
- 禁止默认使用`--privileged`；
- Edge Executor根据已绑定的Device Instance开放明确的`/dev`节点、USB总线节点、用户组和cgroup规则；
- RealSense原生V4L/UVC Runtime开放已解析的`/dev/video*`、`/dev/media*`、USB控制节点和字符设备主设备号81/189；
- RSUSB备用Runtime只开放`/dev/bus/usb`和字符设备主设备号189；
- GPU算法镜像使用NVIDIA Container Runtime，只挂载工作流需要的模型、缓存和共享内存目录。

## RealSense阶段策略

1. 已确认D415被宿主机`uvcvideo`识别并生成V4L设备节点，因此首次台架验证使用原生V4L/UVC镜像，但不向宿主机安装Librealsense或修改内核；
2. 如果原生后端枚举或开流失败，再使用独立RSUSB镜像定位用户态与内核兼容问题；
3. 验证设备枚举、RGB/Depth开流、拔插和30分钟稳定性，只有确有功能缺失时才评估内核模块补丁；
4. RealSense Runtime通过标准端口输出图像，YOLO Runtime不直接依赖Librealsense。

## 结果

- 新硬件或算法的依赖不会污染Edge Agent和宿主机Python；
- 镜像可以固定版本、生成摘要并回滚；
- x86_64与aarch64使用同一资产定义，但分别发布对应架构制品；
- 宿主机内核、设备权限和JetPack兼容性仍必须通过硬件在环验证，不能被容器抽象掩盖。
