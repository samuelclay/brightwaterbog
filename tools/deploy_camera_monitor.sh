#!/usr/bin/env zsh
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
DEPLOY_ENV="${CAMERA_MONITOR_DEPLOY_ENV:-${ROOT_DIR}/tools/deploy.local.env}"
CAMERA_ENV="${CAMERA_MONITOR_DOCKER_ENV:-${ROOT_DIR}/tools/camera_monitor.docker.local.env}"

if [[ -f "${DEPLOY_ENV}" ]]; then
  source "${DEPLOY_ENV}"
fi
if [[ -f "${CAMERA_ENV}" ]]; then
  source "${CAMERA_ENV}"
fi

HA_HOST="${HA_HOST:-cabinha}"
HA_ADDRESS="${CAMERA_MDNS_ADDRESS:-}"
CAMERA_CONFIG_PATH="${CAMERA_MONITOR_CONFIG:-${ROOT_DIR}/tools/camera_monitor.local.json}"
CAMERA_ADDON_SLUG="${CAMERA_MONITOR_ADDON_SLUG:-local_brightwater_camera_monitor}"
CAMERA_ADDON_DIR="${CAMERA_MONITOR_REMOTE_ADDON_DIR:-/addons/brightwater_camera_monitor}"
CAMERA_ADDON_SOURCE="${ROOT_DIR}/home-assistant-addons/brightwater_camera_monitor"
MDNS_ADDON_SLUG="${CAMERA_MDNS_ADDON_SLUG:-local_brightwater_mdns_alias}"
MDNS_ADDON_DIR="${CAMERA_MDNS_REMOTE_ADDON_DIR:-/addons/brightwater_mdns_alias}"
MDNS_ADDON_SOURCE="${ROOT_DIR}/home-assistant-addons/brightwater_mdns_alias"
MDNS_ALIAS="${CAMERA_MDNS_ALIAS:-cameras.local}"
EUFY_VIEWER_SLOTS="${CAMERA_MONITOR_HA_EUFY_VIEWER_SLOTS:-1}"
EUFY_REFRESH_SECONDS="${CAMERA_MONITOR_HA_EUFY_REFRESH_SECONDS:-300}"
WARM_AGENT="${CAMERA_MONITOR_WARM_AGENT_ENABLED:-1}"
WARM_IDLE_HOURS="${CAMERA_MONITOR_WARM_IDLE_HOURS:-48}"

required_files=(
  "${CAMERA_CONFIG_PATH}"
  "${CAMERA_ENV}"
  "${CAMERA_ADDON_SOURCE}/config.yaml"
  "${CAMERA_ADDON_SOURCE}/Dockerfile"
  "${CAMERA_ADDON_SOURCE}/run.sh"
  "${MDNS_ADDON_SOURCE}/config.yaml"
  "${MDNS_ADDON_SOURCE}/Dockerfile"
  "${MDNS_ADDON_SOURCE}/run.sh"
)
for required_file in "${required_files[@]}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "Required deployment file is missing: ${required_file}" >&2
    exit 1
  fi
done

required_values=(
  HA_ADDRESS
  CAMERA_NEST_CLIENT_ID
  CAMERA_NEST_CLIENT_SECRET
  CAMERA_NEST_REFRESH_TOKEN
  CAMERA_NEST_PROJECT_ID
)
for required_name in "${required_values[@]}"; do
  if [[ -z "${(P)required_name:-}" ]]; then
    echo "Required deployment value is missing: ${required_name}" >&2
    exit 1
  fi
done

copy_file() {
  local source_path="$1"
  local destination_path="$2"
  scp -q "${source_path}" "${HA_HOST}:${destination_path}"
}

addon_installed() {
  local slug="$1"
  ssh -o BatchMode=yes "${HA_HOST}" \
    "ha apps --raw-json | jq -e --arg slug '${slug}' '.data.addons[] | select(.slug == \$slug)' >/dev/null"
}

