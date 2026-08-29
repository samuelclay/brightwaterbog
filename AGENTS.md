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

## Website (website/)

Single-page Astro site (`src/pages/index.astro`) for the sculpture trail. `make` in
`website/` (re)starts the dev stack: site on :4321, dev image server on :8788.

### Generated data — `make catalog`, then restart dev

- `src/data/photos.json` and `src/data/trail.json` are generated (and gitignored).
  The site reads only those, never the photo tree or manifests. After ANY change to
  the photos tree, a `_manifest.json`, sculpture frontmatter (GPS, `map:`, folder
  lists), `construction.json`, `drawings.json`, or `MAP_COORDS` in
  `scripts/catalog.mjs`, run `make catalog` in `website/`.
- The Astro dev server does NOT hot-reload the regenerated `photos.json` — run
  `make restart` (or `make`) after `make catalog`, or the browser keeps serving
  the stale photo set.

### Photo tree (`photos/`, content gitignored)

- `photos/scanned/<folder>/` — flatbed scans of old prints and paper drawings.
  Cataloged by directory listing; era `then` (or `construction`/`aerial`/`drawings`
  when referenced via `constructionFolders`/`aerialFolders`/`drawingFolders`).
- `photos/drawings/<folder>/` — born-digital drawings (CAD exports for the mailbox,
  hoopla, etc.), era `drawings-now`. Cataloged by directory listing like `scanned/`,
  so no manifest is needed — but sharp only reads JPG/PNG/WebP, so export CAD to PNG
  rather than dropping in a PDF. Referenced via frontmatter `cadFolders`.
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
- `src/data/drawings.json` (tracked, curated) does the same for drawings — plans,
  sketches, blueprints: a `now` photo becomes `drawings-now`, a `then` scan becomes
  `drawings`. Use it for drawings scanned into a folder alongside that piece's
  photos; use `drawingFolders`/`cadFolders` when the drawings get their own folder.
  It runs after `construction.json`, so a key in both reads as a drawing.
- Strip section order is set by `ERAS` in `PhotoStrip.astro`:
  Now → Construction → Drawings → Aerial → Then → Construction → Drawings.
  Empty sections are dropped, so a piece with no drawings shows no Drawings tab.
- Sculpture pages map to folders via frontmatter `scannedFolders` / `modernFolders`
  in `src/content/sculptures/*.md`. A piece with no modern photos has
  `modernFolders: []` — add the folder name when its first modern photos arrive.
- Folder-name notes: `sculpture_11_welcome_sconce` is the red crystal sconce set
  into the standing stone by the road; `sculpture_15_mailbox` is Samuel's in-progress
  copper/chevron mailbox for that same stone. Both were renamed in Aug 2026 (from
  `sculpture_11_mailbox` / `sculpture_15_stone_mailbox`), so pre-rename prod image
  URLs used the old keys.
  The saguaro cactus is the branching amber floor lamp; the DINOSAUR is the four-point
  rainbow web canopy stretched in the rafters (not a literal dinosaur).
- Photo identity is the path relative to `photos/`; the dev image server resolves
  `GET :8788/img/<key>?w=NNN`. Prod serves pre-rendered WebP from the same keys
  (`/img/<width>/<key>.webp`), baked by `scripts/prerender-images.mjs` during
  `make build` — new photos just need `make catalog` + a redeploy.

### Deploying (`make deploy` → bwb.samuelclay.com)

- `make deploy` in `website/` builds (astro + image ladder) and
  direct-uploads `dist/` to the `brightwaterbog` Cloudflare Pages project on the
  ofbrooklyn account (the same account as cooking.samuelclay.com).
- ALWAYS run wrangler with `--profile ofbrooklyn` for this site. Profiles live in
  `~/.wrangler/config/<name>.toml` (newsblur, ofbrooklyn, tavus); `--profile`
  reads AND writes the named file. NEVER copy a profile toml elsewhere and run
  wrangler against the copy: Cloudflare rotates the OAuth refresh token on every
  use and treats reuse of an old one as theft, revoking the whole grant. If auth
  dies anyway, re-auth with `npx wrangler login --profile ofbrooklyn`.
- `samuelclay.com` DNS is at DNSimple (not Cloudflare) — there is no CF zone, so
  no `/cdn-cgi/image` transforms and no public R2 image serving path; that's why
  the image ladder is baked at build time. Subdomains are plain DNSimple CNAMEs
  to `<project>.pages.dev`. Private R2 is still used as a cloud backup/sync
  target for ignored full-resolution originals.

### Trail minimap

- x/y positions are hand-tuned in `MAP_COORDS` (frontmatter `map: {x,y}` overrides);
  place stops using both latitude and longitude relative to neighboring stops, not
  just path order.

### Photos.app sync

- `make sync-photos` re-exports thumbnail-sized site photos full-size from Photos.app
  (for iCloud-evicted originals), then rebuilds the catalog.

### Original photo cloud sync

- Full-resolution modern originals under `photos/apple-photos-stained-glass/`
  are ignored by git. Keep a Cloudflare R2 copy so a fresh machine can hydrate
  the main photo tree with `make sync-originals-down` from `website/`.
- R2 settings and credentials live in ignored `tools/r2-originals.local.env`;
  copy `tools/r2-originals.example.env` and never print, paste, or commit the
  real R2 account ID, bucket credentials, or secret key.
- `make sync-originals-up` uploads the ignored originals tree to R2 without
  deleting remote-only files. Use `make sync-originals-dry-run` before a first
  upload or any suspicious local change. Deletion requires explicitly running
  `python3 ../tools/r2_originals_sync.py up --delete` from `website/`.
- `make deploy-with-originals` first uploads originals to R2, then runs the
  normal Pages deploy; `make deploy` still only builds the site and uploads the
  baked responsive image ladder to Cloudflare Pages.
