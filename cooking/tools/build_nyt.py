#!/usr/bin/env python3
"""Build data/nyt_recipes.js from data/nyt/raw/<id>.json + data/nyt/tags/<id>.json,
and merge the recipes' OpenGraph rows into data/og.json.

Each raw file is the recipe page's schema.org JSON-LD with NYT's own recipe
object attached as `_scoop` (see tools/fetch_nyt.py). Each tags file holds the
curated cuisine/ingredient/pantry tags written per data/nyt/tagging_instructions.md.

Rows use the same shape as data/recipes.js so index.html renders both sets in
one grid: `pair` is the NYT recipe id (unique, and well above the 131 card
pairs), `front` is the food photo in images/card/ (images/full/ for the
lightbox), `back`/`hero` are null, `era`/`source` are "nyt", and NYT-only
fields ride along: `author`, `url`, `rating`, `yield_text`, `yield_count`,
`ingredient_groups`, `keywords`, `date`, and `course_tags` (Dinner / Lunch /
Breakfast / Dessert / Drinks / Sides & Appetizers, from NYT's own meal +
course + dish tags; the binder cards are all Dinner).

  --tagging-input   write data/nyt/tagging_input/ batches for untagged recipes
  --tags            print the NYT tag inventory (new tags flagged)

Ids listed in data/nyt/excluded.txt (meat / fish) are skipped everywhere.
"""
import glob
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_data import CUISINE_ALIAS, INGREDIENT_ALIAS, ITEM_ALIAS, norm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NYT = os.path.join(ROOT, "data", "nyt")
RAW = os.path.join(NYT, "raw")
TAGS = os.path.join(NYT, "tags")
CARD = os.path.join(ROOT, "images", "card")
OUT = os.path.join(ROOT, "data", "nyt_recipes.js")
OG = os.path.join(ROOT, "data", "og.json")
BATCH = 28

# NYT-specific spellings folded onto the binder's vocabulary
NYT_INGREDIENT_ALIAS = {
    "white bean": "white beans", "cannellini": "cannellini beans",
    "egg": "eggs", "chickpea": "chickpeas", "lentil": "lentils",
    "spaghetti": "pasta", "linguine": "pasta", "penne": "pasta",
    "pappardelle": "pasta", "orecchiette": "pasta", "lasagna noodles": "pasta",
    "mushroom": "mushrooms", "shiitake": "mushrooms", "shiitakes": "mushrooms",
    "tomatoes": "tomato", "cherry tomatoes": "tomato", "heirloom tomatoes": "tomato",
    "eggplants": "eggplant", "beet": "beets", "leek": "leeks", "cucumbers": "cucumber",
    "sweet potatoes": "sweet potato", "potatoes": "potato", "new potatoes": "potato",
    "chocolate chip": "chocolate chips", "walnut": "walnuts", "cashew": "cashews",
    "peanut": "peanuts", "pistachio": "pistachios", "almond": "almonds",
    "scallion": "scallions", "asparagus spears": "asparagus", "persimmons": "persimmon",
    "hazelnut": "hazelnuts",
}
NYT_ITEM_ALIAS = {
    "parmesan cheese": "parmesan", "parmigiano-reggiano": "parmesan",
    "feta cheese": "feta", "goat cheese": "goat cheese", "ricotta cheese": "ricotta",
    "mozzarella cheese": "mozzarella", "gruyère": "gruyere", "gruyère cheese": "gruyere",
    "pecorino romano": "pecorino", "cheddar cheese": "cheddar",
    "extra-virgin olive oil": "olive oil", "unsalted butter": "butter",
    "soy sauce": "soy sauce", "chili flakes": "red chile flakes",
    "red-pepper flakes": "red chile flakes", "red pepper flakes": "red chile flakes",
    "crushed red pepper": "red chile flakes", "scallion": "scallions",
    "lemons": "lemon", "limes": "lime", "garlic cloves": "garlic",
    "fresh ginger": "ginger", "chile crisp": "chili crisp",
    "greek yogurt": "yogurt", "plain yogurt": "yogurt", "heavy cream": "cream",
    "vanilla extract": "vanilla", "anchovy": "anchovies", "cotija cheese": "cotija",
    "kosher salt": None, "black pepper": None,
    "salt": None, "pepper": None, "water": None, "sugar": None, "olive oil": None,
    "all-purpose flour": None, "flour": None,
}


