# 控制面持久化

Platform API使用Python标准库SQLite保存项目、工作流修订、部署控制状态和制品安装事务，不为开发环境增加外部数据库依赖。部署计划引用固定的项目与修订号；API重启后会恢复DeploymentPlan、期望状态、最近一次状态机检查点和安装事务，使Edge Agent能够重新接管同名受管Runtime。

## 数据边界

必须持久化：

- 项目名称、目标和归档状态；
- 工作流定义及每次不可变修订；
- DeploymentPlan、期望状态和状态机检查点；
- 制品安装计划、目标变体与事务状态。

保持易失：

- Agent在线状态和心跳；
- 实时指标和最近预览元数据；
- 低频预览图片；
- 相机帧、点云和模型张量。

高频数据继续留在Edge节点，不能写入控制面SQLite。

## 路径

默认数据库：

```text
services/platform-api/.data/control-plane.sqlite3
```

生产或独立部署通过环境变量指定绝对路径：

```text
ROBOT_PLATFORM_DB_PATH=/data/robot-platform/control-plane/control-plane.sqlite3
```

父目录会在应用启动或第一次数据操作时自动创建。数据库使用`PRAGMA user_version`记录Schema版本；当前Schema v3会从v1/v2顺序补齐`deployment_records`与`artifact_installation_records`表，程序遇到未知版本仍会拒绝读写，不能静默降级。

部署状态只在状态机发生转换、提交或停止请求时写入SQLite。Agent周期上报的指标继续只更新内存，避免以遥测频率写盘。预览JPEG仍由内存保存，API重启后等待Agent重新上传。

## 备份原则

当前阶段在停止Platform API后复制数据库文件即可形成一致备份。进入多用户或长期运行阶段后，应使用SQLite在线备份API或迁移到PostgreSQL，不能在服务写入期间直接复制活动数据库文件。

自动化测试覆盖数据库关闭后重开、v1到v2迁移、多个修订、归档、未知Schema拒绝、从指定不可变修订生成DeploymentPlan，以及API重启后恢复运行计划和停止指令。
