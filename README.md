# brightwaterbog

Archive of the stained glass sculptures at Bright Water Bog.

Photos are digitized on an Epson Perfection V19II scanner, auto-cropped, and
tagged/organized with Claude vision.

## One-shot workflow

Lay one or more photos on the scanner bed, then:

```bash
./digitize.sh                 # 600 dpi color, scan -> crop -> AI tag -> organize
./digitize.sh --dpi 300       # faster, smaller files
./digitize.sh --no-tag        # scan + crop only (no API call)
```

Output: tagged photos land in `photos/<decade>/<category>/`, each with a sidecar
`.json` of metadata (caption, tags, era guess, people, setting, defects).

## Pieces

| Path | What it does |
|------|--------------|
| `scanner/icascan` | Headless CLI driving the scanner via macOS ImageCaptureCore. `icascan list` / `icascan scan --out F --dpi N --color color\|gray`. Build: `swiftc -O scanner/icascan.swift -o scanner/icascan -framework ImageCaptureCore`. |
| `pipeline/crop.py` | OpenCV: finds each photo on the white bed, deskews, writes separate JPEGs. |
| `pipeline/tag.py` | Claude (Opus 4.8) vision tagging → sidecar JSON + `--organize` folder sort. Needs `ANTHROPIC_API_KEY`. |
| `digitize.sh` | Orchestrates scan → crop → tag with scanner process hygiene. |
| `tools/camera_monitor.py` | Standalone local camera wall with direct Eufy control, direct Google Nest signaling, bounded fallback-frame caching, and focused-camera priority. |

## Camera monitor

```bash
cp tools/camera_monitor.example.json tools/camera_monitor.local.json
cp tools/camera_monitor.docker.example.env tools/camera_monitor.docker.local.env
cp tools/eufy-security.example.env tools/eufy-security.local.env
$EDITOR tools/camera_monitor.local.json tools/camera_monitor.docker.local.env tools/eufy-security.local.env
make camera-monitor-docker
```

Open `http://127.0.0.1:8765` or `http://cameras.local`. Tap a camera to expand it
and tap again to return to the grid. The Compose stack owns all three local
services: `eufy-security-ws` controls Eufy P2P sessions, `go2rtc` handles Nest
SDM/WebRTC and remuxes camera media, and `camera-monitor` serves the UI and
bounded cache. The go2rtc API stays private to the Compose network. Browser
signaling uses the monitor's same-origin route, while encrypted WebRTC media
uses the mapped LAN port `8555`. Set `CAMERA_MONITOR_WEBRTC_CANDIDATE` to this
Mac's LAN address and that port.

The low-CPU path never transcodes video. Camera H.264/H.265 remains compressed,
go2rtc packages it for browser playback, and the Mac or iPad performs hardware
decode. Cached JPEGs are captured at a bounded interval and written only when
their content changes. Browser media queues are capped and old segments are
trimmed, so a long-running tab cannot grow memory without bound. Per-camera
starts are serialized and failures use bounded backoff.

Eufy thumbnails are snapshots, not six permanent live streams. While the wall
is visible, at most two cameras wake at once; each is released as soon as the
browser decodes and caches a fresh frame, then the oldest thumbnails go next.
The target refresh age is 20 seconds, with actual timing bounded by each
battery camera's wake latency. Expanding one grants it a renewable 90-second
focus lease: all other Eufy work is released and the selected camera streams
continuously with one-second visual-health checks. A `LIVE` badge requires a
recently decoded frame; transport bytes alone cannot make a frozen image look
live.

When resident warming is enabled, lightweight server-side consumers keep only
selected Nest transports warm without decoding their video. Eufy is never
warmed in the background because its thumbnails require a visible browser to
decode a frame. After 48 hours without a viewer, Nest background streaming
stops; opening the wall wakes it again. Set `CAMERA_MONITOR_WARM_IDLE_HOURS` to
change the window.

Set `"auto_start": false` for a known-offline camera. It will keep showing its
last cached frame without continuously attempting to start. Stale frames remain
visible with their real age while the isolated camera runner retries.

The stack publishes port `8765`, persists its bounded frame cache, and restarts
after a host reboot. `tools/publish_camera_mdns.sh` publishes `cameras.local`
from the Mac's current LAN address. Camera inventory and both credential files
are ignored by git and must never be committed.

## Setup notes

- **Scanner is USB bus-powered** — plugging the cable in turns it on; there's no power button.
- Requires the official **Epson Scan 2** driver installed (provides the macOS ICA driver). SANE/`scanimage` does **not** work with this model.
- Python deps live in `.venv` (opencv, numpy, pillow, anthropic). `ANTHROPIC_API_KEY` must be in the environment for tagging.
- If a scan reports "device busy", a prior `icascan` process is still holding it: `pkill -9 -f "icascan scan"` and retry.

## Tuning the cropper

`crop.py --min-area-frac` sets the smallest blob (as a fraction of the full bed)
counted as a photo. Lower it to catch small prints; raise it to ignore specks.
Documents with whitespace gaps may split into multiple regions — that's expected;
it's tuned for solid photo rectangles.