# ---------- ProseMirror helpers ----------
def inline(node):
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "hard_break":
        return " "
    return "".join(inline(c) for c in node.get("content") or [])


def blocks(node, out):
    """Flatten a doc into [(type, text)] for h3 / paragraph / step nodes."""
    if not isinstance(node, dict):
        return out
    t = node.get("type")
    if t in ("h3", "heading", "paragraph", "step"):
        txt = re.sub(r"\s+", " ", inline(node)).strip()
        if txt:
            out.append((t, txt))
        return out
    for c in node.get("content") or []:
        blocks(c, out)
    return out


def doc_blocks(obj):
    doc = (obj or {}).get("doc") if isinstance(obj, dict) else None
    return blocks(doc, []) if doc else []


# ---------- field mappers ----------
def iso_minutes(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", iso or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    h += d * 24
    parts = []
    if h:
        parts.append(f"{h} hour" + ("s" if h != 1 else ""))
    if mi:
        parts.append(f"{mi} minute" + ("s" if mi != 1 else ""))
    return " ".join(parts) or None


def cook_time(ld, sc):
    lt = (sc or {}).get("legacyTime") or {}
    for k in ("totalTime", "cookingTime", "cookTime"):
        d = (lt.get(k) or {}).get("display")
        if d:
            return d.strip()
    dt = ((sc or {}).get("time") or {}).get("displayTime")
    return dt or iso_minutes(ld.get("totalTime"))


WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "twelve": 12}


def yields(ld, sc):
    """-> servings (str|None), yield_text (str), yield_count (int|None)"""
    y = (sc or {}).get("yield") or {}
    notes = (y.get("notes") or "").strip()
    sv = (y.get("servings") or [{}])[0]
    count, end, unit = sv.get("count"), sv.get("rangeEnd"), (sv.get("unit") or "").lower()
    if not count:
        raw = ld.get("recipeYield")
        raw = raw[0] if isinstance(raw, list) and raw else raw
        notes = notes or (str(raw).strip() if raw else "")
        m = re.match(r"(?:about\s+)?(\d+|[a-z]+)(?:\s*(?:to|-|–)\s*(\d+|[a-z]+))?\s*(.*)", notes, re.I)
        if m:
            count = int(m.group(1)) if m.group(1).isdigit() else WORDNUM.get(m.group(1).lower())
            end = int(m.group(2)) if m.group(2) and m.group(2).isdigit() else WORDNUM.get((m.group(2) or "").lower())
            unit = m.group(3).lower()
    if not count:
        return None, notes, None
    if unit.startswith("dozen"):          # "3 dozen cookies" scales as 36
        count, end = count * 12, (end * 12 if end else end)
        unit = unit[5:].strip()
    is_serv = "serving" in unit or unit in ("as a main", "as a side", "")
    span = f"{count} to {end}" if end and end != count else f"{count}"
    if not notes:
        notes = f"{span} {unit or 'servings'}".strip()
    return (span if is_serv else None), notes, int(count)


def nutrition(ld):
    n = ld.get("nutrition") or {}
    out = {}

    def grams(v):
        m = re.match(r"([\d.]+)", str(v or ""))
        return f"{round(float(m.group(1)))} g" if m else None

    if n.get("calories"):
        m = re.match(r"([\d.]+)", str(n["calories"]))
        if m:
            out["calories"] = str(round(float(m.group(1))))
    for k, src in (("fat", "fatContent"), ("carbohydrates", "carbohydrateContent"), ("protein", "proteinContent")):
        g = grams(n.get(src))
        if g:
            out[k] = g
    return out or None


