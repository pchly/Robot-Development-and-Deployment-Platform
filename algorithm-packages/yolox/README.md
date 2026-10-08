# YOLOX算法包

该包实现平台中的`algorithm.vision.yolo`首个可执行后端。初始模型使用YOLOX-Nano COCO，
先验证ONNX到TensorRT Engine的可追溯构建和录制数据推理，再与RealSense采集器融合。

## 制品边界

- 源码仓库只保存Manifest、模型元数据、Runtime代码和测试；
- ONNX模型保存在`/data/robot-platform/models/yolox`；
- TensorRT Engine保存在`/data/robot-platform/cache/tensorrt`；
- 模型和Engine均按SHA-256校验；目标Jetson生成并验收的Engine可以作为固定摘要分发文件纳入算法包；
- Engine只在兼容的目标Jetson环境生成和部署，不跨GPU、JetPack或TensorRT环境复用。

当前验收Engine位于`dist/yolox_nano-fp16.engine`，大小为3,492,116 bytes，SHA-256为`719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17`。Platform API只按Manifest中的固定摘要发布该文件，Edge Agent只允许把它原子安装到受管TensorRT缓存目录。

## 首版目标环境

- Jetson AGX Orin；
- JetPack 6.2.3、L4T 36.5.2；
- CUDA 12.6、TensorRT 10.3；
- `nvcr.io/nvidia/l4t-tensorrt@sha256:602c0972ae069fc3dcb4dfab6476e40dfbc7a38b6c704aeea5339ea797423eb6`。

模型来源、摘要和输入输出契约见[`models/yolox_nano.json`](models/yolox_nano.json)。

## Jetson构建与合成输入基准

将官方ONNX放到：

```text
/data/robot-platform/models/yolox/0.1.1rc0/yolox_nano.onnx
```

然后执行：

```bash
bash runtime/build-engine.sh
bash runtime/benchmark-engine.sh
```

构建脚本先校验ONNX摘要，再在固定摘要的TensorRT容器内生成FP16 Engine。基准脚本使用
`trtexec`合成输入，只验证Engine可执行性和GPU推理性能，不代表图像预处理、NMS或端到端性能。

## 单张真实图片语义验证

合成输入基准通过后，构建包含NumPy、OpenCV和单图推理程序的独立Runtime镜像：

```bash
bash runtime/build-inference-runtime.sh
bash runtime/validate-image.sh
```

默认验证图片位于`/data/robot-platform/validation-assets/yolox/dog.jpg`。验证程序复现YOLOX官方
ONNX示例的BGR、416×416左上对齐letterbox预处理和类别无关NMS，输出
`vision.detections.v1` JSON与标注图片。该步骤验证模型输出的语义正确性；通过后再与RealSense
实时采集Runtime融合。

Runtime镜像使用补丁版本标签`robot-platform/yolox-runtime:0.1.2-trt10.3`。镜像将Ubuntu仓库中
ABI配套的NumPy 1.x与OpenCV组合置于Python搜索路径首位，并在构建阶段联合导入NumPy、OpenCV
进行ABI检查。TensorRT依赖Jetson宿主驱动库注入，因此镜像构建完成后再通过NVIDIA Runtime
联合导入NumPy、OpenCV和TensorRT；任何一层不兼容时均不会进入图片推理。
