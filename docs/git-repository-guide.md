# Git 仓库与上传指南

## 仓库边界

本目录是独立单仓库根目录：

```text
robot-development-platform/
```

不要把上层工作区或其他项目一并提交。当前目录包含平台所需的应用、服务、Runtime、Manifest、受管制品、文档和测试。

## 为什么使用 Git LFS

以下二进制不是普通源码，但为保证当前基线可复现需要保留：

- YOLOX ONNX 模型；
- Jetson TensorRT Engine；
- 当前 Manifest 引用的 wheel；
- 当前 Manifest 引用的容器归档。

`.gitattributes` 已让 `*.onnx`、`*.engine`、`*.whl`、`*.tar` 和 `*.tar.gz` 走 Git LFS。Manifest 仍使用 SHA-256 固定实际内容；Git LFS 只负责传输和存储，不能替代制品契约。

克隆方需要：

```powershell
git lfs install
git lfs pull
```

## 上传到私有远端

仓库级许可证尚未完成审查，首次上传建议使用私有 GitHub、GitLab 或 Gitea 仓库。

在仓库根目录执行：

```powershell
.\scripts\check-repository.cmd
git add .
git status --short
git lfs ls-files
git diff --cached --stat
git commit -m "chore(repo): establish robot platform baseline"
git tag -a baseline-2026-10-08 -m "Validated robot platform baseline"
git remote add origin <PRIVATE_REPOSITORY_URL>
git push -u origin main
git push origin baseline-2026-10-08
```

首次 `git add .` 后必须人工确认：

- 没有 `.env`、密钥、数据库、日志、`.run/`、虚拟环境和 `node_modules/`；
- LFS 列表包含 ONNX、Engine、当前 wheel 和容器 tar；
- 没有人员照片、视频或其他个人数据；
- 没有本机固定 IP、令牌或账号；
- `tools/validate_asset_packages.py` 通过。

仓库自检脚本会检查当前基线资源是否存在、是否被误忽略、二进制是否命中 Git LFS，以及候选文件中是否混入虚拟环境、运行状态、数据库、日志或常见密钥文件。

## 推荐保护规则

远端创建完成后建议：

- `main` 禁止强制推送；
- 合并必须通过 Pull Request；
- CI 三个 Job 必须成功；
- 至少一人审查核心契约、制品和安全相关变更；
- Release 使用签名 Tag；
- Secret scanning 和依赖告警开启；
- Git LFS 配额和备份策略明确。

## 克隆后复现

```powershell
git clone <PRIVATE_REPOSITORY_URL>
cd robot-development-platform
git lfs install
git lfs pull
.\scripts\setup.cmd
.\scripts\dev.cmd -Lan -SimulationAgent
```

随后按 [项目交接与复现手册](project-handover.md)执行完整验收。

## 发布原则

Git Tag 表示源码和 Manifest 基线；节点真正安装的制品还必须由 artifact ID、version、variant 和 SHA-256 唯一确定。不能用分支名、目录名、`latest` 或 Git LFS 对象 ID 替代制品身份。