def ingredients(ld, sc):
    lines, groups = [], []
    for t, txt in doc_blocks((sc or {}).get("ingredients")):
        if t in ("h3", "heading"):
            groups.append({"at": len(lines), "title": txt.rstrip(":")})
        else:
            lines.append(txt)
    if not lines:
        lines = [re.sub(r"\s+", " ", s).strip() for s in ld.get("recipeIngredient") or []]
    return lines, groups


def steps(ld, sc):
    out, title = [], None
    for t, txt in doc_blocks((sc or {}).get("preparation")):
        if t in ("h3", "heading"):
            title = txt.rstrip(":")
        else:
            out.append({"n": len(out) + 1, "title": title, "text": txt})
            title = None
    if out:
        return out
    # JSON-LD fallback: HowToStep list, possibly wrapped in HowToSection
    def walk(items, sect=None):
        for it in items or []:
            if isinstance(it, str):
                out.append({"n": len(out) + 1, "title": sect, "text": it}); sect = None
            elif it.get("@type") == "HowToSection":
                walk(it.get("itemListElement"), it.get("name"))
            elif it.get("text"):
                out.append({"n": len(out) + 1, "title": sect, "text": re.sub(r"\s+", " ", it["text"]).strip()}); sect = None
    ins = ld.get("recipeInstructions")
    walk(ins if isinstance(ins, list) else [ins])
    return out


def tips(sc):
    out = []
    for tip in (sc or {}).get("tips") or []:
        txt = " ".join(t for _, t in doc_blocks((tip or {}).get("details")))
        if txt:
            out.append(txt)
    return out


def author(ld, sc):
    names = [b.get("displayName") for b in (sc or {}).get("bylines") or [] if b.get("displayName")]
    if not names:
        a = ld.get("author")
        a = a if isinstance(a, list) else [a]
        names = [x.get("name") for x in a if isinstance(x, dict) and x.get("name")]
    return " and ".join(names) or None


def rating(ld, sc):
    r = (sc or {}).get("rating") or {}
    if r.get("count"):
        return {"value": r.get("roundedAverage"), "count": r["count"]}
    ar = ld.get("aggregateRating") or {}
    if ar.get("ratingCount"):
        return {"value": ar.get("ratingValue"), "count": ar["ratingCount"]}
    return None


def nyt_tags(sc):
    by = {}
    for t in (sc or {}).get("tags") or []:
        if t.get("type") in ("ADMIN", None) or t.get("internal"):
            continue
        by.setdefault(t["type"].lower(), []).append(t["name"])
    return by


# ---------- course (Dinner / Dessert / ...) from NYT's MEAL + COURSE + DISH tags ----------
DESSERT_DISHES = {"Cookie", "Chocolate Chip Cookie", "Christmas Cookie", "Bar Cookie", "Brownie",
                  "Icebox Cake", "Custards and Puddings", "Cakes", "Pies and Tarts"}
# recipes NYT left without any meal/course tag
COURSE_FIXES = {
    1013350: ["Sides & Appetizers"], 1016467: ["Dinner"], 1013107: ["Sides & Appetizers"],
    1021758: ["Drinks"], 1013845: ["Sides & Appetizers", "Lunch"], 1016637: ["Breakfast"],
    1021227: ["Drinks"], 12389: ["Sides & Appetizers"], 1014187: ["Dinner"],
    1014809: ["Lunch", "Sides & Appetizers"], 1014916: ["Sides & Appetizers"], 1013416: ["Sides & Appetizers", "Lunch"],
    1014449: ["Sides & Appetizers"], 1021757: ["Drinks"],
}


