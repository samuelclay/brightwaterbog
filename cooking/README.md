# The Cooking Book

A single-page archive of the family's Purple Carrot recipe-card binder: 131 recipes
digitized from 254 photos taken June 5, 2026 (Photos.app, IMG_9134–9389, minus one
beach photo) — plus the recipes saved in Samuel's and Brittany's NYT Cooking recipe boxes
(224 and 611 saves; 760 distinct recipes after 23 meat/fish exclusions, 51 saved by both),
which sit in the same grid with a source chip naming each person's box (see below).

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
- `data/nyt_recipes.js` / `data/nyt_brittany_recipes.js` — the NYT Cooking recipes from
  Sam's and Brittany's recipe boxes (`window.NYT_RECIPES` / `window.NYT_BRITTANY_RECIPES`),
  generated; `index.html` concatenates them after the binder cards
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

- `data/nyt/recipes.txt` (Sam) and `data/nyt/brittany.txt` (Brittany) — one
  `<id>-<slug>` per line in recipe-box order (newest save first). The recipe box pages
  at `cooking.nytimes.com/recipe-box?page=N` (48 per page) link to
  `/recipes/<id>-<slug>`; add new saves at the top of the file, or regenerate the whole
  list with `tools/recipe_box_export.js` (see "Separate NYT collections").
- `data/nyt/excluded.txt` — ids to leave out, each with a note. The family is
  vegetarian, and some saves predate that, so this lists any recipe with meat or
  seafood, even when optional ("bacon (optional)"), unless the same line offers a
  vegetarian alternative ("chicken or vegetable broth"). Condiments the family buys
  in vegetarian versions (fish sauce, Worcestershire, oyster sauce, dashi) don't
  count. `NON_VEGETARIAN_TAGS` in `tools/build_data.py` keeps meat and fish names off
  the filter panels. `build_nyt.py` and
  `fetch_nyt_images.py` skip them; the list files stay plain mirrors of the boxes.
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
- `tools/build_nyt.py` — merges raw + tags into each collection's output (`pair` = NYT id,
  `source: "nyt"`, `era: "nyt"`, `front` = the card photo, plus `author`, `url`, `rating`,
  `yield_text`/`yield_count` for non-serving yields like "24 cookies", `ingredient_groups`,
  `keywords`, `collections`) and adds their OpenGraph rows to `data/og.json`. Run it after
  `build_data.py`, which rewrites og.json from scratch. `--tags` prints the NYT tag
  inventory with tags new to the binder flagged.
- `make nyt` = fetch + images + build. `make build` and `make deploy` include the NYT data.

## Separate NYT collections

Each person's recipe box is its own collection: **Sam's New York Times Cooking**
(`data/nyt/recipes.txt` → `data/nyt_recipes.js`) and **Brittany's New York Times
Cooking** (`data/nyt/brittany.txt` → `data/nyt_brittany_recipes.js`), listed in
`COLLECTIONS` in `tools/build_nyt.py`. Raw pages, photos, tags and `excluded.txt`
are shared by NYT id, so a recipe saved in both boxes is fetched and tagged once
and appears as one card with both source chips, matching either person's Source
filter (`assets/nyt-collections.js`). Recipe ids and existing links stay stable.
`make nyt` fetches, photographs and builds both lists.

Refreshing a list from a recipe box: sign in to NYT Cooking as that person, open
`cooking.nytimes.com/recipe-box`, and paste `tools/recipe_box_export.js` into the
DevTools console. It loads every page of the box in a hidden frame (read-only) and
downloads `nyt-recipe-box.txt` — one `<id>-<slug>` per line, newest save first.
Replace the list file with it, screen the new ids for meat/fish (add those to
`excluded.txt`), then `make nyt` and tag the untagged recipes as above.

### Offline imports

`tools/import_nyt_export.py` imports supplied schema.org Recipe JSON (a Recipe, a
list of them, or `{"recipes": [...]}`) without touching the network — for recipes
that aren't in a recipe-box list. Each recipe needs its full HTTPS NYT URL with id
and slug, `name`, and `recipeIngredient`; it also reads `recipeInstructions`,
`recipeYield`, `totalTime`, `author`, `nutrition`, and other fields understood by
the NYT normalizer. Outputs are `data/nyt_sam_imported_recipes.js` and
`data/nyt_brittany_imported_recipes.js`, kept apart from the list-built files so
`make nyt` never overwrites an import.

