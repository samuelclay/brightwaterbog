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

if [ -n "${HA_TOKEN}" ]; then
  export CABIN_HOME_ASSISTANT_TOKEN="${HA_TOKEN}"
fi

echo "Using Home Assistant API at ${HA_URL}"

exec python3 /app/camera_monitor.py \
  --config "${CAMERA_MONITOR_CONFIG}" \
  --ha-url "${HA_URL}" \
  --host 0.0.0.0 \
  --port "${CAMERA_MONITOR_PORT:-8765}" \
  --cache-dir "${CAMERA_MONITOR_CACHE_DIR}"
