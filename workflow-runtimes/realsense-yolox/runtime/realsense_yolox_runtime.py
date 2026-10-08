#!/usr/bin/env python3
"""RealSense RGB与YOLOX TensorRT的单进程实时融合Runtime。"""

from __future__ import annotations

import argparse
import json
import signal
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyrealsense2 as rs
from infer_image import (
    CUDA_MEMCPY_DEVICE_TO_HOST,
    CUDA_MEMCPY_HOST_TO_DEVICE,
    TensorRTEngine,
    annotate,
    postprocess,
    preprocess,
)

from latest_frame import CapturedFrame, LatestFrameBuffer
from realsense_selector import resolve_sdk_serial


class PersistentTensorRTEngine(TensorRTEngine):
    """在整个工作流期间复用TensorRT上下文、CUDA Stream和GPU缓冲区。"""

    def __init__(self, engine_path: Path) -> None:
        super().__init__(engine_path)
        self._host_output = np.empty(self.output_shape, dtype=self.output_dtype)
        self._device_input = self.cuda.malloc(
            int(np.prod(self.input_shape)) * self.input_dtype.itemsize
        )
        self._device_output = self.cuda.malloc(self._host_output.nbytes)
        self._stream = self.cuda.create_stream()
        if not self.context.set_tensor_address(self.input_name, self._device_input.value):
            raise RuntimeError(f"无法绑定输入Tensor：{self.input_name}")
        if not self.context.set_tensor_address(self.output_name, self._device_output.value):
            raise RuntimeError(f"无法绑定输出Tensor：{self.output_name}")
        self._closed = False

    def infer_persistent(
        self, tensor: np.ndarray
    ) -> tuple[np.ndarray, float, float, float]:
        host_input = np.ascontiguousarray(
            tensor.reshape(self.input_shape), dtype=self.input_dtype
        )
        h2d_started = time.perf_counter()
        self.cuda.memcpy(
            self._device_input.value,
            host_input.ctypes.data,
            host_input.nbytes,
            CUDA_MEMCPY_HOST_TO_DEVICE,
        )
        h2d_ms = (time.perf_counter() - h2d_started) * 1000.0

        inference_started = time.perf_counter()
        if not self.context.execute_async_v3(stream_handle=self._stream.value):
            raise RuntimeError("TensorRT execute_async_v3返回失败")
        self.cuda.synchronize(self._stream)
        inference_ms = (time.perf_counter() - inference_started) * 1000.0

        d2h_started = time.perf_counter()
        self.cuda.memcpy(
            self._host_output.ctypes.data,
            self._device_output.value,
            self._host_output.nbytes,
            CUDA_MEMCPY_DEVICE_TO_HOST,
        )
        d2h_ms = (time.perf_counter() - d2h_started) * 1000.0
        return self._host_output, inference_ms, h2d_ms, d2h_ms

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cuda.destroy_stream(self._stream)
        self.cuda.free(self._device_output)
        self.cuda.free(self._device_input)


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def capture_frames(
    buffer: LatestFrameBuffer,
    stop_event: threading.Event,
    serial: str,
    width: int,
    height: int,
    fps: int,
    counters: dict[str, int],
) -> None:
    pipeline = rs.pipeline()
    configuration = rs.config()
    configuration.enable_device(serial)
    configuration.enable_stream(rs.stream.color, width, height, rs.format.rgb8, fps)
    try:
        pipeline.start(configuration)
        while not stop_event.is_set():
            frames = pipeline.wait_for_frames(2000)
            color_frame = frames.get_color_frame()
            if not color_frame:
                continue
            rgb = np.asanyarray(color_frame.get_data()).copy()
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            counters["captured"] += 1
            buffer.publish(
                CapturedFrame(
                    sequence=counters["captured"],
                    captured_at=time.perf_counter(),
                    image=bgr,
                )
            )
    except (RuntimeError, ValueError, cv2.error) as error:
        if not stop_event.is_set():
            buffer.fail(error)
    finally:
        try:
            pipeline.stop()
        except RuntimeError:
            pass
        buffer.close()


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentage))


