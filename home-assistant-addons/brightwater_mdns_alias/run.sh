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
print(value if value else default)
PY
}

ALIAS="$(option_value alias "cameras.local")"
ADDRESS="$(option_value address "192.0.2.10")"
MAPPINGS="$(option_value mappings "")"
INTERFACE_ADDRESS="$(option_value interface_address "0.0.0.0")"

if [ -n "${MAPPINGS}" ]; then
  exec python3 /app/mdns_alias.py \
    --mappings "${MAPPINGS}" \
    --interface-address "${INTERFACE_ADDRESS}"
fi

exec python3 /app/mdns_alias.py \
  --alias "${ALIAS}" \
  --address "${ADDRESS}" \
  --interface-address "${INTERFACE_ADDRESS}"