def course_tags(rid, nt):
    meal, course, dish = set(nt.get("meal", [])), set(nt.get("course", [])), set(nt.get("dish", []))
    out = set()
    if "Dinner" in meal or ("Main Course" in course and not meal & {"Lunch", "Breakfast", "Brunch"}):
        out.add("Dinner")
    if "Lunch" in meal:
        out.add("Lunch")
    if meal & {"Breakfast", "Brunch"}:
        out.add("Breakfast")
    if "Dessert" in course or dish & DESSERT_DISHES:
        out.add("Dessert")
    if "Cocktails" in dish:
        out.add("Drinks")
    if course & {"Side Dish", "Appetizer", "Small Plate"}:
        out.add("Sides & Appetizers")
    # NYT's "Snack" is dropped: every snack here is also a dessert or a side
    if not out:
        out = set(COURSE_FIXES.get(rid, []))
    return sorted(out)


def load_tags(rid):
    f = os.path.join(TAGS, f"{rid}.json")
    if not os.path.exists(f):
        return None
    t = json.load(open(f))

    def clean(vals, alias, extra, title=False):
        out = set()
        for v in vals or []:
            v = norm(v, alias, title=title)
            key = v.lower()
            if key in extra:
                v = extra[key]
                if v is None:
                    continue
            out.add(v)
        return sorted(out)

    return {
        "cuisine_tags": clean(t.get("cuisine_tags"), CUISINE_ALIAS, {}, title=True),
        "ingredient_tags": clean(t.get("ingredient_tags"), INGREDIENT_ALIAS, NYT_INGREDIENT_ALIAS),
        "item_tags": clean(t.get("item_tags"), ITEM_ALIAS, NYT_ITEM_ALIAS),
    }


def build_row(ld):
    sc = ld.get("_scoop") or {}
    slug = ld.get("_slug") or ""
    rid = int(slug.split("-", 1)[0]) if slug else int(re.search(r"/recipes/(\d+)", ld.get("url", "")).group(1))
    servings, ytext, ycount = yields(ld, sc)
    ing, groups = ingredients(ld, sc)
    front = f"nyt_{rid}.jpg"
    if not os.path.exists(os.path.join(CARD, front)):
        front = None
    desc = (ld.get("description") or ((sc.get("headnote") or {}).get("textContent")) or "").strip() or None
    tags = load_tags(rid) or {"cuisine_tags": [], "ingredient_tags": [], "item_tags": []}
    nt = nyt_tags(sc)
    return {
        "pair": rid,
        "title": (sc.get("title") or ld.get("name") or "").strip(),
        "subtitle": None,
        "author": author(ld, sc),
        "servings": servings,
        "yield_text": ytext or None,
        "yield_count": ycount,
        "cook_time": cook_time(ld, sc),
        "nutrition": nutrition(ld),
        "description": desc,
        "from_your_kitchen": None,
        "ingredients": ing,
        "ingredient_groups": groups or None,
        "tools": None,
        "steps": steps(ld, sc),
        "tips": tips(sc),
        **tags,
        "course_tags": course_tags(rid, nt),
        "keywords": sorted({n for v in nt.values() for n in v}),
        "issues": None,
        "front": front,
        "back": None,
        "era": "nyt",
        "source": "nyt",
        "hero": None,
        "url": f"https://cooking.nytimes.com/recipes/{slug}",
        "rating": rating(ld, sc),
        "date": (ld.get("datePublished") or "")[:10] or None,
    }


def excluded_ids():
    """data/nyt/excluded.txt — ids to leave out (meat / fish); '#' starts a comment."""
    f = os.path.join(NYT, "excluded.txt")
    if not os.path.exists(f):
        return set()
    return {line.split("#", 1)[0].strip() for line in open(f)} - {""}


def ordered_raw():
    order = [s.strip().split("-", 1)[0] for s in open(os.path.join(NYT, "recipes.txt")) if s.strip()]
    skip = excluded_ids()
    have = {os.path.basename(f)[:-5]: f for f in glob.glob(os.path.join(RAW, "*.json"))}
    have = {k: v for k, v in have.items() if k not in skip}
    files = [have[r] for r in order if r in have]
    files += [f for r, f in sorted(have.items()) if r not in order]
    return files


