# 贡献指南

## 开发原则

1. `main` 必须保持可安装、可测试、可运行；
2. 一项功能或修复使用一个短生命周期分支；
3. 不直接修改已保存的 Workflow Revision 或 DeploymentPlan；
4. 新硬件、算法、仿真和运行环境必须通过 Manifest、制品和配方主线接入；
5. Studio 不直接访问硬件，Platform API 不承载高频数据面；
6. Agent 只执行显式支持的 Runtime，不解释任意 Shell；
7. 真实执行器功能必须先经过仿真、安全评审和独立硬安全设计。

## 分支和提交

推荐分支名：

```text
feature/<short-name>
fix/<short-name>
docs/<short-name>
release/<version>
```

提交信息使用明确的动词和范围，例如：

```text
feat(studio): add deployment preflight details
fix(agent): reject mismatched artifact digest
docs(handover): document Jetson recovery
```

不要提交虚拟环境、运行数据库、日志、个人画面、令牌或本机绝对路径。

## 制品变更

- ONNX、Engine、wheel、tar 由 Git LFS 管理；
- 文件内容变化必须更新版本或构建身份；
- Manifest 中的 SHA-256 必须同步更新；
- TensorRT Engine 必须绑定模型摘要、GPU 架构、TensorRT 版本和精度；
- 同一逻辑资产在不同操作系统/架构下使用不同 variant；
- 历史构建输出不进入仓库，只保留当前 Manifest 引用的文件。

修改后执行：

```powershell
.\services\platform-api\.venv\Scripts\python.exe tools\validate_asset_packages.py
git lfs ls-files
```

## 合并前检查

```powershell
cd services\platform-api
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider

cd ..\edge-agent
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider

cd ..\..\simulation-runtimes\mujoco-arm
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m pytest -p no:cacheprovider

cd ..\windows-webcam-yolox-mujoco
..\mujoco-arm\.venv\Scripts\python -m ruff check src tests
..\mujoco-arm\.venv\Scripts\python -m pytest -p no:cacheprovider

cd ..\..\apps\studio
npm.cmd run build

cd ..\..
.\services\platform-api\.venv\Scripts\python.exe tools\validate_asset_packages.py
```

涉及部署状态机、制品安装、Agent 重连、安全停止或控制循环时，还必须执行 [当前基线验收清单](docs/baseline-acceptance-checklist.md)。

## Pull Request 要求

说明以下内容：

- 问题与工程目标；
- 代码和契约变化；
- 风险与回退方式；
- 自动测试结果；
- 需要人工验收的 Windows/Jetson/设备场景；
- 是否改变制品版本、摘要、数据库 Schema 或安全边界。

## 许可证

当前仓库尚未选择仓库级开源许可证。在完成代码所有权、模型、第三方依赖和 NVIDIA/Intel 相关制品许可审查前，应使用私有远端仓库，不应公开发布。
