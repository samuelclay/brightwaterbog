# The Purple Carrot Archive

A static, single-page recipe archive generated from the two Purple Carrot capture runs in the local Photos library.

## Run it

From this directory:

```sh
python3 -m http.server 4173
```

Then open <http://127.0.0.1:4173>.

The site renders all 131 extracted recipes and eagerly loads the 254 source photographs. Filters combine across Cuisine, Ingredients, and Recipe items. Every source image opens in a full-screen reader.

## Rebuild the archive

Use the repository virtual environment so Pillow is available:

```sh
../.venv/bin/python scripts/extract_and_ocr.py
../.venv/bin/python scripts/build_recipes.py
```

`extract_and_ocr.py` reads the Photos database in read-only mode, exports the two June 5 capture runs, compares each original with Photos' current derivative to recover saved rotations, and uses macOS Vision OCR to detect and fix remaining sideways pages. `build_recipes.py` pairs standard cards, preserves six complete one-page Extras recipes, and records one mismatched adjacent pair as two partial recipes rather than inventing missing content.

Generated source data lives in `data/source-pages.json` and `data/recipes.json`. The normalized page images and extracted dish crops live in `public/images/`.
