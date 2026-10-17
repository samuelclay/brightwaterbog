#!/usr/bin/env python3
"""Mark which recipes are vegan, for the site's Vegetarian / Vegan toggle.

Every recipe here is vegetarian; the question is dairy, eggs, honey and the like.
Purple Carrot's binder cards are all vegan. For the NYT and blog recipes an ingredient
screen settles the clear cases: any required dairy, egg or honey line means not vegan,
and a recipe with no animal ingredient at all is vegan. Recipes whose only animal
ingredients are optional or offered with a plant alternative ("butter or olive oil"),
or that lean on ambiguous store-bought items (pesto, puff pastry, chocolate), are
decided by review: Claude subagents following data/diet/review_instructions.md write
data/diet/reviewed/*.json (tracked, one file per batch), which override the screen.

  python3 tools/classify_diet.py                 # write data/diet.js + data/diet/report.json
  python3 tools/classify_diet.py --review-input  # also write batches for review agents
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, DIET = ROOT / 'data', ROOT / 'data/diet'
FILES = {'recipes.js': 'binder', 'nyt_recipes.js': 'nyt', 'nyt_sam_imported_recipes.js': 'nyt',
    'nyt_brittany_recipes.js': 'nyt', 'nyt_brittany_imported_recipes.js': 'nyt',
    'smitten_kitchen_recipes.js': 'blog', 'cookie_and_kate_recipes.js': 'blog'}

# Plant ingredients whose names contain an animal word; removed before the screen runs.
PLANT = re.compile(r"\b(?:(?:peanut|almond|cashew|sunflower(?: seed)?|seed|nut|cocoa|apple|pumpkin|soy|vegan)\s+butter"
    r"|butter(?:nut|cup|head)?\s*(?:beans?|lettuce|squash)|butternut|buttercup|"
    r"(?:coconut|almond|oat|soy|cashew|rice|hemp|plant|nut|pea|flax|macadamia)[- ](?:milk|cream|yogh?urt|creamer|cheese|butter)s?|"
    r"cream of tartar|eggplants?|egg replacer|(?:flax|chia) ?eggs?|honeydew|honeycrisp|honeynut|cheesecloth|"
    r"nutritional yeast|swiss chard|cream of coconut|coconut whipped cream|cream soda|olive ranch|"
    r"(?:vegan|plant[- ]based|dairy[- ]free|non[- ]?dairy|egg[- ]free|vegetarian)\s+(?:[\w'’-]+\s+){0,2}[\w'’-]+)\b", re.I)
ANIMAL = re.compile(r"\b(?:butter|buttermilk|ghee|milk|cream|creme|crème|half[- ]and[- ]half|cheeses?|parmesan|parmigiano|"
    r"pecorino|romano|feta|ricotta|mozzarella|burrata|cheddar|gruy[eè]re|fontina|gouda|brie|camembert|halloumi|"
    r"paneer|mascarpone|queso|cotija|manchego|asiago|provolone|monterey jack|colby|swiss|emmental|comt[eé]|"
    r"taleggio|gorgonzola|ch[eè]vre|labneh|kefir|yogh?urt|skyr|quark|custard|whey|casein|eggs?|yolks?|"
    r"mayonnaise|mayo|aioli|honey|gelatine?|marshmallows?|fish sauce|anchov(?:y|ies)|worcestershire|lard|"
    r"bonito|dashi|tzatziki|raita|ranch|b[eé]chamel|alfredo|brioche|challah|naan|ladyfingers|meringue|"
    r"dulce de leche|caesar|ice cream|paneer)\b", re.I)
AMBIGUOUS = re.compile(r"\b(?:pesto|puff pastry|pie (?:crust|dough)|pastry dough|chocolate|cookies?|graham crackers?|"
    r"biscuits?|wonton wrappers?|dumpling wrappers?|gnocchi|ravioli|tortellini|pierogi(?:es)?|fresh pasta|"
    r"buns?|rolls?|croissants?|sprinkles|caramel|toffee|icing|frosting|whipped topping|kimchi)\b", re.I)
OPTIONAL = re.compile(r"\boptional\b|\bor\b|\bif (?:you )?(?:like|want|desired)\b|\bto taste\b|\bany kind\b|"
    r"\bof (?:your )?choice\b|\byour (?:favorite|choice)\b|\b(?:non-?dairy|dairy-free|plant-based)\b", re.I)


def rows():
    seen = {}
    for name, kind in FILES.items():
        path = DATA / name
        if not path.exists(): continue
        for row in json.loads(path.read_text().split('=', 1)[1].strip().rstrip(';')):
            seen.setdefault(str(row['pair']), {**row, '_kind': kind})
    return seen


def screen(row):
    """('vegan'|'not-vegan'|'review', lines that decided it)."""
    if row['_kind'] == 'binder': return 'vegan', ['Purple Carrot plant-based meal kit']
    if re.search(r'\bvegan\b', row['title'], re.I): return 'vegan', ['titled vegan by its author']
    required, soft = [], []
    for line in row.get('ingredients') or []:
        scan = PLANT.sub(' ', line)
        # The alternative check reads the whole line: 'vegan mayo or regular mayonnaise' offers one.
        if ANIMAL.search(scan): (soft if OPTIONAL.search(line) else required).append(line)
        elif AMBIGUOUS.search(scan): soft.append(line)
    if required: return 'not-vegan', required
    if soft: return 'review', soft
    return 'vegan', []


def main():
    reviewed = {}
    for path in sorted((DIET / 'reviewed').glob('*.json')): reviewed.update(json.loads(path.read_text()))
    vegan, report = [], {'vegan': 0, 'not_vegan': 0, 'pending_review': [], 'reviewed': 0, 'recipes': {}}
    for pair, row in rows().items():
        status, lines = screen(row)
        if pair in reviewed and lines != ['titled vegan by its author']:
            status, lines = ('vegan' if reviewed[pair]['vegan'] else 'not-vegan'), [reviewed[pair]['why']]
            report['reviewed'] += 1
        elif status == 'review':
            report['pending_review'].append(pair)
        if status == 'vegan': vegan.append(int(pair) if pair.isdigit() else pair)
        report['vegan' if status == 'vegan' else 'not_vegan'] += status != 'review'
        report['recipes'][pair] = {'title': row['title'], 'status': status, 'lines': lines}
    DIET.mkdir(exist_ok=True)
    (DATA / 'diet.js').write_text('// Generated by tools/classify_diet.py: pair ids of the vegan recipes.\n'
        'window.VEGAN_PAIRS = ' + json.dumps(sorted(vegan, key=str), separators=(',', ':')) + ';\n')
    (DIET / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n')
    print(f"vegan {report['vegan']}, not vegan {report['not_vegan']}, reviewed {report['reviewed']}, "
          f"pending review {len(report['pending_review'])}")
    if '--review-input' in sys.argv:
        out = DIET / 'review_input'
        out.mkdir(exist_ok=True)
        for old in out.glob('*.json'): old.unlink()
        all_rows = rows()
        pending = [{'pair': p, 'title': all_rows[p]['title'], 'source': all_rows[p].get('source') or 'binder',
            'flagged': report['recipes'][p]['lines'], 'ingredients': all_rows[p].get('ingredients') or [],
            'nyt_keywords': [k for k in all_rows[p].get('keywords') or [] if re.search(r'vegan|dairy|egg', k, re.I)]}
            for p in report['pending_review']]
        for n in range(0, len(pending), 60):
            (out / f'batch_{n // 60 + 1:02d}.json').write_text(json.dumps(pending[n:n + 60], ensure_ascii=False, indent=1))
        print(f'wrote {(len(pending) + 59) // 60} review batches to {out.relative_to(ROOT)}')


if __name__ == '__main__': main()
