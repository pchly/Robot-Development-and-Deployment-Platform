"""Windows webcam YOLOX frontend for a managed MuJoCo workflow."""

__version__ = "0.2.0"

from robot_windows_webcam_yolox_mujoco.runtime import (
    Detection,
    FrameResult,
    WindowsWebcamYoloxRuntime,
)

__all__ = ["Detection", "FrameResult", "WindowsWebcamYoloxRuntime", "__version__"]
