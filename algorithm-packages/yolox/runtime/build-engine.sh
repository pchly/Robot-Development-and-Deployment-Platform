#!/usr/bin/env bash

set -Eeuo pipefail

MODEL_PATH="${1:-/data/robot-platform/models/yolox/0.1.1rc0/yolox_nano.onnx}"
ENGINE_DIR="${2:-/data/robot-platform/cache/tensorrt/yolox-nano-c789161e-sm87-trt10.3-fp16}"
EXPECTED_SHA256="c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"
IMAGE="nvcr.io/nvidia/l4t-tensorrt@sha256:602c0972ae069fc3dcb4dfab6476e40dfbc7a38b6c704aeea5339ea797423eb6"
ENGINE_NAME="yolox_nano-fp16.engine"

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "错误：找不到ONNX模型：$MODEL_PATH" >&2
  exit 1
fi

printf '%s  %s\n' "$EXPECTED_SHA256" "$MODEL_PATH" | sha256sum --check --status || {
  echo "错误：ONNX模型SHA-256不匹配" >&2
  exit 1
}

mkdir -p "$ENGINE_DIR"
MODEL_DIR="$(dirname "$(readlink -f "$MODEL_PATH")")"
MODEL_NAME="$(basename "$MODEL_PATH")"
ENGINE_DIR="$(readlink -f "$ENGINE_DIR")"

docker run --rm \
  --runtime nvidia \
  --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$MODEL_DIR:/models:ro" \
  --volume "$ENGINE_DIR:/engines" \
  "$IMAGE" \
  /usr/src/tensorrt/bin/trtexec \
  --onnx="/models/$MODEL_NAME" \
  --saveEngine="/engines/$ENGINE_NAME" \
  --fp16 \
  --memPoolSize=workspace:1024 \
  --builderOptimizationLevel=3 \
  --profilingVerbosity=detailed \
  --skipInference

sha256sum "$ENGINE_DIR/$ENGINE_NAME" > "$ENGINE_DIR/$ENGINE_NAME.sha256"
echo "TensorRT Engine构建成功：$ENGINE_DIR/$ENGINE_NAME"
cat "$ENGINE_DIR/$ENGINE_NAME.sha256"
