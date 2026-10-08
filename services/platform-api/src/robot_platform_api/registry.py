"""Edge Agent 进程内注册表。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock

from robot_platform_api.models import (
    AgentHeartbeat,
    AgentRecord,
    AgentRegistration,
    AgentStatus,
)


class AgentNotFoundError(LookupError):
    """请求的 Edge Agent 尚未注册。"""


class AgentRegistry:
    """线程安全的 Edge Agent 注册表。

    当前实现用于原型验证，服务重启后数据会丢失。持久化将在后续切片中实现。
    """

    def __init__(
        self,
        *,
        offline_after_s: float = 15.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if offline_after_s <= 0:
            raise ValueError("离线阈值必须大于 0 秒")
        self._offline_after = timedelta(seconds=offline_after_s)
        self._now = now or (lambda: datetime.now(UTC))
        self._records: dict[str, AgentRecord] = {}
        self._lock = Lock()

    def register(self, registration: AgentRegistration) -> AgentRecord:
        """创建或更新节点记录，并将节点标记为在线。"""

        now = self._now()
        with self._lock:
            existing = self._records.get(registration.agent_id)
            registered_at = existing.registered_at if existing else now
            heartbeat_sequence = existing.heartbeat_sequence if existing else 0
            metrics = existing.metrics if existing else None
            devices = existing.devices if existing else []
            record = AgentRecord(
                **registration.model_dump(),
                status=AgentStatus.ONLINE,
                registered_at=registered_at,
                last_seen_at=now,
                heartbeat_sequence=heartbeat_sequence,
                metrics=metrics,
                devices=devices,
            )
            self._records[registration.agent_id] = record
            return record.model_copy(deep=True)

    def heartbeat(self, agent_id: str, heartbeat: AgentHeartbeat) -> AgentRecord:
        """刷新已注册节点的最后在线时间。"""

        now = self._now()
        with self._lock:
            existing = self._records.get(agent_id)
            if existing is None:
                raise AgentNotFoundError(agent_id)
            updates = {
                "status": AgentStatus.ONLINE,
                "last_seen_at": now,
                "heartbeat_sequence": heartbeat.sequence,
            }
            if heartbeat.metrics is not None:
                updates["metrics"] = heartbeat.metrics
            if heartbeat.devices is not None:
                updates["devices"] = heartbeat.devices
            record = existing.model_copy(update=updates)
            self._records[agent_id] = record
            return record.model_copy(deep=True)

    def list_agents(self) -> list[AgentRecord]:
        """返回节点快照，并根据最后心跳时间计算在线状态。"""

        now = self._now()
        with self._lock:
            records = []
            for record in self._records.values():
                status = (
                    AgentStatus.OFFLINE
                    if now - record.last_seen_at > self._offline_after
                    else AgentStatus.ONLINE
                )
                records.append(record.model_copy(update={"status": status}, deep=True))
        return sorted(records, key=lambda item: item.name.casefold())

    def get_agent(self, agent_id: str) -> AgentRecord:
        """返回单个节点快照并计算当前在线状态。"""

        now = self._now()
        with self._lock:
            record = self._records.get(agent_id)
            if record is None:
                raise AgentNotFoundError(agent_id)
            status = (
                AgentStatus.OFFLINE
                if now - record.last_seen_at > self._offline_after
                else AgentStatus.ONLINE
            )
            return record.model_copy(update={"status": status}, deep=True)
