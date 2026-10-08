# RealSense硬件适配包

该资产包由两部分组成：

- Edge插件：只使用Python标准库读取Linux sysfs，识别RealSense USB设备并向平台上报领域信息；
- OCI Runtime：封装Librealsense、`pyrealsense2`和实际RGB-D数据流。

Edge插件不会在Jetson宿主机安装Librealsense，也不会打开视频流。采集、恢复和YOLOX融合推理继续在现有隔离Runtime中执行。

## 契约测试

```powershell
cd hardware-adapters\realsense
..\..\services\edge-agent\.venv\Scripts\python -m pytest -p no:cacheprovider
```

## 构建wheel

```powershell
cd hardware-adapters\realsense
..\..\services\edge-agent\.venv\Scripts\python -m pip wheel . --no-deps -w dist
```

构建产物`dist/robot_hardware_realsense-0.1.0-py3-none-any.whl`可以使用Edge Agent Jetson部署文档中的流程安装到受管插件目录。

当前发布wheel的SHA-256为`4dabedc238dd67d03283656db2d55011b59fc2392273065e6557a9ca998f2392`。
