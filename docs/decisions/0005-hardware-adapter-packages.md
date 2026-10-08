# ADR-0005：以硬件族适配包沉淀可复用能力

- 状态：已接受
- 日期：2026-10-02

## 背景

平台希望在接入一次新硬件后，后续项目能够直接选择、配置并组合。简单保存厂商名称或一段示例代码无法支持稳定复用；不同型号、固件、操作系统、驱动和数据格式也不能被视为完全相同。

## 决策

硬件库的基本复用单位是`HardwareAdapterPackage`（硬件族适配包）：

- 一个适配包对应一个具有共同SDK和行为模型的硬件产品族；
- 同一产品族的型号差异放入`DeviceProfile`，不复制整个适配器；
- 每个真实连接的设备由`DeviceInstance`表示，包含序列号、固件、连接方式和当前能力；
- 适配包通过标准生命周期接口接入平台；
- 输入输出必须使用版本化数据类型，不能只暴露厂商私有对象；
- 驱动、权限、架构和系统要求必须声明，不能隐藏在安装说明中；
- 新硬件只有通过规定的验证级别后，才能用于一键部署。

## 标准生命周期

```text
probe → validate → open → configure → start → health → stop → close
```

- `probe`：只读发现设备，不占用硬件；
- `validate`：检查型号、固件、权限、驱动、带宽和配置；
- `open`：获取设备句柄；
- `configure`：应用经过Schema校验的参数；
- `start`：开始产生数据或接受命令；
- `health`：报告帧率、错误、温度、丢包和重连；
- `stop/close`：确定性释放资源。

控制类硬件还必须增加授权、超时、安全状态和急停契约，不能直接复用普通传感器接口。

## 适配包结构

```text
hardware-adapters/
└── realsense/
    ├── manifest.json
    ├── adapter/
    ├── profiles/
    │   ├── d435i.json
    │   └── d455.json
    ├── schemas/
    ├── tests/
    ├── fixtures/
    ├── examples/
    └── runtime/
```

`manifest.json`至少声明：

- 适配包ID和语义化版本；
- 支持的厂商、产品、型号、固件范围和发现规则；
- 支持的OS、架构、JetPack/L4T和驱动范围；
- 设备节点、USB、网络、用户组和其他权限；
- 参数Schema；
- 输入输出端口及版本化数据类型；
- Runtime制品和不可变摘要；
- 健康检查、资源预算和故障恢复策略。

## 多层设备身份

复合USB设备或厂商SDK可能为同一实体暴露不同序列号，平台不得把所有身份压缩为一个含义模糊的`serial`字段：

- `transport_serial`来自USB、UVC、网络或总线层，用于宿主机发现和热插拔关联；
- `sdk_serial`来自厂商SDK，用于SDK内部选择并打开设备；
- `asic_serial`、`firmware_update_id`等厂商身份作为可检索元数据保留；
- `physical_port`用于拓扑诊断，不作为永久身份，因为换插口后会变化；
- `DeviceInstance`使用平台生成的稳定ID，并保存上述身份之间经过验证的映射。

适配器执行时必须使用对应接口要求的身份。例如RealSense的
`pipeline_config.enable_device()`使用`librealsense`返回的`sdk_serial`，不能直接传入UVC的
`transport_serial`。

首台D415实机验证确认了这一差异：`sdk_serial`为`125322060182`，而
`asic_serial`、`firmware_update_id`和Linux USB/UVC的`transport_serial`均为
`123423024767`。平台保留两者，禁止推断它们必然相等。

## 验证级别

| 级别 | 含义 | 是否允许一键部署 |
| --- | --- | --- |
| `registered` | 已登记定义，尚未运行 | 否 |
| `simulated` | 模拟器或录制数据通过 | 否 |
| `bench_validated` | 指定硬件和目标环境台架验证通过 | 是，限匹配环境 |
| `production_approved` | 长稳、故障和安全验收通过 | 是 |

## 新硬件接入流程

1. 读取USB、网络或总线身份并建立设备档案；
2. 选择已有适配包或由模板创建新适配包；
3. 封装厂商SDK，映射到平台标准生命周期和端口类型；
4. 添加模拟数据、契约测试和错误场景测试；
5. 在目标Edge环境进行硬件在环验证；
6. 固定制品版本与摘要，发布到硬件库；
7. 工作流兼容性解析器才允许使用该适配包。

## RealSense示例

- `realsense`适配包负责librealsense封装、设备枚举、RGB/Depth采集和健康状态；
- D435i、D455等型号通过Profile声明能力差异；
- 输出统一为`image.rgb8.v1`、`image.depth16.v1`和`camera.intrinsics.v1`；
- YOLO只依赖标准图像端口，不依赖RealSense厂商SDK；
- 因此未来更换为普通UVC相机时，只需满足相同图像端口契约。

## 结果

这种设计保留了“新硬件适配一次、以后直接复用”的目标，同时避免为每个序列号复制代码，也避免算法与某个厂商SDK永久绑定。
