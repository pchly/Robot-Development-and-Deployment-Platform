from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_systemd_allows_only_managed_agent_write_paths() -> None:
    unit = (PROJECT_ROOT / "deploy/jetson/robot-edge-agent.service.in").read_text(encoding="utf-8")

    assert "ProtectSystem=strict" in unit
    assert (
        "ReadWritePaths=/data/robot-platform/runs "
        "/data/robot-platform/edge-agent/plugins "
        "/data/robot-platform/edge-agent/staging "
        "/data/robot-platform/edge-agent/calibrations "
        "/data/robot-platform/cache/tensorrt"
    ) in unit


def test_installer_creates_staging_directory_for_service_user() -> None:
    installer = (PROJECT_ROOT / "deploy/jetson/install.sh").read_text(encoding="utf-8")

    assert 'STAGING_ROOT="${INSTALL_ROOT}/staging"' in installer
    assert 'install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$STAGING_ROOT"' in installer
    assert 'TENSORRT_CACHE_ROOT="/data/robot-platform/cache/tensorrt"' in installer
    assert (
        'install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 '
        '"$TENSORRT_CACHE_ROOT"' in installer
    )
