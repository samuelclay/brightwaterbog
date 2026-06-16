#!/usr/bin/env bash
# Digitize one flatbed of photos end to end:
#   scan (icascan) -> auto-crop each photo (crop.py) -> AI tag + organize (tag.py)
#
# Usage:
#   ./digitize.sh [--dpi 600] [--color color|gray] [--no-tag]
#
# Requires: scanner/icascan built, .venv set up, ANTHROPIC_API_KEY for tagging.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DPI=600
COLOR=color
DO_TAG=1

while [ $# -gt 0 ]; do
  case "$1" in
    --dpi) DPI="$2"; shift 2 ;;
    --color) COLOR="$2"; shift 2 ;;
    --no-tag) DO_TAG=0; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
SCAN="$ROOT/captures/scan_$STAMP.tiff"
STAGING="$ROOT/photos/_staging/$STAMP"
PY="$ROOT/.venv/bin/python"

# Ensure no orphaned scan process holds the device (see scanner-busy-gotcha).
cleanup() { pkill -9 -f "icascan scan" 2>/dev/null || true; }
trap cleanup EXIT
pkill -9 -f "icascan scan" 2>/dev/null || true

echo "[1/3] Scanning bed at ${DPI}dpi ($COLOR) -> $SCAN"
"$ROOT/scanner/icascan" scan --out "$SCAN" --dpi "$DPI" --color "$COLOR" --timeout 180
[ -f "$SCAN" ] || { echo "scan failed: no file produced" >&2; exit 1; }

echo "[2/3] Cropping photos -> $STAGING"
mkdir -p "$STAGING"
CROPS="$("$PY" "$ROOT/pipeline/crop.py" "$SCAN" "$STAGING")"
echo "$CROPS"

if [ "$DO_TAG" = "1" ]; then
  if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "[3/3] SKIPPED tagging: ANTHROPIC_API_KEY not set." >&2
    echo "      Crops are in $STAGING. Set the key and run:" >&2
    echo "      $PY pipeline/tag.py --organize photos $STAGING/*.jpg" >&2
    exit 0
  fi
  echo "[3/3] Tagging + organizing into photos/<decade>/<category>/"
  # shellcheck disable=SC2086
  "$PY" "$ROOT/pipeline/tag.py" --organize "$ROOT/photos" $CROPS
else
  echo "[3/3] Tagging disabled (--no-tag). Crops in $STAGING."
fi

echo "Done."
