# Windows摄像头→YOLOX→MuJoCo闭环验收

这条垂直链路用于验证平台的完整工程主流程：创建项目、选择目标环境、准备固定摘要制品、配置类型化画布、统一预检、保存不可变修订、运行、观测和安全停止。

## 1. 边界

运行链路为：

`Windows摄像头 → YOLOX Nano ONNX CPU → 手机检测框 → 受限仿真目标映射 → Safety Gate → MuJoCo六轴机械臂`

- 摄像头画面与YOLOX推理是真实输入和真实算法；
- 普通Windows摄像头没有深度，检测框到三维坐标的转换只是仿真工作空间映射；
- 运动输出只能进入MuJoCo，不允许连接真实机械臂；
- Studio与Platform API只处理控制面，摄像头帧和推理始终留在Windows Edge Agent本机；
- 浏览器不直接请求摄像头权限，摄像头由受管Runtime独占并在停止时释放。

## 2. 一次性准备

在项目根目录执行：

```powershell
.\scripts\stop-dev.cmd
.\scripts\setup.cmd
.\scripts\dev.cmd -Lan -SimulationAgent
```

`setup.cmd`会安装固定版本的MuJoCo、ONNX Runtime和无界面OpenCV依赖。不要同时打开系统相机、会议软件或其他占用摄像头的程序。

进入“概览”，确认：

- `simulation-windows-01`在线；
- Edge Agent版本为`0.21.0`，Platform API版本为`0.29.0`；
- 节点具备`simulation.mujoco`与`inference.onnxruntime.cpu`能力。

## 3. 新建项目

1. 进入“项目”；
2. 新建项目，例如“Windows手机检测机械臂闭环”；
3. 回到“流程”，在“所属项目”选择刚创建的项目。

项目是修订、部署记录和回退关系的归属边界，不要直接复用无关测试项目。

## 4. 准备目标环境与制品

在“流程”中选择：

- 流程模板：`Windows摄像头识别手机→MuJoCo机械臂`；
- 目标Edge节点：`WindowsMuJoCoSimulationNode · simulation-windows-01`。

部署准备区应列出三项端侧制品：

1. `Windows USB摄像头 Edge插件`；
2. `MuJoCo 六轴机械臂 Runtime`；
3. `Windows Webcam YOLOX MuJoCo Runtime`。

点击“检查并准备缺失制品”。安装任务使用Catalog提供的固定摘要wheel，Agent会在安装或激活后自重载并重新盘点。等待安装记录全部成功，再刷新页面。摄像头插件在进程首次发现设备时可能让指示灯短暂点亮；成功发现后使用进程期缓存，不会在心跳中重复打开视频流。需要发现后插入的新摄像头时，重启Windows Simulation Agent以显式重新盘点。

准备完成后应看到`Windows Camera 0`或其他`windows-camera-*`设备。如果没有设备，先检查Windows隐私设置是否允许桌面应用访问摄像头，并关闭占用摄像头的软件。

## 5. 配置画布

模板包含五个节点和四条类型化连接：

1. `Windows USB摄像头`：输出`image.rgb8.v1`；
2. `YOLOX ONNX目标检测器`：输出`vision.detections.v1`；
3. `检测框到仿真目标映射器`：输出`robot.motion.command.v1`；
4. `运动安全门`：输出`safe_command`；
5. `MuJoCo 六轴机械臂`：只接受安全门输出。

逐个选中节点，在右侧检查：

- 摄像头选择已发现的`windows-camera-*`设备，帧率建议先保持15；
- 检测器目标类别默认使用COCO精确类别`cell phone`，置信度可先用0.30；
- 映射器最低触发置信度建议0.50；
- Safety Gate保持命令授权为1、急停为0；
- MuJoCo速度比例和仿真步数保持模板默认值。

连线可以在画布中拖拽端口建立、选中后删除。部署时必须与已验证配方保持一致，不能绕过Safety Gate。

## 6. 检查并启动

1. 点击“检查工作流与部署条件”；
2. 确认显示“统一部署预检通过”；
3. 点击“保存为新修订”，确认项目中出现新的不可变修订；
4. 点击“保存新修订并启动”。

