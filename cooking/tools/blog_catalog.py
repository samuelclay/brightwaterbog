#!/usr/bin/env python3
"""Discover and import recipe catalogs: ingredients, directions, notes and the lead photo
(once fetch_blog_images.py has saved it), with each row linking to the original post.

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
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from build_data import CUISINE_ALIAS, NON_VEGETARIAN_TAGS, norm

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
    r'clams?|mussels?|squid|calamari|cod|halibut|tilapia|trout|haddock|caviar|sardines?|crawfish|crayfish|colatura)\b', re.I)
# Publisher cuisine fields that are diets, labels or placeholders rather than cuisines.
NOT_CUISINES = {'n/a', 'none', 'varies', 'various', 'vegetarian', 'vegan', 'gluten free',
    'whole grain', 'travel', 'classic', 'international'}
BLOG_CUISINE_ALIAS = {**CUISINE_ALIAS, 'asian fusion': 'Fusion', 'east asian': 'Fusion',
    'north american': 'American', 'swedish': 'Scandinavian'}
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


def slug(url):
    return urllib.parse.urlparse(url).path.rstrip('/').rsplit('/', 1)[-1]


def cuisines(values):
    """Normalize publisher cuisines like the binder's: 'Japanese-inspired' is Japanese, and
    diet labels such as 'Vegan' or placeholders such as 'N/A' are dropped."""
    names = (re.sub(r'[\s-]+inspired$', '', value.strip(), flags=re.I) for value in values)
    return sorted({norm(name, BLOG_CUISINE_ALIAS, title=True) for name in names
        if name and name.lower() not in NOT_CUISINES})


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


DIRECTION_START = re.compile(r'^(?:Preheat|Heat the|Bring a|Stir the|Mix the|Place the)\b')
YEARS_AGO = re.compile(r'\byears? ago:', re.I)
YIELD_LINE = re.compile(r'^(?:serves|servings:|yield:)\s', re.I)
NOTE_LINE = re.compile(r'^(?:do ahead|make ahead|notes?|tips?|storage)\b[^:]{0,20}:', re.I)
HEADING = '##heading##'
YIELD_ONLY = re.compile(r'^(?:makes|yield|yields|serves|servings)\b', re.I)


def ingredient_block(block):
    """An old-style post paragraph's leading ingredient lines plus the lines after them
    (directions that share the paragraph), or None when it isn't an ingredient list."""
    lines = [line for line in ingredient_lines(block) if not YIELD_LINE.match(line)]
    cut = next((i for i, line in enumerate(lines) if len(line) >= 350 or DIRECTION_START.match(line)), len(lines))
    part = lines[:cut]
    quantity_lines = sum(bool(QUANTITY.match(t)) for t in part)
    if len(part) >= 2 and quantity_lines >= max(1, len(part) * .2): return part, lines[cut:]
    return None


def step_lines(element):
    """Text lines of a directions block, one per paragraph, list item or line break. A short
    all-bold or heading line comes back prefixed with HEADING."""
    soup = BeautifulSoup(str(element), 'html5lib')
    for el in soup.find_all(['p', 'li', 'h2', 'h3', 'h4', 'h5', 'h6']):
        text = clean(el.get_text())
        bold = clean(' '.join(b.get_text() for b in el.find_all(['strong', 'b'])))
        if text and len(text) <= 70 and (el.name[0] == 'h' or bold == text) and not el.find(['p', 'li']):
            el.replace_with(soup.new_string('\n' + HEADING + text + '\n'))
    for br in soup.find_all('br'): br.replace_with('\n')
    for el in soup.find_all(['p', 'li', 'div', 'ul', 'ol']): el.append('\n')
    return [clean(line) for line in soup.get_text().split('\n') if clean(line)]


def to_steps(lines):
    steps, title = [], None
    for line in lines:
        if line.startswith(HEADING): title = line[len(HEADING):].rstrip(':'); continue
        steps.append({'title': title, 'text': line})
        title = None
    return steps


