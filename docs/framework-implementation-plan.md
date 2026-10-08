# 框架优先实施计划

> 本文是里程碑与验收证据记录，不再作为当前架构说明。当前系统边界、版本和唯一主流程见[机器人开发平台当前基线](current-platform-baseline.md)。

## 目标

先用模拟相机和模拟检测器跑通“选择资产、兼容校验、生成部署计划、端侧运行、状态回报、停止与恢复”的完整闭环，再将模拟组件替换为RealSense适配包和YOLO Runtime。

## M0：控制面与Edge基线

状态：已通过。

交付：

- Web Studio、Platform API、Edge Agent；
- Jetson systemd服务；
- 注册、心跳、遥测、离线判断和重连；
- Windows与Jetson局域网联调。

验收证据：Jetson AGX Orin真实节点在线，Platform API重启后Agent自动重新注册。

## M1：资产与硬件适配器SDK

状态：已通过。

交付：

- 硬件定义、算法定义和设备实例；
- USB只读发现；
- 硬件适配器生命周期SDK；
- 模拟RGB相机适配器；
- Studio组件页显示资产定义与端侧实例。

退出条件：新版Agent在Jetson上发现已连接RealSense，Studio能显示型号、VID:PID、序列号和USB速率；模拟适配器生命周期测试通过。

验收证据：Jetson已上报RealSense D415（`8086:0ad3`、序列号`123423024767`、USB 5000 Mbps）；适配器生命周期测试通过。

## M2：DeploymentPlan与模拟端侧Runtime

状态：已通过。

交付：

- 版本化WorkflowDefinition；
- 端口和兼容性解析；
- 不可变DeploymentPlan；
- 模拟相机→模拟检测器Runtime；
- 运行状态机和本地日志。

退出条件：API生成计划，Agent在Jetson执行模拟工作流并持续回报`running`、FPS和健康状态。

验收证据：Windows本地Agent已成功领取并幂等执行模拟相机→模拟检测器计划；Jetson systemd Agent也已领取计划并持续回报`running`、15 FPS、运行计数和检测计数。

## M3：Studio一键闭环

状态：已通过。Studio已提供目标节点选择、基于Catalog可执行配方的硬件与算法选择、动态参数表单、一键兼容检查与启动、运行状态、指标、低频预览和停止控制。

交付：

- 硬件、算法和目标节点选择；
- 参数Schema表单；
- 部署前兼容性报告；
- 启动、停止和运行详情页。

退出条件：用户不进入终端即可从Studio完成模拟工作流全生命周期。

验收证据：模拟链路和RealSense→YOLOX实机链路均已从Studio完成启动、状态观测与停止；API拒绝仅端口兼容但尚无端侧Runtime配方的组合。

## M4：RealSense适配包

状态：阶段验收通过。真实D415已经完成USB盘点；隔离Runtime镜像已在Jetson构建成功，容器内SDK已枚举并同步采集RGB与Depth。已确认SDK序列号为`125322060182`，ASIC、固件升级和USB传输层序列号为`123423024767`，不是发现错误。有限帧、稳态速率、Edge设备清单热插拔、运行中自动降级/重连及5分钟稳定性均已通过；30分钟长稳保留为发布前验收项，不阻塞M5。

阶段验收证据：640×480、目标30 FPS配置下，先丢弃30组预热帧，再连续取得300组RGB与Depth样本帧；样本耗时10.154秒，稳态速率29.55 FPS。包含初始化和预热的总耗时为11.807秒。

拔插验收证据：相机拔出后在两个心跳周期内从Studio消失，插回原USB 3接口后自动恢复并保持5000 Mbps；SDK/ASIC身份未变化，新的容器进程成功取得60组同步帧。该次仅预热10帧且只采样60帧，21.11 FPS不作为稳态性能结论。

重插后稳态复测：丢弃30组预热帧后连续采集300组样本帧，耗时10.223秒，稳态速率29.35 FPS，与拔插前29.55 FPS一致。

兼容性记录：自动恢复Runtime首次在Jetson Python 3.10启动时因使用Python 3.11新增的`datetime.UTC`失败；已改为Python 3.10支持的`timezone.utc`并通过状态机单元测试。该次失败发生在SDK加载和设备访问之前。

运行中恢复证据：连续采集期间拔出相机后，Runtime因2秒收帧超时进入`degraded`，按2秒周期进入`reconnecting`；相机插回后在同一容器进程内恢复为`running`，`recovery_count=1`，再采集60组同步帧后按验收配置自动退出。完整过程耗时39.73秒。

5分钟稳定性证据：连续运行300.81秒，共采集8900组同步帧；首个30秒窗口29.18 FPS，此后各窗口稳定在约29.76 FPS，全程`recovery_count=0`，未进入`degraded`或`reconnecting`。

