# Agent Notes

## Camera stack

- The production camera monitor runs on the Home Assistant box as the local `brightwater_camera_monitor` and `brightwater_mdns_alias` Docker add-ons in `home-assistant-addons/`.
- Keep camera inventory in ignored `tools/camera_monitor.local.json`; use `tools/camera_monitor.example.json` as the public template.
- Keep Google/Nest credentials in ignored `tools/camera_monitor.docker.local.env`, deployment settings in ignored `tools/deploy.local.env`, and Eufy credentials in ignored `tools/eufy-security.local.env`. Never print, paste, or commit them.
- Home Assistant's existing `eufy-security-ws` add-on is the sole Eufy account and P2P session owner for both cameras and floodlights. Never start the Compose `standalone-eufy` profile for this account.
- The monitor shares Home Assistant's existing `go2rtc` add-on. Monitor-owned Eufy streams use `camera_eufy_` names; never overwrite Home Assistant's serial-number stream names.
- The production profile allows one Eufy viewer slot and targets a five-minute thumbnail refresh to keep station command pressure, CPU, and memory low.
- Publish `cameras.local` only from Home Assistant's `brightwater_mdns_alias` add-on. The laptop LaunchAgent and Compose stack are fallback/development tools and must remain stopped during production.
- Deploy camera-monitor changes with `make camera-monitor-ha-deploy`, then verify all ten live frames, all four Eufy floodlight controls, add-on CPU/memory, Eufy error logs, and go2rtc producer/consumer counts.

## Website (website-fable/)

Single-page Astro site (`src/pages/index.astro`) for the sculpture trail. `make` in
`website-fable/` (re)starts the dev stack: site on :4321, dev image server on :8788.

### Generated data — `make catalog`, then restart dev

- `src/data/photos.json` and `src/data/trail.json` are generated (and gitignored).
  The site reads only those, never the photo tree or manifests. After ANY change to
  the photos tree, a `_manifest.json`, sculpture frontmatter (GPS, `map:`, folder
  lists), `construction.json`, or `MAP_COORDS` in `scripts/catalog.mjs`, run
  `make catalog` in `website-fable/`.
- The Astro dev server does NOT hot-reload the regenerated `photos.json` — run
  `make restart` (or `make`) after `make catalog`, or the browser keeps serving
  the stale photo set.

### Photo tree (`photos/`, content gitignored)

- `photos/scanned/<folder>/` — flatbed scans of old prints. Cataloged by directory
  listing; era `then` (or `construction`/`aerial` when referenced via
  `constructionFolders`/`aerialFolders`).
- `photos/apple-photos-stained-glass/selected/<folder>/` — modern photos, era `now`.
  IMPORTANT: if the folder has a `_manifest.json`, only files with a manifest row
  are cataloged — dropping a JPG in the folder silently does nothing. Append a row
  with at least `filename`, `created` ("YYYY-MM-DD HH:MM:SS", local Eastern time),
  `width`, `height` (display-orientation dims), `latitude`, `longitude`. Existing
  rows come from osxphotos; hand-imported ones use `catalog_source: "desktop_import"`.
- Import convention for one-off photos (e.g. AirDropped to the Desktop): copy into
  the right selected/ folder as `YYYYMMDD_HHMMSS_IMG_NNNN.jpeg`, pull created/GPS/dims
  with `mdls` (its dates are UTC — convert to America/New_York), append manifest rows.
  `exiftool` is not installed; `mdls` gives orientation-corrected pixel dims.
- `src/data/construction.json` (tracked, curated) reclassifies listed photo keys:
  a `now` photo becomes era `construction-now`, a `then` scan becomes `construction`.
  Workshop/build shots belong here.
- Sculpture pages map to folders via frontmatter `scannedFolders` / `modernFolders`
  in `src/content/sculptures/*.md`. A piece with no modern photos has
  `modernFolders: []` — add the folder name when its first modern photos arrive.
- Folder-name gotchas: `sculpture_11_mailbox` is the WELCOME SCONCE (red crystal
  sconce set into the standing stone by the road); `sculpture_15_stone_mailbox` is
  THE MAILBOX (Samuel's in-progress copper/chevron mailbox for that same stone).
  The saguaro cactus is the branching amber floor lamp; the DINOSAUR is the four-point
  rainbow web canopy stretched in the rafters (not a literal dinosaur).
- Photo identity is the path relative to `photos/`; the dev image server resolves
  `GET :8788/img/<key>?w=NNN` and R2 uploads preserve the same key — upload new
  photo keys to R2 when deploying.

### Trail minimap

- x/y positions are hand-tuned in `MAP_COORDS` (frontmatter `map: {x,y}` overrides);
  place stops using both latitude and longitude relative to neighboring stops, not
  just path order.

### Photos.app sync

- `make sync-photos` re-exports thumbnail-sized site photos full-size from Photos.app
  (for iCloud-evicted originals), then rebuilds the catalog.