def old_post_recipe(content):
    """Ingredients, group headings and direction lines of a pre-card Smitten Kitchen post.
    These interleave components: ingredients and their directions, then a short title
    ('Cumin-Lime Crema'), its ingredients and directions, and so on to the footer. An
    untitled ingredient block after the directions starts an unrelated recipe."""
    # Yield-only lines ('Makes about 30 pastries') can sit between a title and its ingredients.
    blocks = [b for b in content.find_all(['p', 'ul', 'ol', 'h2', 'h3', 'h4'], recursive=False)
        if ingredient_lines(b) and not (len(ingredient_lines(b)) == 1 and YIELD_ONLY.match(ingredient_lines(b)[0]))]
    found = [ingredient_block(b) if b.name in ('p', 'ul') else None for b in blocks]
    ingredients, groups, lines, title, last = [], [], [], None, None
    for i, block in enumerate(blocks):
        text = ingredient_lines(block)
        if any(YEARS_AGO.search(line) or re.match(r'see also:', line, re.I) for line in text):
            if ingredients: break
            continue
        if found[i]:
            if last == 'steps' and not title: break
            if ingredients and title:
                groups.append({'at': len(ingredients), 'title': title})
                lines.append(HEADING + title)  # titles the component's first step
            ingredients += found[i][0]
            lines += found[i][1]
            title, last = None, 'ingredients'
            continue
        if not ingredients: continue
        if len(text) == 1 and len(text[0]) <= 80 and not text[0].endswith('.') and i + 1 < len(blocks) and found[i + 1]:
            title = text[0].rstrip(':')
            continue
        if last == 'ingredients' and len(text) >= 3 and all(len(t) < 120 and not t.endswith('.') for t in text[1:]):
            # An unquantified list right after the ingredients ('Fixings', 'For serving').
            groups.append({'at': len(ingredients), 'title': text[0].rstrip(':')})
            ingredients += text[1:]
            continue
        lines += [line for line in step_lines(block) if not YIELD_LINE.match(line)]
        last = 'steps'
    return ingredients, groups, lines


