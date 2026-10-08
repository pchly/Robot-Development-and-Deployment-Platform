#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="robot-platform/yolox-runtime:0.1.2-trt10.3"
BASE_IMAGE="nvcr.io/nvidia/l4t-tensorrt@sha256:602c0972ae069fc3dcb4dfab6476e40dfbc7a38b6c704aeea5339ea797423eb6"

docker build \
  --file "$SCRIPT_DIR/Dockerfile.inference" \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --tag "$IMAGE" \
  "$SCRIPT_DIR"

docker image inspect "$IMAGE" \
  --format 'YOLOX Runtime构建成功：{{.Id}} architecture={{.Architecture}}'

docker run --rm \
  --runtime nvidia \
  --network none \
  --entrypoint python3 \
  "$IMAGE" \
  -c "import cv2, numpy, tensorrt; assert int(numpy.__version__.split('.')[0]) < 2; print('NVIDIA Runtime依赖检查通过:', 'numpy=' + numpy.__version__, 'opencv=' + cv2.__version__, 'tensorrt=' + tensorrt.__version__)"
