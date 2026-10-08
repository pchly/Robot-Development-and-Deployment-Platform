# 容器镜像可信分发

平台支持两种互补的容器镜像来源，工作流和安装事务使用同一个制品契约：

1. 正式Registry：Manifest固定显式标签和仓库`sha256`摘要，Edge Agent通过`docker image pull repository@sha256:...`拉取，验证`RepoDigests`与架构后更新本地标签。
2. 离线归档：Manifest同时固定Docker镜像ID和归档文件SHA-256。Platform API流式发送Catalog目录内的归档；Edge Agent把内容流式写入`/data/robot-platform/edge-agent/staging`，复核归档摘要后执行`docker image load`，再验证镜像ID、架构和最终标签。

当前Windows开发机没有Docker，Jetson已经承担arm64镜像构建，因此开发阶段优先使用离线归档；部署节点增多或进入持续发布后，切换到带TLS和访问控制的OCI Registry。两种方式不改变工作流定义。

## Jetson端到端验收

2026-10-04使用`robot-platform/container-import-smoke:0.1.0`完成真实链路验收：Docker原生归档大小为17,920 bytes，归档SHA-256为`a0f1dad14d6fe146da67201f50efa28f5cfac00465bca5f4f8e169c2ea231d68`，最终镜像ID为`sha256:4c5b95b9366a8a6d5f1a983f80879b17c38e96bfbd1269f2f7f52c7887c808b1`。安装事务`artifact-install-b4d62411bd2141068af7`依次进入`pending`、`downloading`、`verifying`、`installing`和`succeeded`；Agent 0.10.2重新盘点后上报相同标签与摘要，二次准备预检判定节点已经就绪且无需变更。

无Docker环境生成的手工引导归档只用于把最小镜像载入构建节点；Docker可能在载入时规范化镜像配置，因此不能直接把引导配置摘要登记为Catalog镜像ID。正式Catalog归档必须由`export-container-image.sh`调用`docker image save`生成，并采用脚本输出的最终镜像ID和归档SHA-256。

## 生成归档

在构建镜像的Jetson上执行：

```bash
cd /data/robot-platform/manual/edge-agent-0.10.2

bash scripts/export-container-image.sh \
  robot-platform/realsense-yolox-runtime:0.1.3 \
  /data/robot-platform/releases/realsense-yolox-runtime-0.1.3.tar
```

脚本拒绝覆盖现有文件，并输出镜像ID、架构、归档大小、归档SHA-256和可直接写入Manifest的字段。归档很大时应先确认Windows和Jetson两端空间。

## Manifest契约

工作流Runtime示例：

```json
{
  "runtime_image": "robot-platform/realsense-yolox-runtime:0.1.3",
  "runtime_image_digest": "sha256:<docker-image-id>",
  "runtime_image_distribution": {
    "file": "dist/realsense-yolox-runtime-0.1.3.tar",
    "sha256": "<archive-sha256>"
  }
}
```

硬件Runtime使用`runtime.image_distribution`，算法Runtime使用`runtime.runtime_image_distribution`，结构相同。`file`只能指向当前组件目录内的`.tar`、`.tar.gz`或`.tgz`文件。

归档本身的摘要和加载后Docker镜像的摘要是两个不同的值，不能互换：

- `runtime_image_distribution.sha256`保护传输文件；
- `runtime_image_digest`保护加载后的镜像内容。

## 安全与回滚

- Agent不会执行Manifest中的任意命令，只调用固定参数形式的Docker命令。
- 下载采用大小上限和流式SHA-256校验，不把大型镜像读入内存。
- 导入后必须匹配Catalog声明的标签、镜像ID和目标架构。
- 目标标签原先存在时，失败会恢复旧镜像标签；原先不存在时，会移除未通过验证的目标标签。
- 临时归档在事务结束后自动清理，不主动删除已经验证或历史使用的镜像层。