def write_tagging_input(rows):
    d = os.path.join(NYT, "tagging_input")
    os.makedirs(d, exist_ok=True)
    for f in glob.glob(os.path.join(d, "batch_*.json")):
        os.remove(f)
    todo = [r for r in rows if not os.path.exists(os.path.join(TAGS, f"{r['pair']}.json"))]
    raw = {os.path.basename(f)[:-5]: f for f in glob.glob(os.path.join(RAW, "*.json"))}
    for b in range(0, len(todo), BATCH):
        batch = []
        for r in todo[b:b + BATCH]:
            sc = json.load(open(raw[str(r["pair"])])).get("_scoop") or {}
            batch.append({
                "id": r["pair"], "title": r["title"],
                "description": (r["description"] or "")[:240],
                "nyt_tags": nyt_tags(sc),
                "ingredients": r["ingredients"],
            })
        json.dump(batch, open(os.path.join(d, f"batch_{b // BATCH + 1:02d}.json"), "w"),
                  ensure_ascii=False, indent=1)
    # current vocabulary (binder + already-tagged NYT) so batches stay consistent
    pc = json.loads(open(os.path.join(ROOT, "data", "recipes.js")).read().split("=", 1)[1].rstrip().rstrip(";"))
    done = [r for r in rows if r not in todo]
    with open(os.path.join(d, "vocab.txt"), "w") as f:
        for key, label in (("cuisine_tags", "CUISINE"), ("ingredient_tags", "MAIN INGREDIENTS"), ("item_tags", "PANTRY & SPICES")):
            c = Counter(t for r in pc + done for t in r[key])
            f.write(f"== {label} ({len(c)} tags in use; count = recipes using it) ==\n")
            f.write(", ".join(f"{t} ({n})" for t, n in c.most_common()) + "\n\n")
    print(f"wrote {(len(todo) + BATCH - 1) // BATCH} batches for {len(todo)} untagged recipes -> {d}")


def main():
    rows = [build_row(json.load(open(f))) for f in ordered_raw()]
    if "--tagging-input" in sys.argv:
        return write_tagging_input(rows)

    with open(OUT, "w") as f:
        f.write("window.NYT_RECIPES = ")
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    og = json.load(open(OG)) if os.path.exists(OG) else {}
    for rid in excluded_ids():
        og.pop(rid, None)
    for r in rows:
        if not r["front"]:
            continue
        d = f"by {r['author']}" if r["author"] else ""
        if r["cook_time"]:
            d = (d + " · " if d else "") + r["cook_time"]
        og[str(r["pair"])] = {"t": r["title"], "d": d, "i": f"card/{r['front']}"}
    json.dump(og, open(OG, "w"), ensure_ascii=False, separators=(",", ":"))

    untagged = [r["pair"] for r in rows if not (r["cuisine_tags"] or r["ingredient_tags"] or r["item_tags"])]
    noimg = [r["pair"] for r in rows if not r["front"]]
    nosteps = [r["pair"] for r in rows if not r["steps"]]
    noing = [r["pair"] for r in rows if not r["ingredients"]]
    print(f"wrote {len(rows)} NYT recipes -> {OUT} (+{len(rows)} og rows; {len(excluded_ids())} excluded)")
    nocourse = [r["pair"] for r in rows if not r["course_tags"]]
    for label, lst in (("UNTAGGED", untagged), ("NO COURSE", nocourse), ("NO IMAGE", noimg), ("NO STEPS", nosteps), ("NO INGREDIENTS", noing)):
        if lst:
            print(f"{label} ({len(lst)}): {lst}")

    if "--tags" in sys.argv:
        pc = json.loads(open(os.path.join(ROOT, "data", "recipes.js")).read().split("=", 1)[1].rstrip().rstrip(";"))
        for key in ("cuisine_tags", "ingredient_tags", "item_tags"):
            old = {t for r in pc for t in r[key]}
            c = Counter(t for r in rows for t in r[key])
            print(f"\n== {key}: {len(c)} used by NYT, {len([t for t in c if t not in old])} new ==")
            for t, n in c.most_common():
                print(f"  {n:3d}  {t}{'' if t in old else '   *new*'}")


if __name__ == "__main__":
    main()