build_or_install_addon() {
  local slug="$1"
  if addon_installed "${slug}"; then
    if ! ssh -o BatchMode=yes "${HA_HOST}" \
      "ha apps rebuild '${slug}' --force --no-progress >/dev/null 2>&1"; then
      ssh -o BatchMode=yes "${HA_HOST}" \
        "ha apps update '${slug}' --no-progress >/dev/null"
      local attempt
      local versions
      for attempt in {1..90}; do
        versions="$(
          ssh -o BatchMode=yes "${HA_HOST}" \
            "ha apps info '${slug}' --raw-json | jq -r '[.data.version, .data.version_latest] | @tsv'"
        )"
        if [[ "${versions%%$'\t'*}" == "${versions#*$'\t'}" ]]; then
          return
        fi
        sleep 1
      done
      echo "Timed out waiting for Home Assistant to update ${slug}" >&2
      exit 1
    fi
  else
    ssh -o BatchMode=yes "${HA_HOST}" \
      "ha apps install '${slug}' --no-progress >/dev/null"
  fi
}

reset_job_conditions() {
  ssh -o BatchMode=yes "${HA_HOST}" \
    'ha jobs reset --no-progress >/dev/null 2>&1 || true'
}

trap reset_job_conditions EXIT

echo "Staging optimized camera monitor on Home Assistant"
ssh -o BatchMode=yes "${HA_HOST}" \
  "mkdir -p '${CAMERA_ADDON_DIR}' '${MDNS_ADDON_DIR}'"
copy_file "${CAMERA_ADDON_SOURCE}/config.yaml" "${CAMERA_ADDON_DIR}/config.yaml"
copy_file "${CAMERA_ADDON_SOURCE}/Dockerfile" "${CAMERA_ADDON_DIR}/Dockerfile"
copy_file "${CAMERA_ADDON_SOURCE}/run.sh" "${CAMERA_ADDON_DIR}/run.sh"
copy_file "${ROOT_DIR}/tools/camera_monitor.py" "${CAMERA_ADDON_DIR}/camera_monitor.py"
copy_file "${ROOT_DIR}/tools/camera_backends.py" "${CAMERA_ADDON_DIR}/camera_backends.py"
copy_file "${ROOT_DIR}/tools/camera_warm_agent.py" "${CAMERA_ADDON_DIR}/camera_warm_agent.py"
copy_file "${CAMERA_CONFIG_PATH}" "${CAMERA_ADDON_DIR}/camera_monitor.local.json"

echo "Staging Home Assistant mDNS publisher"
copy_file "${MDNS_ADDON_SOURCE}/config.yaml" "${MDNS_ADDON_DIR}/config.yaml"
copy_file "${MDNS_ADDON_SOURCE}/Dockerfile" "${MDNS_ADDON_DIR}/Dockerfile"
copy_file "${MDNS_ADDON_SOURCE}/run.sh" "${MDNS_ADDON_DIR}/run.sh"
copy_file "${ROOT_DIR}/tools/camera_mdns_alias.py" "${MDNS_ADDON_DIR}/mdns_alias.py"

ssh -o BatchMode=yes "${HA_HOST}" 'ha store reload --no-progress >/dev/null'
ssh -o BatchMode=yes "${HA_HOST}" \
  'ha jobs options --ignore-conditions internet_host --no-progress >/dev/null'

echo "Building Home Assistant Docker add-ons"
build_or_install_addon "${CAMERA_ADDON_SLUG}"
build_or_install_addon "${MDNS_ADDON_SLUG}"
reset_job_conditions
trap - EXIT