def directions(body, facts, source):
    """Directions, notes, summary and ingredient-group headings from a post body (REST API
    content). html5lib reads the malformed markup the way a browser does: Smitten Kitchen's
    Jetpack cards close their directions div with a stray </p>, which html.parser stops at."""
    soup = BeautifulSoup(body, 'html5lib')
    found = {'steps': [], 'notes': [], 'summary': None, 'ingredient_groups': []}
    tasty = soup.select_one('.tasty-recipes')
    jetpack = soup.select_one('.jetpack-recipe')
    if tasty:
        def block(selector): return tasty.select_one(selector)
        if block('.tasty-recipes-instructions-body'): found['steps'] = to_steps(step_lines(block('.tasty-recipes-instructions-body')))
        if block('.tasty-recipes-notes-body'): found['notes'] = [l for l in step_lines(block('.tasty-recipes-notes-body')) if not l.startswith(HEADING)]
        if block('.tasty-recipes-description'): found['summary'] = clean(block('.tasty-recipes-description').get_text(' ')) or None
        count, groups = 0, []
        for el in (block('.tasty-recipes-ingredients-body') or soup.new_tag('div')).find_all(True):
            if el.name == 'li': count += 1
            elif el.name in ('p', 'div', 'h3', 'h4', 'h5') and not el.find(['li', 'p', 'div']) and not el.find_parent('li'):
                title = clean(el.get_text()).rstrip(':')
                if title: groups.append({'at': count, 'title': title})
        if count == len(facts['ingredients']): found['ingredient_groups'] = [g for g in groups if g['at'] < count]
    elif jetpack and soup.select_one('.jetpack-recipe-directions'):
        # The first card's directions; malformed nesting can leave them outside its element.
        lines = step_lines(soup.select_one('.jetpack-recipe-directions'))
        if not lines:
            # Some cards leave the div empty, with the steps loose before or after it: take
            # everything from the end of the ingredient list to the end of the card.
            start = body.find('<div class="jetpack-recipe-directions')
            end = body.find('</div></div>', start)
            lines = step_lines(body[body.rfind('</ul></div>', 0, start) + len('</ul></div>'):end if end > 0 else None])
        found['notes'] = [line for line in lines if NOTE_LINE.match(line)]
        found['steps'] = to_steps([line for line in lines if not NOTE_LINE.match(line)])
        # A loose "Notes:" block after a filled directions div, before the card closes.
        start = body.find('<div class="jetpack-recipe-directions')
        trailing = step_lines(body[body.find('</div>', start):body.find('</div></div>', start)])
        if trailing and NOTE_LINE.match(trailing[0]) and trailing[0][:60] not in ' '.join(found['notes']):
            found['notes'] += [line.replace(HEADING, '') for line in trailing]
        if soup.select_one('.jetpack-recipe-notes'): found['notes'] += [l.replace(HEADING, '') for l in step_lines(soup.select_one('.jetpack-recipe-notes'))]
    elif source == 'smitten-kitchen':
        ingredients, groups, lines = old_post_recipe(BeautifulSoup(body, 'html.parser'))
        if ingredients:
            found['ingredients'] = [strip_personal_notes(line) for line in ingredients]
            found['ingredient_groups'] = groups
        found['notes'] = [line for line in lines if NOTE_LINE.match(line)]
        found['steps'] = to_steps([line for line in lines if not NOTE_LINE.match(line)])
    found['notes'] = [line for line in dict.fromkeys(found['notes']) if not re.fullmatch(r'notes?:?', line, re.I)]
    return found


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
                facts['ingredients'], facts['ingredient_groups'], _ = old_post_recipe(content)
                for block in content.find_all('p', recursive=False):
                    for text in ingredient_lines(block):
                        if not facts['yield_text'] and re.fullmatch(r'(?:Yield:?\s*|Makes\s+|Serves\s+|Servings:\s*)(.{1,100})', text, re.I):
                            facts['yield_text'] = text
                        if not facts['cook_time'] and re.fullmatch(r'(?:Prep time|Total time|Time):\s*.{1,60}', text, re.I):
                            facts['cook_time'] = text
            facts['categories'] = [a.get_text(' ',strip=True) for a in soup.select('.cat-links a')]
            facts['cuisines'] = [a.get_text(' ',strip=True) for a in soup.select('.cat-links a') if '/cuisine/' in a.get('href','')]
            facts['author'] = 'Deb Perelman'
    elif source == 'cookie-and-kate' and soup.select_one('.tasty-recipes'):
        # A REST API post body: the Tasty Recipes card without the page's JSON-LD.
        card = soup.select_one('.tasty-recipes')
        def text(selector):
            element = card.select_one(selector)
            return clean(element.get_text()) if element else ''
        facts.update(title=text('.tasty-recipes-title'),
            ingredients=[clean(li.get_text()) for li in card.select('.tasty-recipes-ingredients-body li')],
            yield_text=text('.tasty-recipes-yield'), cook_time=text('.tasty-recipes-total-time') or None,
            # The pages' JSON-LD credits the site rather than the card's author line.
            author=SOURCES[source]['name'],
            categories=text('.tasty-recipes-category'), cuisines=text('.tasty-recipes-cuisine'))
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
    groups = facts.get('ingredient_groups') or []
    for original, selected in choice['replacements'].items():
        if original not in ingredients:
            raise ValueError(f"Vegetarian choice needs review after ingredient changes: {facts['url']}")
        index = ingredients.index(original)
        if selected is None:
            ingredients.pop(index)
            groups = [{**g, 'at': g['at'] - (g['at'] > index)} for g in groups]
        else: ingredients[index] = selected
    return {**facts, 'ingredients':ingredients, 'ingredient_groups':groups, 'selection_note':choice['note']}


