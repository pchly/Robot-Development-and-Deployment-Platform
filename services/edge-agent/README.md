# Edge Agent

安装在机器人、工控机或开发电脑上的边缘执行端。0.14.0版本负责向Platform API注册、发送心跳、上报基础系统遥测、从sysfs只读发现USB设备，协调经过白名单验证的Runtime，并上传受限大小的低频JPEG预览。模拟执行链新增独立Safety Gate，在算法命令抵达执行器前统一执行授权、命令有效期、速度上限、急停锁存、故障锁存和显式复位状态机。Agent支持固定Catalog Edge插件的下载、摘要校验、版本化安装、原子激活、失败回滚和自动重启；重启后会从受管激活指针上报插件摘要、安装路径与激活状态，并在新进程成功加载适配器和完成一次只读设备发现后才确认安装成功。对于容器镜像，Agent既支持按固定仓库摘要拉取，也支持从Platform API流式下载固定摘要的Docker归档；导入后必须再次校验镜像ID、架构和最终标签，失败时恢复旧标签或移除未验证标签。架构校验会把Linux常用的`aarch64`/`x86_64`与OCI常用的`arm64`/`amd64`归一化后比较。Agent还支持把固定摘要的TensorRT Engine流式下载到暂存区，限制目标位于`/data/robot-platform/cache/tensorrt`，复核摘要后原子替换，失败时恢复旧文件。大型制品暂存在`/data/robot-platform/edge-agent/staging`，相关目录由安装器创建并由systemd精确授权，事务结束后自动清理。 Windows开发机还可以使用独立数据根目录运行Simulation Agent，并受控安装固定摘要的Python Runtime Package；安装结果通过版本目录、原子激活指针和重新注册后的库存上报闭环。

首个真实Runtime是`RealSense D415 → YOLOX-Nano TensorRT`。Agent只拼装固定镜像、固定组件组合和受管TensorRT Engine目录的参数；图像与检测结果留在端侧，不经过Platform API。Agent不会直接加载CUDA、librealsense或模型依赖。

当前可执行后端：

- `MockWorkflowRuntime`：Windows和CI可运行的模拟基线；
- `MockWorkflowRuntime`同时支持“模拟相机→模拟检测器→目标选择器→模拟机械臂”，并对命令超时、速度比例和停止安全态执行白名单校验；
- `DockerRealSenseYoloRuntime`：Jetson上的实机容器后端，网络关闭，不使用`--privileged`；
- 容器状态和低频数值指标由Agent回报，控制面断线不终止已经运行的容器；
- 停止指令只处理带平台标签且plan ID匹配的容器，拒绝操作同名外部容器。
- 预览上限256 KB，当前随5秒状态周期上传；原始视频流不离开端侧。

Jetson的systemd部署说明见[deploy/jetson/README.md](deploy/jetson/README.md)。

