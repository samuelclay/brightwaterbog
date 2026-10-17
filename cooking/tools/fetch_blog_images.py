#!/usr/bin/env python3
"""Download each linked blog recipe's lead photo into images/full/<file>_<pair>.jpg
(≤2000px, lightbox) and images/card/<file>_<pair>.jpg (900px, grid cover + detail
photo), mirroring fetch_nyt_images.py. Then rerun blog_catalog.py --build-only so
the rows pick up their photos.

Photo URLs come from the publishers' WordPress REST API, 25 slugs per request:
Cookie and Kate's Yoast og:image (its share photo), else the Jetpack featured
image, else the first uploaded image in the post body. Resolved URLs are kept in
tracked data/catalog/SOURCE/images.json so reruns only download missing files.

Run with: python3 tools/fetch_blog_images.py --source smitten-kitchen
"""
import argparse, io, json, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blog_catalog import SOURCES, slug, write_json

ROOT = Path(__file__).resolve().parents[1]
FULL, CARD = ROOT / 'images/full', ROOT / 'images/card'
UA = 'CookingBookCatalog/1.0 (personal recipe link catalog)'
DELAY = {'smitten-kitchen': .5, 'cookie-and-kate': 1.5}
# The publishers' own uploads (optionally via Jetpack's i0.wp.com CDN), not emoji or ad images.
CONTENT_IMG = re.compile(r'<img[^>]+src="(https://(?:i\d\.wp\.com/)?(?:smittenkitchen\.com/wp-content/uploads|cookieandkate\.com/images)/[^"]+?\.(?:jpe?g|png|webp)[^"]*)"', re.I)


def get(url, timeout=60):
    for attempt in range(4):
        request = urllib.request.Request(url, headers={'User-Agent': UA})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3: raise
            retry_after = error.headers.get('Retry-After', '')
            seconds = max(60, int(retry_after)) if retry_after.isdigit() else 60 * (attempt + 1)
            print(f'Rate limit: pausing {seconds} seconds', flush=True)
            time.sleep(seconds)


def direct(url):
    """Jetpack's i0.wp.com CDN 404s urllib (curl gets 200), so fetch the publisher's own
    upload instead; Smitten Kitchen's featured URLs also carry a stray 'uploads//'."""
    found = re.match(r'https://i\d\.wp\.com/([^?]+)', url)
    return 'https://' + re.sub(r'/{2,}', '/', found[1]) if found else url


def pick(post):
    og = (post.get('yoast_head_json') or {}).get('og_image') or []
    if og and og[0].get('url'): return og[0]['url'], 'og_image'
    if post.get('jetpack_featured_media_url'): return post['jetpack_featured_media_url'], 'featured'
    found = CONTENT_IMG.search((post.get('content') or {}).get('rendered', ''))
    return (found[1], 'content') if found else (None, None)


def resolve(source, urls, known, manifest):
    """Look up photo URLs for recipes not yet in images.json: featured/og images 25 slugs
    per API call, then post bodies one at a time for the few without either."""
    host = SOURCES[source]['host']
    wanted = {slug(url): url for url in urls if url not in known}
    for fields, size in (('slug,yoast_head_json.og_image,jetpack_featured_media_url', 25), ('slug,content', 1)):
        names = [name for name, url in wanted.items() if url not in known]
        for start in range(0, len(names), size):
            batch = names[start:start + size]
            query = urllib.parse.urlencode({'slug': ','.join(batch), 'per_page': 100, '_fields': fields})
            for post in json.loads(get(f'https://{host}/wp-json/wp/v2/posts?{query}')):
                if post['slug'] not in wanted: continue
                image, via = pick(post)
                if image: known[wanted[post['slug']]] = {'image': image, 'via': via}
            write_json(manifest, dict(sorted(known.items())))
            time.sleep(DELAY[source])
    return known


def save(img, path, max_w, q):
    img = img.copy()
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
    img.save(path, 'JPEG', quality=q, optimize=True, progressive=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=SOURCES, required=True)
    source = parser.parse_args().source
    config = SOURCES[source]
    rows = json.loads((ROOT / 'data' / (config['file'] + '_recipes.js')).read_text().split('=', 1)[1].strip().rstrip(';'))
    manifest = ROOT / 'data/catalog' / source / 'images.json'
    known = json.loads(manifest.read_text()) if manifest.exists() else {}
    known = resolve(source, [row['url'] for row in rows], known, manifest)
    n_ok = n_skip = n_fail = 0
    for row in rows:
        name = f"{config['file']}_{row['pair']}.jpg"
        full, card = FULL / name, CARD / name
        if full.exists() and card.exists():
            n_skip += 1
            continue
        if row['url'] not in known:
            print(f"NO IMAGE {row['url']}", flush=True)
            n_fail += 1
            continue
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(get(direct(known[row['url']]['image']))))).convert('RGB')
            save(img, full, 2000, 82)
            save(img, card, 900, 84)
            print(f"ok {img.width}x{img.height} {row['title']}", flush=True)
            n_ok += 1
        except Exception as error:
            print(f"FAIL {row['url']} {error}", flush=True)
            n_fail += 1
        time.sleep(DELAY[source])
    print(f'{source}: {n_ok} fetched, {n_skip} skipped, {n_fail} failed')


if __name__ == '__main__': main()