def vocabulary():
    rows = []
    for filename in ('recipes.js', 'nyt_recipes.js'):
        rows += json.loads((ROOT / 'data' / filename).read_text().split('=', 1)[1].strip().rstrip(';'))
    return {key: sorted({tag for row in rows for tag in row.get(key, []) if tag not in NON_VEGETARIAN_TAGS})
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
    return {'pair': 10_000_000_000 + int(hashlib.sha256(facts['url'].encode()).hexdigest()[:12], 16),
        'title': title, 'source': facts['source'], 'publisher': SOURCES[facts['source']]['name'],
        'url': facts['url'], 'author': facts.get('author'), 'linked': True,
        'servings': servings, 'yield_text': yield_text, 'yield_count': count,
        'cook_time': duration, 'ingredients': ingredients,
        'ingredient_groups': facts.get('ingredient_groups') or [],
        'steps': [{'n': n, 'title': step['title'], 'text': step['text']} for n, step in enumerate(facts.get('steps') or [], 1)],
        'front': None, 'back': None, 'hero': None,
        'description': facts.get('summary'), 'subtitle': None, 'cuisine_tags': cuisines(facts['cuisines']),
        'tips': (facts.get('notes') or []) + ([facts['selection_note']] if facts.get('selection_note') else []),
        'keywords': ['vegetarian'] + (['Edna May', 'Edna Mae'] if facts['url'] == SOURCES['smitten-kitchen']['seed'] else []),
        'course_tags': courses, **tags}


def fact_path(facts_dir, url):
    return facts_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + '.json')


def post_bodies(source, fetcher, urls):
    """Rendered post bodies from the WordPress REST API, 25 slugs per cached request."""
    by_slug = {slug(url): url for url in urls}
    names = sorted(by_slug)
    for start in range(0, len(names), 25):
        query = urllib.parse.urlencode({'slug': ','.join(names[start:start + 25]), 'per_page': 100,
            '_fields': 'slug,content'})
        for post in json.loads(fetcher.fetch(f"https://{SOURCES[source]['host']}/wp-json/wp/v2/posts?{query}")):
            if post['slug'] in by_slug: yield by_slug[post['slug']], post['content']['rendered']
        print(f'{source}: post bodies {min(start + 25, len(names))}/{len(names)}', flush=True)


def recover_blocked(source, fetcher, results, facts_dir):
    """Cookie and Kate's Cloudflare check blocks many post pages, but its WordPress REST API
    serves the same rendered posts. Re-extract blocked pages from there."""
    blocked = {r['url']: r for r in results if r['status'] in ('access-check', 'failed')}
    try:
        for url, body in post_bodies(source, fetcher, blocked):
            facts = parse_page(body, url, source)
            if facts: facts.update(directions(body, facts, source))
            result = blocked[url]
            result.clear()
            result.update({'url':url, 'status':'parsed' if facts else 'no-recipe-data', 'via':'wp-json',
                'parser_version':PARSER_VERSIONS[source], **({'facts':facts} if facts else {})})
            write_json(fact_path(facts_dir, url), result)
    except Exception as error:
        print(f'{source}: REST recovery stopped: {error}', flush=True)


def add_directions(source, fetcher, results, facts_dir):
    """Fill in directions, notes and summaries for recipes extracted before they were kept."""
    missing = {r['url']: r for r in results if r['status'] == 'parsed' and 'steps' not in r['facts']}
    try:
        for url, body in post_bodies(source, fetcher, missing):
            result = missing[url]
            result['facts'].update(directions(body, result['facts'], source))
            write_json(fact_path(facts_dir, url), result)
    except Exception as error:
        print(f'{source}: directions stopped: {error}', flush=True)


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
        if not build_only:
            if source == 'cookie-and-kate': recover_blocked(source, fetcher, results, facts_dir)
            add_directions(source, fetcher, results, facts_dir)
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
            'without_photo':[r['url'] for r in included if not r['front']],
            'without_directions':[r['url'] for r in included if not r['steps']], 'held':held, 'skipped':skipped})
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
