import numpy as np
import pytest

from robot_windows_webcam_yolox_mujoco.runtime import (
    MAX_COMBINED_PREVIEW_BYTES,
    Detection,
    WindowsWebcamYoloxRuntime,
    _nms,
)


class FakeCapture:
    def __init__(
        self,
        results: list[tuple[bool, np.ndarray | None]],
        *,
        opened: bool = True,
    ) -> None:
        self.results = results
        self.opened = opened
        self.read_count = 0
        self.released = False
        self.settings: list[tuple[int, int]] = []

    def isOpened(self) -> bool:
        return self.opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        result = self.results[min(self.read_count, len(self.results) - 1)]
        self.read_count += 1
        return result

    def set(self, key: int, value: int) -> None:
        self.settings.append((key, value))

    def release(self) -> None:
        self.released = True


def test_detection_center() -> None:
    detection = Detection("cell phone", 0.9, 10.0, 20.0, 40.0, 60.0)

    assert detection.center == (30.0, 50.0)


def test_nms_keeps_best_overlapping_box() -> None:
    boxes = np.asarray([[0, 0, 100, 100], [5, 5, 95, 95], [200, 200, 240, 240]], dtype=float)
    scores = np.asarray([0.9, 0.7, 0.8], dtype=float)

    assert _nms(boxes, scores, 0.45) == [0, 2]


def test_combined_preview_is_bounded_jpeg() -> None:
    runtime = WindowsWebcamYoloxRuntime(
        camera_index=0,
        width=640,
        height=480,
        fps=15,
        confidence=0.3,
        nms_threshold=0.45,
        target_label="cell phone",
    )
    runtime._last_annotated = np.random.default_rng(7).integers(
        0,
        256,
        size=(480, 640, 3),
        dtype=np.uint8,
    )
    arm = np.zeros((360, 480, 3), dtype=np.uint8)
    ok, encoded_arm = runtime._cv2.imencode(".png", arm)
    assert ok

    preview = runtime.compose_preview(bytes(encoded_arm))

    assert preview is not None
    assert preview.startswith(b"\xff\xd8")
    assert preview.endswith(b"\xff\xd9")
    assert len(preview) <= MAX_COMBINED_PREVIEW_BYTES


def test_snapshot_preview_is_owned_and_accepts_raw_arm_rgb() -> None:
    runtime = WindowsWebcamYoloxRuntime(
        camera_index=0,
        width=640,
        height=480,
        fps=15,
        confidence=0.3,
        nms_threshold=0.45,
        target_label="cell phone",
    )
    runtime._last_annotated = np.full((480, 640, 3), 64, dtype=np.uint8)

    camera_snapshot = runtime.snapshot_preview_frame()
    assert camera_snapshot is not None
    camera_snapshot[0, 0] = 255
    assert tuple(runtime._last_annotated[0, 0]) == (64, 64, 64)

    arm_rgb = np.zeros((360, 480, 3), dtype=np.uint8)
    preview = runtime.compose_preview_rgb(camera_snapshot, arm_rgb)

    assert preview is not None
    assert preview.startswith(b"\xff\xd8")
    assert preview.endswith(b"\xff\xd9")


def test_camera_read_retries_a_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = object.__new__(WindowsWebcamYoloxRuntime)
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    capture = FakeCapture([(False, None), (True, frame)])
    runtime._capture = capture
    runtime._pending_frame = None
    monkeypatch.setattr("robot_windows_webcam_yolox_mujoco.runtime.time.sleep", lambda _delay: None)

    assert runtime._read_frame() is frame
    assert capture.read_count == 2


def test_camera_read_reports_persistent_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = object.__new__(WindowsWebcamYoloxRuntime)
    capture = FakeCapture([(False, None)])
    runtime._capture = capture
    runtime._pending_frame = None
    monkeypatch.setattr("robot_windows_webcam_yolox_mujoco.runtime.time.sleep", lambda _delay: None)

    with pytest.raises(RuntimeError, match="连续读取失败（3次）"):
        runtime._read_frame()
    assert capture.read_count == 3


def test_camera_open_falls_back_after_backend_cannot_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    media_foundation = FakeCapture([(False, None)])
    direct_show = FakeCapture([(True, frame)])

    class FakeCv2:
        CAP_MSMF = 1
        CAP_DSHOW = 2
        CAP_ANY = 0
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5

        @staticmethod
        def VideoCapture(_index: int, backend: int) -> FakeCapture:
            return {1: media_foundation, 2: direct_show}[backend]

    runtime = object.__new__(WindowsWebcamYoloxRuntime)
    runtime._cv2 = FakeCv2()
    runtime._camera_index = 0
    runtime._width = 640
    runtime._height = 480
    runtime._fps = 15
    monkeypatch.setattr("robot_windows_webcam_yolox_mujoco.runtime.time.sleep", lambda _delay: None)

    capture, first_frame, backend_name = runtime._open_camera()

    assert capture is direct_show
    assert first_frame is frame
    assert backend_name == "DirectShow"
    assert media_foundation.released is True
    assert direct_show.settings == [(3, 640), (4, 480), (5, 15)]
