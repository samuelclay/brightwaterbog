# The Cooking Book — build & deploy
# Run from this directory: make deploy

.PHONY: deploy build nyt nyt-import og-cover serve

# Deploy the static site to Cloudflare Pages (cooking.samuelclay.com)
# Stages only the public files into .dist, then direct-uploads.
deploy:
	rm -rf .dist
	mkdir -p .dist/data
	cp -R index.html favicons.html assets images _worker.js .dist/
	cp data/recipes.js data/nyt_recipes.js data/nyt_sam_imported_recipes.js data/nyt_brittany_recipes.js data/nyt_brittany_imported_recipes.js data/og.json .dist/data/
	npx --yes wrangler@latest pages deploy --profile ofbrooklyn --branch main

# Regenerate everything from source photos + extraction data
build:
	python3 tools/process_images.py
	python3 tools/crop_images.py
	python3 tools/build_data.py
	python3 tools/build_nyt.py
	$(MAKE) og-cover

# Share-preview cover (images/og-cover.jpg). cairosvg needs Homebrew's libcairo on
# the dyld path, and the system python lacks it, so run it through uv.
og-cover:
	DYLD_FALLBACK_LIBRARY_PATH="$$(brew --prefix)/lib" uv run --with cairosvg --with pillow python tools/make_og_cover.py

# Recipes saved from NYT Cooking: fetch any new ids in data/nyt/recipes.txt (Sam)
# and data/nyt/brittany.txt (Brittany), download their photos, rebuild
# data/nyt_recipes.js + data/nyt_brittany_recipes.js (+ og.json rows).
# New recipes still need tags — see README "NYT Cooking recipes".
nyt:
	python3 tools/fetch_nyt.py
	python3 tools/fetch_nyt_images.py
	python3 tools/build_nyt.py

# Offline import of supplied Recipe JSON; COLLECTION=sam or brittany.
# Example: make nyt-import COLLECTION=brittany INPUT=/path/to/recipes.json
nyt-import:
	@test -n "$(COLLECTION)" -a -n "$(INPUT)" || (echo 'Set COLLECTION=sam|brittany and INPUT=/path/to/export.json'; exit 1)
	python3 tools/import_nyt_export.py --collection "$(COLLECTION)" --input "$(INPUT)"

# Local preview at http://localhost:8471
serve:
	python3 -m http.server 8471