交付：

- RealSense发现规则和型号Profile；
- RGB/Depth/CameraInfo端口；
- SDK后端与Runtime镜像；
- 拔插、重连、USB带宽和健康指标。

退出条件：单独采集真实相机30分钟稳定，断开后进入`degraded`，重新连接行为明确且可重复。

## M5：YOLO算法包

状态：进行中。Jetson CUDA 12.6/TensorRT 10.3基线已确认；YOLOX-Nano官方ONNX已固定摘要，并在固定NGC镜像内成功生成目标Orin FP16 Engine；合成输入GPU基准和真实样例图片语义验证均已通过。等待录制视频Runtime和RealSense融合Runtime。

阶段验收证据：ONNX Opset 11解析成功，网络为1输入/1输出；Orin Compute Capability 8.7上构建耗时343.46秒，Engine约3.33 MiB，SHA-256为`719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17`。

合成输入基准：MAXN模式、10秒、6739次查询，吞吐量673.64 qps；Host latency平均1.613 ms、P95 1.622 ms，GPU Compute平均1.483 ms、P95 1.489 ms。该结果仅证明Engine执行性能，不代表含预处理、解码和NMS的真实检测性能。

单图语义验证：`robot-platform/yolox-runtime:0.1.2-trt10.3`在官方`dog.jpg`上输出`vision.detections.v1`，正确检出dog、bicycle和car。首次推理38.418 ms，单次冷启动总耗时202.575 ms；该数据包含Engine反序列化和CUDA首次执行开销，不作为稳态逐帧延迟。

交付：

- 视频文件输入基线；
- ONNX源模型和摘要；
- 目标Jetson生成并缓存TensorRT引擎；
- `vision.detections.v1`输出；
- FPS、P95延迟和丢帧指标。

退出条件：录制视频在Jetson稳定推理，模型和引擎可追溯。

## M6：RealSense→YOLO实机闭环

状态：进行中。单进程融合Runtime与真实语义Smoke Test已通过；Studio→Platform API→Edge Agent→Docker Runtime的一键启动、指标回传、一键停止、容器清理、产物保留和低频Web标注预览均已通过实机验证。等待拔插恢复、API断线和30分钟长稳实测。

阶段验收证据：RealSense D415 640×480@30与YOLOX-Nano TensorRT连续运行31.077秒，采集27.931 FPS、推理27.577 FPS、零丢帧；推理P95 6.874 ms，帧到检测结果链路P95 16.886 ms。857个有效推理帧中727帧检出目标，最佳person置信度0.924987。产物保存在`/data/robot-platform/validation/realsense-yolox-smoke-20261003-091644`。

平台编排证据：Studio下发`plan-df2b13d7e91008c43f76`后，Jetson创建受管容器`robot-plan-df2b13d7e91008c43f76`；网页持续收到采集、推理、检测和延迟指标。观测点为采集870帧、推理860帧、零丢帧、推理均值5.825 ms、链路P95 14.656 ms。

停止验收证据：Studio下发停止目标后，Agent正常停止并删除受管容器；`docker ps -a`中不再存在该plan，端侧仍保留`summary.json`、`source.jpg`与`annotated.jpg`。

Web预览验收证据：`robot-platform/realsense-yolox-runtime:0.1.2`在Jetson端每5秒原子更新低频`preview.jpg`，Edge Agent 0.3.0将最新JPEG独立上传至Platform API，Studio成功显示带检测框、类别和置信度的标注画面。网页观测点为采集573帧、推理563帧、零丢帧、推理均值5.909 ms、链路P95 15.435 ms，并显示`person 0.70`；原始视频和逐帧数据仍留在端侧。该版本独立Smoke Test运行31.091秒，采集27.95 FPS、推理27.597 FPS、零丢帧，产物保存在`/data/robot-platform/validation/realsense-yolox-smoke-20261003-101235`。

交付：

- 端侧融合Runtime；
- 有界最新帧队列；
- 检测框和低频预览；
- API断线继续运行；
- 回滚和30分钟长稳测试。

退出条件：达到`realsense-yolo-vertical-slice.md`中的首版性能与可靠性目标。

## M7：新硬件接入模板

状态：进行中。Platform API已经能够扫描硬件包、算法包和Runtime包Manifest并构建Catalog；新增Demo资产无需修改核心代码即可被发现，非法Manifest和悬空配方引用会在启动阶段被拒绝。硬件适配包脚手架、通用Manifest契约校验命令和Edge Agent 0.4.1受管插件加载器已经完成。插件加载故障与`probe`故障可隔离，重复`adapter_id`会被拒绝；通用USB盘点与插件领域记录会按物理身份合并。首个独立RealSense插件wheel已经通过源码契约、wheel entry point加载和Jetson实机安装验证；第二种真实硬件验证仍待完成。

