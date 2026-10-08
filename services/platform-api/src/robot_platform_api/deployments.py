"""线程安全的进程内部署注册表。"""

import hashlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from threading import Lock

from robot_platform_api.acceptance import build_acceptance_report
from robot_platform_api.models import (
    DeploymentEvent,
    DeploymentLivePreviewSession,
    DeploymentPlan,
    DeploymentPreviewMetadata,
    DeploymentRecord,
    DeploymentStatus,
    DeploymentStatusUpdate,
)


class DeploymentNotFoundError(LookupError):
    """部署计划不存在。"""


class InvalidDeploymentTransition(ValueError):
    """部署状态转换不合法。"""


class DeploymentResourceConflict(ValueError):
    """目标节点上的硬件资源已被其他活动计划占用。"""


_ALLOWED_TRANSITIONS = {
    DeploymentStatus.QUEUED: set(),
    DeploymentStatus.PENDING: {DeploymentStatus.STARTING, DeploymentStatus.FAILED},
    DeploymentStatus.STARTING: {
        DeploymentStatus.STARTING,
        DeploymentStatus.RUNNING,
        DeploymentStatus.FAILED,
    },
    DeploymentStatus.RUNNING: {
        DeploymentStatus.STARTING,
        DeploymentStatus.RUNNING,
        DeploymentStatus.STOPPING,
        DeploymentStatus.FAILED,
    },
    DeploymentStatus.STOPPING: {
        DeploymentStatus.STOPPING,
        DeploymentStatus.STOPPED,
        DeploymentStatus.FAILED,
    },
    DeploymentStatus.STOPPED: set(),
    DeploymentStatus.FAILED: {DeploymentStatus.STARTING},
}


def _hardware_resource_keys(plan: DeploymentPlan) -> set[str]:
    """提取计划占用的端侧硬件实例键。"""

    nodes = {node.node_id: node for node in plan.workflow.nodes}
    resources: set[str] = set()
    for component in plan.components:
        if component.kind != "hardware":
            continue
        node = nodes.get(component.node_id)
        instance = "default"
        if node is not None:
            serial = node.parameters.get("serial")
            if isinstance(serial, str) and serial:
                instance = serial
        resources.add(f"{component.component_ref}:{instance}")
    return resources


def _append_event(
    record: DeploymentRecord,
    *,
    event_type: str,
    status: DeploymentStatus,
    message: str,
    occurred_at: datetime,
    attempt: int | None = None,
) -> list[DeploymentEvent]:
    return [
        *record.events,
        DeploymentEvent(
            attempt=attempt or record.attempt,
            event_type=event_type,
            status=status,
            message=message,
            occurred_at=occurred_at,
        ),
    ]


