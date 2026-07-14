# The Purple Carrot Book

A single-page archive of the family's Purple Carrot recipe-card binder: 131 recipes
digitized from 254 photos taken June 5, 2026 (Photos.app, IMG_9134–9389, minus one
beach photo).

## Viewing

Open `index.html` directly in a browser, or serve the directory:

```sh
python3 -m http.server 8471
# → http://localhost:8471
```

Everything is static: no build step, no dependencies (Google Fonts loads if online,
falls back to system fonts offline).

## Layout

- `index.html` — the whole site (markup, CSS, JS)
- `data/recipes.js` — compiled recipe data (`window.RECIPES`), generated
- `data/pairs.json` — photo pairing manifest, generated
- `data/extracted/NNN.json` — per-recipe OCR extraction (001–127 = card pairs,
  128–131 = standalone single-page recipes found on the back of pairs 15/25/59/102)
- `images/full/` — 2000px images (lightbox), `images/card/` — 900px (in-page)
- `images/hero/NNN.jpg` — the hero food photo cropped out of each front card (grid covers)
- `images/steps/NNN_n.jpg` — each step's process photo cropped out of the back card
- `data/crops/NNN.json` — agent-mapped photo bounding boxes (fractions of image size)
- `assets/favicons/` — favicon candidates; `favicons.html` previews them
- `photos/original/` — untouched exports from Photos.app with JSON sidecars
- `tools/process_images.py` — orient (EXIF + `rotation_fixes.json`), resize, pair
- `tools/build_data.py` — merge extractions + normalize tags → `data/recipes.js`
  (`--tags` prints the tag inventory)

## Regenerating

```sh
python3 tools/process_images.py   # photos/original → images/full+card + data/pairs.json
python3 tools/crop_images.py      # data/crops boxes → images/hero + images/steps
python3 tools/build_data.py       # data/extracted (+hero/step files) → data/recipes.js
```

## Notes

- Pairs 1–83 are the newer portrait binder pages; 84–127 are the older landscape
  cards (era stored per recipe).
- Five images had no EXIF orientation and were rotated via `tools/rotation_fixes.json`.
- Known data quirks are kept in each extraction's `issues` field (e.g. pair 53's
  nutrition line is cut off in the photo; pair 102's front card has no matching
  ingredients/steps because its back was a different recipe).
