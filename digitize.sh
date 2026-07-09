#!/usr/bin/env bash
# Digitize one flatbed of photos end to end:
#   scan -> auto-crop each photo (crop.py) -> AI tag + organize (tag.py)
#
# Usage:
#   ./digitize.sh [--dpi 600] [--color color|gray] [--no-tag]
#
# Photo-correction defaults can be overridden per scan:
#   SCAN_COLOR_RESTORATION=off SCAN_BACKLIGHT=off ./digitize.sh --no-tag
#
# Requires: scanner/epsonscan2 built, .venv set up, ANTHROPIC_API_KEY for tagging.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DPI=600
COLOR=color
DO_TAG=1
SCAN_BACKEND="${SCAN_BACKEND:-epson2}"
SCAN_BRIGHTNESS="${SCAN_BRIGHTNESS:-0}"
SCAN_CONTRAST="${SCAN_CONTRAST:-0}"
SCAN_SATURATION="${SCAN_SATURATION:-0}"
SCAN_EPSON_UNSHARP="${SCAN_EPSON_UNSHARP:-0}"
SCAN_EPSON_DESCREEN="${SCAN_EPSON_DESCREEN:-0}"
SCAN_ROTATE="${SCAN_ROTATE:-270}"
SCAN_COLOR_RESTORATION="${SCAN_COLOR_RESTORATION:-on}"
SCAN_BACKLIGHT="${SCAN_BACKLIGHT:-middle}"
SCAN_UNSHARP="${SCAN_UNSHARP:-}"
SCAN_DESCREEN="${SCAN_DESCREEN:-}"
SCAN_DUST_REMOVAL="${SCAN_DUST_REMOVAL:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dpi) DPI="$2"; shift 2 ;;
    --color) COLOR="$2"; shift 2 ;;
    --no-tag) DO_TAG=0; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
if [ "$SCAN_BACKEND" = "epson2" ]; then
  SCAN="$ROOT/captures/scan_$STAMP.jpg"
else
  SCAN="$ROOT/captures/scan_$STAMP.tiff"
fi
STAGING="$ROOT/photos/_staging/$STAMP"
PY="$ROOT/.venv/bin/python"

# Ensure no orphaned scan process holds the device (see scanner-busy-gotcha).
cleanup() { pkill -9 -f "icascan scan" 2>/dev/null || true; }
trap cleanup EXIT
pkill -9 -f "icascan scan" 2>/dev/null || true

echo "[1/3] Scanning bed at ${DPI}dpi ($COLOR, backend=$SCAN_BACKEND) -> $SCAN"
if [ "$SCAN_BACKEND" = "epson2" ]; then
  "$ROOT/scanner/epsonscan2" scan \
    --out "$SCAN" \
    --dpi "$DPI" \
    --brightness "$SCAN_BRIGHTNESS" \
    --contrast "$SCAN_CONTRAST" \
    --saturation "$SCAN_SATURATION" \
    --unsharp "$SCAN_EPSON_UNSHARP" \
    --descreen "$SCAN_EPSON_DESCREEN"
elif [ "$SCAN_BACKEND" = "ica" ]; then
  SCAN_ARGS=(scan --out "$SCAN" --dpi "$DPI" --color "$COLOR" --timeout 180)
  if [ -n "$SCAN_COLOR_RESTORATION" ]; then
    SCAN_ARGS+=(--color-restoration "$SCAN_COLOR_RESTORATION")
  fi
  if [ -n "$SCAN_BACKLIGHT" ]; then
    SCAN_ARGS+=(--backlight "$SCAN_BACKLIGHT")
  fi
  if [ -n "$SCAN_UNSHARP" ]; then
    SCAN_ARGS+=(--unsharp "$SCAN_UNSHARP")
  fi
  if [ -n "$SCAN_DESCREEN" ]; then
    SCAN_ARGS+=(--descreen "$SCAN_DESCREEN")
  fi
  if [ -n "$SCAN_DUST_REMOVAL" ]; then
    SCAN_ARGS+=(--dust-removal "$SCAN_DUST_REMOVAL")
  fi
  set +e
  "$ROOT/scanner/icascan" "${SCAN_ARGS[@]}"
  SCAN_STATUS=$?
  set -e
  if [ "$SCAN_STATUS" -ne 0 ]; then
    if [ -s "$SCAN" ]; then
      echo "WARN: scanner exited with status $SCAN_STATUS after writing $SCAN; continuing." >&2
    else
      echo "scan failed: scanner exited with status $SCAN_STATUS" >&2
      exit "$SCAN_STATUS"
    fi
  fi
else
  echo "unknown SCAN_BACKEND=$SCAN_BACKEND (expected epson2 or ica)" >&2
  exit 2
fi
[ -f "$SCAN" ] || { echo "scan failed: no file produced" >&2; exit 1; }

echo "[2/3] Cropping photos -> $STAGING"
mkdir -p "$STAGING"
CROPS="$("$PY" "$ROOT/pipeline/crop.py" "$SCAN" "$STAGING" --rotate "$SCAN_ROTATE")"
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
