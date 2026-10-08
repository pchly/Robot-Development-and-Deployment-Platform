# 资产包与可执行配方契约

## 目的

平台核心代码不登记具体厂商或模型。硬件、算法和端侧Runtime分别通过版本化Manifest加入Catalog，Platform API启动时扫描资产根目录并构建只读快照。

默认资产根目录是当前仓库根目录；独立部署时通过`ROBOT_PLATFORM_ASSET_ROOT`指定。Manifest缺失、格式错误、ID重复或配方引用不存在的资产时，API应启动失败并报告具体文件，不能静默忽略。

## 目录约定

```text
hardware-adapters/<package>/manifest.json
algorithm-packages/<package>/manifest.json
workflow-runtimes/<package>/manifest.json
artifact-packages/<package>/manifest.json
```

新增目录会被自动扫描，不需要修改`catalog.py`。

## 逻辑制品与平台发行变体

`artifact-packages`登记用户在Studio中看到的逻辑制品。不能跨平台复用同一文件时，使用`variants`为同一个`artifact_id + version`声明多个不可变发行变体，而不是复制多个逻辑制品。部署前Platform API根据目标Agent上报的操作系统、CPU架构、能力和环境元数据选择唯一兼容变体；准备计划、安装事务和DeploymentPlan使用同一个解析结果。

```json
{
  "schema_version": "1.1",
  "artifact_id": "simulation.mujoco.arm.runtime",
  "name": "MuJoCo 六轴机械臂 Runtime",
  "kind": "runtime_package",
  "scope": "edge_runtime",
  "version": "0.1.0",
  "source_ref": "simulation.mujoco.arm",
  "variants": [
    {
      "variant_id": "windows-amd64-python",
      "locator": "robot-mujoco-arm-runtime==0.1.0",
      "compatibility": {
        "operating_system": "Windows",
        "architecture": "x86_64",
        "required_capabilities": ["simulation.mujoco"]
      },
      "priority": 10
    },
    {
      "variant_id": "linux-arm64-python",
      "locator": "robot-mujoco-arm-runtime==0.1.0",
      "compatibility": {
        "operating_system": "Linux",
        "architecture": "aarch64",
        "required_capabilities": ["simulation.mujoco"]
      }
    }
  ]
}
```

解析规则如下：

- `amd64`与`x86_64`、`arm64`与`aarch64`会归一化比较；
- 显式声明的约束必须由Agent上报，缺失环境字段不会被猜测为兼容；
- 多个变体兼容时先比较`priority`，再选择约束更具体的变体，最后按`variant_id`稳定排序；
- 无兼容变体时阻止准备和部署，并返回每个变体被拒绝的原因；
- 不含`variants`的旧Manifest继续按原单发行规则解析。

当前制品种类包括`edge_plugin`、`container_image`、`model`、`tensorrt_engine`、`simulation_asset`、`runtime_package`和`calibration_profile`。其中插件、容器、Engine、Runtime Package和标定配置已经具备受控安装策略；模型与纯仿真资产可以作为构建源或由Runtime Package承载。任何类型都不会把Manifest内容解释为任意安装命令。

标定配置属于独立制品，不应作为普通节点参数重复保存。相机到机械臂标定使用`robot.calibration.camera-to-arm.v1`文件契约，DeploymentPlan固定其版本与摘要，Agent安装后还要核对设备实例绑定。当前参考实现为`artifact-packages/mock-camera-mujoco-calibration`。

带平台分发文件的容器变体可以各自声明`distribution`；下载地址会固定`variant_id`，从而保证Agent取得的是部署计划已经选择的文件，而不是同逻辑制品下的其他平台文件。

## 硬件适配包

Manifest至少声明：

- `adapter_id`、`version`、`name`、`vendor`和`category`；
- `readiness`，未完成验证的资产必须为`definition_only`；
- 只读`discovery`规则；
- 版本化输出端口；
- 参数Schema、默认值、范围和设备绑定来源；
- Runtime隔离方式、镜像和最小设备权限；
- 型号Profile列表。

同一共享SDK和行为模型对应一个适配包；型号差异放在Profile中；实际序列号设备由Edge Agent作为Device Instance上报。

### Edge发现插件

