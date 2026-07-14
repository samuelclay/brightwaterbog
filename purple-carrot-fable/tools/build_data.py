#!/usr/bin/env python3
"""Merge data/pairs.json + data/extracted/NNN.json into data/recipes.js.

Also normalizes tags across recipes (aliases, casing, plural merges) and
prints a tag inventory so aliases can be tuned.
"""
import glob
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CUISINE_ALIAS = {
    "cajun": "Cajun/Creole", "creole": "Cajun/Creole",
    "mexican-inspired": "Mexican", "tex-mex": "Mexican",
    "middle-eastern": "Middle Eastern",
    "latin": "Latin American",
    "asian": "Fusion",
    "american south": "Southern",
}

INGREDIENT_ALIAS = {
    "chickpea": "chickpeas", "garbanzo beans": "chickpeas",
    "lentil": "lentils",
    "black bean": "black beans",
    "mushroom": "mushrooms",
    "shiitake mushrooms": "mushrooms",
    "portobello mushrooms": "mushrooms",
    "sweet potatoes": "sweet potato",
    "potatoes": "potato",
    "fingerling potatoes": "potato",
    "carrots": "carrot",
    "noodle": "noodles",
    "udon noodles": "udon",
    "tortillas": "tortilla",
    "zucchinis": "zucchini",
    "tomatoes": "tomato",
    "cherry tomatoes": "tomato",
    "grape tomatoes": "tomato",
    "radishes": "radish",
    "romaine": "romaine lettuce",
    "brown rice": "rice",
    "sweet peppers": "bell pepper",
    "garbanzo bean flour": "chickpea flour",
    "snap peas": "sugar snap peas",
    "green peas": "peas",
    "artichoke": "artichokes",
    "shallots": "shallot",
}

ITEM_ALIAS = {
    "chili flakes": "red chile flakes", "crushed red pepper": "red chile flakes",
    "red pepper flakes": "red chile flakes",
    "coconut cream": "coconut milk",
    "scallion": "scallions",
    "chili garlic sauce": "chile garlic sauce",
    "white miso paste": "miso", "red miso paste": "miso",
    "zhoug seasoning": "zhoug",
    "aleppo pepper flakes": "aleppo pepper",
    "radishes": "radish",
    "bouillon cube": "bouillon",
    "dried parsley": "parsley",
    "dried dill": "dill",
    "mustard seeds": "brown mustard seeds",
}


def norm(tag, alias, title=False):
    t = tag.strip()
    t = t if title else t.lower()
    key = t.lower()
    if key in alias:
        return alias[key]
    return t


# pairs whose b-side is a separate standalone recipe (extracted as 128-131)
SPLIT_PAIRS = {15, 25, 59, 102}


def main():
    pairs = {p["pair"]: p for p in json.load(open(os.path.join(ROOT, "data", "pairs.json")))}
    recipes = []
    missing = []
    extracted_ids = sorted(
        int(os.path.basename(f)[:-5])
        for f in glob.glob(os.path.join(ROOT, "data", "extracted", "*.json"))
    )
    for n in sorted(set(pairs) | {i for i in extracted_ids if i not in pairs}):
        f = os.path.join(ROOT, "data", "extracted", f"{n:03d}.json")
        if not os.path.exists(f):
            missing.append(n)
            continue
        try:
            r = json.load(open(f))
        except json.JSONDecodeError as e:
            print(f"BAD JSON {f}: {e}")
            missing.append(n)
            continue
        r["pair"] = n
        if n in pairs:
            p = pairs[n]
            r["front"] = p["front"]
            r["back"] = None if n in SPLIT_PAIRS else p["back"]
            r["era"] = "binder" if n <= 83 else "card"
        else:
            # standalone b-side recipe: "image" names its single source file
            r["front"] = r["image"]
            r["back"] = None
            r["era"] = "binder" if int(r["image"][:3]) <= 83 else "card"
        hero_f = os.path.join(ROOT, "images", "hero", f"{n:03d}.jpg")
        r["hero"] = f"{n:03d}.jpg" if os.path.exists(hero_f) else None
        for s in r.get("steps") or []:
            if s.get("n") and os.path.exists(os.path.join(ROOT, "images", "steps", f"{n:03d}_{s['n']}.jpg")):
                s["img"] = f"{n:03d}_{s['n']}.jpg"
        r["cuisine_tags"] = sorted({norm(t, CUISINE_ALIAS, title=True) for t in r.get("cuisine_tags") or []})
        r["ingredient_tags"] = sorted({norm(t, INGREDIENT_ALIAS) for t in r.get("ingredient_tags") or []})
        r["item_tags"] = sorted({norm(t, ITEM_ALIAS) for t in r.get("item_tags") or []})
        recipes.append(r)

    out = os.path.join(ROOT, "data", "recipes.js")
    with open(out, "w") as f:
        f.write("window.RECIPES = ")
        json.dump(recipes, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print(f"wrote {len(recipes)} recipes -> {out}")
    if missing:
        print(f"MISSING extractions: {missing}")
    issues = [(r["pair"], r["issues"]) for r in recipes if r.get("issues")]
    if issues:
        print("ISSUES:")
        for n, i in issues:
            print(f"  {n:3d}: {i}")

    if "--tags" in sys.argv:
        for key in ("cuisine_tags", "ingredient_tags", "item_tags"):
            c = Counter(t for r in recipes for t in r[key])
            print(f"\n== {key} ({len(c)} unique) ==")
            for t, n in c.most_common():
                print(f"  {n:3d}  {t}")


if __name__ == "__main__":
    main()
