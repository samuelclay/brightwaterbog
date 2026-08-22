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
| `tools/r2_originals_sync.py` | Syncs ignored full-resolution modern photo originals with Cloudflare R2 via the AWS CLI. |

## Website photo originals

The modern site originals live in ignored
`photos/apple-photos-stained-glass/`. The generated Astro catalog and the
deployed responsive WebP ladder use those files, but git does not store the
full-resolution originals.

To hydrate or back up that tree with Cloudflare R2:

```bash
cp tools/r2-originals.example.env tools/r2-originals.local.env
$EDITOR tools/r2-originals.local.env
cd website-fable
make sync-originals-down     # download originals from R2, then rebuild catalog
make sync-originals-dry-run  # preview upload delta
make sync-originals-up       # upload originals to R2
make deploy-with-originals   # upload originals, build image ladder, deploy Pages
```

The local env file contains the R2 account and access keys; keep it out of git.

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
automatic startup. It also enables Home Assistant's watchdog for the shared
go2rtc add-on, so a crashed transport service is restarted without waiting for
a viewer. Open `http://cameras.local`; tap a camera to expand it and tap again
to return to the grid.

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
five-minute thumbnail refresh. A lightweight resident agent captures a JPEG
from shared go2rtc and writes it into the monitor cache, so refreshes continue
without an open browser. Each stream is released as soon as a fresh frame is
cached, then the oldest thumbnail goes next. Cameras with fewer failed attempts
are tried first, preventing one persistently offline camera from starving the
rest of the rotation. After three failed thumbnail starts, background attempts
drop to once an hour so an unhealthy station cannot fill Eufy's command queue;
explicitly opening that camera still tries it immediately.
Expanding one grants it a renewable 90-second focus lease: other Eufy work is
released and the selected camera streams continuously with one-second
visual-health checks. A `LIVE` badge requires a recently decoded frame;
transport bytes alone cannot make a frozen image look live.

The production add-on also runs a bounded Eufy recovery circuit breaker while
either the wall or resident refresh agent is active. It uses the last received
frame—not merely the last visibly changed frame—and repeated refresh failures.
Two cameras stuck for 15 minutes, or one camera stuck for 30 minutes, trigger a
controlled restart of only `eufy-security-ws`. The monitor pauses camera work,
waits for a genuinely new Eufy socket connection, and then retries the affected
frames. Restarts have a one-hour cooldown. Unattended warming remains capped at
two restarts per day and quarantines a camera that still fails, but an open
camera wall bypasses that quarantine and keeps retrying once per hour (up to the
cooldown-imposed maximum of 24 times per day). A fresh frame releases the
quarantine immediately. Eufy restarts are suspended while shared go2rtc is
unavailable, preventing a transport outage from wasting the Eufy recovery
budget.

When resident warming is enabled, lightweight server-side consumers keep
selected Nest transports warm without decoding their video and rotate through
Eufy snapshots one at a time. After 48 hours without a viewer, all background
camera work stops; opening the wall wakes it again. Set
`CAMERA_MONITOR_WARM_IDLE_HOURS` to change the window.

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