RealSense插件实机证据：Jetson安装`robot-hardware-realsense 0.1.0`后，Edge Agent 0.4.1启动日志为`loaded=1 failed=0`并正常注册；Studio只显示一个D415实例，VID:PID为`8086:0ad3`、传输序列号为`123423024767`、速率为5000 Mbps，证明通用USB记录和插件记录按物理身份正确合并。wheel SHA-256为`4dabedc238dd67d03283656db2d55011b59fc2392273065e6557a9ca998f2392`。

交付：

- 适配包脚手架；
- Manifest和Profile模板；
- 模拟器、契约测试和硬件在环检查清单；
- 资产验证级别和发布流程。

退出条件：使用模板接入第二种硬件时，不修改平台核心代码。

## M8：项目空间与设计态持久化

状态：已完成。Platform API 0.6.0已经加入SQLite控制面仓库、项目CRUD、不可变工作流修订以及部署控制状态持久化；Studio可以创建项目、把设计态工作流保存为新修订，并从指定修订创建带`project_id + workflow_revision`来源的DeploymentPlan。Platform API重启后会恢复计划、期望状态和状态机检查点，Edge Agent可以重新接管同名受管Runtime。Agent在线状态、周期指标和低频预览仍保持为易失运行态，避免把高频数据写入控制面数据库。

交付：

- 项目创建、查询、更新与归档；
- 工作流草稿的不可变修订历史；
- 数据库Schema版本和不兼容版本拒绝；
- Studio项目空间；
- 备份、迁移和恢复约定。

退出条件：Platform API重启后项目与工作流修订仍然存在，并能从指定修订编译部署计划。

## M9：通用工作流设计与项目闭环

状态：进行中。项目选择、配方参数编辑、修订保存、修订历史、历史修订载入编辑和从指定修订部署已经接通；正式启动当前配置时会先生成不可变修订，再从该修订部署，避免出现没有项目来源的临时运行。修订卡片会展示相对上一版的参数差异，运行记录可以在当前项目与全部记录之间切换。设备绑定参数会从目标Edge节点的实时发现结果生成下拉选项，单设备自动选中。RealSense Runtime 0.1.3能够把USB/ASIC序列号解析为librealsense SDK序列号，避免由用户人工抄写厂商内部标识。Platform API 0.9.0和Studio已加入Manifest驱动的组件节点库、通用多节点画布、显式类型化端口连线、节点坐标拖拽与位置持久化、节点检查器参数编辑、必需输入提示、重复输入检查与数据流环路检查。参数Schema已经下沉到硬件和算法组件Manifest；可执行配方升级为通用`components[]`结构，并增加“模拟相机→模拟检测器→检测过滤器”三节点流程。该三节点流程已从不可变修订编译并在Jetson Edge Agent 0.4.4上完成启动、指标回传和停止的端到端验证。任意图可以保存为修订，但只有匹配验证配方的图才能部署；RealSense→YOLOX端侧Runtime保持兼容。

交付：项目选择、通用多节点图、端口连线、参数编辑、草稿保存、修订比较、从指定修订执行兼容检查与生成DeploymentPlan。现有RealSense→YOLOX配方继续作为可执行后端，不把任意网页图直接解释为端侧命令。

退出条件：从项目创建到工作流修订、仿真部署、运行记录和停止均可在Studio中完成。

## M10：制品、部署版本与回滚

状态：主体闭环已完成，继续补充多节点与长期可靠性验收。问题定义：端侧制品不能依赖人工记忆，也不能让控制面下发任意Shell命令；任何自动准备动作都必须由Catalog固定版本、来源和摘要，并能在目标节点验证实际结果。验收标准：错误架构、缺失摘要、异常Docker元数据和摘要不一致必须在更新本地标签前失败；标签切换后的校验失败必须恢复旧标签；操作状态必须持久化并在Studio可见。

