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

## Cooking site (cooking/ → cooking.samuelclay.com)

- Static single page (`index.html`) over two generated lists: `data/recipes.js` (the
  131 Purple Carrot cards, from `data/extracted/` OCR) and `data/nyt_recipes.js` (the
  recipes saved in Samuel's NYT Cooking recipe box, tagged "New York Times"). Same row
  shape; `pair` is the card number or the NYT recipe id. The site README documents
  the pipeline (`make nyt`, `tools/fetch_nyt.py`, `tools/build_nyt.py`).
- New NYT saves: add `<id>-<slug>` lines to `data/nyt/recipes.txt` (from the recipe
  box's `/recipes/...` links; bare ids 404), run `make nyt`, tag them with subagents
  per `data/nyt/tagging_instructions.md` (`build_nyt.py --tagging-input` writes the
  batches), then rebuild. Raw NYT page data is gitignored; the tags are tracked.
- The family is vegetarian: saved recipes built on meat or fish are listed in
  `data/nyt/excluded.txt` (with a reason) and skipped by the build. Check new saves
  for meat, fish, fish sauce, bonito, and meat-based fats before adding them.
- The NYT rows carry full recipe text and photos, and both the repo and the deployed
  site are public.

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
  CAVEAT: that UTC rule only holds for videos. For JPGs, `mdls` re-reads the
  naive EXIF time in the MACHINE's timezone, so on a laptop not set to Eastern
  the date comes back shifted (3h off on Pacific). Read EXIF directly instead —
  `.venv/bin/python` has Pillow, and `DateTimeOriginal` (0x9003) is already
  local Eastern time.
- Video clips live in the same selected/ folders and ride the photo pipeline via
  a poster still: the manifest row's `filename` is a `.jpg` frame (taken a third
  of the way in — the opening frame is often black), plus `video: "<same
  stem>.mp4"`. catalog.mjs copies `video` onto the entry, PhotoStrip renders a
  muted looping `<video>` in place of the `<img>`, and the lightbox swaps in
  `[data-lightbox-video]`. Clips sort to the FRONT of their Now section
  (`clipsFirst` in catalog.mjs), ahead of pins.json.
- Encode clips as BOOMERANGS — forward, then the reverse minus its first frame —
  so the file ends on the frame it began on and plain `loop` ping-pongs instead
  of cutting. Handheld footage drifts, so a straight loop is visibly jarring:
    ffmpeg -i in.MOV -filter_complex "[0:v]scale='min(1280,iw)':'min(1280,ih)':\
      force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,split[a][b];\
      [b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];[a][r]concat=n=2:v=1[out]" \
      -map "[out]" -c:v libx264 -profile:v high -crf 26 -preset slow \
      -pix_fmt yuv420p -movflags +faststart -an out.mp4
  Always re-encode from the original MOV, never the delivered MP4. `-an` matters:
  muted autoplay is a browser requirement, so audio is pure weight. The `reverse`
  filter buffers every frame in RAM — scale before reversing, and run clips
  sequentially.
- MP4s are NOT laddered. `videoUrl()` serves them raw: the dev server's `/video/`
  route (with byte ranges — Safari won't play without them), and in prod
  `dist/video/<key>`, copied there by scripts/prerender-images.mjs.
- Strip clips carry no `src` until they scroll into view (islands/strip-video.ts)
  — 22 clips is ~76MB, so eager loading would be brutal. They also pause when
  scrolled away or the tab is hidden, and honor prefers-reduced-motion by
  staying on the poster.
- A manifest row whose `filename` is a `.mov` is silently skipped by catalog.mjs
  (that's how raw clips sometimes land from another machine). To publish one:
  boomerang-encode it with the ffmpeg recipe above, pull the poster from the
  ENCODED mp4 at one third of the original duration (`-ss dur/3 -frames:v 1`,
  so dims/orientation match), then rewrite that row in place — `filename:` the
  poster .jpg, plus `video:`, `duration:`, `video_loop: "boomerang"`, and jpg
  width/height/bytes (copy the shape of an existing video row). Keep the .mov
  in the folder as the re-encode source; non-manifest files are never cataloged.
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
  dies anyway, re-auth with `npx wrangler@latest auth create ofbrooklyn` (wrangler ≥4.127
  rejects `login --profile`; `auth create <name>` re-authenticates a named profile).
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
- The sync script shells out to the `aws` CLI (`brew install awscli`). One-time
  account setup (done 2026-08-30): R2 enabled on the ofbrooklyn account, bucket
  created with `wrangler r2 bucket create brightwaterbog-originals --profile
  ofbrooklyn`, and an API token (dashboard → R2 → Manage R2 API Tokens, "Object
  Read & Write" scoped to that bucket) pasted into the local env. Needs global
  wrangler ≥4.12x for the `--profile` flag (`wrangler whoami` refuses the flag
  and only reports the active profile).
- If the sync dies with `SSLV3_ALERT_HANDSHAKE_FAILURE` from
  `<account>.r2.cloudflarestorage.com`, that is NOT an aws/python problem: the
  S3 endpoint's TLS doesn't exist until R2 is enabled, and lags enablement by
  some minutes. Wait and retry before debugging anything local.
- Fallback while R2 creds aren't on a machine: the ignored photo tree diverges
  per machine (claymac-studio is where imports happen — commits can reference
  keys that exist only there, silently emptying stops elsewhere). Merge over
  the tailnet with `rsync -a` between the `photos/apple-photos-stained-glass/`
  trees — dry-run (`-n --itemize-changes`) BOTH directions first, never
  `--delete` — then `make catalog`.