Set `_vegetarian: true` after reviewing each recipe, or supply an explicit
schema.org `VegetarianDiet`/`VeganDiet`. Missing review, existing exclusions,
and meat/fish ingredient references hold the entry for review — even optional
meat ingredients and ambiguous substitutions, so supply an adapted vegetarian
ingredient list before reimporting. The report is saved in ignored
`data/nyt/imports/<collection>.review.json`; exit status 2 means some entries
were held (accepted entries are still imported).

```sh
make nyt-import COLLECTION=brittany INPUT=/path/to/brittany-recipes.json

# Optional supplied local photos, named <NYT id>.jpg (requires Pillow):
python3 tools/import_nyt_export.py --collection brittany \
  --input /path/to/brittany-recipes.json --images /path/to/photos
```

Imports merge by NYT id, so reruns do not duplicate recipes and separate batches
retain earlier imports; a recipe held on reimport is removed from that imported
collection until corrected. Same-collection runs serialize with an OS lock and
outputs are replaced atomically, but run `make deploy` only after every import
finishes.

## Original binder notes

- Pairs 1–83 are the newer portrait binder pages; 84–127 are the older landscape
  cards (era stored per recipe).
- Five images had no EXIF orientation and were rotated via `tools/rotation_fixes.json`.
- Known data quirks are kept in each extraction's `issues` field (e.g. pair 53's
  nutrition line is cut off in the photo; pair 102's front card has no matching
  ingredients/steps because its back was a different recipe).

## Smitten Kitchen and Cookie and Kate

376 Smitten Kitchen and 856 Cookie and Kate recipes, each complete with ingredients,
directions, notes, and photo. Their import commands and reports are documented in
[data/catalog/README.md](data/catalog/README.md).

## Search

Every word typed has to appear somewhere in the recipe (title, ingredients, steps,
tags), and a plural finds its singular ("potatoes" finds "potato"). As you type, tags
named by the words show up under the search bar ("sweet potato black beans" offers
*sweet potato* and *black beans*; "sweet pot" offers *sweet potato* while you're still
typing), each with the number of recipes it would leave. Tapping one turns it into a
filter and takes its words out of the search box.

## Installable app (PWA)

`manifest.webmanifest`, `sw.js` and the icons in `assets/app-icons/` (rendered by
`tools/make_app_icons.py` from the carrot favicon) make the site installable: Add to
Home Screen on iPhone/iPad, Install app on Android or desktop Chrome. It opens
standalone, with the header and filter bar padded clear of the status bar and notch.

The service worker precaches the page and every recipe data file, so all recipe text
works offline; photos are cached as they're viewed. Pages and data are network-first
(a deploy shows on the next launch when online; after 4 seconds without an answer the
cached copy wins), photos and fonts cache-first. When the page loads a new data file,
add it to `SHELL_FILES` in `sw.js`; bump `VERSION` there when the caching logic changes.

## Vegan or vegetarian

Every recipe is vegetarian; the Vegetarian / Vegan toggle in the filter bar narrows
the grid to the vegan ones and recounts the tag panels. `data/diet.js`
(`window.VEGAN_PAIRS`) is generated by `tools/classify_diet.py` (`make diet`):

- Purple Carrot cards are all vegan, and so is any recipe its author titles vegan.
- For the rest, an ingredient screen decides the clear cases: a required dairy, egg,
  honey, or gelatin line means not vegan; no animal ingredient means vegan. Plant
  look-alikes (peanut butter, coconut milk, eggplant, butter beans) don't count.
- Recipes whose animal ingredients are optional or offered with a plant alternative
  ("butter or olive oil", "milk of choice"), or that use ambiguous store-bought
  items (pesto, puff pastry, chocolate), go to review.
  `python3 tools/classify_diet.py --review-input` writes batches to ignored
  `data/diet/review_input/`; hand each to a Claude subagent with
  `data/diet/review_instructions.md`, which writes `data/diet/reviewed/batch_NN.json`
  (tracked; `manual.json` holds hand decisions). Rerun to merge.
- `data/diet/report.json` records each recipe's status and the lines that decided
  it. Pending recipes count as not vegan until reviewed.

`make nyt` and the blog targets rerun the screen; check its `pending review` count
after any import.

## Tag panels

The Cuisine, Main ingredients, and Pantry & spices panels are grouped into sections
defined in `assets/tag-groups.js`. A tag missing there shows under "Other", so add new
tags to a section when they appear. Each panel sorts by Most used (the top 8 per
section, with "+ more") or A–Z (everything), and the choice is remembered in
`localStorage`.

Selected tags combine with AND, within a panel as well as across them: lentils +
carrot shows only recipes with both. Each tag's count is how many of the current
results carry it (so it follows the other tags, the search box, and the diet toggle),
and a tag that would leave no results isn't listed.
