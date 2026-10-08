# 运动安全门契约

## 目标

Safety Gate位于算法命令与执行器之间。算法不能绕过该节点直接驱动新版本机械臂流程；只有通过授权、时效和状态检查的命令才会从`safe_command`端口送入执行器。

## 五节点拓扑

`workflow.mock_vision_arm_safety@0.2.0`固定包含：

1. 模拟RGB相机；
2. 模拟目标检测器；
3. 模拟视觉目标选择器；
4. `algorithm.motion.safety_gate@0.1.0`；
5. 模拟六轴机械臂。

Safety Gate输入为`robot.motion.command.v1`，输出安全命令`robot.motion.command.v1`、安全状态`robot.safety.state.v1`以及可选故障`robot.fault.v1`。

## 状态与规则

- `safe_stopped`：初始和Runtime停止状态，不放行命令；
- `unauthorized`：尚未获得命令授权；
- `command_enabled`：允许检查并转发新命令；
- `emergency_stopped`：急停锁存，急停信号未解除时拒绝复位；
- `fault_latched`：命令超时或执行故障锁存，必须显式复位；
- 命令年龄超过`command_ttl_ms`时立即阻断并进入`fault_latched`；
- `velocity_limit`是安全门声明的上限，真实适配器仍必须实施自己的物理限速；
- 旧四节点配方继续可执行，但只有五节点配方声明`actuator.safety-gate.v1`能力。

## 当前边界

当前状态机只运行在确定性模拟Runtime中，不会产生串口、CAN、EtherCAT或网络控制输出。急停和复位方法已在端侧Runtime内完成并受测试保护；平台控制按钮和真实安全I/O将在后续阶段接入。

## 验证基线

- Platform API：`0.20.0`，76项测试通过；
- Edge Agent：`0.13.0`，125项测试通过，覆盖率90.54%；
- Studio：TypeScript与Vite生产构建通过；
- 新配方：5个节点、4条类型化连接；
- Jetson发布包：`robot-edge-agent-0.13.0-jetson.tar.gz`，SHA-256为`004b21a99f7f230433b6c7b9e476df81fef6742aa1f8625bc5e1c7d529c5570a`；
- 真实Jetson验收必须确认正常放行、超时锁存、急停锁存、显式复位和停止安全态。