echo "Applying camera monitor options"
CAMERA_DEPLOY_ADDRESS="${HA_ADDRESS}" \
CAMERA_DEPLOY_NEST_CLIENT_ID="${CAMERA_NEST_CLIENT_ID}" \
CAMERA_DEPLOY_NEST_CLIENT_SECRET="${CAMERA_NEST_CLIENT_SECRET}" \
CAMERA_DEPLOY_NEST_REFRESH_TOKEN="${CAMERA_NEST_REFRESH_TOKEN}" \
CAMERA_DEPLOY_NEST_PROJECT_ID="${CAMERA_NEST_PROJECT_ID}" \
CAMERA_DEPLOY_EUFY_VIEWER_SLOTS="${EUFY_VIEWER_SLOTS}" \
CAMERA_DEPLOY_EUFY_REFRESH_SECONDS="${EUFY_REFRESH_SECONDS}" \
CAMERA_DEPLOY_WARM_AGENT="${WARM_AGENT}" \
CAMERA_DEPLOY_WARM_IDLE_HOURS="${WARM_IDLE_HOURS}" \
python3 - <<'PY' | ssh -o BatchMode=yes "${HA_HOST}" \
  "curl -fsS -X POST -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" -H 'Content-Type: application/json' --data-binary @- http://supervisor/addons/${CAMERA_ADDON_SLUG}/options >/dev/null"
import json
import os

address = os.environ["CAMERA_DEPLOY_ADDRESS"]
print(json.dumps({
    "boot": "auto",
    "watchdog": True,
    "options": {
        "eufy_ws_url": f"ws://{address}:3300",
        "go2rtc_url": f"http://{address}:1984",
        "nest_client_id": os.environ["CAMERA_DEPLOY_NEST_CLIENT_ID"],
        "nest_client_secret": os.environ["CAMERA_DEPLOY_NEST_CLIENT_SECRET"],
        "nest_refresh_token": os.environ["CAMERA_DEPLOY_NEST_REFRESH_TOKEN"],
        "nest_project_id": os.environ["CAMERA_DEPLOY_NEST_PROJECT_ID"],
        "eufy_viewer_slots": int(os.environ["CAMERA_DEPLOY_EUFY_VIEWER_SLOTS"]),
        "eufy_thumbnail_refresh_seconds": int(
            os.environ["CAMERA_DEPLOY_EUFY_REFRESH_SECONDS"]
        ),
        "warm_agent": os.environ["CAMERA_DEPLOY_WARM_AGENT"] == "1",
        "warm_idle_hours": int(os.environ["CAMERA_DEPLOY_WARM_IDLE_HOURS"]),
    },
}))
PY

echo "Applying cameras.local options"
CAMERA_DEPLOY_ADDRESS="${HA_ADDRESS}" \
CAMERA_DEPLOY_MDNS_ALIAS="${MDNS_ALIAS}" \
python3 - <<'PY' | ssh -o BatchMode=yes "${HA_HOST}" \
  "curl -fsS -X POST -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" -H 'Content-Type: application/json' --data-binary @- http://supervisor/addons/${MDNS_ADDON_SLUG}/options >/dev/null"
import json
import os

print(json.dumps({
    "boot": "auto",
    "options": {
        "alias": os.environ["CAMERA_DEPLOY_MDNS_ALIAS"],
        "address": os.environ["CAMERA_DEPLOY_ADDRESS"],
        "mappings": "",
        "interface_address": os.environ["CAMERA_DEPLOY_ADDRESS"],
    },
}))
PY

echo "Starting Home Assistant camera services"
ssh -o BatchMode=yes "${HA_HOST}" \
  "ha apps restart '${CAMERA_ADDON_SLUG}' --no-progress >/dev/null || ha apps start '${CAMERA_ADDON_SLUG}' --no-progress >/dev/null"
ssh -o BatchMode=yes "${HA_HOST}" \
  "ha apps restart '${MDNS_ADDON_SLUG}' --no-progress >/dev/null || ha apps start '${MDNS_ADDON_SLUG}' --no-progress >/dev/null"

echo "Camera monitor deployed to Home Assistant at http://${HA_ADDRESS}/"