def write_preview_atomic(
    output_dir: Path, image: np.ndarray, detections: list[dict[str, Any]]
) -> None:
    """以原子替换方式写入低频JPEG预览，避免Agent读到半张图片。"""

    preview = annotate(image, detections)
    encoded, buffer = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not encoded:
        raise RuntimeError("无法编码实时预览JPEG")
    temporary_path = output_dir / ".preview.jpg.tmp"
    temporary_path.write_bytes(buffer.tobytes())
    temporary_path.replace(output_dir / "preview.jpg")


def run(
    arguments: argparse.Namespace,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    stop_event = stop_event or threading.Event()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    frame_buffer = LatestFrameBuffer()
    counters = {"captured": 0}
    resolved_serial = resolve_sdk_serial(rs, arguments.serial)
    engine = PersistentTensorRTEngine(arguments.engine)
    if engine.input_shape != (1, 3, 416, 416):
        raise RuntimeError(f"Engine输入形状不符合契约：{engine.input_shape}")
    if engine.output_shape != (1, 3549, 85):
        raise RuntimeError(f"Engine输出形状不符合契约：{engine.output_shape}")

    capture_thread = threading.Thread(
        target=capture_frames,
        name="realsense-capture",
        args=(
            frame_buffer,
            stop_event,
            resolved_serial,
            arguments.width,
            arguments.height,
            arguments.fps,
            counters,
        ),
        daemon=True,
    )
    capture_thread.start()
    emit(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "starting",
            "serial": resolved_serial,
            "requested_device": arguments.serial,
            "configured_fps": arguments.fps,
        }
    )

    inference_latencies: list[float] = []
    pipeline_latencies: list[float] = []
    inferred = 0
    warmups = 0
    frames_with_detections = 0
    total_detections = 0
    labels_seen: dict[str, int] = {}
    latest_detections: list[dict[str, Any]] = []
    latest_image: np.ndarray | None = None
    selected_detections: list[dict[str, Any]] = []
    selected_image: np.ndarray | None = None
    selected_score = (-1.0, -1)
    last_report = started
    try:
        while (
            not stop_event.is_set()
            and time.perf_counter() - started < arguments.duration_seconds
        ):
            frame = frame_buffer.take(timeout=3.0)
            if frame is None:
                continue
            tensor, ratio = preprocess(frame.image, (416, 416))
            raw_output, inference_ms, _, _ = engine.infer_persistent(
                tensor[np.newaxis, ...]
            )
            detections = postprocess(
                raw_output,
                ratio,
                frame.image.shape[1],
                frame.image.shape[0],
                arguments.confidence,
                arguments.nms,
            )
            completed_at = time.perf_counter()
            if warmups < arguments.warmup_frames:
                warmups += 1
                continue

            inferred += 1
            inference_latencies.append(inference_ms)
            pipeline_latencies.append((completed_at - frame.captured_at) * 1000.0)
            latest_detections = detections
            latest_image = frame.image
            if detections:
                frames_with_detections += 1
                total_detections += len(detections)
                for detection in detections:
                    label = str(detection["label"])
                    labels_seen[label] = labels_seen.get(label, 0) + 1
                frame_score = (
                    max(float(item["confidence"]) for item in detections),
                    len(detections),
                )
                if frame_score > selected_score:
                    selected_score = frame_score
                    selected_image = frame.image.copy()
                    selected_detections = detections

            if completed_at - last_report >= arguments.report_interval_seconds:
                write_preview_atomic(arguments.output_dir, frame.image, detections)
                emit(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "state": "running",
                        "captured_frames": counters["captured"],
                        "inferred_frames": inferred,
                        "dropped_frames": frame_buffer.dropped,
                        "latest_detection_count": len(detections),
                        "frames_with_detections": frames_with_detections,
                        "labels_seen": labels_seen,
                        "inference_mean_ms": round(
                            statistics.fmean(inference_latencies), 3
                        ),
                        "pipeline_p95_ms": round(
                            percentile(pipeline_latencies, 95), 3
                        ),
                    }
                )
                last_report = completed_at
    finally:
        stop_event.set()
        capture_thread.join(timeout=3.0)
        engine.close()

    elapsed = time.perf_counter() - started
    if inferred == 0 or latest_image is None:
        raise RuntimeError("没有完成有效推理帧")
    artifact_image = selected_image if selected_image is not None else latest_image
    artifact_detections = (
        selected_detections if selected_image is not None else latest_detections
    )
    source_path = arguments.output_dir / "source.jpg"
    annotated_path = arguments.output_dir / "annotated.jpg"
    if not cv2.imwrite(str(source_path), artifact_image):
        raise RuntimeError(f"无法写入源图片：{source_path}")
    if not cv2.imwrite(
        str(annotated_path), annotate(artifact_image, artifact_detections)
    ):
        raise RuntimeError(f"无法写入标注图片：{annotated_path}")

    summary = {
        "schema": "robot.workflow.realsense-yolox.validation.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": "stopped",
        "source": {
            "type": "realsense",
            "sdk_serial": resolved_serial,
            "requested_device": arguments.serial,
            "width": arguments.width,
            "height": arguments.height,
            "configured_fps": arguments.fps,
        },
        "model": {
            "id": "yolox.nano.coco",
            "runtime": "tensorrt",
            "confidence_threshold": arguments.confidence,
            "nms_threshold": arguments.nms,
        },
        "metrics": {
            "elapsed_s": round(elapsed, 3),
            "warmup_frames": warmups,
            "captured_frames": counters["captured"],
            "inferred_frames": inferred,
            "dropped_frames": frame_buffer.dropped,
            "frames_with_detections": frames_with_detections,
            "total_detections": total_detections,
            "capture_fps": round(counters["captured"] / elapsed, 3),
            "inference_fps": round(inferred / elapsed, 3),
            "inference_mean_ms": round(statistics.fmean(inference_latencies), 3),
            "inference_p95_ms": round(percentile(inference_latencies, 95), 3),
            "pipeline_mean_ms": round(statistics.fmean(pipeline_latencies), 3),
            "pipeline_p95_ms": round(percentile(pipeline_latencies, 95), 3),
        },
        "labels_seen": dict(sorted(labels_seen.items())),
        "selected_detections": artifact_detections,
        "latest_detections": latest_detections,
        "artifacts": {
            "selection": (
                "highest_confidence_detection_frame"
                if selected_image is not None
                else "last_frame_no_detections"
            ),
            "source_image": str(source_path),
            "annotated_image": str(annotated_path),
        },
    }
    summary_path = arguments.output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    emit(summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--warmup-frames", type=int, default=10)
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--report-interval-seconds", type=float, default=5.0)
    parser.add_argument("--confidence", type=float, default=0.3)
    parser.add_argument("--nms", type=float, default=0.45)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.width, arguments.height, arguments.fps) <= 0:
        parser.error("分辨率和帧率必须大于0")
    if arguments.warmup_frames < 0:
        parser.error("--warmup-frames不得小于0")
    if arguments.duration_seconds <= 0 or arguments.report_interval_seconds <= 0:
        parser.error("持续时间和报告间隔必须大于0")
    if not 0.0 < arguments.confidence < 1.0 or not 0.0 < arguments.nms < 1.0:
        parser.error("置信度和NMS阈值必须位于(0, 1)")
    return arguments


def main() -> int:
    arguments = parse_args()
    stop_requested = threading.Event()

    def request_stop(*_: object) -> None:
        stop_requested.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run(arguments, stop_requested)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
