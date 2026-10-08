# Jetson systemd部署

该部署运行注册、心跳、系统遥测、USB发现和受限的Docker Runtime协调能力。硬件SDK和算法依赖仍封装在独立容器中，Agent进程不直接打开RealSense设备。

## 目录约定

- 版本目录：`/data/robot-platform/edge-agent/releases/<UTC时间>`
- 当前版本：`/data/robot-platform/edge-agent/current`
- 受管插件目录：`/data/robot-platform/edge-agent/plugins`
- 制品暂存目录：`/data/robot-platform/edge-agent/staging`
- 标定配置版本库：`/data/robot-platform/edge-agent/calibrations`
- TensorRT Engine受管目录：`/data/robot-platform/cache/tensorrt`
- 环境配置：`/etc/robot-platform/edge-agent.env`
- systemd单元：`/etc/systemd/system/robot-edge-agent.service`
- 运行日志：systemd journal
- Runtime产物：`/data/robot-platform/runs/<plan_id>`

安装器不会删除旧版本目录，便于后续人工检查和回滚。

## 安装

先停止手动运行的Agent，然后在Edge Agent源码目录执行：

```bash
sudo bash deploy/jetson/install.sh \
  --server-url http://192.168.1.109:8765 \
  --agent-id jetson-agx-orin-01 \
  --name JetsonAGXOrin \
  --user nvidia
```

## 运维命令

```bash
sudo systemctl status robot-edge-agent.service
sudo journalctl -u robot-edge-agent.service -f
sudo systemctl restart robot-edge-agent.service
sudo systemctl stop robot-edge-agent.service
sudo systemctl start robot-edge-agent.service
```

服务启动后仍由Agent自身处理Platform API断线重连；systemd负责进程异常退出后的重启。Agent重启后会根据plan标签接管仍在运行的受管容器，而不是重复启动。

## 安装硬件发现插件

Edge Agent只从`ROBOT_EDGE_PLUGIN_PATH`声明的受管目录发现Python entry point，默认安装器将其设置为`/data/robot-platform/edge-agent/plugins`。插件用于只读设备发现和生命周期接口，不应把Librealsense、CUDA、TensorRT等大型依赖安装到Agent环境；这些依赖继续由对应Runtime镜像承载。

在开发机完成适配包测试并构建wheel：

```powershell
cd hardware-adapters\<package>
python -m build --wheel
```

将wheel复制到Jetson后，安装到独立受管目录并重启Agent：

```bash
python3 -m pip install \
  --no-deps \
  --upgrade \
  --target /data/robot-platform/edge-agent/plugins \
  /path/to/robot_hardware_<package>-0.1.0-py3-none-any.whl

sudo systemctl restart robot-edge-agent.service
sudo journalctl -u robot-edge-agent.service -n 50 --no-pager
```

启动日志中的`硬件插件加载完成：loaded=N failed=M`用于确认加载结果。普通单插件加载或`probe`失败会被隔离并记录日志；两个插件声明相同`adapter_id`属于配置冲突，Agent会拒绝启动，避免设备归属不确定。

适配包升级采用新wheel覆盖安装并重启Agent。正式发布时应保留wheel、版本和SHA-256记录；不要直接在受管目录中手工编辑Python源码。

## 卸载服务

```bash
sudo bash deploy/jetson/uninstall.sh
```

卸载器只停用并移除systemd单元，保留`/data/robot-platform/edge-agent`、环境配置和journal日志，避免误删现场数据。重新运行安装器即可恢复服务。

## 当前安全边界

服务以`nvidia`普通用户运行，启用只读系统文件、空Linux capability集合，并只给Agent的产物目录写权限。服务用户属于`docker`组，因此Docker socket在安全意义上具备主机级权限；部署计划必须继续执行组件、镜像、路径和参数白名单，不得把网页输入直接解释为命令。真实Runtime容器关闭网络，不使用`--privileged`，只映射`/dev`、受管Engine目录和独立产物目录。
