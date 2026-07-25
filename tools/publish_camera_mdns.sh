#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
python_bin="/opt/homebrew/bin/python3"
if [[ ! -x "${python_bin}" ]]; then
  python_bin="$(command -v python3)"
fi

primary_interface() {
  /sbin/route -n get default 2>/dev/null |
    /usr/bin/awk '/interface:/{print $2; exit}' || true
}

interface_address() {
  local interface_name="$1"
  if [[ -n "${interface_name}" ]]; then
    /usr/sbin/ipconfig getifaddr "${interface_name}" 2>/dev/null || true
  fi
}

responder_pid=""

stop_responder() {
  if [[ -n "${responder_pid}" ]] && /bin/kill -0 "${responder_pid}" 2>/dev/null; then
    /bin/kill -TERM "${responder_pid}" 2>/dev/null || true
    wait "${responder_pid}" 2>/dev/null || true
  fi
  responder_pid=""
}

shutdown() {
  trap - INT TERM
  stop_responder
  exit 0
}

trap shutdown INT TERM

while true; do
  interface="$(primary_interface)"
  address="$(interface_address "${interface}")"

  if [[ -z "${interface}" || -z "${address}" ]]; then
    /bin/sleep 2
    continue
  fi

  "${python_bin}" \
    "${ROOT_DIR}/tools/camera_mdns_alias.py" \
    --mappings "cameras.local=${address}" \
    --interface-address "${address}" &
  responder_pid=$!

  while /bin/kill -0 "${responder_pid}" 2>/dev/null; do
    /bin/sleep 5
    current_interface="$(primary_interface)"
    current_address="$(interface_address "${current_interface}")"
    if [[ "${current_interface}" != "${interface}" || "${current_address}" != "${address}" ]]; then
      echo "Primary address changed from ${address} to ${current_address:-unavailable}; restarting mDNS publisher"
      stop_responder
      break
    fi
  done

  stop_responder
  /bin/sleep 1
done
