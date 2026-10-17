#!/usr/bin/env python3
"""Import user-supplied Recipe JSON locally; this command never accesses the network.

Input: a schema.org Recipe, a list of Recipes, or {"recipes": [...]}.
Vegetarian review: set _vegetarian: true on reviewed recipes (or supply an
explicit VegetarianDiet/VeganDiet). Unreviewed recipes are reported and held.
Optional supplied photos: --images DIRECTORY, containing <NYT id>.jpg files.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse

from build_nyt import build_row, excluded_ids

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {
    "sam": ("nyt_sam_imported_recipes.js", "NYT_SAM_IMPORTED_RECIPES"),
    "brittany": ("nyt_brittany_imported_recipes.js", "NYT_BRITTANY_IMPORTED_RECIPES"),
}
ANIMAL = re.compile(
    r"\b(?:chicken|beef|pork|bacon|pancetta|prosciutto|ham|lamb|veal|duck|turkey|"
    r"fish|salmon|tuna|shrimp|prawn|anchov(?:y|ies)|bonito|dashi|gelatin|lard|"
    r"suet|oyster sauce|worcestershire|sausage|chorizo|scallops?|crab|lobster)\b", re.I
)


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".import-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def collection_lock(root, collection):
    directory = root / "data/nyt/imports"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{collection}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def recipe_id(recipe):
    url = recipe.get("url", "")
    parsed = urlparse(url)
    match = re.fullmatch(r"/recipes/(\d+)-([a-zA-Z0-9-]+)/?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "cooking.nytimes.com" or not match:
        raise ValueError("Each recipe needs its full https://cooking.nytimes.com/recipes/<id>-<slug> URL")
    return int(match[1]), f"{match[1]}-{match[2]}"


def read_export(path):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("recipes", [data])
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError("Expected a Recipe object, a list of Recipes, or an object with a recipes list")
    seen = set()
    for recipe in data:
        rid, _ = recipe_id(recipe)
        ingredients = recipe.get("recipeIngredient", [])
        if not isinstance(ingredients, list) or not all(isinstance(line, str) for line in ingredients):
            raise ValueError(f"Recipe {rid}: recipeIngredient must be a list of strings")
        if rid in seen:
            raise ValueError(f"Duplicate recipe id in export: {rid}")
        seen.add(rid)
    return data


def review_reason(recipe, row, excluded):
    if str(row["pair"]) in excluded:
        return "Listed in data/nyt/excluded.txt"
    if not row["title"] or not row["ingredients"]:
        return "Missing title or ingredient list"
    if recipe.get("_vegetarian") is False:
        return "Marked non-vegetarian"
    # Conservative: even recipes carrying a diet label are held if an ingredient
    # mentions meat/fish. A vegetarian substitution should be reflected in the
    # supplied ingredient list before importing, not silently assumed here.
    hits = sorted({m.group(0).lower() for line in row["ingredients"] for m in ANIMAL.finditer(line)})
    if hits:
        return "Review ingredient references: " + ", ".join(hits)
    diets = recipe.get("suitableForDiet", [])
    if isinstance(diets, str):
        diets = [diets]
    vegetarian = any(str(d).rstrip("/").split("/")[-1] in ("VegetarianDiet", "VeganDiet") for d in diets)
    if recipe.get("_vegetarian") is not True and not vegetarian:
        return "Needs vegetarian review: set _vegetarian to true after checking ingredients"
    return None


def import_photo(root, directory, collection, rid):
    if directory is None:
        return None
    path = Path(directory) / f"{rid}.jpg"
    if not path.is_file():
        return None
    from PIL import Image, ImageOps
    with Image.open(path) as supplied:
        image = ImageOps.exif_transpose(supplied).convert("RGB")
        name = f"nyt_{collection}_{rid}.jpg"
        for size, width in (("card", 900), ("full", 2000)):
            destination = root / "images" / size / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".import-", dir=destination.parent)
            os.close(fd)
            try:
                resized = image.copy()
                if resized.width > width:
                    resized = resized.resize((width, round(resized.height * width / resized.width)), Image.Resampling.LANCZOS)
                resized.save(temporary, "JPEG", quality=84, optimize=True)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return name


def import_export(path, collection, images=None, root=ROOT):
    recipes = read_export(path)
    filename, variable = OUTPUTS[collection]
    output = root / "data" / filename
    pending = []
    excluded = excluded_ids()
    # Normalize/validate the whole input before changing the published collection.
    accepted = []
    for recipe in recipes:
        rid, slug = recipe_id(recipe)
        normalized = {**recipe, "_slug": slug}
        normalized["recipeInstructions"] = recipe.get("recipeInstructions") or []
        if isinstance(recipe.get("author"), str):
            normalized["author"] = {"name": recipe["author"]}
        row = build_row(normalized)
        row["collections"] = [collection]
        reason = review_reason(recipe, row, excluded)
        if reason:
            pending.append({"id": rid, "title": row["title"], "reason": reason})
        else:
            accepted.append(row)
    with collection_lock(root, collection):
        rows = json.loads(output.read_text().split("=", 1)[1].strip().rstrip(";")) if output.exists() else []
        merged = {row["pair"]: row for row in rows}
        for row in accepted:
            photo = import_photo(root, images, collection, row["pair"])
            row["front"] = photo or merged.get(row["pair"], {}).get("front") or row["front"]
            merged[row["pair"]] = row
        for row in pending:
            # A failed re-review must not leave an older imported copy published.
            merged.pop(row["id"], None)
        review_path = root / "data/nyt/imports" / f"{collection}.review.json"
        previous = json.loads(review_path.read_text()) if review_path.exists() else []
        reviewed_ids = {recipe_id(recipe)[0] for recipe in recipes}
        pending_all = [row for row in previous if row["id"] not in reviewed_ids] + pending
        atomic_write(review_path, json.dumps(pending_all, ensure_ascii=False, indent=2) + "\n")
        atomic_write(output, f"window.{variable} = " + json.dumps(list(merged.values()), ensure_ascii=False, separators=(",", ":")) + ";\n")
    return {"accepted": len(accepted), "held": len(pending), "total": len(merged), "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", choices=OUTPUTS, required=True)
    parser.add_argument("--input", type=Path, required=True, help="User-supplied local Recipe JSON file")
    parser.add_argument("--images", type=Path, help="Local directory of supplied <NYT id>.jpg photos")
    args = parser.parse_args()
    try:
        result = import_export(args.input, args.collection, args.images)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Import failed: {error}\n")
    print(json.dumps(result))
    if result["held"]:
        print(f"Review held entries in data/nyt/imports/{args.collection}.review.json")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
