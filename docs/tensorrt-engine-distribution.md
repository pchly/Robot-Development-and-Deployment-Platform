# TensorRT Engine受控分发

平台把TensorRT Engine视为与目标GPU、JetPack、CUDA和TensorRT版本绑定的端侧运行制品，而不是通用模型文件。Engine必须先在兼容的目标环境生成和验证，再以固定大小、固定SHA-256和固定目标路径登记到算法Manifest。

## 安全边界

- Platform API只发布Catalog登记且摘要匹配的`.engine`文件；
- Edge Agent必须声明`artifact.file-install.v1`能力；
- 目标路径必须位于`/data/robot-platform/cache/tensorrt`；
- 文件先流式下载到受管暂存目录并校验摘要；
- Agent在目标目录生成临时副本，再次校验后使用原子替换；
- 已有目标会先移动为事务备份，替换后校验失败时恢复旧文件；
- 控制面只下发结构化安装计划，不下发Shell命令。

## 真实Jetson验收

验收日期：2026-10-04。

- Platform API：`0.17.0`；
- Edge Agent：`0.11.0`；
- Artifact ID：`algorithm.vision.yolo.tensorrt-engine`；
- 文件：`yolox_nano-fp16.engine`；
- 大小：3,492,116 bytes；
- SHA-256：`719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17`；
- 目标：`/data/robot-platform/cache/tensorrt/yolox-nano-c789161e-sm87-trt10.3-fp16/yolox_nano-fp16.engine`；
- 安装事务：`artifact-install-f9b5f5d7465041df93ef`；
- 结果：`succeeded`。

安装后Agent重新盘点得到相同目标路径、大小和摘要。再次执行部署准备预检得到`ready_for_deployment=true`、`requires_changes=false`、制品状态`ready`，证明Catalog、下载、摘要校验、原子替换和节点库存闭环一致。
