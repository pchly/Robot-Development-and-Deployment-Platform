"""线程安全的受控制品安装事务注册表。"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from robot_platform_api.models import (
    ArtifactDefinition,
    ArtifactInstallationPlan,
    ArtifactInstallationRecord,
    ArtifactInstallationStatus,
    ArtifactInstallationUpdate,
)


class ArtifactInstallationNotFoundError(LookupError):
    """安装事务不存在。"""


class ArtifactInstallationConflict(ValueError):
    """目标节点已经有同制品的活动安装事务。"""


class InvalidArtifactInstallationTransition(ValueError):
    """安装事务状态转换不合法。"""


_TERMINAL = {
    ArtifactInstallationStatus.SUCCEEDED,
    ArtifactInstallationStatus.FAILED,
    ArtifactInstallationStatus.ROLLED_BACK,
}
_ALLOWED_TRANSITIONS = {
    ArtifactInstallationStatus.PENDING: {
        ArtifactInstallationStatus.PENDING,
        ArtifactInstallationStatus.DOWNLOADING,
        ArtifactInstallationStatus.FAILED,
    },
    ArtifactInstallationStatus.DOWNLOADING: {
        ArtifactInstallationStatus.DOWNLOADING,
        ArtifactInstallationStatus.VERIFYING,
        ArtifactInstallationStatus.FAILED,
    },
    ArtifactInstallationStatus.VERIFYING: {
        ArtifactInstallationStatus.VERIFYING,
        ArtifactInstallationStatus.INSTALLING,
        ArtifactInstallationStatus.FAILED,
    },
    ArtifactInstallationStatus.INSTALLING: {
        ArtifactInstallationStatus.INSTALLING,
        ArtifactInstallationStatus.ACTIVATING,
        ArtifactInstallationStatus.SUCCEEDED,
        ArtifactInstallationStatus.FAILED,
        ArtifactInstallationStatus.ROLLED_BACK,
    },
    ArtifactInstallationStatus.ACTIVATING: {
        ArtifactInstallationStatus.ACTIVATING,
        ArtifactInstallationStatus.SUCCEEDED,
        ArtifactInstallationStatus.FAILED,
        ArtifactInstallationStatus.ROLLED_BACK,
    },
    ArtifactInstallationStatus.SUCCEEDED: set(),
    ArtifactInstallationStatus.FAILED: set(),
    ArtifactInstallationStatus.ROLLED_BACK: set(),
}


class ArtifactInstallationRegistry:
    """保存安装事务，并只向目标Agent返回活动计划。"""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        initial_records: Iterable[ArtifactInstallationRecord] = (),
        record_writer: Callable[[ArtifactInstallationRecord], None] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"artifact-install-{uuid4().hex[:20]}")
        self._records = {
            item.plan.operation_id: item.model_copy(deep=True) for item in initial_records
        }
        self._record_writer = record_writer
        self._lock = Lock()

    def _save(self, record: ArtifactInstallationRecord) -> None:
        if self._record_writer is not None:
            self._record_writer(record.model_copy(deep=True))

    def submit(
        self,
        target_agent_id: str,
        artifact: ArtifactDefinition,
    ) -> ArtifactInstallationRecord:
        with self._lock:
            for record in self._records.values():
                if (
                    record.plan.target_agent_id == target_agent_id
                    and record.plan.artifact.artifact_id == artifact.artifact_id
                    and record.status not in _TERMINAL
                ):
                    raise ArtifactInstallationConflict(
                        f"制品已有活动安装事务：{record.plan.operation_id}"
                    )
            timestamp = self._now()
            record = ArtifactInstallationRecord(
                plan=ArtifactInstallationPlan(
                    operation_id=self._id_factory(),
                    target_agent_id=target_agent_id,
                    artifact=artifact,
                    created_at=timestamp,
                ),
                status=ArtifactInstallationStatus.PENDING,
                updated_at=timestamp,
            )
            self._save(record)
            self._records[record.plan.operation_id] = record
            return record.model_copy(deep=True)

    def list_records(self) -> list[ArtifactInstallationRecord]:
        with self._lock:
            records = [item.model_copy(deep=True) for item in self._records.values()]
        return sorted(records, key=lambda item: item.plan.created_at)

    def list_active_for_agent(self, agent_id: str) -> list[ArtifactInstallationRecord]:
        with self._lock:
            records = [
                item.model_copy(deep=True)
                for item in self._records.values()
                if item.plan.target_agent_id == agent_id and item.status not in _TERMINAL
            ]
        return sorted(records, key=lambda item: item.plan.created_at)

    def update(
        self,
        operation_id: str,
        update: ArtifactInstallationUpdate,
    ) -> ArtifactInstallationRecord:
        with self._lock:
            current = self._records.get(operation_id)
            if current is None:
                raise ArtifactInstallationNotFoundError(operation_id)
            if update.status not in _ALLOWED_TRANSITIONS[current.status]:
                raise InvalidArtifactInstallationTransition(
                    f"安装状态不能从{current.status.value}转换为{update.status.value}"
                )
            updated = current.model_copy(
                update={
                    "status": update.status,
                    "message": update.message,
                    "updated_at": self._now(),
                },
                deep=True,
            )
            self._save(updated)
            self._records[operation_id] = updated
            return updated.model_copy(deep=True)