如果按钮不可用，优先查看部署准备区。设备、能力、制品、摘要或当前画布任一项不满足时，按钮保持禁用是预期安全行为。

## 7. 观察闭环

启动后先让摄像头画面中没有手机，运行记录应显示等待手机，且：

- `phone_detected = 0`；
- `motion_commands_emitted = 0`；
- `motion_commands_accepted = 0`。

随后把手机放入画面。置信度达到映射器阈值后应看到：

- 摄像头侧出现`cell phone`检测框；
- `phone_detected = 1`且`phone_confidence`更新；
- `simulation_target_x_m/y_m/z_m`更新；
- `motion_commands_emitted`与`motion_commands_accepted`增加；
- `mujoco_end_effector_*`与`mujoco_target_reached`更新；
- 组合预览左侧为Windows摄像头与YOLOX，右侧为MuJoCo机械臂。

二维框到三维目标的映射会被限制在MuJoCo工作空间内，不能把这些坐标用于真实机械臂。

Runtime在Agent本地以配置帧率为目标节拍连续执行，不等待5秒一次的控制心跳。运行卡片中的组合画面上限约以5 FPS刷新；点击“打开实时监控”后，Platform API创建15秒TTL且每5秒续期的按需会话，Agent从下一次上传开始提升到上限约10 FPS。上一帧加载完成后才调度下一帧，网络或渲染变慢时会自动降速而不积压请求；关闭窗口、失联或部署结束后自动回落。这是有界的实时观测通道，不参与控制与Safety Gate判定，也不是面向远程驾驶的低延迟视频流。

Edge Agent 0.21.0把控制循环与预览渲染/上传拆成两个执行通道。控制循环只复制当前相机标注帧和机械臂状态；预览线程只消费最新快照，处理不过来时覆盖旧快照。验收时重点查看：

- `control_loop_actual_fps`、`control_tick_mean_ms`和`control_tick_p95_ms`：判断控制闭环是否达到配置节拍；
- `camera_read_*`、`preprocess_*`、`inference_*`、`postprocess_*`和`control_path_*`：定位感知与控制阶段耗时；
- `preview_render_*`、`preview_upload_*`和`preview_age_*`：定位观察链路耗时；
- `preview_frames_dropped`：大于0表示观察端来不及处理，旧帧已按设计丢弃，但不应伴随控制频率明显下降；
- `control_deadline_misses`：持续增加表示本地感知或控制已经超过帧预算，不能再通过提高网页预览帧率解决。

YOLOX Nano COCO必须按配置类别精确触发。若画面中的手机被框成`remote`，则`phone_detected = 0`且不运动是正确的安全行为。为了验证仿真链路，可明确把检测器的目标类别改为`remote`并保存新修订；此时应以`target_detected`和`target_confidence`观察通用目标触发，而`phone_detected`继续保持0，避免把遥控器类别伪装成手机。这一设置会同时响应真实遥控器，只适用于仿真验收。正式手机任务应更换或微调模型，不能把类别别名当作生产修复。

## 8. 安全停止

点击运行记录中的“停止Runtime”。确认：

- 部署状态变为“已停止”；
- `actuator_safe_stop = 1`；
- 摄像头被释放，其他应用可以重新打开；
- MuJoCo机械臂进入安全停止状态；
- 项目修订和运行记录保留，可用于追溯和回退。

## 9. 失败定位

- `required_device_missing`：插件尚未激活、摄像头权限被禁用，或设备被其他程序占用；
- `required_artifact_missing`：继续执行制品准备并等待Agent完成自重载；
- `artifact_digest_mismatch`：重新安装Catalog当前版本，不要手工替换受管目录内容；
- `target_capability_missing`：使用`-SimulationAgent`启动Windows Agent，并先执行最新`setup.cmd`；
- 摄像头打开或连续读取失败：确认已安装摄像头插件`0.1.2`和视觉Runtime `0.2.0`，关闭相机、会议和录屏软件后停止旧Runtime，再重新部署；
- 有检测但不运动：先核对检测框类别是否与检测器目标类别完全一致，再检查`phone_confidence`是否达到映射器最低阈值，以及Safety Gate是否已授权。
