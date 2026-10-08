"""Edge Agent 命令行入口。"""

import argparse
import logging
import os
import socket
from pathlib import Path
from typing import Any

from robot_edge_agent.adapters import (
    AdapterPluginRegistry,
    DuplicateAdapterIdError,
    load_adapter_plugins,
)
from robot_edge_agent.artifacts import collect_installed_artifacts
from robot_edge_agent.client import PlatformClient
from robot_edge_agent.deployment_runtime import (
    EdgeDeploymentManager,
    create_workflow_runtime,
)
from robot_edge_agent.devices import discover_edge_devices
from robot_edge_agent.plugin_installation import (
    ArtifactInstallationManager,
    active_plugin_paths,
)
from robot_edge_agent.runtime import build_registration, run_agent

LOGGER = logging.getLogger(__name__)


def plugin_health_check(
    adapter_registry: AdapterPluginRegistry,
    plan: dict[str, Any],
) -> tuple[bool, str]:
    """确认新进程已经加载目标适配器，并完成一次只读设备发现。"""

    artifact = plan.get("artifact", {})
    adapter_id = str(artifact.get("source_ref", ""))
    adapter = next(
        (item for item in adapter_registry.adapters if item.adapter_id == adapter_id),
        None,
    )
    if adapter is None:
        failures = "；".join(item.message for item in adapter_registry.failures)
        suffix = f"：{failures}" if failures else ""
        return False, f"新进程未加载目标硬件适配器{adapter_id}{suffix}"
    try:
        devices = adapter.probe()
    except Exception as error:
        return False, f"适配器已加载但设备发现失败：{error}"
    return True, f"插件已由新进程加载，设备发现完成（发现{len(devices)}个设备）"


def default_plugin_paths() -> list[Path]:
    """从环境变量读取显式受管插件目录。"""

    configured = os.getenv("ROBOT_EDGE_PLUGIN_PATH", "")
    roots = [Path(item) for item in configured.split(os.pathsep) if item]
    return [path for root in roots for path in active_plugin_paths(root)]


def default_data_root() -> Path:
    """返回当前操作系统上的Agent受管数据根目录。"""

    configured = os.getenv("ROBOT_EDGE_DATA_ROOT")
    if configured:
        return Path(configured)
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "RobotPlatform" / "edge-agent"
    return Path("/data/robot-platform/edge-agent")


def parse_args() -> argparse.Namespace:
    """解析 Edge Agent 命令行参数。"""

    parser = argparse.ArgumentParser(description="机器人开发平台 Edge Agent")
    parser.add_argument("--server-url", default="http://localhost:8765")
    parser.add_argument("--agent-id", default=socket.gethostname().lower())
    parser.add_argument("--name", default="本地边缘节点")
    parser.add_argument("--heartbeat-interval", type=float, default=5.0)
    parser.add_argument("--reconnect-initial", type=float, default=1.0)
    parser.add_argument("--reconnect-max", type=float, default=30.0)
    parser.add_argument(
        "--capability",
        action="append",
        default=["system.telemetry", "device.discovery.usb"],
    )
    parser.add_argument(
        "--plugin-path",
        action="append",
        type=Path,
        default=default_plugin_paths(),
        help="仅从指定受管目录发现硬件适配器插件，可重复提供",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=default_data_root(),
        help="插件、Runtime Package和缓存的受管根目录",
    )
    parser.add_argument(
        "--self-restart",
        action="store_true",
        help="安装制品后在当前进程内重建注册信息，适用于开发机",
    )
    parser.add_argument("--once", action="store_true", help="发送一次心跳后退出")
    return parser.parse_args()


def main() -> None:
    """启动 Edge Agent。"""

    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    client = PlatformClient(args.server_url)
    try:
        while True:
            plugin_root = args.data_root / "plugins"
            runtime_package_root = args.data_root / "runtime-packages"
            calibration_root = args.data_root / "calibrations"
            cache_root = args.data_root / "cache"
            plugin_paths = [
                *args.plugin_path,
                *active_plugin_paths(plugin_root),
            ]
            plugin_paths = list(dict.fromkeys(path.resolve() for path in plugin_paths))
            try:
                adapter_registry = load_adapter_plugins(plugin_paths=plugin_paths)
            except DuplicateAdapterIdError as error:
                raise SystemExit(f"硬件插件配置冲突：{error}") from error
            LOGGER.info(
                "硬件插件加载完成：loaded=%d failed=%d",
                len(adapter_registry.adapters),
                len(adapter_registry.failures),
            )

            def artifact_inventory(
                current_cache_root: Path = cache_root,
                current_plugin_root: Path = plugin_root,
                current_runtime_package_root: Path = runtime_package_root,
                current_calibration_root: Path = calibration_root,
            ) -> list[dict[str, Any]]:
                return collect_installed_artifacts(
                    cache_roots=(current_cache_root,),
                    plugin_roots=(current_plugin_root,),
                    runtime_package_roots=(current_runtime_package_root,),
                    calibration_roots=(current_calibration_root,),
                )

            registration = build_registration(
                agent_id=args.agent_id,
                name=args.name,
                capabilities=[
                    *args.capability,
                    *adapter_registry.capabilities,
                    "artifact.inventory.v1",
                    "artifact.installation.v1",
                    "artifact.activation-health.v1",
                    "artifact.container-pull.v1",
                    "artifact.container-import.v1",
                    "artifact.file-install.v1",
                    "artifact.runtime-package-install.v1",
                    "artifact.calibration-profile-install.v1",
                    "spatial.calibration.v1",
                    "simulation",
                    "actuator.safety.v1",
                    "actuator.safety-gate.v1",
                ],
                installed_artifacts=artifact_inventory(),
            )
            restart_required = run_agent(
                client,
                registration,
                heartbeat_interval_s=args.heartbeat_interval,
                reconnect_initial_s=args.reconnect_initial,
                reconnect_max_s=args.reconnect_max,
                discover_devices=lambda registry=adapter_registry: discover_edge_devices(registry),
                deployment_manager=EdgeDeploymentManager(
                    runtime_factory=lambda plan: create_workflow_runtime(
                        plan,
                        artifact_inventory=artifact_inventory,
                        runtime_package_root=args.data_root / "runtime-packages",
                        calibration_root=args.data_root / "calibrations",
                    )
                ),
                artifact_manager=ArtifactInstallationManager(
                    plugin_root,
                    runtime_package_root=runtime_package_root,
                    calibration_root=calibration_root,
                    managed_file_roots=(cache_root / "tensorrt",),
                    health_check=lambda plan, registry=adapter_registry: plugin_health_check(
                        registry, plan
                    ),
                ),
                once=args.once,
            )
            if not restart_required:
                break
            if not args.self_restart:
                raise SystemExit(75)
            LOGGER.info("制品库存已变化，正在当前进程内重建Agent注册信息")
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Edge Agent 已停止")
    except OSError as error:
        raise SystemExit(f"无法连接 Platform API：{error}") from error


if __name__ == "__main__":
    main()
