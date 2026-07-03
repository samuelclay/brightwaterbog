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
| `tools/camera_monitor.py` | Local Home Assistant camera wall. Reads `CABIN_HOME_ASSISTANT_TOKEN`, starts eufy P2P streams on demand, polls Home Assistant snapshot cameras, captures frames from WebRTC-only cameras, and keeps stale frames while retrying. |

## Camera monitor

```bash
cp tools/camera_monitor.example.json tools/camera_monitor.local.json
$EDITOR tools/camera_monitor.local.json
source ~/.zshrc
make camera-monitor
```

Open the printed `http://127.0.0.1:<port>` URL on the extra monitor. The viewer
is a wall-to-wall video grid; tap a camera once to expand it full screen, then
tap it again to return to the grid. Cameras can be marked as best-effort in the
local config, which keeps the last good frame visible and continues retrying
instead of dropping the whole wall. Camera starts are serialized and delayed
slightly after `start_p2p_livestream` so go2rtc has time to register the stream
before Home Assistant asks for frames. Last good frames are cached under
`.cache/camera_monitor/`, so a refresh or viewer restart can immediately show
the last image with the time its visual content last changed.

WebRTC-only cameras are streamed in the browser through Home Assistant's WebRTC
signaling and captured locally to cached JPEG frames every couple seconds. If
Home Assistant or the camera provider rate-limits stream generation, the monitor
backs off before retrying.

The eufy integration depends on a local Home Assistant RTSP/go2rtc relay. That
relay should be running on Home Assistant ports `1984`/`8554`; if those ports
are up but a tile still times out, it is usually a camera/P2P startup issue.
After repeated stale eufy stream kicks with no new frames, the monitor escalates
to a throttled `eufy-security-ws` add-on restart to clear cases where Home
Assistant reports a camera as streaming while the add-on says no livestream is
actually running.

The wall marks a camera as live only when recently received frames have distinct
content. If Home Assistant keeps serving the same frozen image, the visible
frame remains on screen but its badge ages into `STALE` and the stale watchdog
can restart the stream.

### Home Assistant add-on

Deploy the Home Assistant camera wall and its local mDNS alias with:

```bash
cp tools/deploy.example.env tools/deploy.local.env
$EDITOR tools/deploy.local.env
make deploy
```

The local Home Assistant add-on wrapper lives in
`home-assistant-addons/brightwater_camera_monitor/`; Supervisor exposes it as
`local_brightwater_camera_monitor`. The local mDNS alias add-on lives in
`home-assistant-addons/brightwater_mdns_alias/`; Supervisor exposes it as
`local_brightwater_mdns_alias`.

The add-on listens on container port `8765` and maps it to host port `80`, so
the camera wall is available at `http://<home-assistant-ip>/`. The mDNS alias
add-on publishes the configured alias, usually `cameras.local`, as an IPv4-only
alias for that address.

For eufy streams, configure the add-on `ha_url` option to
`http://homeassistant.local.hass.io:8123` and set `ha_token` to a Home Assistant
long-lived access token. The Supervisor proxy works for service calls, but it
does not reliably carry long MJPEG camera streams. The add-on keeps the token in
Home Assistant's local add-on options; do not commit it. Camera inventory lives
in `tools/camera_monitor.local.json`, and deploy settings live in
`tools/deploy.local.env`; both files are ignored because they contain local
network and device details.

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
