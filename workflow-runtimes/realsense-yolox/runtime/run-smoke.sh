#!/usr/bin/env bash

set -Eeuo pipefail

ENGINE_PATH="${1:-/data/robot-platform/cache/tensorrt/yolox-nano-c789161e-sm87-trt10.3-fp16/yolox_nano-fp16.engine}"
DEVICE_SELECTOR="${2:-123423024767}"
OUTPUT_ROOT="${3:-/data/robot-platform/validation}"
IMAGE="robot-platform/realsense-yolox-runtime:0.1.3"
EXPECTED_ENGINE_SHA256="719df3d33f1924f06925fa3c96030e4b4a51c110138f9a5b55709bdf53c31e17"

if [[ ! -f "$ENGINE_PATH" ]]; then
  echo "错误：找不到TensorRT Engine：$ENGINE_PATH" >&2
  exit 1
fi
printf '%s  %s\n' "$EXPECTED_ENGINE_SHA256" "$ENGINE_PATH" |
  sha256sum --check --status || {
    echo "错误：TensorRT Engine SHA-256不匹配" >&2
    exit 1
  }
docker image inspect "$IMAGE" >/dev/null

ENGINE_DIR="$(dirname "$(readlink -f "$ENGINE_PATH")")"
ENGINE_NAME="$(basename "$ENGINE_PATH")"
RUN_ID="realsense-yolox-smoke-$(date +%Y%m%d-%H%M%S)"
OUTPUT_DIR="$(readlink -m "$OUTPUT_ROOT/$RUN_ID")"
mkdir -p "$OUTPUT_DIR"

docker run --rm \
  --runtime nvidia \
  --network none \
  --device-cgroup-rule 'c 81:* rw' \
  --device-cgroup-rule 'c 189:* rw' \
  --volume /dev:/dev \
  --volume "$ENGINE_DIR:/engines:ro" \
  --volume "$OUTPUT_DIR:/outputs" \
  "$IMAGE" \
  --engine "/engines/$ENGINE_NAME" \
  --serial "$DEVICE_SELECTOR" \
  --width 640 \
  --height 480 \
  --fps 30 \
  --warmup-frames 10 \
  --duration-seconds 30 \
  --report-interval-seconds 5 \
  --output-dir /outputs

# Jetson设备验证阶段容器内使用root访问NVIDIA/USB设备；将产物所有权恢复给调用用户。
docker run --rm \
  --network none \
  --volume "$OUTPUT_DIR:/outputs" \
  --entrypoint chown \
  "$IMAGE" \
  -R "$(id -u):$(id -g)" /outputs

echo "融合Smoke Test完成：$OUTPUT_DIR"
echo "结果：$OUTPUT_DIR/summary.json"
echo "证据原图：$OUTPUT_DIR/source.jpg"
echo "检测证据标注图：$OUTPUT_DIR/annotated.jpg"
echo "低频预览图：$OUTPUT_DIR/preview.jpg"
