"""可重连的RealSense连续采集Runtime。"""

from __future__ import annotations

import argparse
import importlib
import json
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RuntimeState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class StreamConfig:
    sdk_serial: str = ""
    width: int = 640
    height: int = 480
    fps: int = 30
    frame_timeout_ms: int = 2000
    reconnect_interval_s: float = 2.0
    report_interval_s: float = 5.0


def _json_emitter(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


class RealSenseCameraRuntime:
    """保持RGB/Depth采集，并在设备断开后重新枚举和开流。"""

    def __init__(
        self,
        sdk: Any,
        config: StreamConfig,
        *,
        emit: Callable[[dict[str, object]], None] = _json_emitter,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sdk = sdk
        self._config = config
        self._emit = emit
        self._monotonic = monotonic
        self._sleep = sleep
        self._target_serial = config.sdk_serial
        self._stop_requested = False
        self._state: RuntimeState | None = None
        self._frames_received = 0
        self._recovery_count = 0
        self._started_at = 0.0

    def request_stop(self) -> None:
        self._stop_requested = True

    def _event(self, state: RuntimeState, **fields: object) -> None:
        self._state = state
        self._emit(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": state.value,
                "sdk_serial": self._target_serial or None,
                "frames_received": self._frames_received,
                "recovery_count": self._recovery_count,
                **fields,
            }
        )

    def _find_target_serial(self) -> str:
        serials: list[str] = []
        serial_info = self._sdk.camera_info.serial_number
        for device in self._sdk.context().query_devices():
            try:
                if hasattr(device, "supports") and not device.supports(serial_info):
                    continue
                serial = str(device.get_info(serial_info)).strip()
            except RuntimeError:
                continue
            if serial:
                serials.append(serial)

        if self._target_serial:
            if self._target_serial not in serials:
                raise RuntimeError(f"目标RealSense不可用：{self._target_serial}")
            return self._target_serial
        if not serials:
            raise RuntimeError("没有发现RealSense设备")

        self._target_serial = serials[0]
        return self._target_serial

    def _start_pipeline(self, serial: str) -> Any:
        pipeline = self._sdk.pipeline()
        pipeline_config = self._sdk.config()
        pipeline_config.enable_device(serial)
        pipeline_config.enable_stream(
            self._sdk.stream.color,
            self._config.width,
            self._config.height,
            self._sdk.format.rgb8,
            self._config.fps,
        )
        pipeline_config.enable_stream(
            self._sdk.stream.depth,
            self._config.width,
            self._config.height,
            self._sdk.format.z16,
            self._config.fps,
        )
        pipeline.start(pipeline_config)
        return pipeline

    @staticmethod
    def _stop_pipeline(pipeline: Any | None) -> None:
        if pipeline is None:
            return
        try:
            pipeline.stop()
        except RuntimeError:
            pass

    def _expired(self, duration_s: float) -> bool:
        return duration_s > 0 and self._monotonic() - self._started_at >= duration_s

    def run(
        self,
        *,
        duration_s: float = 0,
        exit_after_recoveries: int = 0,
        post_recovery_frames: int = 60,
    ) -> dict[str, float]:
        """运行采集循环；零持续时间表示直到收到停止信号。"""

        self._started_at = self._monotonic()
        had_degradation = False
        self._event(RuntimeState.STARTING)
        try:
            while not self._stop_requested and not self._expired(duration_s):
                pipeline: Any | None = None
                recovered_at_frame: int | None = None
                try:
                    serial = self._find_target_serial()
                    pipeline = self._start_pipeline(serial)
                    if had_degradation:
                        self._recovery_count += 1
                        recovered_at_frame = self._frames_received
                        had_degradation = False
                    self._event(
                        RuntimeState.RUNNING,
                        configured_fps=self._config.fps,
                        recovered=recovered_at_frame is not None,
                    )
                    report_started = self._monotonic()
                    report_frames = self._frames_received

                    while not self._stop_requested and not self._expired(duration_s):
                        frames = pipeline.wait_for_frames(self._config.frame_timeout_ms)
                        if not frames.get_color_frame() or not frames.get_depth_frame():
                            continue
                        self._frames_received += 1

                        now = self._monotonic()
                        report_elapsed = now - report_started
                        if report_elapsed >= self._config.report_interval_s:
                            window_frames = self._frames_received - report_frames
                            self._event(
                                RuntimeState.RUNNING,
                                configured_fps=self._config.fps,
                                observed_fps=(window_frames / report_elapsed),
                            )
                            report_started = now
                            report_frames = self._frames_received

                        if (
                            exit_after_recoveries > 0
                            and self._recovery_count >= exit_after_recoveries
                            and recovered_at_frame is not None
                            and self._frames_received - recovered_at_frame
                            >= post_recovery_frames
                        ):
                            self._stop_requested = True
                            break
                except RuntimeError as error:
                    had_degradation = True
                    self._event(RuntimeState.DEGRADED, error=str(error))
                finally:
                    self._stop_pipeline(pipeline)

                if not self._stop_requested and not self._expired(duration_s):
                    self._event(
                        RuntimeState.RECONNECTING,
                        retry_after_s=self._config.reconnect_interval_s,
                    )
                    self._sleep(self._config.reconnect_interval_s)
        finally:
            elapsed_s = self._monotonic() - self._started_at
            self._event(RuntimeState.STOPPED, elapsed_s=elapsed_s)

        return {
            "elapsed_s": self._monotonic() - self._started_at,
            "frames_received": float(self._frames_received),
            "recovery_count": float(self._recovery_count),
        }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于0")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("不得小于0")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="")
    parser.add_argument("--width", type=_positive_int, default=640)
    parser.add_argument("--height", type=_positive_int, default=480)
    parser.add_argument("--fps", type=_positive_int, default=30)
    parser.add_argument("--frame-timeout-ms", type=_positive_int, default=2000)
    parser.add_argument("--reconnect-interval-seconds", type=float, default=2.0)
    parser.add_argument("--report-interval-seconds", type=float, default=5.0)
    parser.add_argument("--duration-seconds", type=_non_negative_float, default=0.0)
    parser.add_argument("--exit-after-recoveries", type=int, default=0)
    parser.add_argument("--post-recovery-frames", type=_positive_int, default=60)
    arguments = parser.parse_args()

    if arguments.reconnect_interval_seconds <= 0:
        parser.error("--reconnect-interval-seconds必须大于0")
    if arguments.report_interval_seconds <= 0:
        parser.error("--report-interval-seconds必须大于0")
    if arguments.exit_after_recoveries < 0:
        parser.error("--exit-after-recoveries不得小于0")

    sdk = importlib.import_module("pyrealsense2")
    runtime = RealSenseCameraRuntime(
        sdk,
        StreamConfig(
            sdk_serial=arguments.serial,
            width=arguments.width,
            height=arguments.height,
            fps=arguments.fps,
            frame_timeout_ms=arguments.frame_timeout_ms,
            reconnect_interval_s=arguments.reconnect_interval_seconds,
            report_interval_s=arguments.report_interval_seconds,
        ),
    )

    signal.signal(signal.SIGINT, lambda *_: runtime.request_stop())
    signal.signal(signal.SIGTERM, lambda *_: runtime.request_stop())
    runtime.run(
        duration_s=arguments.duration_seconds,
        exit_after_recoveries=arguments.exit_after_recoveries,
        post_recovery_frames=arguments.post_recovery_frames,
    )


if __name__ == "__main__":
    main()
