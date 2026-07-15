# The Purple Carrot Book — build & deploy
# Run from this directory: make deploy

.PHONY: deploy build serve

# Deploy the static site to Cloudflare Pages (cooking.samuelclay.com)
# Stages only the public files into .dist, then direct-uploads.
deploy:
	rm -rf .dist
	mkdir -p .dist/data
	cp -R index.html favicons.html assets images _worker.js .dist/
	cp data/recipes.js data/og.json .dist/data/
	npx --yes wrangler@latest pages deploy --branch main

# Regenerate everything from source photos + extraction data
build:
	python3 tools/process_images.py
	python3 tools/crop_images.py
	python3 tools/build_data.py

# Local preview at http://localhost:8471
serve:
	python3 -m http.server 8471
