#!/usr/bin/env bash

set -Eeuo pipefail

ENGINE_PATH="${1:-/data/robot-platform/cache/tensorrt/yolox-nano-c789161e-sm87-trt10.3-fp16/yolox_nano-fp16.engine}"
IMAGE_PATH="${2:-/data/robot-platform/validation-assets/yolox/dog.jpg}"
OUTPUT_ROOT="${3:-/data/robot-platform/validation}"
RUNTIME_IMAGE="robot-platform/yolox-runtime:0.1.2-trt10.3"
EXPECTED_ENGINE_SHA256="719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17"

if [[ ! -f "$ENGINE_PATH" ]]; then
  echo "错误：找不到TensorRT Engine：$ENGINE_PATH" >&2
  exit 1
fi
if [[ ! -f "$IMAGE_PATH" ]]; then
  echo "错误：找不到验证图片：$IMAGE_PATH" >&2
  exit 1
fi
printf '%s  %s\n' "$EXPECTED_ENGINE_SHA256" "$ENGINE_PATH" | sha256sum --check --status || {
  echo "错误：TensorRT Engine SHA-256不匹配" >&2
  exit 1
}

ENGINE_DIR="$(dirname "$(readlink -f "$ENGINE_PATH")")"
ENGINE_NAME="$(basename "$ENGINE_PATH")"
INPUT_DIR="$(dirname "$(readlink -f "$IMAGE_PATH")")"
INPUT_NAME="$(basename "$IMAGE_PATH")"
RUN_ID="yolox-image-$(date +%Y%m%d-%H%M%S)"
OUTPUT_DIR="$(readlink -m "$OUTPUT_ROOT/$RUN_ID")"
mkdir -p "$OUTPUT_DIR"

docker run --rm \
  --runtime nvidia \
  --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$ENGINE_DIR:/engines:ro" \
  --volume "$INPUT_DIR:/inputs:ro" \
  --volume "$OUTPUT_DIR:/outputs" \
  "$RUNTIME_IMAGE" \
  --engine "/engines/$ENGINE_NAME" \
  --image "/inputs/$INPUT_NAME" \
  --output-json /outputs/detections.json \
  --annotated-image /outputs/annotated.jpg \
  --confidence 0.3 \
  --nms 0.45

sha256sum "$ENGINE_PATH" "$IMAGE_PATH" > "$OUTPUT_DIR/artifacts.sha256"
echo "单图语义验证完成：$OUTPUT_DIR"
echo "检测结果：$OUTPUT_DIR/detections.json"
echo "标注图片：$OUTPUT_DIR/annotated.jpg"
