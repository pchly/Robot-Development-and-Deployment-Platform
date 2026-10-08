#!/usr/bin/env python3
"""使用TensorRT执行YOLOX单张图片推理并输出平台检测契约。"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import tensorrt as trt


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

CUDA_MEMCPY_HOST_TO_DEVICE = 1
CUDA_MEMCPY_DEVICE_TO_HOST = 2


class CudaError(RuntimeError):
    """CUDA Runtime API调用失败。"""


class CudaRuntime:
    """通过ctypes使用最小CUDA Runtime API，避免在镜像中引入PyTorch。"""

    def __init__(self) -> None:
        candidates = (
            ctypes.util.find_library("cudart"),
            "libcudart.so.12",
            "libcudart.so",
            "/usr/local/cuda/lib64/libcudart.so",
        )
        self.library = None
        for candidate in candidates:
            if not candidate:
                continue
            try:
                self.library = ctypes.CDLL(candidate)
                break
            except OSError:
                continue
        if self.library is None:
            raise CudaError("找不到CUDA Runtime动态库libcudart")

        self.library.cudaGetErrorString.argtypes = [ctypes.c_int]
        self.library.cudaGetErrorString.restype = ctypes.c_char_p
        self.library.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        self.library.cudaMalloc.restype = ctypes.c_int
        self.library.cudaFree.argtypes = [ctypes.c_void_p]
        self.library.cudaFree.restype = ctypes.c_int
        self.library.cudaMemcpy.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        self.library.cudaMemcpy.restype = ctypes.c_int
        self.library.cudaStreamCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.library.cudaStreamCreate.restype = ctypes.c_int
        self.library.cudaStreamDestroy.argtypes = [ctypes.c_void_p]
        self.library.cudaStreamDestroy.restype = ctypes.c_int
        self.library.cudaStreamSynchronize.argtypes = [ctypes.c_void_p]
        self.library.cudaStreamSynchronize.restype = ctypes.c_int

    def check(self, result: int, operation: str) -> None:
        if result == 0:
            return
        message = self.library.cudaGetErrorString(result)
        detail = message.decode("utf-8", errors="replace") if message else "unknown"
        raise CudaError(f"{operation}失败：CUDA {result} ({detail})")

    def malloc(self, size: int) -> ctypes.c_void_p:
        pointer = ctypes.c_void_p()
        self.check(self.library.cudaMalloc(ctypes.byref(pointer), size), "cudaMalloc")
        return pointer

    def free(self, pointer: ctypes.c_void_p) -> None:
        if pointer.value:
            self.check(self.library.cudaFree(pointer), "cudaFree")

    def memcpy(self, destination: int, source: int, size: int, kind: int) -> None:
        self.check(
            self.library.cudaMemcpy(
                ctypes.c_void_p(destination),
                ctypes.c_void_p(source),
                size,
                kind,
            ),
            "cudaMemcpy",
        )

    def create_stream(self) -> ctypes.c_void_p:
        stream = ctypes.c_void_p()
        self.check(self.library.cudaStreamCreate(ctypes.byref(stream)), "cudaStreamCreate")
        return stream

    def synchronize(self, stream: ctypes.c_void_p) -> None:
        self.check(self.library.cudaStreamSynchronize(stream), "cudaStreamSynchronize")

    def destroy_stream(self, stream: ctypes.c_void_p) -> None:
        if stream.value:
            self.check(self.library.cudaStreamDestroy(stream), "cudaStreamDestroy")


class TensorRTEngine:
    """只负责静态单输入、单输出TensorRT Engine的内存与执行。"""

    def __init__(self, engine_path: Path) -> None:
        self.logger = trt.Logger(trt.Logger.WARNING)
        trt.init_libnvinfer_plugins(self.logger, "")
        self.runtime = trt.Runtime(self.logger)
        self.engine = self.runtime.deserialize_cuda_engine(engine_path.read_bytes())
        if self.engine is None:
            raise RuntimeError(f"无法反序列化TensorRT Engine：{engine_path}")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("无法创建TensorRT执行上下文")

        inputs: list[str] = []
        outputs: list[str] = []
        for index in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(index)
            mode = self.engine.get_tensor_mode(name)
            if mode == trt.TensorIOMode.INPUT:
                inputs.append(name)
            elif mode == trt.TensorIOMode.OUTPUT:
                outputs.append(name)
        if len(inputs) != 1 or len(outputs) != 1:
            raise RuntimeError(
                f"当前Runtime仅支持单输入单输出Engine，实际为{len(inputs)}输入/{len(outputs)}输出"
            )

        self.input_name = inputs[0]
        self.output_name = outputs[0]
        self.input_shape = tuple(self.context.get_tensor_shape(self.input_name))
        self.output_shape = tuple(self.context.get_tensor_shape(self.output_name))
        if any(dimension < 1 for dimension in (*self.input_shape, *self.output_shape)):
            raise RuntimeError("当前Runtime不支持未解析的动态Tensor形状")
        self.input_dtype = np.dtype(trt.nptype(self.engine.get_tensor_dtype(self.input_name)))
        self.output_dtype = np.dtype(trt.nptype(self.engine.get_tensor_dtype(self.output_name)))
        self.cuda = CudaRuntime()

    def infer(self, tensor: np.ndarray) -> tuple[np.ndarray, float, float, float]:
        expected = int(np.prod(self.input_shape))
        if tensor.size != expected:
            raise ValueError(f"输入元素数量不匹配：期望{expected}，实际{tensor.size}")
        host_input = np.ascontiguousarray(tensor.reshape(self.input_shape), dtype=self.input_dtype)
        host_output = np.empty(self.output_shape, dtype=self.output_dtype)
        device_input = self.cuda.malloc(host_input.nbytes)
        device_output = self.cuda.malloc(host_output.nbytes)
        stream = self.cuda.create_stream()
        try:
            transfer_in_started = time.perf_counter()
            self.cuda.memcpy(
                device_input.value,
                host_input.ctypes.data,
                host_input.nbytes,
                CUDA_MEMCPY_HOST_TO_DEVICE,
            )
            h2d_ms = (time.perf_counter() - transfer_in_started) * 1000.0

            if not self.context.set_tensor_address(self.input_name, device_input.value):
                raise RuntimeError(f"无法绑定输入Tensor：{self.input_name}")
            if not self.context.set_tensor_address(self.output_name, device_output.value):
                raise RuntimeError(f"无法绑定输出Tensor：{self.output_name}")

            inference_started = time.perf_counter()
            if not self.context.execute_async_v3(stream_handle=stream.value):
                raise RuntimeError("TensorRT execute_async_v3返回失败")
            self.cuda.synchronize(stream)
            inference_ms = (time.perf_counter() - inference_started) * 1000.0

            transfer_out_started = time.perf_counter()
            self.cuda.memcpy(
                host_output.ctypes.data,
                device_output.value,
                host_output.nbytes,
                CUDA_MEMCPY_DEVICE_TO_HOST,
            )
            d2h_ms = (time.perf_counter() - transfer_out_started) * 1000.0
            return host_output, inference_ms, h2d_ms, d2h_ms
        finally:
            self.cuda.destroy_stream(stream)
            self.cuda.free(device_output)
            self.cuda.free(device_input)


def preprocess(image: np.ndarray, input_size: tuple[int, int]) -> tuple[np.ndarray, float]:
    """复现YOLOX官方ONNX示例的左上对齐letterbox预处理。"""
    input_height, input_width = input_size
    padded = np.full((input_height, input_width, 3), 114, dtype=np.uint8)
    ratio = min(input_height / image.shape[0], input_width / image.shape[1])
    resized_width = int(image.shape[1] * ratio)
    resized_height = int(image.shape[0] * ratio)
    resized = cv2.resize(
        image,
        (resized_width, resized_height),
        interpolation=cv2.INTER_LINEAR,
    ).astype(np.uint8)
    padded[:resized_height, :resized_width] = resized
    tensor = padded.transpose((2, 0, 1))
    return np.ascontiguousarray(tensor, dtype=np.float32), ratio


def decode_yolox(output: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    """将YOLOX head原始输出还原为输入画布上的cxcywh。"""
    grids: list[np.ndarray] = []
    expanded_strides: list[np.ndarray] = []
    for stride in (8, 16, 32):
        height = input_size[0] // stride
        width = input_size[1] // stride
        x_grid, y_grid = np.meshgrid(np.arange(width), np.arange(height))
        grid = np.stack((x_grid, y_grid), axis=2).reshape(1, -1, 2)
        grids.append(grid)
        expanded_strides.append(np.full((*grid.shape[:2], 1), stride))
    grid = np.concatenate(grids, axis=1)
    strides = np.concatenate(expanded_strides, axis=1)
    decoded = output.astype(np.float32, copy=True)
    decoded[..., :2] = (decoded[..., :2] + grid) * strides
    decoded[..., 2:4] = np.exp(decoded[..., 2:4]) * strides
    return decoded


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """YOLOX官方示例等价的类别无关NMS。"""
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1 + 1.0) * (y2 - y1 + 1.0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        index = int(order[0])
        keep.append(index)
        intersection_x1 = np.maximum(x1[index], x1[order[1:]])
        intersection_y1 = np.maximum(y1[index], y1[order[1:]])
        intersection_x2 = np.minimum(x2[index], x2[order[1:]])
        intersection_y2 = np.minimum(y2[index], y2[order[1:]])
        width = np.maximum(0.0, intersection_x2 - intersection_x1 + 1.0)
        height = np.maximum(0.0, intersection_y2 - intersection_y1 + 1.0)
        overlap = width * height / (areas[index] + areas[order[1:]] - width * height)
        remaining = np.where(overlap <= threshold)[0]
        order = order[remaining + 1]
    return keep


def postprocess(
    raw_output: np.ndarray,
    ratio: float,
    image_width: int,
    image_height: int,
    confidence_threshold: float,
    nms_threshold: float,
) -> list[dict[str, Any]]:
    predictions = decode_yolox(raw_output, (416, 416))[0]
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
    valid = class_scores > confidence_threshold
    if not np.any(valid):
        return []
    valid_boxes = boxes_xyxy[valid]
    valid_scores = class_scores[valid]
    valid_class_ids = class_ids[valid]
    kept = nms(valid_boxes, valid_scores, nms_threshold)

    detections: list[dict[str, Any]] = []
    for index in kept:
        x1, y1, x2, y2 = valid_boxes[index]
        x1 = float(np.clip(x1, 0.0, image_width - 1.0))
        y1 = float(np.clip(y1, 0.0, image_height - 1.0))
        x2 = float(np.clip(x2, 0.0, image_width - 1.0))
        y2 = float(np.clip(y2, 0.0, image_height - 1.0))
        if x2 <= x1 or y2 <= y1:
            continue
        class_id = int(valid_class_ids[index])
        detections.append(
            {
                "class_id": class_id,
                "label": COCO_CLASSES[class_id],
                "confidence": round(float(valid_scores[index]), 6),
                "bbox": {
                    "x": round(x1, 3),
                    "y": round(y1, 3),
                    "width": round(x2 - x1, 3),
                    "height": round(y2 - y1, 3),
                    "coordinate_space": "pixels",
                },
            }
        )
    return detections


def annotate(image: np.ndarray, detections: list[dict[str, Any]]) -> np.ndarray:
    annotated = image.copy()
    for detection in detections:
        bbox = detection["bbox"]
        x1, y1 = int(bbox["x"]), int(bbox["y"])
        x2 = int(bbox["x"] + bbox["width"])
        y2 = int(bbox["y"] + bbox["height"])
        class_id = detection["class_id"]
        color = (
            int((37 * class_id + 71) % 255),
            int((17 * class_id + 149) % 255),
            int((29 * class_id + 211) % 255),
        )
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        text = f"{detection['label']} {detection['confidence']:.2f}"
        cv2.putText(
            annotated,
            text,
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )
    return annotated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--annotated-image", type=Path)
    parser.add_argument("--confidence", type=float, default=0.3)
    parser.add_argument("--nms", type=float, default=0.45)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.confidence < 1.0:
        raise ValueError("--confidence必须位于(0, 1)区间")
    if not 0.0 < args.nms < 1.0:
        raise ValueError("--nms必须位于(0, 1)区间")
    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取输入图片：{args.image}")

    total_started = time.perf_counter()
    preprocess_started = time.perf_counter()
    tensor, ratio = preprocess(image, (416, 416))
    preprocess_ms = (time.perf_counter() - preprocess_started) * 1000.0

    engine = TensorRTEngine(args.engine)
    if engine.input_shape != (1, 3, 416, 416):
        raise RuntimeError(f"Engine输入形状不符合YOLOX-Nano契约：{engine.input_shape}")
    if engine.output_shape != (1, 3549, 85):
        raise RuntimeError(f"Engine输出形状不符合YOLOX-Nano契约：{engine.output_shape}")
    raw_output, inference_ms, h2d_ms, d2h_ms = engine.infer(tensor[np.newaxis, ...])

    postprocess_started = time.perf_counter()
    detections = postprocess(
        raw_output,
        ratio,
        image.shape[1],
        image.shape[0],
        args.confidence,
        args.nms,
    )
    postprocess_ms = (time.perf_counter() - postprocess_started) * 1000.0
    result = {
        "schema": "vision.detections.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {
            "type": "image_file",
            "uri": str(args.image),
            "width": image.shape[1],
            "height": image.shape[0],
            "color": "BGR",
        },
        "model": {
            "id": "yolox.nano.coco",
            "runtime": "tensorrt",
            "input_shape": [1, 3, 416, 416],
            "confidence_threshold": args.confidence,
            "nms_threshold": args.nms,
        },
        "metrics": {
            "preprocess_ms": round(preprocess_ms, 3),
            "h2d_ms": round(h2d_ms, 3),
            "inference_ms": round(inference_ms, 3),
            "d2h_ms": round(d2h_ms, 3),
            "postprocess_ms": round(postprocess_ms, 3),
            "total_ms": round((time.perf_counter() - total_started) * 1000.0, 3),
            "detection_count": len(detections),
        },
        "detections": detections,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(serialized + "\n", encoding="utf-8")
    if args.annotated_image:
        args.annotated_image.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.annotated_image), annotate(image, detections)):
            raise RuntimeError(f"无法写入标注图片：{args.annotated_image}")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
