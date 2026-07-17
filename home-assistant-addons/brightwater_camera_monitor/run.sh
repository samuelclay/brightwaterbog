#!/usr/bin/with-contenv sh
set -eu

export CAMERA_MONITOR_CACHE_DIR="${CAMERA_MONITOR_CACHE_DIR:-/data/camera_monitor}"
export CAMERA_MONITOR_CONFIG="${CAMERA_MONITOR_CONFIG:-/app/camera_monitor.local.json}"
mkdir -p "${CAMERA_MONITOR_CACHE_DIR}"

option_value() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

key = sys.argv[1]
default = sys.argv[2]
try:
    with open("/data/options.json", encoding="utf-8") as options_file:
        value = json.load(options_file).get(key)
except Exception:
    value = None
print(value if value else default)
PY
}

HA_URL="$(option_value ha_url "${CAMERA_MONITOR_HA_URL:-http://supervisor/core}")"
HA_TOKEN="$(option_value ha_token "${CABIN_HOME_ASSISTANT_TOKEN:-}")"
WARM_AGENT="${CAMERA_MONITOR_WARM_AGENT:-1}"

if [ -n "${HA_TOKEN}" ]; then
  export CABIN_HOME_ASSISTANT_TOKEN="${HA_TOKEN}"
fi

echo "Using Home Assistant API at ${HA_URL}"

monitor_pid=""
warm_agent_pid=""

cleanup() {
  if [ -n "${warm_agent_pid}" ]; then
    kill "${warm_agent_pid}" >/dev/null 2>&1 || true
    wait "${warm_agent_pid}" >/dev/null 2>&1 || true
  fi
  if [ -n "${monitor_pid}" ]; then
    kill "${monitor_pid}" >/dev/null 2>&1 || true
  fi
}

trap cleanup INT TERM EXIT

python3 /app/camera_monitor.py \
  --config "${CAMERA_MONITOR_CONFIG}" \
  --ha-url "${HA_URL}" \
  --host 0.0.0.0 \
  --port "${CAMERA_MONITOR_PORT:-8765}" \
  --cache-dir "${CAMERA_MONITOR_CACHE_DIR}" &
monitor_pid="$!"

ready=0
attempt=0
while [ "${attempt}" -lt 60 ]; do
  if ! kill -0 "${monitor_pid}" >/dev/null 2>&1; then
    break
  fi
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${CAMERA_MONITOR_PORT:-8765}/api/status', timeout=2).read()" >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ "${ready}" -ne 1 ]; then
  echo "Camera monitor did not become ready"
  wait "${monitor_pid}"
  exit $?
fi

if [ "${WARM_AGENT}" = "1" ]; then
  chromium_bin="$(command -v chromium-browser || command -v chromium)"
  echo "Starting resident camera warm agent"
fi

while kill -0 "${monitor_pid}" >/dev/null 2>&1; do
  if [ "${WARM_AGENT}" = "1" ] && { [ -z "${warm_agent_pid}" ] || ! kill -0 "${warm_agent_pid}" >/dev/null 2>&1; }; then
    if [ -n "${warm_agent_pid}" ]; then
      wait "${warm_agent_pid}" || true
      echo "Camera warm agent exited; restarting"
    fi
    python3 /app/camera_warm_agent.py \
      --chromium "${chromium_bin}" \
      --config "${CAMERA_MONITOR_CONFIG}" \
      --base-url "http://127.0.0.1:${CAMERA_MONITOR_PORT:-8765}" \
      --ha-url "${HA_URL}" &
    warm_agent_pid="$!"
  fi
  sleep 2
done

wait "${monitor_pid}"
