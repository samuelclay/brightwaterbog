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

The production camera wall runs as two local Home Assistant Docker add-ons:
`Brightwater Camera Monitor` serves the wall on host port 80 and
`Brightwater mDNS Alias` publishes `cameras.local`. The monitor reuses Home
Assistant's existing `eufy-security-ws` and `go2rtc` add-ons, so the four Eufy
floodlight entities and the six Eufy cameras share one account/session owner.
It does not contain another Eufy login or another go2rtc instance.

```bash
cp tools/camera_monitor.example.json tools/camera_monitor.local.json
cp tools/camera_monitor.docker.example.env tools/camera_monitor.docker.local.env
cp tools/deploy.example.env tools/deploy.local.env
$EDITOR tools/camera_monitor.local.json tools/camera_monitor.docker.local.env tools/deploy.local.env
make camera-monitor-ha-deploy
```

The ignored Docker environment file supplies the Google/Nest credentials during
deployment. The ignored deploy file selects the Home Assistant SSH host and LAN
address. The deploy script stages both add-ons, builds them on Home Assistant,
applies credentials without writing them to the repository, and enables
automatic startup. Open `http://cameras.local`; tap a camera to expand it and
tap again to return to the grid.

Eufy camera streams use the `camera_eufy_` namespace in shared go2rtc. Home
Assistant's original Eufy stream names are never replaced, which keeps its
camera entities and floodlight controls independent of the wall.

The low-CPU path never transcodes video. Camera H.264/H.265 remains compressed,
go2rtc packages it for browser playback, and the Mac or iPad performs hardware
decode. Cached JPEGs are captured at a bounded interval and written only when
their content changes. Browser media queues are capped and old segments are
trimmed, so a long-running tab cannot grow memory without bound. Per-camera
starts are serialized and failures use bounded backoff.

Eufy thumbnails are snapshots, not six permanent live streams. The Home
Assistant deployment wakes at most one Eufy camera at a time and targets a
five-minute thumbnail refresh. Each stream is released as soon as the browser
decodes and caches a fresh frame, then the oldest thumbnail goes next.
Expanding one grants it a renewable 90-second focus lease: other Eufy work is
released and the selected camera streams continuously with one-second
visual-health checks. A `LIVE` badge requires a recently decoded frame;
transport bytes alone cannot make a frozen image look live.

When resident warming is enabled, lightweight server-side consumers keep only
selected Nest transports warm without decoding their video. Eufy is never
warmed in the background because its thumbnails require a visible browser to
decode a frame. After 48 hours without a viewer, Nest background streaming
stops; opening the wall wakes it again. Set `CAMERA_MONITOR_WARM_IDLE_HOURS` to
change the window.

Set `"auto_start": false` for a known-offline camera. It will keep showing its
last cached frame without continuously attempting to start. Stale frames remain
visible with their real age while the isolated camera runner retries.

The add-ons persist the bounded frame cache and restart after a Home Assistant
reboot. Camera inventory and credential files are ignored by git and must never
be committed.

The root Docker Compose file remains a laptop/development fallback. It is not
part of the cabin production path and must not publish `cameras.local` while the
Home Assistant add-ons are active. Stop it with
`make camera-monitor-docker-stop`.

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
