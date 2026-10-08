"""项目与工作流设计态的SQLite持久化仓库。"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from robot_platform_api.models import (
    ArtifactInstallationRecord,
    DeploymentRecord,
    ProjectCreate,
    ProjectRecord,
    ProjectStatus,
    ProjectUpdate,
    WorkflowDefinition,
    WorkflowRevisionRecord,
)

_SCHEMA_VERSION = 3


class ProjectNotFoundError(LookupError):
    """项目不存在。"""


class WorkflowRevisionNotFoundError(LookupError):
    """工作流修订不存在。"""


class UnsupportedSchemaVersionError(RuntimeError):
    """数据库版本高于当前程序支持范围。"""


def default_control_store_path() -> Path:
    """返回生产应用的控制面数据库路径。"""

    configured = os.getenv("ROBOT_PLATFORM_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / ".data" / "control-plane.sqlite3"


class ControlPlaneStore:
    """线程安全的轻量SQLite仓库；实时Agent状态不写入此库。"""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._path = str(path)
        self._now = now or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"project-{uuid4().hex[:12]}")
        self._connection: sqlite3.Connection | None = None
        self._lock = Lock()

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            connection.executescript(
                """
                CREATE TABLE projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE workflow_revisions (
                    project_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    workflow_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, workflow_id, revision),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX workflow_revisions_project_idx
                    ON workflow_revisions(project_id, created_at);
                CREATE TABLE deployment_records (
                    plan_id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX deployment_records_updated_idx
                    ON deployment_records(updated_at);
                CREATE TABLE artifact_installation_records (
                    operation_id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX artifact_installation_records_updated_idx
                    ON artifact_installation_records(updated_at);
                PRAGMA user_version = 3;
                """
            )
            connection.commit()
        elif version == 1:
            connection.executescript(
                """
                CREATE TABLE deployment_records (
                    plan_id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX deployment_records_updated_idx
                    ON deployment_records(updated_at);
                CREATE TABLE artifact_installation_records (
                    operation_id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX artifact_installation_records_updated_idx
                    ON artifact_installation_records(updated_at);
                PRAGMA user_version = 3;
                """
            )
            connection.commit()
        elif version == 2:
            connection.executescript(
                """
                CREATE TABLE artifact_installation_records (
                    operation_id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX artifact_installation_records_updated_idx
                    ON artifact_installation_records(updated_at);
                PRAGMA user_version = 3;
                """
            )
            connection.commit()
        elif version != _SCHEMA_VERSION:
            connection.close()
            raise UnsupportedSchemaVersionError(
                f"不支持的控制面数据库版本：{version}，当前支持{_SCHEMA_VERSION}"
            )
        self._connection = connection
        return connection

    def close(self) -> None:
        """关闭当前连接；后续访问会按需重新打开。"""

        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __del__(self) -> None:  # pragma: no cover - 解释器退出时的兜底路径
        with suppress(Exception):
            self.close()

    @staticmethod
    def _project(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"],
            status=ProjectStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _revision(row: sqlite3.Row) -> WorkflowRevisionRecord:
        workflow = WorkflowDefinition.model_validate_json(row["workflow_json"])
        return WorkflowRevisionRecord(
            project_id=row["project_id"],
            workflow_id=row["workflow_id"],
            revision=row["revision"],
            workflow=workflow,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def create_project(self, project: ProjectCreate) -> ProjectRecord:
        with self._lock:
            connection = self._connect()
            project_id = self._id_factory()
            timestamp = self._now().isoformat()
            connection.execute(
                """
                INSERT INTO projects (
                    project_id, name, description, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    project.name,
                    project.description,
                    ProjectStatus.ACTIVE.value,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            assert row is not None
            return self._project(row)

    def list_projects(self) -> list[ProjectRecord]:
        with self._lock:
            rows = (
                self._connect()
                .execute("SELECT * FROM projects ORDER BY updated_at DESC, project_id")
                .fetchall()
            )
            return [self._project(row) for row in rows]

    def get_project(self, project_id: str) -> ProjectRecord:
        with self._lock:
            row = (
                self._connect()
                .execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
                .fetchone()
            )
            if row is None:
                raise ProjectNotFoundError(project_id)
            return self._project(row)

    def update_project(self, project_id: str, update: ProjectUpdate) -> ProjectRecord:
        with self._lock:
            connection = self._connect()
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFoundError(project_id)
            current = self._project(row)
            values = update.model_dump(exclude_unset=True, exclude_none=True)
            if not values:
                return current
            updated = current.model_copy(
                update={**values, "updated_at": self._now()},
            )
            connection.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, status = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (
                    updated.name,
                    updated.description,
                    updated.status.value,
                    updated.updated_at.isoformat(),
                    project_id,
                ),
            )
            connection.commit()
            return updated

    def save_workflow(
        self, project_id: str, workflow: WorkflowDefinition
    ) -> WorkflowRevisionRecord:
        with self._lock:
            connection = self._connect()
            project = connection.execute(
                "SELECT project_id FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise ProjectNotFoundError(project_id)
            row = connection.execute(
                """
                SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision
                FROM workflow_revisions
                WHERE project_id = ? AND workflow_id = ?
                """,
                (project_id, workflow.workflow_id),
            ).fetchone()
            assert row is not None
            revision = int(row["next_revision"])
            timestamp = self._now()
            connection.execute(
                """
                INSERT INTO workflow_revisions (
                    project_id, workflow_id, revision, workflow_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    workflow.workflow_id,
                    revision,
                    workflow.model_dump_json(),
                    timestamp.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE projects SET updated_at = ? WHERE project_id = ?",
                (timestamp.isoformat(), project_id),
            )
            connection.commit()
            return WorkflowRevisionRecord(
                project_id=project_id,
                workflow_id=workflow.workflow_id,
                revision=revision,
                workflow=workflow,
                created_at=timestamp,
            )

    def list_workflow_revisions(self, project_id: str) -> list[WorkflowRevisionRecord]:
        with self._lock:
            connection = self._connect()
            project = connection.execute(
                "SELECT project_id FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise ProjectNotFoundError(project_id)
            rows = connection.execute(
                """
                SELECT * FROM workflow_revisions
                WHERE project_id = ?
                ORDER BY workflow_id, revision DESC
                """,
                (project_id,),
            ).fetchall()
            return [self._revision(row) for row in rows]

    def get_workflow_revision(
        self, project_id: str, workflow_id: str, revision: int
    ) -> WorkflowRevisionRecord:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    """
                SELECT * FROM workflow_revisions
                WHERE project_id = ? AND workflow_id = ? AND revision = ?
                """,
                    (project_id, workflow_id, revision),
                )
                .fetchone()
            )
            if row is None:
                raise WorkflowRevisionNotFoundError(f"{project_id}/{workflow_id}/{revision}")
            return self._revision(row)

    def save_deployment_record(self, record: DeploymentRecord) -> None:
        """持久化部署控制状态；预览二进制及其易失元数据不进入SQLite。"""

        persistent = record.model_copy(
            update={
                "metrics": {},
                "preview_updated_at": None,
                "preview_content_type": None,
            },
            deep=True,
        )
        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                INSERT INTO deployment_records (plan_id, record_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    record_json = excluded.record_json,
                    updated_at = excluded.updated_at
                """,
                (
                    persistent.plan.plan_id,
                    persistent.model_dump_json(),
                    persistent.updated_at.isoformat(),
                ),
            )
            connection.commit()

    def list_deployment_records(self) -> list[DeploymentRecord]:
        """读取全部部署控制记录，用于Platform API启动恢复。"""

        with self._lock:
            rows = (
                self._connect()
                .execute(
                    """
                SELECT record_json FROM deployment_records
                ORDER BY updated_at, plan_id
                """
                )
                .fetchall()
            )
        return [DeploymentRecord.model_validate_json(row["record_json"]) for row in rows]

    def save_artifact_installation_record(
        self,
        record: ArtifactInstallationRecord,
    ) -> None:
        """持久化受控制品安装事务。"""

        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                INSERT INTO artifact_installation_records (
                    operation_id, record_json, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    record_json = excluded.record_json,
                    updated_at = excluded.updated_at
                """,
                (
                    record.plan.operation_id,
                    record.model_dump_json(),
                    record.updated_at.isoformat(),
                ),
            )
            connection.commit()

    def list_artifact_installation_records(self) -> list[ArtifactInstallationRecord]:
        """读取安装事务，用于Platform API重启恢复。"""

        with self._lock:
            rows = (
                self._connect()
                .execute(
                    """
                SELECT record_json FROM artifact_installation_records
                ORDER BY updated_at, operation_id
                """
                )
                .fetchall()
            )
        return [ArtifactInstallationRecord.model_validate_json(row["record_json"]) for row in rows]
