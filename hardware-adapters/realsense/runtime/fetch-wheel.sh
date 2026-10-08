#!/usr/bin/env bash

set -Eeuo pipefail

WHEEL_NAME="pyrealsense2-2.58.4.10922-cp310-cp310-manylinux2014_aarch64.whl"
WHEEL_SHA256="4f9b615c9dc4be3f49d72977c5f70f3ccf2234edb2cd82e9f5d0c32077ec2b32"
WHEEL_URL="https://files.pythonhosted.org/packages/28/80/93b0dba660e9693c9355562ac204a7bc21c11ba485d4bb09283f74d3d4f4/${WHEEL_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${SCRIPT_DIR}/vendor"
WHEEL_PATH="${VENDOR_DIR}/${WHEEL_NAME}"

mkdir -p "$VENDOR_DIR"
curl --fail --location --retry 3 --connect-timeout 15 --output "$WHEEL_PATH" "$WHEEL_URL"
printf '%s  %s\n' "$WHEEL_SHA256" "$WHEEL_PATH" | sha256sum --check --status
echo "RealSense官方wheel下载并校验成功：${WHEEL_PATH}"
