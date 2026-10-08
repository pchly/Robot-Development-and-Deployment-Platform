#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MANUAL_ROOT="$(readlink -f "$SCRIPT_DIR/../../..")"
YOLO_RUNTIME_DIR="$MANUAL_ROOT/algorithm-packages/yolox/runtime"
REALSENSE_VENDOR_DIR="$MANUAL_ROOT/realsense/runtime/vendor"
BUILD_CONTEXT="/data/robot-platform/build-context/realsense-yolox-0.1.3"
WHEEL_NAME="pyrealsense2-2.58.4.10922-cp310-cp310-manylinux2014_aarch64.whl"
WHEEL_SHA256="4f9b615c9dc4be3f49d72977c5f70f3ccf2234edb2cd82e9f5d0c32077ec2b32"
IMAGE="robot-platform/realsense-yolox-runtime:0.1.3"
BASE_IMAGE="robot-platform/yolox-runtime:0.1.2-trt10.3"

for required in \
  "$SCRIPT_DIR/Dockerfile" \
  "$SCRIPT_DIR/latest_frame.py" \
  "$SCRIPT_DIR/realsense_selector.py" \
  "$SCRIPT_DIR/realsense_yolox_runtime.py" \
  "$YOLO_RUNTIME_DIR/infer_image.py" \
  "$REALSENSE_VENDOR_DIR/$WHEEL_NAME"; do
  if [[ ! -f "$required" ]]; then
    echo "错误：缺少构建输入：$required" >&2
    exit 1
  fi
done

printf '%s  %s\n' "$WHEEL_SHA256" "$REALSENSE_VENDOR_DIR/$WHEEL_NAME" |
  sha256sum --check --status || {
    echo "错误：pyrealsense2 Wheel SHA-256不匹配" >&2
    exit 1
  }

docker image inspect "$BASE_IMAGE" >/dev/null
mkdir -p "$BUILD_CONTEXT/vendor"
install -m 0644 "$SCRIPT_DIR/Dockerfile" "$BUILD_CONTEXT/Dockerfile"
install -m 0644 \
  "$SCRIPT_DIR/latest_frame.py" \
  "$SCRIPT_DIR/realsense_selector.py" \
  "$SCRIPT_DIR/realsense_yolox_runtime.py" \
  "$BUILD_CONTEXT/"
install -m 0644 "$YOLO_RUNTIME_DIR/infer_image.py" "$BUILD_CONTEXT/infer_image.py"
install -m 0644 \
  "$REALSENSE_VENDOR_DIR/$WHEEL_NAME" \
  "$BUILD_CONTEXT/vendor/$WHEEL_NAME"

docker build \
  --file "$BUILD_CONTEXT/Dockerfile" \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "PYREALSENSE_WHEEL=$WHEEL_NAME" \
  --tag "$IMAGE" \
  "$BUILD_CONTEXT"

docker run --rm \
  --runtime nvidia \
  --network none \
  --entrypoint python3 \
  "$IMAGE" \
  -c "import cv2, numpy, pyrealsense2, tensorrt; print('融合Runtime依赖检查通过:', 'numpy=' + numpy.__version__, 'opencv=' + cv2.__version__, 'tensorrt=' + tensorrt.__version__)"

docker image inspect "$IMAGE" \
  --format '融合Runtime构建成功：{{.Id}} architecture={{.Architecture}}'
