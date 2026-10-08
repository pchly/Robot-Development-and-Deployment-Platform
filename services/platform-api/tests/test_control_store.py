"""项目与工作流持久化测试。"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from robot_platform_api.control_store import (
    ControlPlaneStore,
    ProjectNotFoundError,
    UnsupportedSchemaVersionError,
    WorkflowRevisionNotFoundError,
)
from robot_platform_api.main import create_app
from robot_platform_api.models import (
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    WorkflowDefinition,
)


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)

    def now(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def workflow_payload(*, fps: int = 15) -> dict[str, object]:
    return {
        "workflow_id": "workflow.mock_detection",
        "version": "0.1.0",
        "name": "模拟相机目标检测",
        "nodes": [
            {
                "node_id": "camera",
                "component_ref": "hardware.camera.mock",
                "component_version": "0.1.0",
                "parameters": {"width": 640, "height": 480, "fps": fps},
                "position": {"x": 80, "y": 90},
            },
            {
                "node_id": "detector",
                "component_ref": "algorithm.vision.mock_detector",
                "component_version": "0.1.0",
                "position": {"x": 340, "y": 90},
            },
        ],
        "links": [
            {
                "source": {"node_id": "camera", "port": "color"},
                "target": {"node_id": "detector", "port": "image"},
            }
        ],
    }


def test_store_persists_project_and_immutable_workflow_revisions(tmp_path: Path) -> None:
    database = tmp_path / "control-plane.sqlite3"
    clock = Clock()
    store = ControlPlaneStore(
        database,
        now=clock.now,
        id_factory=lambda: "project-persistent",
    )
    project = store.create_project(ProjectCreate(name="视觉项目", description="D415验证"))
    first = store.save_workflow(
        project.project_id,
        WorkflowDefinition.model_validate(workflow_payload(fps=15)),
    )
    second = store.save_workflow(
        project.project_id,
        WorkflowDefinition.model_validate(workflow_payload(fps=30)),
    )
    updated = store.update_project(
        project.project_id,
        ProjectUpdate(status=ProjectStatus.ARCHIVED),
    )

    reopened = ControlPlaneStore(database)
    persisted = reopened.get_project(project.project_id)
    revisions = reopened.list_workflow_revisions(project.project_id)

    assert persisted.status is ProjectStatus.ARCHIVED
    assert persisted.updated_at == updated.updated_at
    assert first.revision == 1
    assert second.revision == 2
    assert [item.revision for item in revisions] == [2, 1]
    assert revisions[0].workflow.nodes[0].parameters["fps"] == 30
    assert revisions[0].workflow.nodes[1].position is not None
    assert revisions[0].workflow.nodes[1].position.x == 340
    assert (
        reopened.get_workflow_revision(project.project_id, "workflow.mock_detection", 1)
        .workflow.nodes[0]
        .parameters["fps"]
        == 15
    )


def test_store_reports_missing_records_and_accepts_empty_update() -> None:
    store = ControlPlaneStore(id_factory=lambda: "project-one")
    created = store.create_project(ProjectCreate(name="项目"))

    assert store.update_project(created.project_id, ProjectUpdate()) == created
    with pytest.raises(ProjectNotFoundError):
        store.get_project("project-missing")
    with pytest.raises(ProjectNotFoundError):
        store.update_project("project-missing", ProjectUpdate(name="新名称"))
    with pytest.raises(ProjectNotFoundError):
        store.save_workflow(
            "project-missing", WorkflowDefinition.model_validate(workflow_payload())
        )
    with pytest.raises(ProjectNotFoundError):
        store.list_workflow_revisions("project-missing")
    with pytest.raises(WorkflowRevisionNotFoundError):
        store.get_workflow_revision(created.project_id, "workflow.missing", 1)


def test_store_rejects_unknown_database_schema(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 99")
    connection.close()

    with pytest.raises(UnsupportedSchemaVersionError, match="数据库版本：99"):
        ControlPlaneStore(database).list_projects()


def test_store_migrates_v1_database_for_deployment_records(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
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
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    store = ControlPlaneStore(database)

    assert store.list_deployment_records() == []
    check = sqlite3.connect(database)
    try:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 3
        assert (
            check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("deployment_records",),
            ).fetchone()[0]
            == "deployment_records"
        )
    finally:
        check.close()


def test_deployment_control_state_survives_api_restart(tmp_path: Path) -> None:
    database = tmp_path / "persistent-deployments.sqlite3"
    registration = {
        "agent_id": "edge-dev-01",
        "name": "Development node",
        "version": "0.4.3",
        "capabilities": ["simulation"],
    }

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as first_client:
        first_client.post("/api/v1/agents/register", json=registration)
        submission = first_client.post(
            "/api/v1/deployments",
            json={"target_agent_id": "edge-dev-01", "workflow": workflow_payload()},
        )
        plan_id = submission.json()["plan"]["plan_id"]
        first_client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
        first_client.post(
            f"/api/v1/deployments/{plan_id}/status",
            json={
                "status": "running",
                "message": "Runtime运行中",
                "metrics": {"configured_fps": 15.0},
            },
        )
        first_client.post(
            f"/api/v1/deployments/{plan_id}/preview",
            content=b"\xff\xd8preview\xff\xd9",
            headers={"Content-Type": "image/jpeg"},
        )

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as recovered_client:
        records = recovered_client.get("/api/v1/deployments").json()
        active = recovered_client.get("/api/v1/agents/edge-dev-01/deployments").json()

        assert records[0]["plan"]["plan_id"] == plan_id
        assert records[0]["status"] == "running"
        assert records[0]["metrics"] == {}
        assert records[0]["preview_updated_at"] is None
        assert active[0]["plan_id"] == plan_id
        stopping = recovered_client.post(f"/api/v1/deployments/{plan_id}/stop")
        assert stopping.json()["status"] == "stopping"
        assert stopping.json()["plan"]["desired_state"] == "stopped"

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as stopped_client:
        recovered = stopped_client.get("/api/v1/deployments").json()[0]
        assert recovered["status"] == "stopping"
        assert recovered["plan"]["desired_state"] == "stopped"


def test_acceptance_report_and_event_timeline_survive_api_restart(tmp_path: Path) -> None:
    database = tmp_path / "persistent-acceptance.sqlite3"
    registration = {
        "agent_id": "edge-dev-01",
        "name": "Development node",
        "version": "0.21.0",
        "capabilities": ["simulation"],
    }

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as first_client:
        first_client.post("/api/v1/agents/register", json=registration)
        submission = first_client.post(
            "/api/v1/deployments",
            json={"target_agent_id": "edge-dev-01", "workflow": workflow_payload()},
        )
        plan_id = submission.json()["plan"]["plan_id"]
        first_client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "starting"})
        first_client.post(f"/api/v1/deployments/{plan_id}/status", json={"status": "running"})
        first_client.post(f"/api/v1/deployments/{plan_id}/stop")
        first_client.post(
            f"/api/v1/deployments/{plan_id}/status",
            json={
                "status": "stopped",
                "metrics": {
                    "configured_fps": 15.0,
                    "control_loop_actual_fps": 14.5,
                    "control_tick_p95_ms": 50.0,
                    "control_deadline_misses": 0.0,
                    "runtime_ticks": 100.0,
                },
            },
        )

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as recovered_client:
        record = recovered_client.get("/api/v1/deployments").json()[0]

    assert record["metrics"] == {}
    assert record["acceptance_reports"][0]["verdict"] == "passed"
    assert record["acceptance_reports"][0]["evidence_metrics"][
        "control_loop_actual_fps"
    ] == 14.5
    assert [event["status"] for event in record["events"]] == [
        "pending",
        "starting",
        "running",
        "stopping",
        "stopped",
    ]


def test_queued_rollback_survives_restart_and_promotes_after_stop(
    tmp_path: Path,
) -> None:
    database = tmp_path / "persistent-rollback.sqlite3"
    registration = {
        "agent_id": "edge-dev-01",
        "name": "Development node",
        "version": "0.4.3",
        "capabilities": ["simulation"],
    }

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as first_client:
        first_client.post("/api/v1/agents/register", json=registration)
        first = first_client.post(
            "/api/v1/deployments",
            json={"target_agent_id": "edge-dev-01", "workflow": workflow_payload()},
        ).json()
        first_id = first["plan"]["plan_id"]
        first_client.post(f"/api/v1/deployments/{first_id}/status", json={"status": "starting"})
        first_client.post(f"/api/v1/deployments/{first_id}/status", json={"status": "running"})
        first_client.post(f"/api/v1/deployments/{first_id}/stop")
        first_client.post(f"/api/v1/deployments/{first_id}/status", json={"status": "stopped"})

        second = first_client.post(
            "/api/v1/deployments",
            json={"target_agent_id": "edge-dev-01", "workflow": workflow_payload(fps=20)},
        ).json()
        second_id = second["plan"]["plan_id"]
        first_client.post(f"/api/v1/deployments/{second_id}/status", json={"status": "starting"})
        first_client.post(f"/api/v1/deployments/{second_id}/status", json={"status": "running"})
        rollback = first_client.post(f"/api/v1/deployments/{first_id}/rollback").json()
        rollback_id = rollback["plan"]["plan_id"]
        assert rollback["status"] == "queued"

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as recovered_client:
        records = recovered_client.get("/api/v1/deployments").json()
        recovered_rollback = next(
            item for item in records if item["plan"]["plan_id"] == rollback_id
        )
        assert recovered_rollback["status"] == "queued"
        assert recovered_rollback["plan"]["supersedes_plan_ids"] == [second_id]

        stopped = recovered_client.post(
            f"/api/v1/deployments/{second_id}/status", json={"status": "stopped"}
        )
        promoted = next(
            item
            for item in recovered_client.get("/api/v1/deployments").json()
            if item["plan"]["plan_id"] == rollback_id
        )

        assert stopped.status_code == 200
        assert promoted["status"] == "pending"


def test_artifact_installation_state_survives_api_restart(tmp_path: Path) -> None:
    database = tmp_path / "persistent-artifact-installations.sqlite3"
    registration = {
        "agent_id": "edge-dev-01",
        "name": "Development node",
        "version": "0.7.0",
        "capabilities": [
            "artifact.inventory.v1",
            "artifact.installation.v1",
            "artifact.activation-health.v1",
        ],
    }

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as first_client:
        first_client.post("/api/v1/agents/register", json=registration)
        created = first_client.post(
            "/api/v1/artifact-installations",
            json={
                "target_agent_id": "edge-dev-01",
                "artifact_id": "hardware.camera.realsense.edge-plugin",
            },
        ).json()
        operation_id = created["plan"]["operation_id"]
        first_client.post(
            f"/api/v1/artifact-installations/{operation_id}/status",
            json={"status": "downloading", "message": "下载中"},
        )

    with TestClient(create_app(control_store=ControlPlaneStore(database))) as recovered_client:
        records = recovered_client.get("/api/v1/artifact-installations").json()

    assert records[0]["plan"]["operation_id"] == operation_id
    assert records[0]["status"] == "downloading"
    assert records[0]["message"] == "下载中"


def test_project_api_supports_crud_and_workflow_revisions() -> None:
    store = ControlPlaneStore(id_factory=lambda: "project-api")
    client = TestClient(create_app(control_store=store))

    created = client.post(
        "/api/v1/projects",
        json={"name": "机器人视觉", "description": "从相机到检测"},
    )
    project_id = created.json()["project_id"]
    listed = client.get("/api/v1/projects")
    fetched = client.get(f"/api/v1/projects/{project_id}")
    updated = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "机器人视觉平台", "status": "archived"},
    )
    revision_one = client.post(
        f"/api/v1/projects/{project_id}/workflows",
        json=workflow_payload(fps=15),
    )
    revision_two = client.post(
        f"/api/v1/projects/{project_id}/workflows",
        json=workflow_payload(fps=30),
    )
    revisions = client.get(f"/api/v1/projects/{project_id}/workflows")
    fetched_revision = client.get(
        f"/api/v1/projects/{project_id}/workflows/workflow.mock_detection/revisions/1"
    )

    assert created.status_code == 201
    assert listed.json()[0]["name"] == "机器人视觉"
    assert fetched.json()["description"] == "从相机到检测"
    assert updated.json()["name"] == "机器人视觉平台"
    assert updated.json()["status"] == "archived"
    assert revision_one.status_code == 201
    assert revision_one.json()["revision"] == 1
    assert revision_two.json()["revision"] == 2
    assert [item["revision"] for item in revisions.json()] == [2, 1]
    assert fetched_revision.json()["workflow"]["nodes"][0]["parameters"]["fps"] == 15


def test_project_revision_can_be_deployed_with_traceable_provenance() -> None:
    store = ControlPlaneStore(id_factory=lambda: "project-deploy")
    client = TestClient(create_app(control_store=store))
    client.post(
        "/api/v1/agents/register",
        json={
            "agent_id": "edge-dev-01",
            "name": "Development node",
            "version": "0.4.1",
            "capabilities": ["simulation"],
        },
    )
    project = client.post(
        "/api/v1/projects",
        json={"name": "Traceable deployment"},
    ).json()
    saved = client.post(
        f"/api/v1/projects/{project['project_id']}/workflows",
        json=workflow_payload(fps=30),
    ).json()

    response = client.post(
        (
            f"/api/v1/projects/{project['project_id']}/workflows/"
            "workflow.mock_detection/revisions/1/deployments"
        ),
        json={"target_agent_id": "edge-dev-01"},
    )
    project_deployments = client.get(f"/api/v1/projects/{project['project_id']}/deployments")

    assert response.status_code == 200
    assert response.json()["report"]["compatible"] is True
    plan = response.json()["plan"]
    assert plan["project_id"] == project["project_id"]
    assert plan["workflow_revision"] == saved["revision"]
    assert plan["workflow"]["nodes"][0]["parameters"]["fps"] == 30
    assert project_deployments.status_code == 200
    assert project_deployments.json()[0]["plan"]["plan_id"] == plan["plan_id"]


def test_project_revision_deployment_reports_missing_revision() -> None:
    store = ControlPlaneStore(id_factory=lambda: "project-deploy")
    client = TestClient(create_app(control_store=store))
    project = client.post(
        "/api/v1/projects",
        json={"name": "Missing revision"},
    ).json()

    response = client.post(
        (
            f"/api/v1/projects/{project['project_id']}/workflows/"
            "workflow.missing/revisions/1/deployments"
        ),
        json={"target_agent_id": "edge-dev-01"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "payload", "detail"),
    [
        ("get", "/api/v1/projects/project-missing", None, "项目不存在"),
        ("patch", "/api/v1/projects/project-missing", {"name": "x"}, "项目不存在"),
        (
            "post",
            "/api/v1/projects/project-missing/workflows",
            workflow_payload(),
            "项目不存在",
        ),
        (
            "get",
            "/api/v1/projects/project-missing/workflows",
            None,
            "项目不存在",
        ),
        (
            "get",
            "/api/v1/projects/project-missing/workflows/workflow.missing/revisions/1",
            None,
            "工作流修订不存在",
        ),
    ],
)
def test_project_api_returns_not_found(
    method: str,
    path: str,
    payload: dict[str, object] | None,
    detail: str,
) -> None:
    client = TestClient(create_app())

    response = client.request(method, path, json=payload)

    assert response.status_code == 404
    assert response.json()["detail"] == detail
