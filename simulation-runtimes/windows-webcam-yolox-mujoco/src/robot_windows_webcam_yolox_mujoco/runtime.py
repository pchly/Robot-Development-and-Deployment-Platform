"""Windows webcam capture and CPU YOLOX inference for a simulation-only workflow."""

from __future__ import annotations

import time
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import numpy as np

COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli",
    "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)
MAX_COMBINED_PREVIEW_BYTES = 240 * 1024
CAMERA_READ_ATTEMPTS = 3
CAMERA_READ_RETRY_DELAY_SECONDS = 0.03


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2.0, self.y + self.height / 2.0


@dataclass(frozen=True)
class FrameResult:
    width: int
    height: int
    detections: tuple[Detection, ...]
    target: Detection | None
    inference_ms: float
    frame_sequence: int
    camera_read_ms: float
    preprocess_ms: float
    postprocess_ms: float
    annotation_ms: float
    total_ms: float


def _decode_yolox(output: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    grids: list[np.ndarray] = []
    strides: list[np.ndarray] = []
    for stride in (8, 16, 32):
        height = input_size[0] // stride
        width = input_size[1] // stride
        x_grid, y_grid = np.meshgrid(np.arange(width), np.arange(height))
        grid = np.stack((x_grid, y_grid), axis=2).reshape(1, -1, 2)
        grids.append(grid)
        strides.append(np.full((*grid.shape[:2], 1), stride))
    decoded = output.astype(np.float32, copy=True)
    decoded[..., :2] = (decoded[..., :2] + np.concatenate(grids, axis=1)) * np.concatenate(
        strides, axis=1
    )
    decoded[..., 2:4] = np.exp(decoded[..., 2:4]) * np.concatenate(strides, axis=1)
    return decoded


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1 + 1.0) * (y2 - y1 + 1.0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        index = int(order[0])
        keep.append(index)
        ix1 = np.maximum(x1[index], x1[order[1:]])
        iy1 = np.maximum(y1[index], y1[order[1:]])
        ix2 = np.minimum(x2[index], x2[order[1:]])
        iy2 = np.minimum(y2[index], y2[order[1:]])
        width = np.maximum(0.0, ix2 - ix1 + 1.0)
        height = np.maximum(0.0, iy2 - iy1 + 1.0)
        overlap = width * height / (areas[index] + areas[order[1:]] - width * height)
        order = order[np.where(overlap <= threshold)[0] + 1]
    return keep


class WindowsWebcamYoloxRuntime:
    """Own the webcam and ONNX session inside the managed runtime boundary."""

    def __init__(
        self,
        *,
        camera_index: int,
        width: int,
        height: int,
        fps: int,
        confidence: float,
        nms_threshold: float,
        target_label: str,
        model_path: str | None = None,
    ) -> None:
        try:
            import cv2
            import onnxruntime
        except ImportError as error:
            raise RuntimeError("Windows视觉Runtime缺少OpenCV或ONNX Runtime环境依赖") from error
        self._cv2 = cv2
        self._onnxruntime = onnxruntime
        self._camera_index = camera_index
        self._width = width
        self._height = height
        self._fps = fps
        self._confidence = confidence
        self._nms_threshold = nms_threshold
        self._target_label = target_label
        self._model_path = model_path or str(
            files("robot_windows_webcam_yolox_mujoco").joinpath("models/yolox_nano.onnx")
        )
        self._capture: Any | None = None
        self._session: Any | None = None
        self._input_name = ""
        self._output_name = ""
        self._last_annotated: np.ndarray | None = None
        self._pending_frame: np.ndarray | None = None
        self._capture_backend = ""
        self._sequence = 0

    def start(self) -> None:
        capture, first_frame, backend_name = self._open_camera()
        try:
            session = self._onnxruntime.InferenceSession(
                self._model_path,
                providers=["CPUExecutionProvider"],
            )
            inputs = session.get_inputs()
            outputs = session.get_outputs()
            if len(inputs) != 1 or len(outputs) != 1:
                raise RuntimeError("YOLOX ONNX模型必须为单输入单输出")
            if tuple(inputs[0].shape) != (1, 3, 416, 416):
                raise RuntimeError(f"YOLOX ONNX输入形状不符合契约：{inputs[0].shape}")
        except Exception:
            capture.release()
            raise
        self._capture = capture
        self._session = session
        self._input_name = inputs[0].name
        self._output_name = outputs[0].name
        self._pending_frame = first_frame
        self._capture_backend = backend_name

    def _open_camera(self) -> tuple[Any, np.ndarray, str]:
        candidates = (
            ("Media Foundation", getattr(self._cv2, "CAP_MSMF", None)),
            ("DirectShow", getattr(self._cv2, "CAP_DSHOW", None)),
            ("OpenCV自动后端", getattr(self._cv2, "CAP_ANY", 0)),
        )
        attempted: set[int] = set()
        for backend_name, backend in candidates:
            if not isinstance(backend, int) or backend in attempted:
                continue
            attempted.add(backend)
            capture = self._cv2.VideoCapture(self._camera_index, backend)
            if not capture.isOpened():
                capture.release()
                continue
            capture.set(self._cv2.CAP_PROP_FRAME_WIDTH, self._width)
            capture.set(self._cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            capture.set(self._cv2.CAP_PROP_FPS, self._fps)
            frame = self._read_capture(capture)
            if frame is not None:
                return capture, frame, backend_name
            capture.release()
        raise RuntimeError(
            f"无法稳定读取Windows摄像头索引 {self._camera_index}；"
            "请检查摄像头权限、驱动或是否被其他程序占用"
        )

    @staticmethod
    def _read_capture(capture: Any) -> np.ndarray | None:
        for attempt in range(CAMERA_READ_ATTEMPTS):
            ok, frame = capture.read()
            if ok and frame is not None:
                return frame
            if attempt + 1 < CAMERA_READ_ATTEMPTS:
                time.sleep(CAMERA_READ_RETRY_DELAY_SECONDS)
        return None

    def _read_frame(self) -> np.ndarray:
        if self._pending_frame is not None:
            frame = self._pending_frame
            self._pending_frame = None
            return frame
        frame = self._read_capture(self._capture)
        if frame is None:
            raise RuntimeError(
                f"Windows摄像头连续读取失败（{CAMERA_READ_ATTEMPTS}次）；"
                "请检查设备占用或驱动状态"
            )
        return frame

    def _preprocess(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        padded = np.full((416, 416, 3), 114, dtype=np.uint8)
        ratio = min(416 / image.shape[0], 416 / image.shape[1])
        resized_width = int(image.shape[1] * ratio)
        resized_height = int(image.shape[0] * ratio)
        resized = self._cv2.resize(image, (resized_width, resized_height))
        padded[:resized_height, :resized_width] = resized
        tensor = padded.transpose((2, 0, 1)).astype(np.float32)
        return np.ascontiguousarray(tensor[np.newaxis, ...]), ratio

    def _postprocess(
        self,
        raw_output: np.ndarray,
        ratio: float,
        width: int,
        height: int,
    ) -> tuple[Detection, ...]:
        predictions = _decode_yolox(raw_output, (416, 416))[0]
        boxes = predictions[:, :4]
        scores = predictions[:, 4:5] * predictions[:, 5:]
        boxes_xyxy = np.empty_like(boxes)
        boxes_xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
        boxes_xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
        boxes_xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
        boxes_xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
        boxes_xyxy /= ratio
        class_ids = scores.argmax(axis=1)
        class_scores = scores[np.arange(len(class_ids)), class_ids]
        valid = class_scores > self._confidence
        valid_boxes = boxes_xyxy[valid]
        valid_scores = class_scores[valid]
        valid_class_ids = class_ids[valid]
        detections: list[Detection] = []
        for index in _nms(valid_boxes, valid_scores, self._nms_threshold):
            x1, y1, x2, y2 = valid_boxes[index]
            x1 = float(np.clip(x1, 0.0, width - 1.0))
            y1 = float(np.clip(y1, 0.0, height - 1.0))
            x2 = float(np.clip(x2, 0.0, width - 1.0))
            y2 = float(np.clip(y2, 0.0, height - 1.0))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(
                Detection(
                    label=COCO_CLASSES[int(valid_class_ids[index])],
                    confidence=float(valid_scores[index]),
                    x=x1,
                    y=y1,
                    width=x2 - x1,
                    height=y2 - y1,
                )
            )
        return tuple(detections)

    def detect(self) -> FrameResult:
        if self._capture is None or self._session is None:
            raise RuntimeError("Windows视觉Runtime尚未启动")
        total_started = time.perf_counter()
        stage_started = total_started
        frame = self._read_frame()
        camera_read_ms = (time.perf_counter() - stage_started) * 1000.0
        stage_started = time.perf_counter()
        tensor, ratio = self._preprocess(frame)
        preprocess_ms = (time.perf_counter() - stage_started) * 1000.0
        started = time.perf_counter()
        output = self._session.run([self._output_name], {self._input_name: tensor})[0]
        inference_ms = (time.perf_counter() - started) * 1000.0
        stage_started = time.perf_counter()
        detections = self._postprocess(output, ratio, frame.shape[1], frame.shape[0])
        postprocess_ms = (time.perf_counter() - stage_started) * 1000.0
        targets = [item for item in detections if item.label == self._target_label]
        target = max(targets, key=lambda item: item.confidence, default=None)
        stage_started = time.perf_counter()
        annotated = frame.copy()
        for detection in detections:
            color = (52, 210, 132) if detection is target else (130, 155, 140)
            start = (int(detection.x), int(detection.y))
            end = (int(detection.x + detection.width), int(detection.y + detection.height))
            self._cv2.rectangle(annotated, start, end, color, 2)
            self._cv2.putText(
                annotated,
                f"{detection.label} {detection.confidence:.2f}",
                (start[0], max(20, start[1] - 7)),
                self._cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                self._cv2.LINE_AA,
            )
        annotation_ms = (time.perf_counter() - stage_started) * 1000.0
        self._last_annotated = annotated
        self._sequence += 1
        return FrameResult(
            width=frame.shape[1],
            height=frame.shape[0],
            detections=detections,
            target=target,
            inference_ms=inference_ms,
            frame_sequence=self._sequence,
            camera_read_ms=camera_read_ms,
            preprocess_ms=preprocess_ms,
            postprocess_ms=postprocess_ms,
            annotation_ms=annotation_ms,
            total_ms=(time.perf_counter() - total_started) * 1000.0,
        )

    def snapshot_preview_frame(self) -> np.ndarray | None:
        """Return an owned frame so preview work cannot race with detection."""

        if self._last_annotated is None:
            return None
        return self._last_annotated.copy()

    def compose_preview_rgb(
        self,
        camera_frame: np.ndarray,
        arm_rgb: np.ndarray,
    ) -> bytes | None:
        """Compose immutable camera and simulator snapshots into one JPEG."""

        if (
            camera_frame.ndim != 3
            or camera_frame.shape[2] != 3
            or arm_rgb.ndim != 3
            or arm_rgb.shape[2] != 3
        ):
            return None
        camera = camera_frame.copy()
        arm = self._cv2.cvtColor(arm_rgb, self._cv2.COLOR_RGB2BGR)
        return self._compose_preview_images(camera, arm)

    def compose_preview(self, arm_png: bytes) -> bytes | None:
        camera = self.snapshot_preview_frame()
        if camera is None:
            return None
        arm = self._cv2.imdecode(np.frombuffer(arm_png, dtype=np.uint8), self._cv2.IMREAD_COLOR)
        if arm is None:
            return None
        return self._compose_preview_images(camera, arm)

    def _compose_preview_images(
        self,
        camera: np.ndarray,
        arm: np.ndarray,
    ) -> bytes | None:
        target_height = 360
        camera = self._cv2.resize(
            camera,
            (int(camera.shape[1] * target_height / camera.shape[0]), target_height),
        )
        arm = self._cv2.resize(
            arm,
            (int(arm.shape[1] * target_height / arm.shape[0]), target_height),
        )
        self._cv2.putText(
            camera, "WINDOWS CAMERA + YOLOX", (14, 28), self._cv2.FONT_HERSHEY_SIMPLEX,
            0.65, (52, 210, 132), 2, self._cv2.LINE_AA,
        )
        self._cv2.putText(
            arm, "MUJOCO ARM", (14, 28), self._cv2.FONT_HERSHEY_SIMPLEX,
            0.65, (52, 210, 132), 2, self._cv2.LINE_AA,
        )
        combined = self._cv2.hconcat([camera, arm])
        # Keep the operational preview below the Agent upload limit even when
        # the camera image contains high-frequency texture.
        for quality in (82, 72, 60, 48):
            ok, encoded = self._cv2.imencode(
                ".jpg",
                combined,
                [self._cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            if ok and len(encoded) <= MAX_COMBINED_PREVIEW_BYTES:
                return bytes(encoded)
        return None

    def stop(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None
        self._session = None
        self._last_annotated = None
        self._pending_frame = None
        self._capture_backend = ""
