# RGB-D 视觉引导机械臂架构

## 工程目标

平台中的视觉链路不能直接把二维检测框当作机械臂目标，也不能由执行器节点填写目标坐标。目标坐标必须来自可验证的空间定位过程，并经过规划、安全门和执行器适配器。

统一数据链路如下：

```text
RGB + 对齐深度 + 相机内参
        ↓
二维目标检测（vision.detections.v1）
        ↓
RGB-D空间定位 + T_arm_base_camera
        ↓
目标位姿（robot.target_pose.v1）
        ↓
笛卡尔点到点规划
        ↓
运动命令（robot.motion.command.v1）
        ↓
独立Safety Gate
        ↓
MuJoCo虚拟机械臂 / 未来SR101适配器
```

## 部署位置

| 模块 | 当前仿真位置 | 未来真实系统位置 | 原因 |
|---|---|---|---|
| RGB-D采集与目标检测 | Windows仿真节点 | Jetson | 原始图像靠近传感器和TensorRT处理 |
| 空间定位 | Windows仿真节点 | Jetson或机器人本体节点 | 需要同步使用检测、深度、内参和外参 |
| 任务规划与Safety Gate | Windows仿真节点 | 机器人本体节点 | 运动安全不能依赖控制平面的网络往返 |
| MuJoCo执行器 | Windows仿真节点 | 不适用 | 便于可视化、调试和回归 |
| SR101执行器 | 不适用 | 机器人本体节点 | 仅替换末端适配器，不改变上游契约 |
| Platform API | Windows控制平面 | 可迁移服务器 | 只负责低频编排、版本、审计与状态 |

高频原始RGB、深度和伺服命令不经过Platform API。跨节点时只传输带序号、采集时间、有效期、坐标系和标定版本的目标事件。

## 数据契约

`robot.target_pose.v1`至少包含：

```json
{
  "schema": "robot.target_pose.v1",
  "sequence": 42,
  "captured_at_ms": 1791154200000,
  "expires_at_ms": 1791154200300,
  "frame_id": "arm_base",
  "calibration_id": "realsense-arm-v1",
  "source": {
    "label": "bottle",
    "confidence": 0.91
  },
  "position_m": {"x": 0.31, "y": 0.08, "z": 0.42}
}
```

定位器使用针孔模型将像素和对齐深度反投影到相机光学坐标系，再使用版本化的相机到机械臂基座刚体变换转换到 `arm_base`。规划器必须检查：

- 坐标系必须是目标机械臂的基座坐标系；
- 目标尚未超过有效期；
- 目标位于配置的三维工作空间；
- 标定ID存在且与部署绑定一致；
- Safety Gate已授权且没有急停或锁存故障。

## 当前边界

当前0.3.0仿真配方使用模拟RGB-D观测，真实执行器输出始终关闭。相机内参与相机到机械臂基座的外参已由独立 `calibration_profile` 制品提供；部署计划固定版本和SHA-256，Agent安装后还会核对模拟相机与MuJoCo机械臂实例绑定。工作流修订只保存标定ID和任务参数，不再复制标定数值。

MuJoCo通过不等于真实机械臂通过安全验收。真实系统还必须具备独立急停、硬限位、软限位、速度与力矩限制、看门狗、控制权仲裁和断线安全停止。

## 验收阶段

1. MuJoCo本地目标运动与PNG预览；
2. Windows仿真节点注册、制品安装和运行状态回传；
3. RGB-D反投影、坐标变换、TTL与工作空间检查；
4. 已完成：将标定配置制品化并绑定相机/机械臂实例；
5. Jetson真实RealSense生成 `robot.target_pose.v1`；
6. 建立低带宽、带TTL的跨节点目标通道；
7. 在MuJoCo完成真实视觉目标驱动；
8. 最后设计SR101只读适配、台架和安全验收。
