# RealSense隔离验证Runtime

该镜像把Librealsense和`pyrealsense2`封装在容器内，不向Jetson宿主机Python安装任何包，也不修改内核模块。当前只用于D415台架验证，不是最终生产镜像。

本机已经由`uvcvideo`生成V4L设备节点。当前网络无法访问Docker Hub和GitHub，因此先使用NVIDIA NGC基础镜像与RealSense官方aarch64 wheel完成最小开流验证；V4L2/RSUSB源码构建配置继续保留，用于后续明确后端和生产优化。

## 推荐：官方wheel验证

先下载固定版本wheel并校验SHA-256：

```bash
bash fetch-wheel.sh
```

再构建不需要联网下载Python依赖的镜像：

```bash
docker compose -f compose.wheel.yaml build
docker compose -f compose.wheel.yaml run --rm realsense-probe
```

首次构建时会从 NVIDIA NGC 拉取 L4T 基础镜像；wheel 已由 `fetch-wheel.sh`
下载到本地构建上下文，镜像构建阶段不再访问 PyPI。

## RGB 与 Depth 有限帧测试

```bash
docker compose -f compose.wheel.yaml run --rm realsense-probe \
  --frames 60 \
  --warmup-frames 30 \
  --width 640 \
  --height 480 \
  --fps 30
```

探针先丢弃30组预热帧，再单独统计60组样本帧的稳态速率；达到帧数后会主动停止
Pipeline并释放相机。容器没有网络权限，也没有使用`--privileged`。

## 运行中断开与自动恢复测试

```bash
docker compose -f compose.wheel.yaml run --rm \
  --entrypoint python3 realsense-probe \
  /opt/robot-runtime/camera_runtime.py \
  --serial 125322060182 \
  --exit-after-recoveries 1 \
  --post-recovery-frames 60 \
  --duration-seconds 180
```

看到`running`事件后拔出相机。Runtime应输出`degraded`和`reconnecting`；重新插入后应输出
`running`且`recovered`为`true`。恢复后成功采集60组帧时进程自动退出。180秒内未完成时也会
停止，避免验收进程无限运行。

## 源码构建备用路径

在Jetson本目录执行：

```bash
docker compose -f compose.v4l2.yaml build
```

构建过程会下载并编译固定版本Librealsense，产物保存在Docker位于`/data/docker`的存储中。

基础镜像可通过环境变量替换。例如Docker Hub不可达、但NVIDIA NGC可达时：

```bash
REALSENSE_BASE_IMAGE=nvcr.io/nvidia/l4t-base:r36.2.0 \
  docker compose -f compose.v4l2.yaml build
```

## 只读枚举

```bash
docker compose -f compose.v4l2.yaml run --rm realsense-probe
```

如果V4L2枚举失败，再构建并运行备用RSUSB版本：

```bash
docker compose -f compose.rsusb.yaml build
docker compose -f compose.rsusb.yaml run --rm realsense-probe
```
