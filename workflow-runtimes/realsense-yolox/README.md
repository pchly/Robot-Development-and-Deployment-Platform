# RealSense→YOLOX端侧融合Runtime

该Runtime在同一个Jetson容器进程中完成RealSense RGB采集、YOLOX TensorRT推理和检测结果
输出。相机线程与推理线程之间使用容量为1的最新帧缓存：推理落后时丢弃旧帧，避免延迟持续累积。

首个版本用于30秒实机Smoke Test：

- RealSense D415 640×480 RGB输入；
- YOLOX-Nano 416×416 TensorRT FP16推理；
- Engine、ExecutionContext、CUDA Stream与GPU缓冲区全程复用；
- 周期输出采集帧数、推理帧数、丢帧数和延迟；
- 统计整段运行中的目标类别、带目标帧数和总检测数；
- 最终写入`summary.json`、最佳检测证据原图`source.jpg`与`annotated.jpg`；
- 运行期间原子更新低频标注预览`preview.jpg`，供Edge Agent安全上传；
- 接受Edge发现的USB/ASIC标识并在容器内解析为librealsense SDK序列号；
- 不产生任何机器人运动控制输出。

Jetson手工部署目录默认要求：

```text
/data/robot-platform/manual/algorithm-packages/yolox/runtime/infer_image.py
/data/robot-platform/manual/realsense/runtime/vendor/pyrealsense2-*.whl
/data/robot-platform/cache/tensorrt/.../yolox_nano-fp16.engine
```

构建和运行：

```bash
bash runtime/build-runtime.sh
bash runtime/run-smoke.sh
```

当前0.1.3版本已经接入Edge Agent调度、Studio设备自动绑定和低频预览。Studio按目标节点实时发现结果生成设备下拉，单设备自动选中；Runtime在启动时解析并校验真正的SDK序列号。相机拔插重连、控制面断线和长稳仍作为后续可靠性门槛。

Smoke Test不使用`--privileged`且关闭网络。Jetson设备验证阶段容器内以root访问由NVIDIA Runtime
和设备cgroup规则授权的GPU、USB/视频节点；运行脚本结束后将输出文件所有权恢复给宿主调用用户。
生产化阶段再依据目标镜像内的`video`、`render`和USB udev组映射收紧为非root运行。
