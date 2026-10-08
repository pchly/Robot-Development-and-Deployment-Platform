#!/usr/bin/env bash

set -Eeuo pipefail

SERVICE_NAME="robot-edge-agent.service"
INSTALL_ROOT="/data/robot-platform/edge-agent"
PLUGIN_ROOT="${INSTALL_ROOT}/plugins"
STAGING_ROOT="${INSTALL_ROOT}/staging"
CALIBRATION_ROOT="${INSTALL_ROOT}/calibrations"
TENSORRT_CACHE_ROOT="/data/robot-platform/cache/tensorrt"
CONFIG_DIR="/etc/robot-platform"
CONFIG_PATH="${CONFIG_DIR}/edge-agent.env"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"

usage() {
    cat <<'EOF'
用法：
  sudo bash deploy/jetson/install.sh \
    --server-url http://192.168.1.109:8765 \
    --agent-id jetson-agx-orin-01 \
    --name JetsonAGXOrin \
    [--user nvidia]

安装程序会：
  1. 把源码复制到 /data/robot-platform/edge-agent/releases/<UTC时间>；
  2. 原子更新 current 符号链接；
  3. 写入 /etc/robot-platform/edge-agent.env；
  4. 安装并启动 robot-edge-agent.service。
EOF
}

fail() {
    echo "错误：$*" >&2
    exit 1
}

SERVER_URL=""
AGENT_ID="$(hostname)"
AGENT_NAME="$(hostname)"
SERVICE_USER="${SUDO_USER:-nvidia}"

while (($# > 0)); do
    case "$1" in
        --server-url)
            (($# >= 2)) || fail "--server-url 缺少参数"
            SERVER_URL="$2"
            shift 2
            ;;
        --agent-id)
            (($# >= 2)) || fail "--agent-id 缺少参数"
            AGENT_ID="$2"
            shift 2
            ;;
        --name)
            (($# >= 2)) || fail "--name 缺少参数"
            AGENT_NAME="$2"
            shift 2
            ;;
        --user)
            (($# >= 2)) || fail "--user 缺少参数"
            SERVICE_USER="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "未知参数：$1"
            ;;
    esac
done

((EUID == 0)) || fail "请使用sudo运行安装程序"
[[ -n "$SERVER_URL" ]] || fail "必须提供--server-url"
[[ "$SERVER_URL" =~ ^https?://[A-Za-z0-9._:/-]+$ ]] || fail "server URL格式不合法"
[[ "$AGENT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail "agent ID只能包含字母、数字、点、下划线和连字符"
[[ "$AGENT_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail "节点名称只能包含字母、数字、点、下划线和连字符"
[[ "$SERVICE_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || fail "服务用户名格式不合法"

id "$SERVICE_USER" >/dev/null 2>&1 || fail "用户不存在：${SERVICE_USER}"
[[ -x /usr/bin/python3 ]] || fail "未找到/usr/bin/python3"
command -v systemctl >/dev/null 2>&1 || fail "未找到systemctl"
command -v systemd-analyze >/dev/null 2>&1 || fail "未找到systemd-analyze"
command -v docker >/dev/null 2>&1 || fail "未找到docker"
id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx docker || \
    fail "服务用户${SERVICE_USER}不在docker组，无法管理端侧Runtime"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SOURCE_DIR="${PROJECT_DIR}/src/robot_edge_agent"
UNIT_TEMPLATE="${SCRIPT_DIR}/robot-edge-agent.service.in"

[[ -f "${SOURCE_DIR}/__main__.py" ]] || fail "Edge Agent源码不完整：${SOURCE_DIR}"
[[ -f "$UNIT_TEMPLATE" ]] || fail "缺少systemd单元模板：${UNIT_TEMPLATE}"

CURRENT_SERVICE_PID="$(systemctl show "$SERVICE_NAME" --property MainPID --value 2>/dev/null || true)"
CURRENT_SERVICE_PID="${CURRENT_SERVICE_PID:-0}"
while read -r agent_pid; do
    [[ -z "$agent_pid" ]] && continue
    if [[ "$agent_pid" != "$CURRENT_SERVICE_PID" ]]; then
        fail "检测到手动启动的Edge Agent进程PID=${agent_pid}，请先按Ctrl+C停止它"
    fi
done < <(pgrep -u "$SERVICE_USER" -f 'python3 -m robot_edge_agent' || true)

SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RELEASE_DIR="${INSTALL_ROOT}/releases/${RELEASE_ID}"
CONFIG_TMP="$(mktemp)"
UNIT_TMP="$(mktemp)"
trap 'rm -f "$CONFIG_TMP" "$UNIT_TMP"' EXIT

systemctl stop "$SERVICE_NAME" 2>/dev/null || true

install -d -o root -g root -m 0755 "${RELEASE_DIR}/src/robot_edge_agent"
install -d -o root -g root -m 0755 "${RELEASE_DIR}/deploy/jetson"
while IFS= read -r -d '' source_file; do
    relative_path="${source_file#"${SOURCE_DIR}/"}"
    target_file="${RELEASE_DIR}/src/robot_edge_agent/${relative_path}"
    install -d -o root -g root -m 0755 "$(dirname "$target_file")"
    install -o root -g root -m 0644 "$source_file" "$target_file"
done < <(find "$SOURCE_DIR" -type f -name '*.py' -print0)
install -o root -g root -m 0644 "${PROJECT_DIR}/pyproject.toml" "${RELEASE_DIR}/"
install -o root -g root -m 0644 "${SCRIPT_DIR}/README.md" "${RELEASE_DIR}/deploy/jetson/"

printf '%s\n' \
    "ROBOT_PLATFORM_SERVER_URL=${SERVER_URL}" \
    "ROBOT_EDGE_AGENT_ID=${AGENT_ID}" \
    "ROBOT_EDGE_AGENT_NAME=${AGENT_NAME}" \
    "ROBOT_EDGE_PLUGIN_PATH=${PLUGIN_ROOT}" \
    >"$CONFIG_TMP"

sed \
    -e "s/@SERVICE_USER@/${SERVICE_USER}/g" \
    -e "s/@SERVICE_GROUP@/${SERVICE_GROUP}/g" \
    "$UNIT_TEMPLATE" >"$UNIT_TMP"

install -d -o root -g root -m 0755 "$CONFIG_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "/data/robot-platform/runs"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$PLUGIN_ROOT"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$STAGING_ROOT"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$CALIBRATION_ROOT"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$TENSORRT_CACHE_ROOT"
install -o root -g "$SERVICE_GROUP" -m 0640 "$CONFIG_TMP" "$CONFIG_PATH"
install -o root -g root -m 0644 "$UNIT_TMP" "$UNIT_PATH"

ln -sfn "$RELEASE_DIR" "${INSTALL_ROOT}/current.next"
mv -Tf "${INSTALL_ROOT}/current.next" "${INSTALL_ROOT}/current"

systemd-analyze verify "$UNIT_PATH"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager >&2 || true
    fail "服务启动失败，请检查上面的日志"
fi

echo "Edge Agent服务安装成功。"
echo "版本目录：${RELEASE_DIR}"
echo "配置文件：${CONFIG_PATH}"
echo "查看状态：sudo systemctl status ${SERVICE_NAME}"
echo "跟踪日志：sudo journalctl -u ${SERVICE_NAME} -f"
