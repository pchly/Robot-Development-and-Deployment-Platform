"""实时工作流使用的容量为1最新值缓存。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CapturedFrame:
    sequence: int
    captured_at: float
    image: Any


class LatestFrameBuffer:
    """新值覆盖尚未消费的旧值，使消费者延迟保持有界。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._frame: CapturedFrame | None = None
        self._closed = False
        self._error: Exception | None = None
        self.dropped = 0

    def publish(self, frame: CapturedFrame) -> None:
        with self._condition:
            if self._closed:
                return
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame
            self._condition.notify()

    def fail(self, error: Exception) -> None:
        with self._condition:
            self._error = error
            self._closed = True
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def take(self, timeout: float) -> CapturedFrame | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._frame is None and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if self._error is not None:
                raise RuntimeError("RealSense采集线程失败") from self._error
            frame = self._frame
            self._frame = None
            return frame
