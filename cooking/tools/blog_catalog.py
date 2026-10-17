#!/usr/bin/env python3
"""Discover and import factual recipe catalogs. No directions are published; each row
links to the original and shows its lead photo once fetch_blog_images.py has saved it.

Run with: uv run --with beautifulsoup4 python tools/blog_catalog.py --source SOURCE
Each source has its own manifest, cache, lock, records, report, JS, and OG output.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import tempfile
import os
import threading
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
PARSER_VERSIONS = {'smitten-kitchen': 4, 'cookie-and-kate': 2}
SOURCES = {
    'smitten-kitchen': {'name': 'Smitten Kitchen', 'host': 'smittenkitchen.com',
        'index': 'https://smittenkitchen.com/recipes/diet/vegetarian/?format=list',
        'seed': 'https://smittenkitchen.com/2010/01/edna-maes-sour-cream-pancakes/',
        'variable': 'SMITTEN_KITCHEN_RECIPES', 'file': 'smitten_kitchen'},
    'cookie-and-kate': {'name': 'Cookie and Kate', 'host': 'cookieandkate.com',
        'index': 'https://cookieandkate.com/post-sitemap.xml',
        'seed': 'https://cookieandkate.com/vegetable-paella-recipe/',
        'variable': 'COOKIE_AND_KATE_RECIPES', 'file': 'cookie_and_kate'},
}
ANIMAL = re.compile(r'\b(?:chicken|beef|pork|bacon|pancetta|prosciutto|ham|lamb|veal|duck|turkey|'
    r'fish|salmon|tuna|shrimp|prawns?|anchov(?:y|ies)|bonito|dashi|'
    r'gelatin|marshmallows?|lard|suet|oysters?|worcestershire|sausage|chorizo|scallops?|crab|lobster|'
    r'clams?|mussels?|squid|calamari|cod|halibut|tilapia|trout|haddock|caviar|sardines?)\b', re.I)
QUANTITY = re.compile(r'^(?:[\d¼½¾⅓⅔⅛⅜⅝⅞]|one\b|two\b|three\b|a\s+(?:pinch|handful|splash))', re.I)


class AccessCheck(Exception):
    pass


def clean(text):
    return re.sub(r'\s+', ' ', html.unescape(str(text or ''))).strip()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.catalog-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb' if isinstance(value, bytes) else 'w') as out:
            out.write(value)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def write_json(path, data):
    atomic(path, json.dumps(data, ensure_ascii=False, indent=1) + '\n')


class Fetcher:
    def __init__(self, cache, delay=.5):
        self.cache = cache
        self.lock = threading.Lock()
        self.last = 0
        self.delay = delay
        self.resume_at = 0
        self.stopped = False

    def fetch(self, url, refresh=False):
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + '.html')
        compressed = path.with_suffix('.html.gz')
        if compressed.exists() and not refresh: return gzip.decompress(compressed.read_bytes()).decode('utf-8')
        if path.exists() and not refresh: return path.read_text()
        for attempt in range(3):
            with self.lock:
                if self.stopped: raise RuntimeError('Scan paused after repeated rate limiting; rerun later')
                time.sleep(max(0, self.delay - (time.monotonic() - self.last), self.resume_at - time.monotonic()))
                self.last = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={
                    'User-Agent': 'CookingBookCatalog/1.0 (personal recipe link catalog)',
                    'Accept': 'text/html,application/xml',
                })
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = response.read()
                if body[:2] == b'\x1f\x8b': body = gzip.decompress(body)
                text = body.decode('utf-8', 'replace')
                atomic(compressed, gzip.compress(text.encode('utf-8'), compresslevel=6))
                return text
            except urllib.error.HTTPError as error:
                if error.code == 429:
                    body = error.read(2048).decode('utf-8', 'replace')
                    if 'Just a moment' in body or 'cf-chl' in body:
                        raise AccessCheck('Publisher returned a Cloudflare access check; not bypassed')
                    retry_after = error.headers.get('Retry-After', '')
                    seconds = max(60, int(retry_after)) if retry_after.isdigit() else 60
                    self.resume_at = max(self.resume_at, time.monotonic() + seconds)
                    print(f'Rate limit: pausing requests for {seconds} seconds', flush=True)
                    if attempt == 2:
                        self.stopped = True
                        raise
                    continue
                if error.code in (401, 403, 404) or attempt == 2: raise
                time.sleep(2 ** (attempt + 1))
            except Exception:
                if attempt == 2: raise
                time.sleep(2 ** (attempt + 1))


def discover(source, fetcher, refresh=False):
    config = SOURCES[source]
    page = fetcher.fetch(config['index'], refresh=refresh)
    if source == 'smitten-kitchen':
        soup = BeautifulSoup(page, 'html.parser')
        urls = [a['href'] for a in soup.select('main > li > a[href]')]
    else:
        tree = ET.fromstring(page)
        urls = [node.text for node in tree.findall('{*}url/{*}loc')]
    urls = [url for url in urls if url.startswith('https://' + config['host'] + '/')]
    if not urls: raise ValueError('Discovery returned no recipe candidates')
    return list(dict.fromkeys([config['seed']] + urls))


def find_recipe(data):
    if isinstance(data, list):
        for item in data:
            found = find_recipe(item)
            if found: return found
    if isinstance(data, dict):
        kind = data.get('@type', [])
        if kind == 'Recipe' or isinstance(kind, list) and 'Recipe' in kind: return data
        return find_recipe(data.get('@graph', []))
    return None


def strip_personal_notes(line):
    # Ingredient quantities and preparation labels are facts; omit personal asides.
    line = re.sub(r'\s*\([^)]*\b(?:I|my|we|you)\b[^)]*\)', '', line)
    return re.sub(r';\s*(?:I|we|you)\b.*', '', line).strip()


def ingredient_lines(block):
    block = BeautifulSoup(str(block), 'html.parser')
    for br in block.find_all('br'): br.replace_with('\n')
    for li in block.find_all('li'): li.append('\n')
    return [clean(line) for line in block.get_text().split('\n') if clean(line)]


def parse_page(page, url, source):
    soup = BeautifulSoup(page, 'html.parser')
    recipe = None
    for script in soup.select('script[type="application/ld+json"]'):
        try: recipe = find_recipe(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError): continue
        if recipe: break
    title = soup.select_one('h1.entry-title, h1')
    facts = {'title': clean(title.get_text(' ', strip=True)) if title else '',
        'ingredients': [], 'yield_text': '', 'cook_time': None, 'categories': [], 'cuisines': []}
    if recipe:
        author = recipe.get('author') or {}
        if isinstance(author, list): author = author[0] if author else {}
        yield_value = recipe.get('recipeYield', '')
        if isinstance(yield_value, list): yield_value = max(yield_value, key=lambda x: len(str(x)), default='')
        facts.update(title=clean(recipe.get('name')), ingredients=[clean(x) for x in recipe.get('recipeIngredient', [])],
            yield_text=clean(yield_value), cook_time=clean(recipe.get('totalTime')) or None,
            author=clean(author.get('name') if isinstance(author, dict) else author),
            categories=recipe.get('recipeCategory') or [], cuisines=recipe.get('recipeCuisine') or [])
    elif source == 'smitten-kitchen':
        card = soup.select_one('.jetpack-recipe')
        content = soup.select_one('.entry-content')
        if content:
            # Older malformed p/div nesting sometimes puts ingredient markup
            # outside both the recipe wrapper and entry-content in the DOM.
            ingredient_scope = card if card and card.select('.jetpack-recipe-ingredient') else soup
            facts['ingredients'] = [clean(x.get_text(' ', strip=True)) for x in ingredient_scope.select('.jetpack-recipe-ingredient')]
            for selector, key in (('.jetpack-recipe-servings', 'yield_text'), ('.jetpack-recipe-time', 'cook_time')):
                element = soup.select_one(selector)
                if element: facts[key] = re.sub(r'^(Servings|Time):\s*', '', clean(element.get_text(' ', strip=True)))
            if not facts['ingredients']:
                # Older posts store ingredient blocks as paragraphs separated by br.
                for block in content.find_all(['p', 'ul'], recursive=False):
                    lines = ingredient_lines(block)
                    if any(re.search(r'\byears? ago:', line, re.I) for line in lines): continue
                    lines = [line for line in lines if not re.match(r'^(?:serves|servings:|yield:)\s', line, re.I)]
                    ingredient_part = []
                    for line in lines:
                        if len(line) >= 350 or re.match(r'^(?:Preheat|Heat the|Bring a|Stir the|Mix the|Place the)\b', line): break
                        ingredient_part.append(line)
                    lines = ingredient_part
                    quantity_lines = sum(bool(QUANTITY.match(t)) for t in lines)
                    if len(lines) >= 2 and quantity_lines >= max(1, len(lines) * .2) and all(len(t) < 350 for t in lines):
                        facts['ingredients'].extend(lines)
                    elif facts['ingredients'] and block.get_text(' ', strip=True):
                        # Stop at the first directions paragraph. Some old posts
                        # contain a second, unrelated recipe below the primary one.
                        break
                for block in content.find_all('p', recursive=False):
                    for text in ingredient_lines(block):
                        if not facts['yield_text'] and re.fullmatch(r'(?:Yield:?\s*|Makes\s+|Serves\s+|Servings:\s*)(.{1,100})', text, re.I):
                            facts['yield_text'] = text
                        if not facts['cook_time'] and re.fullmatch(r'(?:Prep time|Total time|Time):\s*.{1,60}', text, re.I):
                            facts['cook_time'] = text
            facts['categories'] = [a.get_text(' ',strip=True) for a in soup.select('.cat-links a')]
            facts['cuisines'] = [a.get_text(' ',strip=True) for a in soup.select('.cat-links a') if '/cuisine/' in a.get('href','')]
            facts['author'] = 'Deb Perelman'
    else:
        return None
    if not facts['ingredients']: return None
    facts['ingredients'] = [strip_personal_notes(x) for x in facts['ingredients']]
    for key in ('categories', 'cuisines'):
        if isinstance(facts[key], str): facts[key] = [clean(x) for x in facts[key].split(',') if clean(x)]
    # Explicit factual corrections for the legacy, unstructured pancake recipe.
    if url == SOURCES['smitten-kitchen']['seed']:
        facts['title'] = 'Edna Mae’s Sour Cream Pancakes'
        facts['yield_text'] = '8 five-inch pancakes or 12 four-inch pancakes'
        facts['yield_count'] = 8
        facts['categories'] = ['Breakfast']
    facts['url'] = url
    facts['source'] = source
    return facts


def dietary_flags(facts):
    flags = []
    for line in facts['ingredients']:
        # These literal plant ingredients can contain animal words in their names.
        scan = re.sub(r'\b(?:oyster mushrooms?|coconut bacon|chicken of the woods|chick[’\']?n)\b', '', line, flags=re.I)
        scan = re.sub(r'\b(?:vegan|vegetarian)\s+(?:fish sauce|worcestershire sauce|marshmallows?|sausages?|chorizo)\b', '', scan, flags=re.I)
        if ANIMAL.search(scan): flags.append(line)
    return flags


def apply_choices(facts, choices):
    choice = choices.get(facts['url'])
    if not choice: return facts
    ingredients = list(facts['ingredients'])
    for original, selected in choice['replacements'].items():
        if original not in ingredients:
            raise ValueError(f"Vegetarian choice needs review after ingredient changes: {facts['url']}")
        index = ingredients.index(original)
        if selected is None: ingredients.pop(index)
        else: ingredients[index] = selected
    return {**facts, 'ingredients':ingredients, 'selection_note':choice['note']}


def vocabulary():
    rows = []
    for filename in ('recipes.js', 'nyt_recipes.js'):
        rows += json.loads((ROOT / 'data' / filename).read_text().split('=', 1)[1].strip().rstrip(';'))
    return {key: sorted({tag for row in rows for tag in row.get(key, [])})
        for key in ('ingredient_tags', 'item_tags')}


def row_from_facts(facts, vocab):
    title, ingredients = facts['title'], facts['ingredients']
    hay = ' '.join([title] + ingredients).lower()
    tags = {}
    for key, values in vocab.items():
        tags[key] = [tag for tag in values if re.search(r'(?<!\w)' + re.escape(tag) + r'(?:es|s)?(?!\w)', hay)][:10]
    category = ' '.join(facts['categories']).lower()
    courses = []
    course_patterns = ((r'dessert|\bcakes?\b|cookie|brownie|pudding|crumble|cobbler', 'Dessert'),
            (r'breakfast|pancake|waffle|brunch|muffin|granola', 'Breakfast'),
            (r'drink|cocktail|smoothie|lemonade', 'Drinks'),
            (r'side dish|appetizer|snack|dip|condiment|sauce', 'Sides & Appetizers'),
            (r'main dish|dinner|entree|pasta|stew|paella|pizza|taco|curry|lasagna|soup', 'Dinner'),
            (r'salad|sandwich|lunch', 'Lunch'))
    for pattern, label in course_patterns:
        if re.search(pattern, category): courses.append(label)
    if not courses:
        for pattern, label in course_patterns:
            if re.search(pattern, title, re.I): courses.append(label)
    yield_text = facts['yield_text'] or None
    count = facts.get('yield_count')
    if not count:
        match = re.match(r'(?:(?:yield:|makes|serves|about)\s*)*(\d+)(?![\d./¼½¾⅓⅔⅛⅜⅝⅞]|\s*\d+\s*/)', yield_text or '', re.I)
        count = int(match[1]) if match else None
    servings = None
    if yield_text and (re.fullmatch(r'\d+(?:\s*(?:to|–|-)\s*\d+)?', yield_text) or re.search(r'serves|servings', yield_text, re.I)):
        number = r'(\d+(?:\s*(?:to|–|-)\s*\d+)?)'
        match = (re.search(r'\bserves\s+' + number, yield_text, re.I)
            or re.search(number + r'\s+servings?\b', yield_text, re.I)
            or re.fullmatch(number, yield_text))
        servings = match[1] if match else None
        if servings: count = int(re.search(r'\d+', servings)[0])
    duration = facts['cook_time']
    if duration and duration.startswith('PT'):
        parts = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if parts:
            duration = ' '.join(f'{n} {unit if int(n)!=1 else unit[:-1]}' for n, unit in zip(parts.groups(), ['hours','minutes','seconds']) if n)
    main = tags['ingredient_tags'][:3]
    description = ('Featuring ' + ', '.join(main) + '.') if main else None
    return {'pair': 10_000_000_000 + int(hashlib.sha256(facts['url'].encode()).hexdigest()[:12], 16),
        'title': title, 'source': facts['source'], 'publisher': SOURCES[facts['source']]['name'],
        'url': facts['url'], 'author': facts.get('author'), 'linked': True,
        'servings': servings, 'yield_text': yield_text, 'yield_count': count,
        'cook_time': duration, 'ingredients': ingredients, 'steps': [], 'front': None, 'back': None, 'hero': None,
        'description': description, 'subtitle': None, 'cuisine_tags': facts['cuisines'],
        'tips': [facts['selection_note']] if facts.get('selection_note') else [],
        'keywords': ['vegetarian'] + (['Edna May', 'Edna Mae'] if facts['url'] == SOURCES['smitten-kitchen']['seed'] else []),
        'course_tags': courses, **tags}


def run(source, refresh=False, build_only=False):
    config = SOURCES[source]
    directory = ROOT / 'data/catalog' / source
    cache = ROOT / '.cache/blog-catalog' / source
    directory.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / 'import.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fetcher = Fetcher(cache, delay=1.5 if source == 'cookie-and-kate' else .5)
        manifest = directory / 'urls.txt'
        if refresh or not manifest.exists(): atomic(manifest, '\n'.join(discover(source, fetcher, refresh=refresh)) + '\n')
        urls = manifest.read_text().splitlines()
        extras = directory / 'extra_urls.txt'
        if extras.exists(): urls += [line.strip() for line in extras.read_text().splitlines() if line.strip() and not line.startswith('#')]
        urls = list(dict.fromkeys(urls))
        if any(not url.startswith('https://' + config['host'] + '/') for url in urls):
            raise ValueError('Every candidate must belong to the selected source')
        facts_dir = directory / 'facts'
        facts_dir.mkdir(exist_ok=True)
        def extract(url):
            key = hashlib.sha256(url.encode()).hexdigest()[:16]
            path = facts_dir / (key + '.json')
            if path.exists():
                saved = json.loads(path.read_text())
                if saved.get('parser_version') == PARSER_VERSIONS[source] or build_only: return saved
            if build_only: return {'url': url, 'status': 'missing'}
            try:
                facts = parse_page(fetcher.fetch(url), url, source)
                result = {'url':url, 'status':'parsed', 'facts':facts} if facts else {'url':url, 'status':'no-recipe-data'}
                result['parser_version'] = PARSER_VERSIONS[source]
                write_json(path, result)
                return result
            except Exception as error:
                result = {'url':url, 'status':'access-check' if isinstance(error, AccessCheck) else 'failed', 'error':str(error)}
                if isinstance(error, AccessCheck):
                    result['parser_version'] = PARSER_VERSIONS[source]
                    write_json(path, result)
                return result
        results = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(extract, url) for url in urls]
            for future in as_completed(futures):
                results.append(future.result())
                if len(results) % 25 == 0 or len(results) == len(urls):
                    print(f'{source}: {len(results)}/{len(urls)} checked', flush=True)
        by_url = {r['url']:r for r in results}
        included, held, skipped = [], [], []
        vocab = vocabulary()
        choice_file = directory / 'vegetarian_options.json'
        choices = json.loads(choice_file.read_text()) if choice_file.exists() else {}
        for url in urls:
            result = by_url[url]
            if result['status'] != 'parsed': skipped.append(result); continue
            facts = apply_choices(result['facts'], choices)
            flags = dietary_flags(facts)
            if flags: held.append({'url':url, 'title':facts['title'], 'ingredient_references':flags}); continue
            included.append(row_from_facts(facts, vocab))
        for row in included:
            # Lead photos come from fetch_blog_images.py; a row without one keeps the linked cover.
            photo = f"{config['file']}_{row['pair']}.jpg"
            if (ROOT / 'images/card' / photo).exists(): row['front'] = photo
        ids = [r['pair'] for r in included]
        if len(set(ids)) != len(ids): raise ValueError('Recipe identity collision')
        write_json(directory / 'report.json', {'source':source, 'candidates':len(urls), 'included':len(included),
            'without_photo':[r['url'] for r in included if not r['front']], 'held':held, 'skipped':skipped})
        atomic(ROOT / 'data' / (config['file'] + '_recipes.js'), f"window.{config['variable']} = " + json.dumps(included,ensure_ascii=False,separators=(',',':')) + ';\n')
        write_json(ROOT / 'data' / (config['file'] + '_og.json'), {str(r['pair']):{'t':r['title'],'d':r['publisher'],
            'i':f"card/{r['front']}" if r['front'] else 'og-cover.jpg'} for r in included})
        print(f'{source}: published catalog data for {len(included)} ({sum(bool(r["front"]) for r in included)} with photos); '
            f'held {len(held)}; skipped/failed {len(skipped)}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=SOURCES, required=True)
    parser.add_argument('--discover', action='store_true', help='Refresh the candidate manifest')
    parser.add_argument('--build-only', action='store_true', help='Build only from already extracted facts')
    args = parser.parse_args()
    run(args.source, refresh=args.discover, build_only=args.build_only)