class DeploymentRegistry:
    """保存计划、状态并为Agent返回其活动计划。"""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        initial_records: Iterable[DeploymentRecord] = (),
        record_writer: Callable[[DeploymentRecord], None] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._records = {
            record.plan.plan_id: record.model_copy(deep=True) for record in initial_records
        }
        self._record_writer = record_writer
        self._previews: dict[str, bytes] = {}
        self._live_preview_sessions: dict[str, tuple[datetime, int]] = {}
        self._lock = Lock()

    def _save(self, record: DeploymentRecord) -> None:
        if self._record_writer is not None:
            self._record_writer(record.model_copy(deep=True))

    def submit(self, plan: DeploymentPlan) -> DeploymentRecord:
        """幂等提交计划；已结束的相同计划允许重新启动。"""

        with self._lock:
            existing = self._records.get(plan.plan_id)
            if existing is not None:
                if existing.status in {DeploymentStatus.STOPPED, DeploymentStatus.FAILED}:
                    now = self._now()
                    attempt = existing.attempt + 1
                    restarted = DeploymentRecord(
                        plan=plan.model_copy(update={"desired_state": "running"}),
                        status=DeploymentStatus.PENDING,
                        message="",
                        metrics={},
                        attempt=attempt,
                        events=_append_event(
                            existing,
                            event_type="restarted",
                            status=DeploymentStatus.PENDING,
                            message="重新提交相同部署计划",
                            occurred_at=now,
                            attempt=attempt,
                        ),
                        acceptance_reports=existing.acceptance_reports,
                        updated_at=now,
                    )
                    self._save(restarted)
                    self._records[plan.plan_id] = restarted
                    self._previews.pop(plan.plan_id, None)
                    self._live_preview_sessions.pop(plan.plan_id, None)
                    return restarted.model_copy(deep=True)
                return existing.model_copy(deep=True)
            requested_resources = _hardware_resource_keys(plan)
            blocking_statuses = {
                DeploymentStatus.QUEUED,
                DeploymentStatus.PENDING,
                DeploymentStatus.STARTING,
                DeploymentStatus.RUNNING,
                DeploymentStatus.STOPPING,
            }
            for active in self._records.values():
                if (
                    active.plan.target_agent_id == plan.target_agent_id
                    and active.status in blocking_statuses
                    and requested_resources & _hardware_resource_keys(active.plan)
                ):
                    raise DeploymentResourceConflict(
                        f"目标节点硬件资源已被计划{active.plan.plan_id}占用"
                    )
            now = self._now()
            record = DeploymentRecord(
                plan=plan,
                status=DeploymentStatus.PENDING,
                updated_at=now,
                events=[
                    DeploymentEvent(
                        attempt=1,
                        event_type="submitted",
                        status=DeploymentStatus.PENDING,
                        message="部署计划已提交",
                        occurred_at=now,
                    )
                ],
            )
            self._save(record)
            self._records[plan.plan_id] = record
            return record.model_copy(deep=True)

    def get_record(self, plan_id: str) -> DeploymentRecord:
        """返回单条部署记录快照。"""

        with self._lock:
            record = self._records.get(plan_id)
            if record is None:
                raise DeploymentNotFoundError(plan_id)
            return record.model_copy(deep=True)

    def rollback(self, source_plan_id: str) -> DeploymentRecord:
        """从历史计划创建新部署；冲突Runtime停止前保持安全排队。"""

        with self._lock:
            source = self._records.get(source_plan_id)
            if source is None:
                raise DeploymentNotFoundError(source_plan_id)
            if source.status not in {DeploymentStatus.STOPPED, DeploymentStatus.FAILED}:
                raise InvalidDeploymentTransition("仅已停止或失败的历史部署可以作为回退版本")

            rollback_states = {
                DeploymentStatus.QUEUED,
                DeploymentStatus.PENDING,
                DeploymentStatus.STARTING,
                DeploymentStatus.RUNNING,
                DeploymentStatus.STOPPING,
            }
            for existing in self._records.values():
                if (
                    existing.plan.rollback_of_plan_id == source_plan_id
                    and existing.status in rollback_states
                ):
                    return existing.model_copy(deep=True)

            requested_resources = _hardware_resource_keys(source.plan)
            blocking_statuses = {
                DeploymentStatus.PENDING,
                DeploymentStatus.STARTING,
                DeploymentStatus.RUNNING,
                DeploymentStatus.STOPPING,
            }
            blockers = [
                record
                for record in self._records.values()
                if record.plan.target_agent_id == source.plan.target_agent_id
                and record.status in blocking_statuses
                and requested_resources & _hardware_resource_keys(record.plan)
            ]
            now = self._now()
            plan_seed = (
                f"{source_plan_id}:{source.plan.target_agent_id}:"
                f"{now.isoformat()}:{len(self._records)}"
            )
            rollback_plan_id = f"plan-{hashlib.sha256(plan_seed.encode('utf-8')).hexdigest()[:20]}"
            blocker_ids = [record.plan.plan_id for record in blockers]
            rollback_plan = source.plan.model_copy(
                update={
                    "plan_id": rollback_plan_id,
                    "rollback_of_plan_id": source_plan_id,
                    "supersedes_plan_ids": blocker_ids,
                    "desired_state": "running",
                    "created_at": now,
                },
                deep=True,
            )

            for blocker in blockers:
                if blocker.status is DeploymentStatus.STOPPING:
                    continue
                stopping = blocker.model_copy(
                    update={
                        "plan": blocker.plan.model_copy(
                            update={"desired_state": "stopped"}, deep=True
                        ),
                        "status": DeploymentStatus.STOPPING,
                        "message": f"正在停止Runtime，随后回退到计划{source_plan_id}",
                        "events": _append_event(
                            blocker,
                            event_type="stop_requested",
                            status=DeploymentStatus.STOPPING,
                            message=f"准备回退到计划{source_plan_id}",
                            occurred_at=now,
                        ),
                        "updated_at": now,
                    },
                    deep=True,
                )
                self._save(stopping)
                self._records[blocker.plan.plan_id] = stopping

            rollback_status = DeploymentStatus.QUEUED if blockers else DeploymentStatus.PENDING
            message = (
                "等待前序Runtime停止后自动启动"
                if blockers
                else f"已从历史计划{source_plan_id}创建回退部署"
            )
            rollback_record = DeploymentRecord(
                plan=rollback_plan,
                status=rollback_status,
                message=message,
                updated_at=now,
                events=[
                    DeploymentEvent(
                        attempt=1,
                        event_type="rollback_created",
                        status=rollback_status,
                        message=message,
                        occurred_at=now,
                    )
                ],
            )
            self._save(rollback_record)
            self._records[rollback_plan_id] = rollback_record
            return rollback_record.model_copy(deep=True)

    def _promote_queued_rollbacks(self) -> None:
        """在所有被替换计划确认停止后，把回退计划交给Agent。"""

        for plan_id, queued in list(self._records.items()):
            if queued.status is not DeploymentStatus.QUEUED:
                continue
            blockers = [
                self._records.get(blocker_id) for blocker_id in queued.plan.supersedes_plan_ids
            ]
            if blockers and all(
                blocker is not None and blocker.status is DeploymentStatus.STOPPED
                for blocker in blockers
            ):
                pending = queued.model_copy(
                    update={
                        "status": DeploymentStatus.PENDING,
                        "message": "前序Runtime已停止，等待Edge Agent启动回退版本",
                        "events": _append_event(
                            queued,
                            event_type="status_changed",
                            status=DeploymentStatus.PENDING,
                            message="前序Runtime已停止，回退版本进入待启动状态",
                            occurred_at=self._now(),
                        ),
                        "updated_at": self._now(),
                    },
                    deep=True,
                )
                self._save(pending)
                self._records[plan_id] = pending
            elif any(
                blocker is not None and blocker.status is DeploymentStatus.FAILED
                for blocker in blockers
            ):
                blocked = queued.model_copy(
                    update={
                        "message": "前序Runtime停止失败，回退版本未启动",
                        "updated_at": self._now(),
                    },
                    deep=True,
                )
                self._save(blocked)
                self._records[plan_id] = blocked

    def update_preview(
        self, plan_id: str, image: bytes, content_type: str
    ) -> DeploymentPreviewMetadata:
        """保存Agent上传的最近一帧，并返回下一次采样策略。"""

        with self._lock:
            existing = self._records.get(plan_id)
            if existing is None:
                raise DeploymentNotFoundError(plan_id)
            if existing.status not in {DeploymentStatus.STARTING, DeploymentStatus.RUNNING}:
                raise InvalidDeploymentTransition(f"部署状态{existing.status.value}不接受预览上传")
            if content_type not in {"image/jpeg", "image/png"}:
                raise ValueError("预览只允许image/jpeg或image/png")
            updated_at = self._now()
            self._previews[plan_id] = bytes(image)
            self._records[plan_id] = existing.model_copy(
                update={
                    "preview_updated_at": updated_at,
                    "preview_content_type": content_type,
                },
                deep=True,
            )
            live_session = self._live_preview_policy_locked(plan_id, updated_at)
            return DeploymentPreviewMetadata(
                plan_id=plan_id,
                content_type=content_type,
                size_bytes=len(image),
                updated_at=updated_at,
                live_session_active=live_session.active,
                requested_fps=live_session.requested_fps,
            )

    def _live_preview_policy_locked(
        self, plan_id: str, now: datetime
    ) -> DeploymentLivePreviewSession:
        session = self._live_preview_sessions.get(plan_id)
        if session is not None:
            expires_at, requested_fps = session
            if expires_at > now:
                return DeploymentLivePreviewSession(
                    plan_id=plan_id,
                    active=True,
                    requested_fps=requested_fps,
                    expires_at=expires_at,
                )
            self._live_preview_sessions.pop(plan_id, None)
        return DeploymentLivePreviewSession(
            plan_id=plan_id,
            active=False,
            requested_fps=5,
        )

    def start_live_preview(
        self,
        plan_id: str,
        *,
        requested_fps: int = 10,
        ttl_seconds: int = 15,
    ) -> DeploymentLivePreviewSession:
        """开启或续期一个有界的实时监控会话。"""

        if not 1 <= requested_fps <= 15:
            raise ValueError("实时监控帧率必须位于[1, 15]")
        if not 5 <= ttl_seconds <= 60:
            raise ValueError("实时监控会话TTL必须位于[5, 60]秒")
        with self._lock:
            record = self._records.get(plan_id)
            if record is None:
                raise DeploymentNotFoundError(plan_id)
            if record.status is not DeploymentStatus.RUNNING:
                raise InvalidDeploymentTransition("只有运行中的部署可以开启实时监控")
            expires_at = self._now() + timedelta(seconds=ttl_seconds)
            self._live_preview_sessions[plan_id] = (expires_at, requested_fps)
            return DeploymentLivePreviewSession(
                plan_id=plan_id,
                active=True,
                requested_fps=requested_fps,
                expires_at=expires_at,
            )

    def stop_live_preview(self, plan_id: str) -> DeploymentLivePreviewSession:
        """关闭实时监控并让Agent恢复默认预览帧率。"""

        with self._lock:
            if plan_id not in self._records:
                raise DeploymentNotFoundError(plan_id)
            self._live_preview_sessions.pop(plan_id, None)
            return DeploymentLivePreviewSession(
                plan_id=plan_id,
                active=False,
                requested_fps=5,
            )

    def get_preview(self, plan_id: str) -> tuple[bytes, str]:
        """返回最近一张预览及其媒体类型。"""

        with self._lock:
            record = self._records.get(plan_id)
            image = self._previews.get(plan_id)
            if record is None or image is None or record.preview_content_type is None:
                raise DeploymentNotFoundError(plan_id)
            return bytes(image), record.preview_content_type

    def list_records(self) -> list[DeploymentRecord]:
        """返回全部部署记录快照。"""

        with self._lock:
            records = [record.model_copy(deep=True) for record in self._records.values()]
        return sorted(records, key=lambda item: item.plan.created_at)

    def list_for_project(self, project_id: str) -> list[DeploymentRecord]:
        """返回来源于指定项目修订的部署记录快照。"""

        with self._lock:
            records = [
                record.model_copy(deep=True)
                for record in self._records.values()
                if record.plan.project_id == project_id
            ]
        return sorted(records, key=lambda item: item.plan.created_at)

    def list_active_for_agent(self, agent_id: str) -> list[DeploymentPlan]:
        """返回目标Agent应确保存在的活动计划。"""

        active = {
            DeploymentStatus.PENDING,
            DeploymentStatus.STARTING,
            DeploymentStatus.RUNNING,
            DeploymentStatus.STOPPING,
        }
        with self._lock:
            plans = [
                record.plan.model_copy(deep=True)
                for record in self._records.values()
                if record.plan.target_agent_id == agent_id and record.status in active
            ]
        return sorted(plans, key=lambda item: item.created_at)

    def request_stop(self, plan_id: str) -> DeploymentRecord:
        """把运行目标改为停止，等待目标Agent完成容器清理。"""

        with self._lock:
            existing = self._records.get(plan_id)
            if existing is None:
                raise DeploymentNotFoundError(plan_id)
            if existing.status is DeploymentStatus.STOPPED:
                return existing.model_copy(deep=True)
            if existing.status is DeploymentStatus.QUEUED:
                now = self._now()
                cancelled = existing.model_copy(
                    update={
                        "plan": existing.plan.model_copy(
                            update={"desired_state": "stopped"}, deep=True
                        ),
                        "status": DeploymentStatus.STOPPED,
                        "message": "排队中的回退部署已取消",
                        "events": _append_event(
                            existing,
                            event_type="stop_requested",
                            status=DeploymentStatus.STOPPED,
                            message="排队中的回退部署已取消",
                            occurred_at=now,
                        ),
                        "updated_at": now,
                    },
                    deep=True,
                )
                report = build_acceptance_report(cancelled, generated_at=now)
                cancelled = cancelled.model_copy(
                    update={"acceptance_reports": [*cancelled.acceptance_reports, report]},
                    deep=True,
                )
                self._save(cancelled)
                self._records[plan_id] = cancelled
                return cancelled.model_copy(deep=True)
            if existing.status not in {
                DeploymentStatus.PENDING,
                DeploymentStatus.STARTING,
                DeploymentStatus.RUNNING,
                DeploymentStatus.STOPPING,
            }:
                raise InvalidDeploymentTransition(
                    f"部署状态{existing.status.value}不能接收停止指令"
                )
            now = self._now()
            record = existing.model_copy(
                update={
                    "plan": existing.plan.model_copy(update={"desired_state": "stopped"}),
                    "status": DeploymentStatus.STOPPING,
                    "message": "等待Edge Agent停止Runtime",
                    "events": _append_event(
                        existing,
                        event_type="stop_requested",
                        status=DeploymentStatus.STOPPING,
                        message="用户请求停止Runtime",
                        occurred_at=now,
                    ),
                    "updated_at": now,
                },
                deep=True,
            )
            self._save(record)
            self._records[plan_id] = record
            self._live_preview_sessions.pop(plan_id, None)
            return record.model_copy(deep=True)

    def update(self, plan_id: str, update: DeploymentStatusUpdate) -> DeploymentRecord:
        """校验状态转换并保存Agent回报。"""

        with self._lock:
            existing = self._records.get(plan_id)
            if existing is None:
                raise DeploymentNotFoundError(plan_id)
            if update.status not in _ALLOWED_TRANSITIONS[existing.status]:
                raise InvalidDeploymentTransition(
                    f"部署状态不能从{existing.status.value}转换为{update.status.value}"
                )
            now = self._now()
            restarting = (
                existing.status is DeploymentStatus.FAILED
                and update.status is DeploymentStatus.STARTING
            )
            attempt = existing.attempt + 1 if restarting else existing.attempt
            event_type = "restarted" if restarting else "status_changed"
            events = existing.events
            if update.status is not existing.status:
                events = _append_event(
                    existing,
                    event_type=event_type,
                    status=update.status,
                    message=update.message,
                    occurred_at=now,
                    attempt=attempt,
                )
            record = existing.model_copy(
                update={
                    "status": update.status,
                    "message": update.message,
                    # Agent的停止/失败确认通常不再携带采样指标；保留最后一份
                    # 非空运行指标，避免终态历史记录丢失验收证据。
                    "metrics": update.metrics or ({} if restarting else existing.metrics),
                    "attempt": attempt,
                    "events": events,
                    "updated_at": now,
                },
                deep=True,
            )
            if (
                record.status in {DeploymentStatus.STOPPED, DeploymentStatus.FAILED}
                and record.status is not existing.status
            ):
                report = build_acceptance_report(record, generated_at=now)
                record = record.model_copy(
                    update={"acceptance_reports": [*record.acceptance_reports, report]},
                    deep=True,
                )
            if record.status is not existing.status:
                self._save(record)
            self._records[plan_id] = record
            if record.status in {DeploymentStatus.STOPPED, DeploymentStatus.FAILED}:
                self._live_preview_sessions.pop(plan_id, None)
                self._promote_queued_rollbacks()
            return record.model_copy(deep=True)
