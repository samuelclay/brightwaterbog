# The Cooking Book

A single-page archive of the family's Purple Carrot recipe-card binder: 131 recipes
digitized from 254 photos taken June 5, 2026 (Photos.app, IMG_9134–9389, minus one
beach photo) — plus the 224 recipes saved in Samuel's NYT Cooking recipe box, which sit
in the same grid tagged "New York Times" (see below).

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
- `data/nyt_recipes.js` — the NYT Cooking recipes (`window.NYT_RECIPES`), generated;
  `index.html` concatenates both lists
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

## NYT Cooking recipes

The saved recipes from Samuel's NYT Cooking recipe box use the same row shape as the
binder cards, so filters, search, the servings scaler, the lightbox and `/r/<id>` links
all work unchanged. A "Source" filter (New York Times / Purple Carrot), a filled source chip leading
every card's tag row (orange for the binder, slate blue for NYT), and a byline in the
expanded view are the only things that set them apart.

- `data/nyt/recipes.txt` — one `<id>-<slug>` per line in recipe-box order (newest save
  first). The recipe box pages at `cooking.nytimes.com/recipe-box?page=N` (48 per page)
  link to `/recipes/<id>-<slug>`; add new saves at the top of the file.
- `data/nyt/excluded.txt` — ids to leave out (the family is vegetarian, so saved
  recipes built on meat or fish go here with a note). `build_nyt.py` and
  `fetch_nyt_images.py` skip them; `recipes.txt` stays a plain mirror of the box.
- `tools/fetch_nyt.py` — fetches each page once and stores its schema.org JSON-LD plus
  NYT's own `scoopRecipe` object (`_scoop`: tips, display time, yield, typed tags,
  ingredient-group headings — none of which the JSON-LD carries) in ignored
  `data/nyt/raw/<id>.json`. Re-run to fetch new ids and retry failures; the CDN
  sometimes gzips regardless of headers and the script handles that. Bare `/recipes/<id>`
  URLs 404 — the slug is required.
- `tools/fetch_nyt_images.py` — downloads each recipe's photo (the 3:2 "superJumbo"
  rendition) into `images/full/nyt_<id>.jpg` (≤2000px, lightbox) and
  `images/card/nyt_<id>.jpg` (900px, grid cover + detail photo). There is no hero crop;
  the grid falls back to the card image.
- `data/nyt/tags/<id>.json` — curated `cuisine_tags` / `ingredient_tags` / `item_tags`
  (tracked). Written by Claude subagents following `data/nyt/tagging_instructions.md`:
  `python3 tools/build_nyt.py --tagging-input` writes batches of untagged recipes plus
  the current tag vocabulary into ignored `data/nyt/tagging_input/`; hand each batch to
  an agent, then rebuild. `build_nyt.py` normalizes the results through `build_data.py`'s
  alias tables plus its own NYT spellings map.
- `tools/build_nyt.py` — merges raw + tags into `data/nyt_recipes.js` (`pair` = NYT id,
  `source: "nyt"`, `era: "nyt"`, `front` = the card photo, plus `author`, `url`, `rating`,
  `yield_text`/`yield_count` for non-serving yields like "24 cookies", `ingredient_groups`,
  `keywords`) and adds their OpenGraph rows to `data/og.json`. Run it after
  `build_data.py`, which rewrites og.json from scratch. `--tags` prints the NYT tag
  inventory with tags new to the binder flagged.
- `make nyt` = fetch + images + build. `make build` and `make deploy` include the NYT data.

## Notes

- Pairs 1–83 are the newer portrait binder pages; 84–127 are the older landscape
  cards (era stored per recipe).
- Five images had no EXIF orientation and were rotated via `tools/rotation_fixes.json`.
- Known data quirks are kept in each extraction's `issues` field (e.g. pair 53's
  nutrition line is cut off in the photo; pair 102's front card has no matching
  ingredients/steps because its back was a different recipe).
