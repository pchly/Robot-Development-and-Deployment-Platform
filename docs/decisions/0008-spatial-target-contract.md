# ADR-0008：在视觉与执行器之间建立空间目标契约

- 状态：已接受
- 日期：2026-10-07

## 背景

早期MuJoCo流程由机械臂节点直接提供固定 `target_x_m/y_m/z_m`。这种方式可验证执行器和预览，但无法说明目标坐标来自哪台相机、哪个坐标系和哪次标定，也不能安全迁移到RealSense与真实SR101。

## 决策

- 模拟RGB相机升级为RGB-D数据源，同时输出 `image.rgb8.v1`、`image.depth16.v1` 和 `camera.intrinsics.v1`。
- 新增 `algorithm.spatial.rgbd_target_localizer`，将检测、对齐深度、内参及相机到基座外参转换为 `robot.target_pose.v1`。
- 新增 `algorithm.motion.cartesian_point_to_point`，负责坐标系、TTL和工作空间检查，并输出 `robot.motion.command.v1`。
- 目标坐标从机械臂适配器配置中移除。执行器只接收已规划并通过Safety Gate的命令。
- MuJoCo 0.2.0配方采用六节点、七条连接的显式拓扑。
- Edge Agent继续接受旧五节点MuJoCo修订，以保证不可变历史修订可回滚；新建修订只使用空间契约。
- 当前标定值保存在修订参数中，下一阶段再提升为绑定设备实例且带摘要的独立标定制品。

## 结果

视觉、空间定位、规划、安全和执行器职责分离。未来将模拟相机换成RealSense、将MuJoCo机械臂换成SR101时，`robot.target_pose.v1` 和 `robot.motion.command.v1` 边界保持不变。

代价是流程节点与连线增加，且真实设备接入前仍需完成标定制品、时间同步、深度质量过滤和跨节点目标通道。
