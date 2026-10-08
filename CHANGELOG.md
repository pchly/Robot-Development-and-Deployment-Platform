# 变更记录

本项目遵循“不可变工作流修订 + 不可变部署计划 + 带摘要制品”的版本原则。应用版本由各包自己的 `pyproject.toml` 或 `package.json` 管理；本文件记录仓库级稳定基线。

## [Unreleased]

### 计划

- 统一发布源码、Git LFS 制品和 SHA-256 清单；
- Windows Agent 服务化；
- 控制面身份认证、TLS、RBAC 与审计；
- 结构化诊断包和运行回放。

## [baseline-2026-10-08] - 2026-10-08

### 已具备

- React Web Studio、FastAPI Platform API 和 Windows/Jetson Agent；
- SQLite Schema v3 控制面持久化；
- Manifest Catalog、平台制品变体、摘要校验和受控安装；
- 不可变工作流修订、DeploymentPlan、停止、替换和回退状态机；
- Jetson RealSense D415 + YOLOX TensorRT 感知链路；
- Windows 摄像头 + YOLOX ONNX CPU + Safety Gate + MuJoCo 闭环；
- MuJoCo RGB-D 空间定位与机械臂仿真配方；
- 分段性能指标、轻量预览、按需实时监控和终态验收报告；
- Platform API、Edge Agent、两套 Runtime 和 Studio 的自动检查基线。

### 仓库化

- 建立独立 Git 单仓库边界；
- 对 ONNX、TensorRT Engine、wheel 和容器归档启用 Git LFS；
- 只纳入当前 Manifest 引用的不可变发行文件；
- 增加 CI、贡献规范、安全说明和完整交接文档。