硬件包可以通过标准Python entry point提供端侧发现实现：

```toml
[project.entry-points."robot_platform.hardware_adapters"]
demo_lidar = "robot_hardware_demo_lidar.adapter:DemoLidarAdapter"
```

插件对象必须实现Edge Agent的`HardwareAdapter`协议，并提供全局唯一的`adapter_id`。Edge Agent只从`ROBOT_EDGE_PLUGIN_PATH`指定的目录读取已安装distribution元数据，不扫描工作目录，也不执行Manifest携带的任意路径或命令。

发现插件保持轻量，只承担设备只读枚举、配置校验和受控生命周期接口。必须依赖厂商SDK的采集、推理和高频数据处理放进独立Runtime镜像；插件不能污染Agent的基础Python环境。

Jetson默认插件目录为`/data/robot-platform/edge-agent/plugins`。wheel安装、升级和日志检查流程见[Edge Agent Jetson部署文档](../services/edge-agent/deploy/jetson/README.md)。

## 算法包

Manifest至少声明：

- `algorithm_id`、`version`、`name`和`category`；
- `readiness`和`artifact_formats`；
- 版本化输入输出端口；
- 参数Schema、默认值与数值约束；
- 模型清单；
- 目标架构、基础镜像和推理后端。

模型文件与TensorRT Engine必须记录来源、摘要和目标平台，不能只使用可变文件名标识。

## 可执行配方

端口类型兼容不等于可以部署。`workflow-runtimes/<package>/manifest.json`中的`deployment_recipe`用于声明已经实现并验证的组合，包括：

- 配方ID、版本和显示名称；
- 任意数量组件的固定ID、版本与节点ID；
- 节点ID及类型化连接；
- 目标Edge节点必须具备的能力。

参数Schema属于组件Manifest，不在Runtime配方中重复声明。Catalog加载时把组件参数绑定到配方节点，生成Studio动态表单。需要绑定实体硬件的参数使用`binding_source: target_hardware_devices`，由目标Edge节点发现结果生成选项，禁止要求用户抄写设备标识。

只有存在有效配方的组合才会出现在Studio一键部署界面。配方不能携带任意Shell命令；实际执行入口和允许的镜像仍由Edge Agent受控Runtime注册表决定。

## 接入门槛

新资产按以下顺序升级成熟度：

1. 创建Manifest并标记`definition_only`；
2. 通过Manifest契约测试；
3. 实现模拟器或离线测试；
4. 完成端侧发现和适配器生命周期；
5. 构建固定版本和摘要的Runtime制品；
6. 完成硬件在环、故障恢复和稳定性测试；
7. 将资产标记为`validated`；
8. 增加可执行配方后开放一键部署。

当前参考实现：

- `hardware-adapters/realsense/manifest.json`；
- `algorithm-packages/yolox/manifest.json`；
- `workflow-runtimes/realsense-yolox/manifest.json`。
- `artifact-packages/mujoco-arm-runtime/manifest.json`；
- `artifact-packages/mujoco-six-axis-model/manifest.json`；
- `artifact-packages/mock-camera-mujoco-calibration/manifest.json`。

平台还内置“模拟RGB相机→模拟目标检测器→模拟检测结果过滤器”三节点流程，用于在没有真实硬件时验证通用图、参数Schema、修订和端侧协调链路。

## 工程命令

校验仓库中所有资产包：

```powershell
.\services\platform-api\.venv\Scripts\python.exe tools\validate_asset_packages.py
```

生成一个尚未验证的USB硬件适配包骨架：

```powershell
python tools\scaffold_hardware_adapter.py `
  --slug demo-lidar `
  --adapter-id hardware.lidar.demo `
  --class-name DemoLidar `
  --name "Demo LiDAR" `
  --vendor "Demo Robotics" `
  --category lidar.3d `
  --transport usb `
  --vendor-id 1234 `
  --output points:pointcloud.xyz.v1
```

脚手架不会覆盖已有目录。生成包包含Manifest、型号Profile、Python entry point、真实适配器骨架、确定性Simulator和生命周期契约测试。生成包要求Edge Agent 0.4.1或更高版本；该版本能够把插件领域信息合并到同一个底层USB设备记录中，避免重复实例。
