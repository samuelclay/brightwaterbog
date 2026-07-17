#!/usr/bin/env zsh
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
LOCAL_ENV="${CAMERA_MONITOR_DEPLOY_ENV:-${ROOT_DIR}/tools/deploy.local.env}"
if [[ -f "${LOCAL_ENV}" ]]; then
  source "${LOCAL_ENV}"
fi

HA_HOST="${HA_HOST:-homeassistant}"
ADDON_SLUG="${CAMERA_MONITOR_ADDON_SLUG:-local_brightwater_camera_monitor}"
REMOTE_ADDON_DIR="${CAMERA_MONITOR_REMOTE_ADDON_DIR:-/addons/brightwater_camera_monitor}"
ADDON_SRC_DIR="${ROOT_DIR}/home-assistant-addons/brightwater_camera_monitor"
HA_URL="${CAMERA_MONITOR_HA_URL:-http://homeassistant.local.hass.io:8123}"
CAMERA_CONFIG_PATH="${CAMERA_MONITOR_CONFIG:-${ROOT_DIR}/tools/camera_monitor.local.json}"
MDNS_ADDON_SLUG="${CAMERA_MDNS_ADDON_SLUG:-local_brightwater_mdns_alias}"
MDNS_REMOTE_ADDON_DIR="${CAMERA_MDNS_REMOTE_ADDON_DIR:-/addons/brightwater_mdns_alias}"
MDNS_ADDON_SRC_DIR="${ROOT_DIR}/home-assistant-addons/brightwater_mdns_alias"
MDNS_ALIAS="${CAMERA_MDNS_ALIAS:-cameras.local}"
MDNS_ADDRESS="${CAMERA_MDNS_ADDRESS:-}"

if [[ ! -f "${CAMERA_CONFIG_PATH}" ]]; then
  echo "Camera config not found: ${CAMERA_CONFIG_PATH}" >&2
  echo "Copy tools/camera_monitor.example.json to tools/camera_monitor.local.json and fill in local camera details." >&2
  exit 1
fi

if [[ -z "${MDNS_ADDRESS}" ]]; then
  echo "CAMERA_MDNS_ADDRESS is not set; add it to tools/deploy.local.env or export it." >&2
  exit 1
fi

if [[ -z "${CABIN_HOME_ASSISTANT_TOKEN:-}" ]]; then
  loaded_token="$(zsh -lc 'source ~/.zshrc >/dev/null 2>&1 || true; print -rn -- ${CABIN_HOME_ASSISTANT_TOKEN-}' 2>/dev/null || true)"
  if [[ -n "${loaded_token}" ]]; then
    export CABIN_HOME_ASSISTANT_TOKEN="${loaded_token}"
  fi
fi

