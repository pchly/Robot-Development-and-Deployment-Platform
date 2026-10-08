# 工程环境与部署边界

系统结构和领域逻辑见[当前平台基线](current-platform-baseline.md)。本文只说明各部分运行在哪里以及如何启动。

## 当前支持矩阵

| 部分 | 当前运行位置 | 已验证环境 | 主要用途 |
|---|---|---|---|
| Web Studio | Windows开发机 | Node.js 24 / Chromium | 设计与运维界面 |
| Platform API | Windows开发机 | Python 3.14 / SQLite | 控制面与持久化 |
| Simulation Agent | Windows开发机 | Python 3.14 / AMD64 | MuJoCo仿真执行节点 |
| Jetson Edge Agent | Jetson AGX Orin | Ubuntu 22.04 / Python 3.10 / ARM64 | 实机感知执行节点 |
| RealSense→YOLOX Runtime | Jetson Docker | JetPack 6.2.3 / TensorRT 10.3 | RGB-D采集与目标检测 |
| MuJoCo Runtime | Windows受管Python包 | MuJoCo 3.14 | 六轴机械臂仿真 |

Windows的`AMD64/x64/x86_64`以及Linux常见的`ARM64/aarch64`在制品解析中分别视为同一架构族。

## 三个常驻角色

```text
浏览器中的Web Studio
        │ HTTP
        ▼
Windows Platform API + SQLite
        │ HTTP / Tailscale
        ├────────▶ Windows Simulation Agent
        └────────▶ Jetson Edge Agent（systemd）
```

Web Studio关闭不会停止已授权Runtime；Platform API短暂断开时，Agent继续维持当前Runtime并重试注册。确定性运动安全不能依赖这条网络链路。

## Windows启动方式

### 控制面 + MuJoCo仿真节点

```powershell
cd <PROJECT_ROOT>
.\scripts\dev.cmd -Lan -SimulationAgent
```

### 控制面 + 远端Jetson节点

```powershell
cd <PROJECT_ROOT>
.\scripts\dev.cmd -Lan -WithoutAgent
```

### 停止

```powershell
.\scripts\stop-dev.cmd
```

脚本通过`.run/processes.json`管理后台进程，日志写入：

- `.run/platform-api.out.log`和`.run/platform-api.err.log`；
- `.run/studio.out.log`和`.run/studio.err.log`；
- `.run/simulation-agent.out.log`和`.run/simulation-agent.err.log`。

如果提示环境已经运行，先执行停止命令，不要重复启动第二套API或Agent。

## Windows仿真节点数据根

`-SimulationAgent`使用：

```text
.run/simulation-agent/
├── runtime-packages/   # 受管MuJoCo Runtime版本
├── calibrations/       # 受管标定配置版本
├── staging/            # 制品下载暂存
└── runs/               # 运行产物
```

这些内容属于开发运行状态，不是Catalog源文件。Catalog源文件仍位于仓库中的`artifact-packages/`和`simulation-runtimes/`。

## Jetson服务

Jetson Agent以systemd服务运行：

```bash
sudo systemctl status robot-edge-agent.service
sudo journalctl -u robot-edge-agent.service -f
```

默认受管目录：

```text
/data/robot-platform/edge-agent/
├── releases/           # Agent版本
├── current             # 当前Agent链接
├── plugins/            # 轻量发现插件
├── staging/            # 下载暂存
├── calibrations/       # 标定配置版本
└── runtime-packages/   # 本地Runtime包

/data/robot-platform/cache/tensorrt/  # TensorRT Engine
/data/robot-platform/runs/            # Runtime产物
```

安装和升级见[Jetson部署文档](../services/edge-agent/deploy/jetson/README.md)。

## 网络

- 当前开发控制面地址为Windows的Tailscale或可信局域网地址；
- Jetson只需要主动访问Platform API，不要求从Windows直接SSH控制；
- Tailscale File可用于人工传递尚未进入平台分发流程的发布包；
- 原始相机数据、深度图、张量和控制周期数据不通过Platform API；
- 当前LAN模式没有认证，不能暴露到公网。

## 环境与制品的关系

代码运行环境和业务制品分开管理：

- Platform API/Studio代码升级需要重启Windows开发环境；
- Edge Agent代码升级需要更新对应节点的Agent版本；
- Runtime、插件、Engine和标定配置通过平台制品事务独立准备；
- 不同操作系统使用同一逻辑制品的不同发行变体；
- 不允许为了运行一个工作流而直接修改Agent基础环境。

## 尚未进入当前基线

- Windows Service形式的正式Agent安装；
- Linux生产控制面与反向代理；
- 用户身份、TLS、权限和审计；
- CI中的Windows/Linux/ARM64多平台矩阵；
- ROS 2和真实运动执行器；
- 24小时Agent长稳及完整灾难恢复演练。