Platform API 0.17.0已经从硬件、算法、模型和工作流Runtime Manifest派生统一制品清单，并区分`build_source`与`edge_runtime`。Catalog可以登记仓库内的固定文件，启动时验证路径边界、大小与SHA-256，下载时再次校验；下载API只接受Catalog ID。平台受控安装事务状态机与Agent拉取/回报接口限制同一节点同一制品只能有一条活动事务，事务状态进入SQLite并可在Windows控制面重启后恢复。Edge Agent 0.11.0支持Edge插件的版本化原子激活、固定摘要Docker镜像的仓库拉取或归档导入，以及TensorRT Engine的受管文件安装。Engine只能写入`/data/robot-platform/cache/tensorrt`，先流式下载到受管暂存区并校验摘要，再在目标目录内生成临时副本、复核摘要并原子替换；替换后校验失败会恢复旧文件。systemd继续使用`ProtectSystem=strict`，只精确放行运行产物、插件版本库、制品暂存目录和TensorRT缓存目录。Studio根据节点能力与Catalog摘要决定是否显示“准备到节点”入口，并展示完整事务进度；工作流部署前检查也接入同一套准备计划。真实Jetson已经分别完成Docker归档和TensorRT Engine链路验收：Engine制品大小为3,492,116 bytes，固定摘要为`sha256:719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17`，安装事务`artifact-install-f9b5f5d7465041df93ef`进入`succeeded`；Agent重新盘点得到相同路径、大小和摘要，二次准备预检返回`ready_for_deployment=true`且`requires_changes=false`。下一步补齐Deployment版本快照与一键回退，使同一套受控制品能够部署到新节点并恢复上一已验证版本。

Platform API 0.18.0进一步把每个DeploymentPlan作为包含工作流修订、组件版本、制品定位符与摘要的不可变运行快照。Studio可以从已停止或失败的历史记录一键创建回退部署；若当前Runtime占用相同硬件，平台先把它切换为`stopping`，新部署保持`queued`且不会下发到Agent，只有全部被替换计划明确回报`stopped`后才进入`pending`。停止失败时回退继续保持排队，用户也可以显式取消。排队状态、回退来源和替换关系均持久化，控制面重启后仍可继续推进。真实Jetson验收中，R8快照`plan-136d82c1d8daf3d46408`先成功运行，随后从R7创建的`plan-abbb0d0be719fda069e2`保持排队，直到R8停止后才启动；R7回退运行后采集122帧、推理112帧、丢帧0并上传预览，最后正常停止清理。

Platform API 0.22.0把Catalog从“一个制品对应一个安装文件”扩展为“一个逻辑制品对应多个平台发行变体”。变体可以约束操作系统、CPU架构、Agent能力和环境元数据；准备计划、安装事务与DeploymentPlan统一调用确定性解析器并固化`variant_id`。Studio按目标节点展示被选中的变体或不兼容原因。旧单发行Manifest保持兼容；MuJoCo六轴机械臂Runtime已登记Windows x86_64与Linux ARM64变体，MJCF模型作为独立仿真资产进入Catalog。Edge Agent 0.14.0已经支持固定摘要Runtime wheel的版本化安装、原子激活与库存回报；Windows Simulation Agent使用独立MuJoCo环境和数据根目录运行，不影响Jetson节点。

交付：插件wheel、Runtime镜像、模型和配置制品清单；摘要验证；目标架构约束；安装状态；部署历史；失败回滚。控制面只引用固定版本和摘要，不传递任意Shell命令。

退出条件：能够把同一已验证版本部署到新Edge节点，并回退到上一版本。

## M11：运动设备仿真与安全边界

状态：已完成软件闭环。Platform API 0.20.0把硬件组件从只读数据源扩展为同时具有类型化输入与输出的执行器契约，并新增“模拟相机→模拟检测器→目标选择器→Safety Gate→模拟六轴机械臂”五节点配方。Edge Agent 0.13.0保留旧四节点拓扑兼容性，同时对白名单五节点拓扑执行命令授权、命令有效期、速度上限、急停锁存、故障锁存和显式复位状态机；停止时进入`safe_stopped`并保留终态安全指标。节点必须同时声明`actuator.safety.v1`和`actuator.safety-gate.v1`能力后平台才允许编译新配方。该阶段仍只产生模拟运动，不包含真实驱动或物理输出。

交付：机械臂、移动底盘、关节状态、轨迹、速度命令和急停的版本化端口类型；纯仿真适配器；命令授权；限位、超时和心跳丢失状态机。真实运动输出保持禁用。

退出条件：SR101或底盘尚未接入时，也能用确定性模拟器验证“任务→规划→安全门→执行状态”的完整控制流程；只有硬件在环验收后才能启用真实输出。

## M12：工程化发布基线

状态：未开始。

交付：用户身份与权限、审计事件、数据库迁移与备份、结构化日志、指标与告警、CI、多架构构建、发布清单和现场恢复手册。

退出条件：平台可以在新Windows开发机和新Jetson节点上按文档重复安装、升级、备份和恢复。

## 实施纪律

- 每个里程碑均需独立可运行和回退；
- 先模拟、后真实硬件；
- 先传感器、后任何运动设备；
- 端侧数据不绕行Platform API；
- 未通过台架验证的资产不得标记为可一键部署；
- 每次Jetson部署都记录版本、命令、日志和验收结果。
