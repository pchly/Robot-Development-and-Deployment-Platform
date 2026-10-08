# ADR-0003：工作流在端侧本地执行

- 状态：已接受
- 日期：2026-10-02

## 背景

平台的核心使用场景是：工程师在Web Studio中从硬件库选择RealSense相机，从算法库选择YOLO检测器，完成少量配置后部署到Jetson，并在端侧直接采集图像、执行推理和产生检测结果。

如果把原始视频上传到Platform API再处理，会引入带宽、延迟、断网和安全问题；如果把每个逻辑组件都机械地拆成独立容器并通过HTTP传帧，也会产生不必要的序列化和内存复制。

## 决策

采用“控制面集中、数据面端侧”的架构：

- Web Studio负责选型、配置、部署和观察，不接触USB设备；
- Platform API保存资产、工作流和部署期望状态，生成不可变部署计划；
- Edge Agent负责设备盘点、计划校验、下载、启动、健康检查和状态回报；
- 相机帧、深度图、模型输入和检测结果的主数据流保留在Jetson本地；
- 浏览器只接收降采样预览、检测元数据和运行指标；
- 逻辑组件保持可组合，部署编译器可以根据性能约束把多个节点融合到同一进程或同一容器；
- 首个RealSense→YOLO工作流采用融合运行时和有界内存队列，不通过Platform API或HTTP传递逐帧数据；
- Edge Agent自身不加载相机SDK和模型推理库，工作负载运行在独立的端侧Runtime中。

## 安全边界

- Edge Agent继续以普通用户运行；
- 未来由受限Edge Executor执行需要设备和容器权限的操作，不能让任意资产定义执行任意Shell命令；
- 运行包必须来自允许的制品仓库并固定版本与摘要；
- 设备权限按工作流声明最小开放，不使用`--privileged`作为常规方案；
- 该感知链路不产生运动指令；运动控制仍需独立安全控制器和后续安全验收。

## 结果

优点：

- 断开Windows或Platform API后，已授权工作流仍可在Jetson运行；
- 原始视频不离开端侧，降低延迟和带宽；
- 同一硬件、算法和工作流定义可以复用；
- 可以在不改变Studio配置体验的情况下逐步替换端侧传输实现。

代价：

- 需要资产契约、兼容性解析器、部署编译器和端侧执行器；
- 不能假设任意两个组件天然兼容；
- ARM64、JetPack、CUDA、TensorRT、驱动和模型制品必须联合版本化。

## 参考资料

- RealSense官方Jetson安装说明：https://github.com/realsenseai/librealsense/blob/master/doc/installation_jetson.md
- Ultralytics官方Jetson指南：https://docs.ultralytics.com/guides/nvidia-jetson
- NVIDIA JetPack文档：https://docs.nvidia.com/jetson/jetpack/6.0/introduction/index.html
- 访问日期：2026-10-02
