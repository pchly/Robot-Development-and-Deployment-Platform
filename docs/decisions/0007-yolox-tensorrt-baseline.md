# ADR-0007：首个YOLO后端采用YOLOX-Nano与目标端TensorRT Engine

- 状态：已接受
- 日期：2026-10-02

## 背景

平台需要先建立一条许可清晰、体积小、可复现的目标检测基线，再验证RealSense到检测结果的
端侧闭环。Jetson宿主机已有CUDA 12.6和TensorRT 10.3，但算法依赖不应继续安装到宿主机。

## 决策

- UI和工作流保持通用资产ID`algorithm.vision.yolo`；首个实现后端为YOLOX；
- 基线模型使用YOLOX-Nano COCO 0.1.1rc0，输入尺寸416×416；
- 使用官方ONNX制品，固定大小3,659,407字节及SHA-256
  `c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d`；
- 使用Apache-2.0许可的YOLOX项目作为首版，避免在原型阶段引入不清晰的商业分发边界；
- 使用NVIDIA L4T TensorRT 10.3容器，并固定镜像摘要
  `sha256:602c0972ae069fc3dcb4dfab6476e40dfbc7a38b6c704aeea5339ea797423eb6`；
- ONNX只作为可移植源模型；FP16 TensorRT Engine在目标Jetson上生成和缓存；
- Engine缓存键包含模型摘要、镜像摘要、GPU、TensorRT版本、精度和输入形状；
- 首先用合成输入验证解析、构建和执行性能，再用样例图像验证预处理与后处理，最后接入视频和RealSense。

## 不采用的方案

- 不在Jetson宿主机安装PyTorch、ONNX Runtime或Ultralytics；
- 不复用其他项目的50GB开发镜像，避免依赖和许可边界耦合；
- 不从其他GPU复制TensorRT Engine；
- 不在尚未验证语义正确性时把纯`trtexec`吞吐量宣称为端到端检测性能。

## 结果

首版模型精度不是最终业务目标，但可以低成本验证模型制品、GPU Runtime、端口契约和部署缓存。
未来替换其他YOLO实现或自训练模型时，保持`image.rgb8.v1 → vision.detections.v1`接口不变。
