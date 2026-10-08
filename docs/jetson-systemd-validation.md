# Jetson Edge Agent systemd验收

本流程把已通过手动联调的Edge Agent安装为Jetson系统服务。当前服务只读取系统遥测，不访问摄像头、CAN和运动设备，也不修改Docker配置或现有容器。

## 1. 从Windows复制部署文件

在项目根目录执行：

```powershell
& "D:\Program Files\Git\usr\bin\scp.exe" -r ".\services\edge-agent\deploy" "nvidia@192.168.1.111:/data/robot-platform/manual/edge-agent/"
```

## 2. 停止手动Agent

在当前运行Agent的Jetson终端按`Ctrl+C`。安装器检测到手动Agent进程时会拒绝安装，防止同一个节点重复上报。

## 3. 安装服务

在Jetson执行：

```bash
cd /data/robot-platform/manual/edge-agent

sudo bash deploy/jetson/install.sh \
  --server-url http://192.168.1.109:8765 \
  --agent-id jetson-agx-orin-01 \
  --name JetsonAGXOrin \
  --user nvidia
```

安装器会先用`systemd-analyze verify`检查单元文件，再启用并启动服务。

## 4. 验证状态和日志

```bash
sudo systemctl status robot-edge-agent.service --no-pager
sudo systemctl is-enabled robot-edge-agent.service
sudo systemctl is-active robot-edge-agent.service
sudo journalctl -u robot-edge-agent.service -n 30 --no-pager
```

预期`is-enabled`输出`enabled`，`is-active`输出`active`，日志包含：

```text
Edge Agent 已注册：jetson-agx-orin-01
```

刷新Web Studio后，节点应保持在线并继续更新心跳与遥测。

## 5. 当前阶段不要重启Jetson

Jetson上还有`openral-openral-dev-1`和`moss-tts-server`两个既有容器。确认它们的重启策略和业务影响前，不执行整机重启。先完成systemd服务运行和日志验收，再单独安排开机自启动测试。

## 6. 回退到手动运行

如果服务异常，执行：

```bash
sudo systemctl disable --now robot-edge-agent.service
sudo journalctl -u robot-edge-agent.service -n 100 --no-pager
```

该操作只停用服务，不删除版本、配置或日志。随后可以继续使用原来的`PYTHONPATH=src python3 -m robot_edge_agent ...`命令进行排查。

需要移除systemd单元时使用保守卸载器：

```bash
sudo bash deploy/jetson/uninstall.sh
```
