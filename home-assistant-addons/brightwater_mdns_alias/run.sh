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

alias_name="$(option_value alias "cameras.local")"
address="$(option_value address "192.0.2.10")"
mappings="$(option_value mappings "")"
interface_address="$(option_value interface_address "0.0.0.0")"

if [ -n "${mappings}" ]; then
  exec python3 /app/mdns_alias.py \
    --mappings "${mappings}" \
    --interface-address "${interface_address}"
fi

exec python3 /app/mdns_alias.py \
  --alias "${alias_name}" \
  --address "${address}" \
  --interface-address "${interface_address}"
