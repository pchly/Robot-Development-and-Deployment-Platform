#!/usr/bin/env bash

set -Eeuo pipefail

ENGINE_PATH="${1:-/data/robot-platform/cache/tensorrt/yolox-nano-c789161e-sm87-trt10.3-fp16/yolox_nano-fp16.engine}"
IMAGE="nvcr.io/nvidia/l4t-tensorrt@sha256:602c0972ae069fc3dcb4dfab6476e40dfbc7a38b6c704aeea5339ea797423eb6"

if [[ ! -f "$ENGINE_PATH" ]]; then
  echo "错误：找不到TensorRT Engine：$ENGINE_PATH" >&2
  exit 1
fi

ENGINE_DIR="$(dirname "$(readlink -f "$ENGINE_PATH")")"
ENGINE_NAME="$(basename "$ENGINE_PATH")"

docker run --rm \
  --runtime nvidia \
  --network none \
  --user "$(id -u):$(id -g)" \
  --volume "$ENGINE_DIR:/engines:ro" \
  "$IMAGE" \
  /usr/src/tensorrt/bin/trtexec \
  --loadEngine="/engines/$ENGINE_NAME" \
  --warmUp=1000 \
  --duration=10 \
  --useSpinWait
