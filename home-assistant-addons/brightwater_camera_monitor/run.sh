#!/usr/bin/with-contenv sh
set -eu

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
if value is None:
    print(default)
elif isinstance(value, bool):
    print("1" if value else "0")
else:
    print(value)
PY
}

export CAMERA_MONITOR_CONFIG="/app/camera_monitor.local.json"
export CAMERA_MONITOR_CACHE_DIR="/data/camera_monitor"
export CAMERA_MONITOR_PORT="8765"
export CAMERA_EUFY_WS_URL="$(option_value eufy_ws_url "ws://192.0.2.10:3300")"
export CAMERA_MONITOR_GO2RTC_URL="$(option_value go2rtc_url "http://192.0.2.10:1984")"
export CAMERA_NEST_CLIENT_ID="$(option_value nest_client_id "")"
export CAMERA_NEST_CLIENT_SECRET="$(option_value nest_client_secret "")"
export CAMERA_NEST_REFRESH_TOKEN="$(option_value nest_refresh_token "")"
export CAMERA_NEST_PROJECT_ID="$(option_value nest_project_id "")"
export CAMERA_MONITOR_EUFY_VIEWER_SLOTS="$(option_value eufy_viewer_slots "1")"
export CAMERA_MONITOR_EUFY_THUMBNAIL_REFRESH_SECONDS="$(
  option_value eufy_thumbnail_refresh_seconds "300"
)"
export CAMERA_MONITOR_EUFY_AUTO_RECOVERY="$(
  option_value eufy_auto_recovery "1"
)"
export CAMERA_MONITOR_EUFY_ADDON_SLUG="$(
  option_value eufy_addon_slug "402f1039_eufy_security_ws"
)"
export CAMERA_MONITOR_SUPERVISOR_URL="http://supervisor"
export CAMERA_MONITOR_WARM_AGENT_ENABLED="$(option_value warm_agent "1")"
export CAMERA_MONITOR_WARM_IDLE_HOURS="$(option_value warm_idle_hours "48")"

mkdir -p "${CAMERA_MONITOR_CACHE_DIR}"

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

echo "Starting optimized camera monitor with ${CAMERA_MONITOR_EUFY_VIEWER_SLOTS} Eufy viewer slot"
python3 /app/camera_monitor.py \
  --config "${CAMERA_MONITOR_CONFIG}" \
  --host 0.0.0.0 \
  --port "${CAMERA_MONITOR_PORT}" \
  --cache-dir "${CAMERA_MONITOR_CACHE_DIR}" &
monitor_pid="$!"

ready=0
attempt=0
while [ "${attempt}" -lt 60 ]; do
  if ! kill -0 "${monitor_pid}" >/dev/null 2>&1; then
    break
  fi
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${CAMERA_MONITOR_PORT}/api/status', timeout=2).read()" >/dev/null 2>&1; then
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

while kill -0 "${monitor_pid}" >/dev/null 2>&1; do
  if [ "${CAMERA_MONITOR_WARM_AGENT_ENABLED}" = "1" ] && {
    [ -z "${warm_agent_pid}" ] || ! kill -0 "${warm_agent_pid}" >/dev/null 2>&1
  }; then
    if [ -n "${warm_agent_pid}" ]; then
      wait "${warm_agent_pid}" 2>/dev/null || true
      echo "Camera warm agent exited; restarting"
    fi
    python3 /app/camera_warm_agent.py \
      --config "${CAMERA_MONITOR_CONFIG}" \
      --base-url "http://127.0.0.1:${CAMERA_MONITOR_PORT}" &
    warm_agent_pid="$!"
  fi
  sleep 2
done

wait "${monitor_pid}"
