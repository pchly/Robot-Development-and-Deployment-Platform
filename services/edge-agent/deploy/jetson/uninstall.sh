#!/usr/bin/env bash

set -Eeuo pipefail

SERVICE_NAME="robot-edge-agent.service"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"

fail() {
    echo "错误：$*" >&2
    exit 1
}

((EUID == 0)) || fail "请使用sudo运行卸载程序"
command -v systemctl >/dev/null 2>&1 || fail "未找到systemctl"

systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true

if [[ -f "$UNIT_PATH" ]]; then
    rm -f -- "$UNIT_PATH"
fi

systemctl daemon-reload
systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

echo "Edge Agent systemd服务已卸载。"
echo "以下内容已保留，便于排查或重新安装："
echo "  /data/robot-platform/edge-agent"
echo "  /etc/robot-platform/edge-agent.env"
echo "  systemd journal日志"
