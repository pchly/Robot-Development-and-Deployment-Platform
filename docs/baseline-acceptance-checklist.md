# 当前基线验收清单

本清单用于冻结当前框架。执行期间不增加新组件或Runtime，只验证[当前平台基线](current-platform-baseline.md)描述的主流程。

## A. 静态契约

- [ ] Platform API Ruff与全量测试通过；
- [ ] Edge Agent Ruff与全量测试通过；
- [ ] Studio生产构建通过；
- [ ] 全部Manifest契约校验通过；
- [ ] 当前版本与基线文档一致。

推荐命令见仓库根目录[README](../README.md#自动检查)。

## B. Windows MuJoCo完整闭环

启动：

```powershell
.\scripts\stop-dev.cmd
.\scripts\dev.cmd -Lan -SimulationAgent
```

验收：

- [ ] `simulation-windows-01`在线，版本为Edge Agent 0.21.0；
- [ ] 节点具备`simulation.mujoco`与`spatial.calibration.v1`；
- [ ] MuJoCo Runtime的Windows变体被正确解析；
- [ ] 标定配置可以准备、校验并激活；
- [ ] 0.3.0六节点工作流通过统一部署预检，结果同时覆盖画布、配方、节点、设备、能力与制品；
- [ ] 修改参数或切换目标节点后旧预检自动失效，重新检查前启动按钮不可用；
- [ ] 仅移动画布节点位置不会使已通过的预检失效；
- [ ] 保存新修订后可以启动；
- [ ] 运行记录包含目标位姿、运动、安全门和仿真指标；
- [ ] PNG预览可见；
- [ ] 停止后进入`stopped/safe_stopped`，无遗留运行实例。
- [ ] 停止后自动生成验收结论，展开后可查看分项实测值、要求和运行时间线。

## C. Windows真实摄像头视觉闭环

详细步骤见[Windows摄像头→YOLOX→MuJoCo闭环验收](windows-webcam-yolox-mujoco-acceptance.md)。

- [ ] Windows摄像头插件安装后发现`windows-camera-*`设备；
- [ ] YOLOX ONNX Runtime与MuJoCo Runtime均以固定摘要制品安装；
- [ ] 五节点、四连线配方通过统一部署预检；
- [ ] 无手机时不产生运动命令；
- [ ] 手机置信度达到阈值后，命令经过Safety Gate并只驱动MuJoCo；
- [ ] 运行记录显示检测、推理、目标映射、运动与安全指标；
- [ ] Runtime连续执行，不受5秒控制心跳节拍限制；
- [ ] 上限约5 FPS且无请求积压的组合预览同时显示Windows摄像头检测框和MuJoCo机械臂；
- [ ] 点击“打开实时监控”后按需提升到上限约10 FPS，关闭窗口后自动恢复默认预览策略；
- [ ] 停止后摄像头释放，仿真机械臂进入安全停止状态。
- [ ] 验收报告区分“未通过”“有告警”和“证据不足”，不会把未触发目标误判为系统故障。

## D. 不可变版本与回退

- [ ] 修改一个非安全关键参数并保存为新修订；
- [ ] 新旧修订都保留且内容不被覆盖；
- [ ] 从历史0.2.0空间工作流创建回退部署；
- [ ] 旧修订使用其内嵌标定快照运行，不要求伪造0.3.0标定制品；
- [ ] 如果当前计划占用相同资源，回退先排队并停止旧计划；
- [ ] 回退完成后来源计划、替换关系和项目修订可追溯。

## E. 控制面恢复

- [ ] 在存在项目、修订、停止部署和安装记录时停止开发环境；
- [ ] 再次启动后项目与修订仍存在；
- [ ] 部署历史与制品安装历史仍存在；
- [ ] Agent重新注册后设备和制品库存恢复；
- [ ] 易失指标和预览允许重新生成，不要求从SQLite恢复；终态验收报告和事件时间线必须恢复。

## F. 节点连接语义

- [ ] 停止Windows Simulation Agent后，节点在超时后显示离线；
- [ ] 重新启动后恢复为同一`agent_id`，不会生成重复逻辑节点；
- [ ] Jetson暂时断网时，Windows控制面保持可用；
- [ ] Jetson恢复网络后自动重新注册并盘点；
- [ ] Agent断线不应把高频数据或执行责任转移到Platform API。

## G. Jetson感知回归

在RealSense可用时执行：

- [ ] Jetson Agent在线并发现唯一D415实例；
- [ ] RealSense→YOLOX配方解析出ARM64制品；
- [ ] 旧的已验证修订可启动并上传低频预览；
- [ ] 运行中指标持续更新且无重复相机占用；
- [ ] 停止后受管容器被清理，运行产物保留。

## 验收记录模板

```text
日期：
Platform API：
Edge Agent（Windows）：
Edge Agent（Jetson）：
工作流修订：
DeploymentPlan：
通过项：
未通过项：
日志/截图位置：
结论：通过 / 有条件通过 / 阻塞
```

只有A—F通过、G在硬件可用时完成回归后，才进入下一类硬件或算法功能开发。
