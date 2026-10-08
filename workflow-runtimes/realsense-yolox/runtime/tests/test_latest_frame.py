from __future__ import annotations

import threading
import time

import pytest
from latest_frame import CapturedFrame, LatestFrameBuffer


def make_frame(sequence: int) -> CapturedFrame:
    return CapturedFrame(sequence=sequence, captured_at=time.monotonic(), image=object())


def test_new_frame_overwrites_unconsumed_frame() -> None:
    buffer = LatestFrameBuffer()

    buffer.publish(make_frame(1))
    buffer.publish(make_frame(2))

    frame = buffer.take(timeout=0.01)
    assert frame is not None
    assert frame.sequence == 2
    assert buffer.dropped == 1


def test_take_waits_for_producer() -> None:
    buffer = LatestFrameBuffer()
    producer = threading.Thread(
        target=lambda: (time.sleep(0.02), buffer.publish(make_frame(3)))
    )

    producer.start()
    frame = buffer.take(timeout=0.2)
    producer.join()

    assert frame is not None
    assert frame.sequence == 3


def test_capture_error_is_forwarded_with_cause() -> None:
    buffer = LatestFrameBuffer()
    cause = RuntimeError("camera disconnected")

    buffer.fail(cause)

    with pytest.raises(RuntimeError, match="采集线程失败") as captured:
        buffer.take(timeout=0.01)
    assert captured.value.__cause__ is cause


def test_take_times_out_when_no_frame_arrives() -> None:
    buffer = LatestFrameBuffer()

    assert buffer.take(timeout=0.001) is None


def test_close_unblocks_consumer_and_rejects_new_frames() -> None:
    buffer = LatestFrameBuffer()

    buffer.close()
    buffer.publish(make_frame(4))

    assert buffer.take(timeout=0.01) is None
    assert buffer.dropped == 0
