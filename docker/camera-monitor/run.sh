#!/bin/sh
set -eu

: "${CABIN_HOME_ASSISTANT_TOKEN:?CABIN_HOME_ASSISTANT_TOKEN is required}"
: "${CAMERA_MONITOR_HA_URL:?CAMERA_MONITOR_HA_URL is required}"

config_path="${CAMERA_MONITOR_CONFIG:-/config/camera_monitor.json}"
cache_dir="${CAMERA_MONITOR_CACHE_DIR:-/data/camera_monitor}"
monitor_port="${CAMERA_MONITOR_PORT:-8765}"
warm_agent_enabled="${CAMERA_MONITOR_WARM_AGENT_ENABLED:-0}"

if [ ! -r "${config_path}" ]; then
  echo "Camera config is not readable: ${config_path}" >&2
  exit 1
fi

mkdir -p "${cache_dir}" /data/chromium

monitor_pid=""
warm_agent_pid=""

cleanup() {
  if [ -n "${warm_agent_pid}" ]; then
    kill "${warm_agent_pid}" >/dev/null 2>&1 || true
    wait "${warm_agent_pid}" 2>/dev/null || true
  fi
  if [ -n "${monitor_pid}" ]; then
    kill "${monitor_pid}" >/dev/null 2>&1 || true
    wait "${monitor_pid}" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

python3 /app/camera_monitor.py \
  --config "${config_path}" \
  --ha-url "${CAMERA_MONITOR_HA_URL}" \
  --host 0.0.0.0 \
  --port "${monitor_port}" \
  --cache-dir "${cache_dir}" &
monitor_pid="$!"

ready=0
attempt=0
while [ "${attempt}" -lt 60 ]; do
  if ! kill -0 "${monitor_pid}" >/dev/null 2>&1; then
    break
  fi
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${monitor_port}/api/status', timeout=2).read()" >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ "${ready}" -ne 1 ]; then
  echo "Camera monitor did not become ready" >&2
  wait "${monitor_pid}"
  exit $?
fi

if [ "${warm_agent_enabled}" = "1" ]; then
  chromium_bin="$(command -v chromium-browser || command -v chromium)"
  echo "Starting resident camera warm agent"
fi

while kill -0 "${monitor_pid}" >/dev/null 2>&1; do
  if [ "${warm_agent_enabled}" = "1" ] && { [ -z "${warm_agent_pid}" ] || ! kill -0 "${warm_agent_pid}" >/dev/null 2>&1; }; then
    if [ -n "${warm_agent_pid}" ]; then
      wait "${warm_agent_pid}" 2>/dev/null || true
      echo "Camera warm agent exited; restarting"
    fi
    python3 /app/camera_warm_agent.py \
      --chromium "${chromium_bin}" \
      --config "${config_path}" \
      --base-url "http://127.0.0.1:${monitor_port}" \
      --ha-url "${CAMERA_MONITOR_HA_URL}" \
      --profile-root /data/chromium &
    warm_agent_pid="$!"
  fi
  sleep 2
done

wait "${monitor_pid}"
