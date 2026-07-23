#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
interface="$(/sbin/route -n get default | /usr/bin/awk '/interface:/{print $2; exit}')"
address="$(/usr/sbin/ipconfig getifaddr "${interface}")"

if [[ -z "${interface}" || -z "${address}" ]]; then
  echo "Unable to determine the primary local IPv4 address" >&2
  exit 1
fi

python_bin="/opt/homebrew/bin/python3"
if [[ ! -x "${python_bin}" ]]; then
  python_bin="$(command -v python3)"
fi

exec "${python_bin}" \
  "${ROOT_DIR}/tools/camera_mdns_alias.py" \
  --mappings "cameras.local=${address}" \
  --interface-address "${address}"
