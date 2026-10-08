# Jetson Edge Agent手动联调

本流程用于Phase 1第一次跨设备验证。它不安装systemd服务、不修改Docker、不访问摄像头和CAN，也不会控制机器人动作。

## 1. 在Jetson准备临时目录

```bash
mkdir -p /data/robot-platform/manual/edge-agent
```

## 2. 在Windows启动局域网控制面

在项目根目录执行：

```powershell
.\scripts\dev.cmd -Lan -WithoutAgent
```

该模式把Platform API和Web Studio监听到局域网接口。当前没有身份认证，只能在可信开发网络中使用，不得配置公网端口转发。

如果Windows防火墙弹出提示，只允许“专用网络”，不要允许公用网络。

## 3. 从Jetson检查API

```bash
curl http://192.168.1.109:8765/health
```

预期：

```json
{"status":"ok","version":"0.1.0"}
```

如果连接失败，先检查Windows防火墙、两台设备是否仍在同一Wi-Fi网络，以及Windows地址是否仍为`192.168.1.109`。

## 4. 从Windows复制Edge Agent源码

打开新的PowerShell终端，先进入项目根目录：

```powershell
cd <PROJECT_ROOT>
```

然后使用当前电脑上Git自带的OpenSSH客户端复制文件：

```powershell
& "D:\Program Files\Git\usr\bin\scp.exe" -r `
  .\services\edge-agent\src `
  .\services\edge-agent\pyproject.toml `
  nvidia@192.168.1.111:/data/robot-platform/manual/edge-agent/
```

这里只复制运行所需的源码和项目描述文件，不复制Windows虚拟环境、测试缓存和构建产物。

如果直接执行`scp`时提示“无法将scp识别为命令”，继续使用上面的完整路径即可。可以用以下命令确认程序存在：

```powershell
Test-Path "D:\Program Files\Git\usr\bin\scp.exe"
```

首次连接会询问主机指纹，核对目标地址后确认，并按提示输入Jetson用户密码。

## 5. 在Jetson前台运行

```bash
cd /data/robot-platform/manual/edge-agent

PYTHONPATH=src python3 -m robot_edge_agent \
  --server-url http://192.168.1.109:8765 \
  --agent-id jetson-agx-orin-01 \
  --name JetsonAGXOrin \
  --capability jetson
```

该命令直接使用系统Python 3.10和项目源码，不安装第三方运行依赖。保持终端运行，并在Windows浏览器打开：

```text
http://192.168.1.109:5173
```

预期看到：

- 节点`JetsonAGXOrin`在线；
- 设备型号为Jetson AGX Orin；
- 系统为Ubuntu 22.04；
- 架构为aarch64；
- Python为3.10.12；
- 显示内存、根分区、`/data`、运行时间和最高温度。

## 6. 手动停止

Jetson终端按`Ctrl+C`停止Agent。

Windows项目根目录执行：

```powershell
.\scripts\stop-dev.cmd
```

## 验收记录

记录以下结果：

- Windows到Jetson的SSH是否稳定；
- Jetson到Platform API的HTTP是否连通；
- Agent能否注册并持续心跳；
- Web Studio显示的静态信息是否正确；
- 内存、磁盘、温度和运行时间是否合理；
- API停止并恢复后，Agent是否自动重新注册。

### 2026-10-02 首次实机结果

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| Windows到Jetson的SSH/SCP | 通过 | 源码成功复制到`/data/robot-platform/manual/edge-agent` |
| Jetson到Platform API的HTTP | 通过 | Agent成功注册并持续上报心跳 |
| 设备静态信息 | 通过 | Jetson AGX Orin、Ubuntu 22.04.5、aarch64、Python 3.10.12 |
| 系统遥测 | 通过 | 可用内存52.1 GB/61.4 GB、数据盘约1.5 TB可用、运行6天23小时、最高温度50.8 °C |
| Web Studio展示 | 通过 | 节点总数1、在线1、离线0，节点详情完整显示 |
| 控制面重启恢复 | 通过 | API停止后按1、2、4、8秒退避重试，并在API恢复后自动重新注册 |