if [[ -z "${CABIN_HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "CABIN_HOME_ASSISTANT_TOKEN is not set; source ~/.zshrc or export it before deploy." >&2
  exit 1
fi

reset_job_conditions() {
  ssh -o BatchMode=yes "${HA_HOST}" 'ha jobs reset --no-progress >/dev/null 2>&1 || true'
}

copy_file() {
  local source_path="$1"
  local dest_path="$2"
  scp "${source_path}" "${HA_HOST}:${dest_path}" >/dev/null
}

build_or_install_addon() {
  local slug="$1"
  if ssh -o BatchMode=yes "${HA_HOST}" "ha apps info '${slug}' --raw-json | jq -e '.data.version != null' >/dev/null 2>&1"; then
    local versions
    versions="$(
      ssh -o BatchMode=yes "${HA_HOST}" \
        "ha apps info '${slug}' --raw-json | jq -r '[.data.version, .data.version_latest] | @tsv'"
    )"
    local installed_version="${versions%%$'\t'*}"
    local available_version="${versions#*$'\t'}"
    if [[ -n "${available_version}" && "${installed_version}" != "${available_version}" ]]; then
      ssh -o BatchMode=yes "${HA_HOST}" "ha apps update '${slug}' --no-progress >/dev/null"
    else
      ssh -o BatchMode=yes "${HA_HOST}" "ha apps rebuild '${slug}' --force --no-progress >/dev/null"
    fi
  else
    ssh -o BatchMode=yes "${HA_HOST}" "ha apps install '${slug}' --no-progress >/dev/null"
  fi
}

trap reset_job_conditions EXIT

echo "Deploying camera monitor add-on to ${HA_HOST}:${REMOTE_ADDON_DIR}"
ssh -o BatchMode=yes "${HA_HOST}" "mkdir -p '${REMOTE_ADDON_DIR}'"
copy_file "${ADDON_SRC_DIR}/config.yaml" "${REMOTE_ADDON_DIR}/config.yaml"
copy_file "${ADDON_SRC_DIR}/Dockerfile" "${REMOTE_ADDON_DIR}/Dockerfile"
copy_file "${ADDON_SRC_DIR}/run.sh" "${REMOTE_ADDON_DIR}/run.sh"
copy_file "${ROOT_DIR}/tools/camera_monitor.py" "${REMOTE_ADDON_DIR}/camera_monitor.py"
copy_file "${ROOT_DIR}/tools/camera_warm_agent.py" "${REMOTE_ADDON_DIR}/camera_warm_agent.py"
copy_file "${CAMERA_CONFIG_PATH}" "${REMOTE_ADDON_DIR}/camera_monitor.local.json"

echo "Deploying mDNS alias add-on to ${HA_HOST}:${MDNS_REMOTE_ADDON_DIR}"
ssh -o BatchMode=yes "${HA_HOST}" "mkdir -p '${MDNS_REMOTE_ADDON_DIR}'"
copy_file "${MDNS_ADDON_SRC_DIR}/config.yaml" "${MDNS_REMOTE_ADDON_DIR}/config.yaml"
copy_file "${MDNS_ADDON_SRC_DIR}/Dockerfile" "${MDNS_REMOTE_ADDON_DIR}/Dockerfile"
copy_file "${MDNS_ADDON_SRC_DIR}/run.sh" "${MDNS_REMOTE_ADDON_DIR}/run.sh"
copy_file "${MDNS_ADDON_SRC_DIR}/mdns_alias.py" "${MDNS_REMOTE_ADDON_DIR}/mdns_alias.py"

echo "Reloading Home Assistant local add-on store"
ssh -o BatchMode=yes "${HA_HOST}" 'ha store reload --no-progress >/dev/null'

echo "Building Home Assistant add-on image"
ssh -o BatchMode=yes "${HA_HOST}" 'ha jobs options --ignore-conditions internet_host --no-progress >/dev/null'
build_or_install_addon "${ADDON_SLUG}"
build_or_install_addon "${MDNS_ADDON_SLUG}"
reset_job_conditions
trap - EXIT

echo "Updating add-on options"
CAMERA_MONITOR_DEPLOY_HA_URL="${HA_URL}" \
CAMERA_MONITOR_DEPLOY_HA_TOKEN="${CABIN_HOME_ASSISTANT_TOKEN}" \
CAMERA_MONITOR_DEPLOY_WARM_AGENT="${CAMERA_MONITOR_WARM_AGENT:-0}" \
python3 - <<'PY' | ssh -o BatchMode=yes "${HA_HOST}" "curl -fsS -X POST -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" -H \"Content-Type: application/json\" -d @- http://supervisor/addons/${ADDON_SLUG}/options >/dev/null"
import json
import os

print(json.dumps({
    "watchdog": True,
    "options": {
        "ha_url": os.environ["CAMERA_MONITOR_DEPLOY_HA_URL"],
        "ha_token": os.environ["CAMERA_MONITOR_DEPLOY_HA_TOKEN"],
        "warm_agent": os.environ["CAMERA_MONITOR_DEPLOY_WARM_AGENT"] == "1",
    }
}))
PY

echo "Updating mDNS alias options"
CAMERA_MDNS_DEPLOY_ALIAS="${MDNS_ALIAS}" \
CAMERA_MDNS_DEPLOY_ADDRESS="${MDNS_ADDRESS}" \
python3 - <<'PY' | ssh -o BatchMode=yes "${HA_HOST}" "curl -fsS -X POST -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" -H \"Content-Type: application/json\" -d @- http://supervisor/addons/${MDNS_ADDON_SLUG}/options >/dev/null"
import json
import os

print(json.dumps({
    "options": {
        "alias": os.environ["CAMERA_MDNS_DEPLOY_ALIAS"],
        "address": os.environ["CAMERA_MDNS_DEPLOY_ADDRESS"],
    }
}))
PY

echo "Restarting camera monitor add-on"
ssh -o BatchMode=yes "${HA_HOST}" "ha apps restart '${ADDON_SLUG}' --no-progress >/dev/null || ha apps start '${ADDON_SLUG}' --no-progress >/dev/null"

echo "Restarting mDNS alias add-on"
ssh -o BatchMode=yes "${HA_HOST}" "ha apps restart '${MDNS_ADDON_SLUG}' --no-progress >/dev/null || ha apps start '${MDNS_ADDON_SLUG}' --no-progress >/dev/null"

echo "Deployed ${ADDON_SLUG} at http://${MDNS_ADDRESS}/"
echo "Published ${MDNS_ALIAS} -> ${MDNS_ADDRESS}"
