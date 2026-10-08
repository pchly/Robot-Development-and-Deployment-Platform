from types import SimpleNamespace

from robot_hardware_windows_webcam.adapter import WindowsWebcamAdapter


class FakeCapture:
    def __init__(self, opened: bool = True) -> None:
        self.opened = opened
        self.released = False
        self.read_count = 0

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        self.read_count += 1
        return False, None

    def release(self) -> None:
        self.released = True

    def getBackendName(self) -> str:
        return "TEST"


def test_probe_reports_stable_runtime_selector() -> None:
    captures: list[FakeCapture] = []

    def factory(index: int) -> FakeCapture:
        capture = FakeCapture(opened=index == 0)
        captures.append(capture)
        return capture

    adapter = WindowsWebcamAdapter(
        capture_factory=factory,
        system_name="Windows",
        maximum_devices=2,
    )

    devices = adapter.probe()
    cached_devices = adapter.probe()

    assert len(devices) == 1
    assert devices[0].device_id == "windows-camera-0"
    assert devices[0].metadata["runtime_selector"] == "0"
    assert devices[0].metadata["capture_backend"] == "TEST"
    assert cached_devices == devices
    assert len(captures) == 2
    assert all(capture.read_count == 0 for capture in captures)
    assert all(capture.released for capture in captures)


def test_probe_does_not_require_a_frame_to_identify_camera() -> None:
    capture = FakeCapture(opened=True)
    adapter = WindowsWebcamAdapter(
        capture_factory=lambda _index: capture,
        system_name="Windows",
        maximum_devices=1,
    )

    devices = adapter.probe()

    assert [device.device_id for device in devices] == ["windows-camera-0"]
    assert capture.read_count == 0
    assert capture.released is True


def test_empty_probe_is_retried_for_late_camera_connection() -> None:
    attempts = 0

    def factory(_index: int) -> FakeCapture:
        nonlocal attempts
        attempts += 1
        return FakeCapture(opened=attempts > 1)

    adapter = WindowsWebcamAdapter(
        capture_factory=factory,
        system_name="Windows",
        maximum_devices=1,
    )

    assert adapter.probe() == []
    assert len(adapter.probe()) == 1
    assert attempts == 2


def test_non_windows_probe_is_empty() -> None:
    adapter = WindowsWebcamAdapter(
        capture_factory=lambda _index: SimpleNamespace(),
        system_name="Linux",
    )

    assert adapter.probe() == []
