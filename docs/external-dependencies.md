# 外部依赖与离线边界

## 结论

当前 Git 仓库包含完整的平台源码、测试、文档、Manifest，以及当前 Manifest 直接引用的本地模型、Engine、wheel、标定和容器验证归档。它是可复现的开发仓库，但不是完全离线安装镜像。

首次安装仍可能需要访问 Python/npm/容器镜像源，目标机器也必须预装对应驱动和系统运行时。

## 已随仓库交付

下列二进制由 Git LFS 管理，并继续受 Manifest SHA-256 校验：

- `simulation-runtimes/windows-webcam-yolox-mujoco/models/yolox_nano.onnx`；
- `algorithm-packages/yolox/dist/yolox_nano-fp16.engine`；
- 当前 Manifest 引用的 RealSense、Windows Webcam、MuJoCo 和 Windows 视觉闭环 wheel；
- Container Import Smoke 的 Docker save 归档；
- 模拟相机到 MuJoCo 的标定 JSON 不使用 LFS，但由 Manifest 摘要固定。

## 未内嵌到 Git

### Python 与 Node 依赖

- Python 包由各模块 `pyproject.toml`、`requirements-dev.lock` 和 `scripts/setup.cmd` 安装；
- Web Studio 依赖由 `apps/studio/package-lock.json` 和 `npm ci` 安装；
- `.venv`、`node_modules` 和 npm/pip 缓存不进入仓库。

### Windows 系统依赖

- Python 目标版本见 `.python-version`；
- Node.js 目标版本见 `.nvmrc`；
- Windows 摄像头驱动和摄像头隐私授权；
- Git 与 Git LFS；
- 可选的 Docker Desktop 和 Tailscale。

### Jetson 系统依赖

当前验证环境为 Jetson AGX Orin、Ubuntu 22.04、JetPack 6.2.3、L4T 36.5.2、CUDA 12.6、TensorRT 10.3、Python 3.10、Docker、RealSense D415 与 Tailscale/可信局域网。

这些系统组件、JetPack、CUDA、TensorRT、RealSense 内核/用户态驱动以及厂商 SDK 不随仓库分发。

### 容器镜像

以下镜像使用不可变版本或基础镜像摘要描述，但完整镜像归档尚未全部放入 Git：

- `robot-platform/realsense-runtime:0.1.0-wheel`；
- `robot-platform/yolox-runtime:0.1.2-trt10.3`；
- `robot-platform/realsense-yolox-runtime:0.1.3`；
- NVIDIA L4T TensorRT 基础镜像，以 `algorithm-packages/yolox/manifest.json` 中的 digest 为准。

对应构建上下文位于：

- `hardware-adapters/realsense/runtime/`；
- `workflow-runtimes/realsense-yolox/runtime/`。

正式离线交付时，应把目标架构镜像导出为独立 Release 制品，生成 SHA-256，并让 Manifest/发布清单记录其不可变身份；不建议把大型镜像层长期放在普通 Git 历史中。

### Agent 发布包

`services/edge-agent/dist/` 中的 wheel 和 Jetson tar.gz 是可由源码重建的发布输出，不进入当前源码提交。正式 Release 只应发布当前版本，不应把所有历史构建输出重新提交到 Git。

## 完全离线交付需要额外准备

1. Python wheelhouse；
2. npm 离线缓存或已审核的内部 registry；
3. Windows/Jetson 对应的容器镜像归档；
4. JetPack、CUDA、TensorRT 和 RealSense 驱动安装介质；
5. 当前 Edge Agent 发行包；
6. 所有文件的 SHA-256 清单和第三方许可证清单；
7. 在隔离环境完成一次从零安装验收。

## 许可证边界

Git LFS 解决的是大文件传输，不代表获得公开再分发权。把仓库或 Release 设为公开前，应分别审查源码、YOLOX/模型权重、MuJoCo、ONNX Runtime、OpenCV、Intel RealSense、NVIDIA 基础镜像、CUDA/TensorRT 和其他依赖的许可证及再分发条款。
