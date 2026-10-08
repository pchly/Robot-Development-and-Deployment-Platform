"""Edge Agent 运行循环。"""

import logging
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from robot_edge_agent import __version__
from robot_edge_agent.deployment_runtime import EdgeDeploymentManager
from robot_edge_agent.devices import discover_usb_devices
from robot_edge_agent.telemetry import collect_static_metadata, collect_system_metrics

LOGGER = logging.getLogger(__name__)


class AgentPlatformClient(Protocol):
    """Edge Agent 运行循环所需的平台客户端接口。"""

    def register(self, payload: dict[str, object]) -> dict[str, Any]: ...

    def heartbeat(
        self,
        agent_id: str,
        sequence: int,
        metrics: dict[str, object] | None = None,
        devices: list[dict[str, object]] | None = None,
    ) -> dict[str, Any]: ...


class ArtifactManager(Protocol):
    """受控制品安装协调器。"""

    def reconcile(self, client: AgentPlatformClient, agent_id: str) -> bool: ...


def build_registration(
    *,
    agent_id: str,
    name: str,
    capabilities: Iterable[str] = (),
    installed_artifacts: Iterable[dict[str, object]] = (),
) -> dict[str, object]:
    """构造不包含敏感信息的节点注册数据。"""

    return {
        "agent_id": agent_id,
        "name": name,
        "version": __version__,
        "capabilities": sorted(set(capabilities)),
        "metadata": collect_static_metadata(),
        "installed_artifacts": list(installed_artifacts),
    }


def run_agent(
    client: AgentPlatformClient,
    registration: dict[str, object],
    *,
    heartbeat_interval_s: float = 5.0,
    reconnect_initial_s: float = 1.0,
    reconnect_max_s: float = 30.0,
    once: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    collect_metrics: Callable[[], dict[str, object]] = collect_system_metrics,
    discover_devices: Callable[[], list[dict[str, object]]] = discover_usb_devices,
    deployment_manager: EdgeDeploymentManager | None = None,
    artifact_manager: ArtifactManager | None = None,
) -> bool:
    """注册节点并持续发送心跳。

    汇报遥测、协调声明式Runtime，并只接受类型固定且摘要锁定的制品安装计划。
    """

    if heartbeat_interval_s <= 0:
        raise ValueError("心跳间隔必须大于 0 秒")
    if reconnect_initial_s <= 0:
        raise ValueError("初始重连间隔必须大于 0 秒")
    if reconnect_max_s < reconnect_initial_s:
        raise ValueError("最大重连间隔不得小于初始重连间隔")

    agent_id = str(registration["agent_id"])
    sequence = 0
    retry_delay_s = reconnect_initial_s
    is_registered = False

    while True:
        try:
            if not is_registered:
                client.register(registration)
                is_registered = True
                LOGGER.info("Edge Agent 已注册：%s", agent_id)

            client.heartbeat(agent_id, sequence, collect_metrics(), discover_devices())
            if deployment_manager is not None:
                deployment_manager.reconcile(client, agent_id)
            if artifact_manager is not None and artifact_manager.reconcile(client, agent_id):
                LOGGER.info("制品已激活，准备重启Agent并重新加载插件")
                return True
            LOGGER.debug("心跳已发送：agent_id=%s sequence=%d", agent_id, sequence)
            if once:
                return False

            sequence += 1
            retry_delay_s = reconnect_initial_s
            sleep(heartbeat_interval_s)
        except OSError as error:
            if once:
                raise
            is_registered = False
            LOGGER.warning(
                "Platform API 连接失败，%.1f 秒后重试：%s",
                retry_delay_s,
                error,
            )
            sleep(retry_delay_s)
            retry_delay_s = min(retry_delay_s * 2, reconnect_max_s)
