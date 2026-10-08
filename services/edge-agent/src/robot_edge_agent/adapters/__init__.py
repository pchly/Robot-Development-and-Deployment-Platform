"""硬件适配器SDK。"""

from robot_edge_agent.adapters.lifecycle import AdapterController, AdapterState
from robot_edge_agent.adapters.models import DeviceDescriptor, HealthReport
from robot_edge_agent.adapters.plugins import (
    AdapterPluginRegistry,
    DuplicateAdapterIdError,
    PluginLoadFailure,
    load_adapter_plugins,
)
from robot_edge_agent.adapters.protocol import HardwareAdapter
from robot_edge_agent.adapters.realsense import RealSenseAdapter, RealSenseDependencyError

__all__ = [
    "AdapterController",
    "AdapterPluginRegistry",
    "AdapterState",
    "DeviceDescriptor",
    "HardwareAdapter",
    "HealthReport",
    "DuplicateAdapterIdError",
    "PluginLoadFailure",
    "RealSenseAdapter",
    "RealSenseDependencyError",
    "load_adapter_plugins",
]
