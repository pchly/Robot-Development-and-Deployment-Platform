# MuJoCo虚拟机械臂Runtime

这是视觉引导执行链的第一阶段仿真实现。它提供六轴虚拟机械臂、笛卡尔目标命令、阻尼最小二乘逆解、工作空间和关节限位，以及可审计的安全状态。当前版本只在本地接收测试目标，不接收真实机器人命令。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\robot-mujoco-arm --headless --target 0.25 0.10 0.45
```

输出为JSON，包含关节位置、末端位置、目标位置、误差和安全状态。不带`--headless`时会启动MuJoCo交互查看器。

## 平台边界

- 输入契约：`robot.motion.cartesian_target.v1`；
- 输出契约：`robot.motion.state.v1`；
- 故障契约：`robot.fault.v1`；
- Safety Gate未处于`command_enabled`时拒绝新目标；
- 真实视觉链路将在Jetson端把RealSense深度与YOLO框转换为三维目标，再通过后续数据平面传给本Runtime。
