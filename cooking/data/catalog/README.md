# Linked recipe catalogs

Smitten Kitchen and Cookie and Kate have independent catalogs. Each recipe is
complete in the cookbook, so it works offline: title, ingredients (with group
headings), directions, notes, published yields/times, the recipe card's
description, filter tags, the lead photo, and a link to the original post. The
posts' narrative introductions stay on the publishers' sites.

From `cooking/`, run both imports concurrently:

```sh
make -j2 smitten-kitchen cookie-and-kate
```

Or run just one target. Each command discovers candidates on its first run,
fetches missing pages at no more than two requests per second, and resumes
from its own cache on reruns. It then runs `tools/fetch_blog_images.py` for
recipes without a photo and rebuilds so the rows pick them up. Same-source runs serialize through an OS lock;
different sources can run alongside each other and the NYT pipeline. Writes
use atomic replacement. NYT files and `data/og.json` are never modified by
these commands.

- Smitten Kitchen: all links in the publisher's vegetarian index, plus the
  requested Edna Mae's Sour Cream Pancakes.
- Cookie and Kate: every post in the publisher's post sitemap. Roundups,
  articles, and other pages without recipe data are recorded as skipped.
- `SOURCE/urls.txt`: discovered candidates, retained in publisher order.
- `SOURCE/extra_urls.txt`: optional additional URLs, one per line. You can
  add your own recipes here while another source imports. Rerun the source's
  import to collect additions. This file survives discovery refreshes.
- `SOURCE/facts/`: each recipe's extracted data, with parser version: ingredients,
  `steps`, `notes`, `summary`, and `ingredient_groups`.
- Directions come from each publisher's WordPress REST API (`/wp-json/wp/v2/posts`,
  25 slugs per request, cached in `.cache/`), parsed with html5lib because the
  markup is malformed. Smitten Kitchen's Jetpack cards close their directions div
  with a stray `</p>`, which `html.parser` stops at, and some leave the div empty
  with the steps loose around it. Pre-card posts interleave components
  (ingredients, directions, a short title such as "Cumin-Lime Crema", its
  ingredients and directions); a titled component becomes an ingredient group, and
  an untitled ingredient block after the directions starts an unrelated recipe.
  Cookie and Kate's Tasty Recipes card gives instructions, notes, and description.
- Cookie and Kate's Cloudflare check blocks many post pages; those are re-extracted
  from the same REST API instead (`recover_blocked`), without bypassing the check.
- `SOURCE/vegetarian_options.json`: reviewed choices of vegetarian alternatives
  explicitly offered by the publisher (or omission of optional meat garnishes).
  Exact ingredient matches prevent applying a stale choice after a page changes.
  The entry displays a note when one of these choices is used.
- `SOURCE/images.json`: each recipe's lead photo URL, resolved through the
  publisher's WordPress REST API (25 slugs per request): Cookie and Kate's
  Yoast og:image, else Smitten Kitchen's Jetpack featured image, else the first
  uploaded image in the post. Photos are saved like NYT ones, as
  `images/full/<file>_<pair>.jpg` (≤2000px) and `images/card/<file>_<pair>.jpg`
  (900px), credited and linked to the publisher under the detail photo.
- `SOURCE/report.json`: counts, recipes still `without_photo` or
  `without_directions`, held recipes with ingredient references, and skipped or
  failed pages. The report accounts for every candidate.
- Ignored `.cache/blog-catalog/SOURCE/`: cached source HTML and process lock.
- `data/smitten_kitchen_recipes.js` and `data/cookie_and_kate_recipes.js`: browser data.
- Matching `*_og.json`: independent share-preview metadata, combined by the worker.

Refresh an index with `uv run --with beautifulsoup4 python tools/blog_catalog.py
--source smitten-kitchen --discover` (or `cookie-and-kate`). Rebuild only from
extracted facts with `make blog-catalog-build`. Parser-version changes trigger
re-extraction from cached HTML on the next ordinary run. Failed requests are
retried on the next run.

The ingredient check holds meat, fish, fish sauce, bonito, meat fats, gelatin,
and ambiguous sauces after applying the documented vegetarian choices above.
This conservative screen supplements the publishers'
vegetarian categorization. Missing yields and times remain unknown; the UI
does not invent them. Fractional or ambiguous yields are not scaled.

Cookie and Kate requests are spaced at least 1.5 seconds apart. Ordinary rate
limits pause requests and respect numeric Retry-After headers; repeated rate
limits stop network requests for the run. Pages behind Cloudflare's access check
are read from the REST API instead, as above.

Finish all imports (NYT included) before `make deploy`, which stages the
generated catalog files and photos alongside the rest of the cookbook. Validate
candidate accounting, an empty `without_photo`, the two requested recipes,
independent source filters, and their original-site links. The targets rerun
`tools/classify_diet.py`; settle any pending vegan reviews it reports (see the
cookbook README) before deploying.

Tests: `uv run --with beautifulsoup4 --with html5lib --with pillow python -m
unittest discover -s tools -p 'test_blog_catalog.py'` (also covers the diet
screen); `node tools/test_blog_worker.mjs`.
