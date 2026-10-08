#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: $0 <显式标签镜像> <输出.tar>" >&2
  echo "示例: $0 robot-platform/realsense-yolox-runtime:0.1.3 /data/robot-platform/releases/realsense-yolox-runtime-0.1.3.tar" >&2
}

if [ "$#" -ne 2 ]; then
  usage
  exit 2
fi

IMAGE="$1"
OUTPUT="$2"

case "$IMAGE" in
  -*|*://*|*@*|*" "*)
    echo "镜像必须使用安全的显式标签，不能包含协议、摘要或空格：$IMAGE" >&2
    exit 2
    ;;
esac

if [[ "$IMAGE" != *:* ]] || [[ "$OUTPUT" != *.tar ]]; then
  usage
  exit 2
fi

if [ -e "$OUTPUT" ]; then
  echo "拒绝覆盖已有文件：$OUTPUT" >&2
  exit 1
fi

OUTPUT_DIR="$(dirname -- "$OUTPUT")"
mkdir -p -- "$OUTPUT_DIR"
TEMP_OUTPUT="${OUTPUT}.partial.$$"
trap 'rm -f -- "$TEMP_OUTPUT"' EXIT

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
ARCHITECTURE="$(docker image inspect --format '{{.Architecture}}' "$IMAGE")"
if [[ ! "$IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Docker返回了无效镜像ID：$IMAGE_ID" >&2
  exit 1
fi

docker image save --output "$TEMP_OUTPUT" "$IMAGE"
ARCHIVE_SHA256="$(sha256sum "$TEMP_OUTPUT" | awk '{print $1}')"
mv -- "$TEMP_OUTPUT" "$OUTPUT"
trap - EXIT

ARCHIVE_SIZE="$(stat -c '%s' "$OUTPUT")"
ARCHIVE_NAME="$(basename -- "$OUTPUT")"

printf '\n镜像归档导出完成\n'
printf '镜像: %s\n镜像ID: %s\n架构: %s\n归档: %s\n大小: %s bytes\n归档SHA-256: %s\n' \
  "$IMAGE" "$IMAGE_ID" "$ARCHITECTURE" "$OUTPUT" "$ARCHIVE_SIZE" "$ARCHIVE_SHA256"
printf '\nManifest字段示例:\n'
printf '"runtime_image_digest": "%s",\n' "$IMAGE_ID"
printf '"runtime_image_distribution": {"file": "dist/%s", "sha256": "%s"}\n' \
  "$ARCHIVE_NAME" "$ARCHIVE_SHA256"
