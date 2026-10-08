"""RealSense连续采集Runtime状态机测试。"""

from camera_runtime import RealSenseCameraRuntime, StreamConfig


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.1
        return self.value


class FakeDevice:
    def supports(self, _key: object) -> bool:
        return True

    def get_info(self, _key: object) -> str:
        return "sdk-001"


class FakeFrames:
    def get_color_frame(self) -> object:
        return object()

    def get_depth_frame(self) -> object:
        return object()


class FakePipeline:
    def __init__(self, *, fail_after: int | None) -> None:
        self.fail_after = fail_after
        self.calls = 0
        self.stopped = False

    def start(self, _config: object) -> None:
        return None

    def wait_for_frames(self, _timeout_ms: int) -> FakeFrames:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("device disconnected")
        return FakeFrames()

    def stop(self) -> None:
        self.stopped = True


class FakePipelineConfig:
    def enable_device(self, _serial: str) -> None:
        return None

    def enable_stream(self, *_arguments: object) -> None:
        return None


class FakeSDK:
    camera_info = type("CameraInfo", (), {"serial_number": "serial_number"})()
    stream = type("Stream", (), {"color": "color", "depth": "depth"})()
    format = type("Format", (), {"rgb8": "rgb8", "z16": "z16"})()

    def __init__(self) -> None:
        self.pipelines = [FakePipeline(fail_after=2), FakePipeline(fail_after=None)]

    def context(self) -> object:
        return type("Context", (), {"query_devices": lambda _self: [FakeDevice()]})()

    def pipeline(self) -> FakePipeline:
        return self.pipelines.pop(0)

    def config(self) -> FakePipelineConfig:
        return FakePipelineConfig()


def test_runtime_degrades_and_recovers_after_disconnect() -> None:
    events: list[dict[str, object]] = []
    sdk = FakeSDK()
    runtime = RealSenseCameraRuntime(
        sdk,
        StreamConfig(report_interval_s=100, reconnect_interval_s=0.01),
        emit=events.append,
        monotonic=FakeClock(),
        sleep=lambda _seconds: None,
    )

    result = runtime.run(exit_after_recoveries=1, post_recovery_frames=3)

    states = [event["state"] for event in events]
    assert states == [
        "starting",
        "running",
        "degraded",
        "reconnecting",
        "running",
        "stopped",
    ]
    assert events[4]["recovered"] is True
    assert result["frames_received"] == 5.0
    assert result["recovery_count"] == 1.0
    assert sdk.pipelines == []
