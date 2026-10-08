from datetime import UTC, datetime

from robot_platform_api.artifact_resolution import resolve_artifact
from robot_platform_api.models import AgentRecord, ArtifactDefinition


def agent(
    *,
    operating_system: str = "Linux",
    machine: str = "aarch64",
    capabilities: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> AgentRecord:
    timestamp = datetime(2026, 10, 5, tzinfo=UTC)
    return AgentRecord(
        agent_id="edge-01",
        name="Edge",
        version="0.13.0",
        capabilities=capabilities or [],
        metadata={
            "operating_system": operating_system,
            "machine": machine,
            **(metadata or {}),
        },
        status="online",
        registered_at=timestamp,
        last_seen_at=timestamp,
        heartbeat_sequence=1,
    )


def multi_platform_artifact() -> ArtifactDefinition:
    return ArtifactDefinition(
        artifact_id="simulation.mujoco.runtime",
        name="MuJoCo Runtime",
        kind="runtime_package",
        scope="edge_runtime",
        version="0.1.0",
        locator="robot-mujoco-arm-runtime==0.1.0",
        source_ref="simulation.mujoco.arm",
        variants=[
            {
                "variant_id": "windows-amd64",
                "locator": "robot-mujoco-arm-runtime==0.1.0+win_amd64",
                "compatibility": {
                    "operating_system": "Windows",
                    "architecture": "amd64",
                    "required_capabilities": ["simulation.mujoco"],
                },
            },
            {
                "variant_id": "linux-arm64",
                "locator": "robot-mujoco-arm-runtime==0.1.0+linux_aarch64",
                "compatibility": {
                    "operating_system": "Linux",
                    "architecture": "arm64",
                    "required_capabilities": ["simulation.mujoco"],
                },
            },
            {
                "variant_id": "jetson-jp6",
                "locator": "robot-platform/mujoco-arm:0.1.0-jp6",
                "compatibility": {
                    "operating_system": "Linux",
                    "architecture": "aarch64",
                    "required_capabilities": ["simulation.mujoco"],
                    "metadata": {"jetpack_version": "6.2.1"},
                },
                "priority": 10,
            },
        ],
    )


def test_resolver_selects_target_platform_variant_and_normalizes_architecture() -> None:
    artifact = multi_platform_artifact()

    windows = resolve_artifact(
        artifact,
        agent(
            operating_system="Windows",
            machine="AMD64",
            capabilities=["simulation.mujoco"],
        ),
    )
    jetson = resolve_artifact(
        artifact,
        agent(capabilities=["simulation.mujoco"], metadata={"jetpack_version": "6.2.1"}),
    )

    assert windows.compatible is True
    assert windows.selected_variant_id == "windows-amd64"
    assert windows.artifact is not None
    assert windows.artifact.locator.endswith("+win_amd64")
    assert jetson.selected_variant_id == "jetson-jp6"
    assert jetson.artifact is not None
    assert jetson.artifact.selected_variant_id == "jetson-jp6"


def test_resolver_reports_every_rejected_variant_reason() -> None:
    resolution = resolve_artifact(
        multi_platform_artifact(),
        agent(operating_system="Linux", machine="riscv64"),
    )

    assert resolution.compatible is False
    assert resolution.artifact is None
    assert {item.variant_id for item in resolution.rejected_variants} == {
        "windows-amd64",
        "linux-arm64",
        "jetson-jp6",
    }
    assert any(
        "缺少节点能力" in reason for item in resolution.rejected_variants for reason in item.reasons
    )


def test_legacy_single_distribution_remains_compatible() -> None:
    artifact = ArtifactDefinition(
        artifact_id="runtime.legacy.image",
        name="Legacy",
        kind="container_image",
        scope="edge_runtime",
        version="1.0.0",
        locator="robot/legacy:1.0.0",
        architecture="arm64",
        source_ref="runtime.legacy",
    )

    resolution = resolve_artifact(artifact, agent())

    assert resolution.compatible is True
    assert resolution.selected_variant_id is None
    assert resolution.artifact == artifact
