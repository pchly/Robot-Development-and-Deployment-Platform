# 模拟执行器与运动安全契约

## 目标

在没有SR101或移动底盘实机的条件下，验证平台能否表达并执行“感知→目标选择→运动命令→执行状态”链路。当前实现严格限定为模拟运动，不加载任何真实驱动，也不会向串口、CAN或网络控制器发送命令。

## 组件与端口

可执行配方`workflow.mock_vision_arm@0.1.0`包含四个节点：

1. `hardware.camera.mock`输出`image.rgb8.v1`；
2. `algorithm.vision.mock_detector`输出`vision.detections.v1`；
3. `algorithm.motion.mock_target_selector`把检测结果转换成`robot.motion.command.v1`；
4. `hardware.arm.mock`消费运动命令，并声明`robot.motion.state.v1`和`robot.fault.v1`输出。

硬件输入端口已经成为Catalog正式字段。执行器的必需命令输入未连接、被重复连接或数据类型不一致时，工作流不能通过校验。

## 安全边界

- 目标节点必须上报`actuator.safety.v1`和`simulation`能力；
- Edge Agent只接受固定组件版本和固定三条连接；
- `command_timeout_ms`限制为100至5000毫秒；
- `max_velocity_scale`限制为0.01至1.0，默认0.2；
- 停止Runtime时模拟执行器无条件进入`safe_stopped`；
- 当前版本没有真实运动输出，不能把模拟能力视为真实机械臂验收。

## 后续演进

下一阶段将把安全门拆成独立节点，加入命令授权、急停、故障注入、超时失效和显式复位状态机。真实SR101适配器只有在设备发现、只读状态、台架限位和急停验证完成后，才能声明真实运动能力。

## 2026-10-04验证记录

- Platform API：`0.19.0`，74项测试通过，覆盖率92.71%；
- Edge Agent：`0.12.1`，120项测试通过，覆盖率90.10%；
- Studio：TypeScript与Vite生产构建通过；
- 在线Catalog：已返回四节点、三连接配方以及机械臂命令输入、状态和故障输出；
- 真实Jetson模拟验收：R1计划`plan-264f05b22126e481fc1a`运行成功，命令发送与接受均为1、执行器故障为0，随后正常停止并释放活动计划；
- 控制面终态上报现在保留最后一份非空运行指标，停止后仍可审计运动命令与故障统计；
- Jetson发布包：`robot-edge-agent-0.12.1-jetson.tar.gz`；
- 发布包SHA-256：`943d8407483e1621943c1ca2ae18be8168a3740f4b16fe950ccb089717d1b9a9`。

该记录只证明模拟执行器的软件闭环，不构成任何真实机械臂运动安全认证。
