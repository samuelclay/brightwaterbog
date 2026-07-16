#!/usr/bin/env python3
"""Turn raw Vision OCR pages into website-ready Purple Carrot recipes."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source-pages.json"
OUTPUT = ROOT / "data" / "recipes.json"

CATEGORY_LABELS = {
    "extras",
    "extras!",
    "breakfast",
    "lunch",
    "dinner",
    "snack",
    "appetizer",
}

TITLE_OVERRIDES = {
    "071": ("Fiesta Enchilada Skillet", "with Mole Black Beans & Avocado"),
    "090": ("Portobello Steaks", "with Vegetable Orzo Salad & Chimichurri"),
    "096": ("Sweet-and-Sour Brussels Sprouts Stir-Fry", "with Black Quinoa and Mint"),
    "098": ("Wicked Healthy: Beetroot Tacos", "with Avocado Salsa"),
    "102a": ("Fabcake Sliders", ""),
    "102b": ("Sweet Potato Lasagna", "with Tofu Ricotta and Marinated Bean Salad"),
}

CUISINE_RULES = {
    "Indian": r"punjabi|kadhi|pakora|\bdal\b|kashmiri|malai|kofta|\bkorma\b|\bmatar\b|paneer|raita|chutney|dosa",
    "Thai": r"\bthai\b|massaman|pad thai|khao soi|red curry",
    "Korean": r"\bkorean\b|kimchi|gochujang",
    "Japanese": r"\bjapanese\b|\bsushi\b|\budon\b",
    "Mexican & Tex-Mex": r"taco|enchilada|burrito|tostada|elote|\bmole\b|quesadilla|fajita|guacamole|poblano",
    "Middle Eastern": r"falafel|tahini|gyro|za['’]?atar|shakshuka|baharat|hummus",
    "Mediterranean": r"mediterranean|greek|tzatziki|artichoke|olive",
    "Italian": r"pesto|parmesan|gnocchi|fettuccine|penne|cavatappi|bolognese|arrabbiata|florentine|ciabatta|ragout",
    "Chinese": r"chow mein|black pepper tofu|chinese|dan dan",
    "Vietnamese": r"vietnamese",
    "Caribbean": r"caribbean|jerk seasoning|jamaic",
    "Cajun & Creole": r"jambalaya|cajun|creole",
    "American": r"burger|mac n['’]? cheese|grilled cheese|chowder|caesar|lobster roll|slider|barbecue|cobb salad",
}

PRIMARY_RULES = {
    "Chickpeas": r"chickpea|garbanzo",
    "Lentils": r"lentil|\bdal\b",
    "Black beans": r"black bean",
    "White beans": r"white bean|butter bean|cannellini|kidney bean",
    "Tofu": r"\btofu\b",
    "Tempeh": r"tempeh",
    "Seitan": r"seitan",
    "Mushrooms": r"mushroom|shiitake|portobello",
    "Cauliflower": r"cauliflower",
    "Eggplant": r"eggplant",
    "Potatoes": r"\bpotato|gnocchi|latke",
    "Sweet potatoes": r"sweet potato|\byam\b",
    "Squash": r"squash|pumpkin(?!\s+seed)|delicata|butternut",
    "Zucchini": r"zucchini",
    "Broccoli": r"broccoli|broccolini",
    "Brussels sprouts": r"brussels sprout",
    "Artichokes": r"artichoke",
    "Hearts of palm": r"hearts? of palm",
    "Corn": r"\bcorn\b|elote",
    "Carrots": r"\bcarrots?\b",
    "Beets": r"beetroot|\bbeet",
    "Pasta & noodles": r"noodle|pasta|fettuccine|penne|cavatappi|gnocchi|udon|chow mein|pad thai",
    "Rice & grains": r"\brice\b|quinoa|farro|barley|millet|couscous",
    "Tacos & wraps": r"taco|burrito|gyro|wrap|enchilada|quesadilla|tortilla|arepa|tostada",
}

ITEM_RULES = {
    "Garlic": r"garlic",
    "Ginger": r"ginger",
    "Cilantro": r"cilantro",
    "Basil": r"basil",
    "Dill": r"\bdill",
    "Mint": r"\bmint",
    "Parsley": r"parsley",
    "Scallions": r"scallion",
    "Jalapeño": r"jalape[ñn]o",
    "Chile": r"chile|chili|pepper flake",
    "Cumin": r"cumin",
    "Turmeric": r"turmeric",
    "Paprika": r"paprika",
    "Curry powder": r"curry powder",
    "Garam masala": r"garam masala",
    "Gochujang": r"gochujang",
    "Za'atar": r"za['’]?atar",
    "Baharat": r"baharat",
    "Togarashi": r"togarashi",
    "Tamari": r"tamari",
    "Miso": r"\bmiso\b",
    "Tahini": r"tahini",
    "Sesame oil": r"sesame oil",
    "Peanut butter": r"peanut butter",
    "Almond butter": r"almond butter",
    "Cashews": r"cashew",
    "Walnuts": r"walnut",
    "Pistachios": r"pistachio",
    "Hazelnuts": r"hazelnut",
    "Sunflower seeds": r"sunflower seed",
    "Pumpkin seeds": r"pumpkin seed",
    "Coconut milk": r"coconut milk",
    "Lemon": r"\blemon",
    "Lime": r"\blime",
    "Avocado": r"avocado",
    "Tomatoes": r"tomato",
    "Red onion": r"red onion",
    "Shallots": r"shallot",
    "Spinach": r"spinach",
    "Kale": r"\bkale\b|lacinato",
    "Cabbage": r"cabbage|coleslaw",
    "Radishes": r"radish",
    "Bell pepper": r"bell pepper|sweet pepper",
    "Maple": r"maple",
    "Agave": r"agave",
    "Dijon mustard": r"dijon",
    "Nutritional yeast": r"nutritional yeast",
    "Vegan cheese": r"vegan (?:cheddar|mozzarella|parmesan)|vegan cheese",
    "Vegenaise": r"vegenaise",
}

BOILERPLATE = re.compile(
    r"ingredients listed|directions for|nutrition per|allergen|processed and packaged|"
    r"don['’]?t forget|this item|these items|this ingredient|these ingredients|"
    r"split between|multiple steps|find a clove|the garlic included|bag is also|"
    r"cauliflower steaks|substitute:|contact us|call \(|email |purple\s*[|i]?\s*carrot|"
    r"tag your best|featured on|follow us|visit us|questions|tools|"
    r"vitamin.?packed base|bright,? fresh vegetables|delicious crunch|cal.?ories",
    re.I,
)

BULLET = re.compile(r"^\s*[»›•>\"·]\s*")
QUANTITY_START = re.compile(r"^\s*(?:\d+(?:[.⅓⅔¼½¾⅛⅜⅝⅞/]|\s)|[¼½¾⅓⅔⅛⅜⅝⅞])")


def clean_text(text: str) -> str:
    text = text.replace("\u00ad", "").replace("İ", "I")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def join_lines(lines: list[str]) -> str:
    output = ""
    for raw in lines:
        line = clean_text(raw)
        if not line:
            continue
        if output.endswith("-") and line[:1].islower():
            output = output[:-1] + line
        else:
            output = f"{output} {line}".strip()
    return output


def exact_line(line: dict[str, Any], label: str) -> bool:
    return bool(re.fullmatch(rf"(?i)\s*{label}:?\s*", line["text"]))


def is_standalone(page: dict[str, Any]) -> bool:
    lines = page["ocr"]["lines"]
    return any(exact_line(line, "ingredients") for line in lines) and any(
        exact_line(line, "directions") for line in lines
    )


def title_case(value: str) -> str:
    if not value.isupper():
        return value
    value = value.title()
    replacements = {
        "And": "and",
        "With": "with",
        "N'": "n'",
        "Za'Atar": "Za'atar",
        "Al ": "al ",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def page_title(page: dict[str, Any], recipe_key: str, standalone: bool) -> tuple[str, str]:
    if recipe_key in TITLE_OVERRIDES:
        return TITLE_OVERRIDES[recipe_key]

    lines = page["ocr"]["lines"]
    if standalone:
        serving = next(
            (line for line in lines if re.search(r"(?i)\bservings\b", line["text"])),
            None,
        )
        if serving:
            candidates = [
                clean_text(line["text"])
                for line in lines
                if line["box"]["y"] > serving["box"]["y"] + 0.01
                and len(clean_text(line["text"])) >= 4
                and clean_text(line["text"]).lower() not in CATEGORY_LABELS
                and not re.search(r"purple carrot|tag your|featured", line["text"], re.I)
            ]
            with_index = next(
                (index for index, value in enumerate(candidates) if value.lower().startswith("with ")),
                None,
            )
            if with_index is not None and with_index > 0:
                return candidates[with_index - 1], candidates[with_index]
            if candidates:
                return candidates[-1], ""

    if page["recipe_number"] <= 83:
        ingredients = [line for line in lines if exact_line(line, "ingredients")]
        times = [line for line in lines if re.search(r"(?i)\b(?:cook\s*)?time\s*:", line["text"])]
        if ingredients and times:
            low = max(line["box"]["y"] for line in ingredients)
            high = max(line["box"]["y"] for line in times)
            candidates = [
                clean_text(line["text"])
                for line in lines
                if low + 0.012 < line["box"]["y"] < high - 0.012
                and line["box"]["x"] < 0.75
                and len(clean_text(line["text"])) > 3
            ]
            if candidates:
                title = next((value for value in candidates if not value.lower().startswith("with ")), candidates[0])
                subtitle = next((value for value in candidates if value.lower().startswith("with ")), "")
                return title, subtitle

    candidates = [
        line
        for line in lines
        if line["box"]["y"] > 0.4
        and line["box"]["height"] > 0.025
        and len(clean_text(line["text"])) > 3
        and not re.search(r"summary|keep in touch|purple carrot", line["text"], re.I)
    ]
    candidates.sort(key=lambda line: line["box"]["y"], reverse=True)
    if candidates:
        top = candidates[0]
        title_lines = [top["text"]]
        for line in candidates[1:]:
            if top["box"]["y"] - line["box"]["y"] < 0.09:
                title_lines.append(line["text"])
            else:
                break
        return title_case(join_lines(title_lines)), ""
    return f"Recipe {recipe_key}", ""


def extract_ingredients(page: dict[str, Any]) -> list[str]:
    lines = page["ocr"]["lines"]
    header = next((line for line in lines if exact_line(line, "ingredients")), None)
    if not header:
        return []

    direction = next((line for line in lines if exact_line(line, "directions")), None)
    tools = [line for line in lines if exact_line(line, "tools")]
    listed = [line for line in lines if re.search(r"(?i)^ingredients listed", line["text"])]

    if direction:
        right_edge = max(header["box"]["x"] + 0.16, direction["box"]["x"] - 0.015)
    elif page["ocr"]["width"] > page["ocr"]["height"]:
        right_edge = 0.20
    else:
        right_edge = 0.56

    bottom = 0.015
    if tools:
        bottom = max(bottom, max(line["box"]["y"] for line in tools) + 0.012)
    if listed:
        bottom = max(bottom, max(line["box"]["y"] for line in listed) + 0.018)
    nutrition_headers = [
        line
        for line in lines
        if re.search(r"(?i)^nut.?ition per", line["text"])
        and line["box"]["x"] < right_edge
        and line["box"]["y"] < header["box"]["y"]
    ]
    if nutrition_headers:
        bottom = max(
            bottom,
            max(line["box"]["y"] for line in nutrition_headers) + 0.012,
        )

    candidates = [
        line
        for line in lines
        if bottom < line["box"]["y"] < header["box"]["y"] - 0.004
        and header["box"]["x"] - 0.035 <= line["box"]["x"] < right_edge
        and not BOILERPLATE.search(line["text"])
    ]
    bullets = [
        line
        for line in candidates
        if BULLET.match(line["text"]) or QUANTITY_START.match(line["text"])
    ]
    if not bullets:
        return []

    centers: list[float] = []
    for line in sorted(bullets, key=lambda item: item["box"]["x"]):
        x = line["box"]["x"]
        if not centers or x - centers[-1] > 0.12:
            centers.append(x)
        else:
            centers[-1] = (centers[-1] + x) / 2

    boundaries = [-1.0]
    boundaries.extend((a + b) / 2 for a, b in zip(centers, centers[1:]))
    boundaries.append(right_edge)

    ingredients: list[str] = []
    for column, center in enumerate(centers):
        left = max(header["box"]["x"] - 0.04, boundaries[column])
        right = boundaries[column + 1]
        column_lines = sorted(
            [line for line in candidates if left <= line["box"]["x"] < right],
            key=lambda line: line["box"]["y"],
            reverse=True,
        )
        current: list[str] = []
        for line in column_lines:
            text = clean_text(line["text"])
            if BULLET.match(text) or QUANTITY_START.match(text):
                if current:
                    ingredients.append(join_lines(current))
                current = [BULLET.sub("", text)]
            elif current and not re.match(r"^[*†/]", text):
                current.append(text)
        if current:
            ingredients.append(join_lines(current))

    cleaned: list[str] = []
    for ingredient in ingredients:
        ingredient = re.split(
            r"(?i)\s+(?:i?gredients listed|nut.?ition|calories\s*:|add the |"
            r"the garlic included|roasted tomato gratin recipe bag)",
            ingredient,
            maxsplit=1,
        )[0]
        ingredient = re.sub(r"\s+([,.)])", r"\1", ingredient).strip(" ;")
        ingredient = re.sub(
            r"(?i)\s+(?:taco recipe bag\.?|steps\.?)$", "", ingredient
        )
        ingredient = ingredient.strip(" *°/≥")
        if len(ingredient) > 2:
            cleaned.append(ingredient)
    split_items: list[str] = []
    for ingredient in cleaned:
        ingredient = ingredient.replace(" ≥ ", " ")
        match = re.match(
            r"^([A-Za-z][A-Za-z ]{2,}?)\s+(?=(?:\d+|[¼½¾⅓⅔⅛⅜⅝⅞])\s)",
            ingredient,
        )
        if match and len(match.group(1).split()) <= 3:
            split_items.extend([match.group(1), ingredient[match.end() :]])
        else:
            split_items.append(ingredient)
    return [item.strip() for item in split_items if len(item.strip()) > 2]


def numbered_steps(page: dict[str, Any], standalone: bool = False) -> list[dict[str, Any]]:
    lines = page["ocr"]["lines"]
    header = next((line for line in lines if exact_line(line, "directions")), None)
    left = (header["box"]["x"] - 0.04) if header and standalone else 0.02
    right = 0.92 if standalone else 0.78
    candidates = sorted(
        [line for line in lines if left <= line["box"]["x"] < right],
        key=lambda line: (-line["box"]["y"], line["box"]["x"]),
    )

    steps: list[dict[str, Any]] = []
    current_number: int | None = None
    current_lines: list[str] = []
    for line in candidates:
        text = clean_text(line["text"])
        match = re.match(r"^([1-9])[.)]\s*(.*)", text)
        if match:
            if current_number is not None and current_lines:
                steps.append(
                    {
                        "number": current_number,
                        "title": f"Step {current_number}",
                        "text": join_lines(current_lines),
                    }
                )
            current_number = int(match.group(1))
            current_lines = [match.group(2)] if match.group(2) else []
        elif current_number is not None:
            if BOILERPLATE.search(text) or exact_line(line, "directions"):
                continue
            if re.fullmatch(r"\d+\.\d+", text):
                continue
            if re.match(r"(?i)^(nutrition|calories|fat:|carbohydrates|protein:)", text):
                continue
            current_lines.append(text)
    if current_number is not None and current_lines:
        steps.append(
            {
                "number": current_number,
                "title": f"Step {current_number}",
                "text": join_lines(current_lines),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[int] = set()
    for step in steps:
        if step["number"] not in seen and len(step["text"]) > 8:
            deduped.append(step)
            seen.add(step["number"])
    return deduped


def grid_steps(page: dict[str, Any]) -> list[dict[str, Any]]:
    lines = page["ocr"]["lines"]
    rows = [(0.45, 0.79), (0.03, 0.35)]
    steps: list[dict[str, Any]] = []
    number = 0
    for bottom, top in rows:
        headings = sorted(
            [
                line
                for line in lines
                if 0.22 <= line["box"]["x"] < 0.97
                and bottom <= line["box"]["y"] < top
                and 3 <= len(clean_text(line["text"])) <= 48
                and sum(char.isupper() for char in line["text"] if char.isalpha())
                >= max(2, sum(char.isalpha() for char in line["text"]) * 0.72)
                and not line["text"].upper().startswith("TIP:")
                and not re.search(r"purple\s*[|i]?\s*carrot", line["text"], re.I)
            ],
            key=lambda line: line["box"]["x"],
        )
        # A few OCR runs split a heading; retain the left-most heading in each
        # of the three spatial columns.
        selected: list[dict[str, Any]] = []
        for heading in headings:
            if not selected or heading["box"]["x"] - selected[-1]["box"]["x"] > 0.12:
                selected.append(heading)
        if len(selected) < 3:
            continue
        selected = selected[:3]
        for column, heading_line in enumerate(selected):
            number += 1
            left = max(0.22, heading_line["box"]["x"] - 0.012)
            right = (
                selected[column + 1]["box"]["x"] - 0.012
                if column < 2
                else 0.985
            )
            region = sorted(
                [
                    line
                    for line in lines
                    if left <= line["box"]["x"] < right
                    and bottom <= line["box"]["y"] <= heading_line["box"]["y"] + 0.006
                    and not re.fullmatch(r"[1-8][°.]?", line["text"].strip())
                    and not re.search(r"purple\s*[|i]?\s*carrot", line["text"], re.I)
                ],
                key=lambda line: line["box"]["y"],
                reverse=True,
            )
            if not region:
                continue
            heading_index = next(
                (index for index, line in enumerate(region) if line is heading_line),
                None,
            )
            if heading_index is None:
                continue
            heading = title_case(clean_text(region[heading_index]["text"]))
            body = [line["text"] for line in region[heading_index + 1 :] if not BOILERPLATE.search(line["text"])]
            text = join_lines(body)
            if text:
                steps.append({"number": number, "title": heading, "text": text})
    return steps


def parse_servings(text: str) -> int | None:
    patterns = [r"SERVINGS\s*:\s*(\d+)", r"(\d+)(?:\s*\(\s*\d+\s*\))?\s*SERVINGS"]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


def parse_cook_time(text: str) -> int | None:
    match = re.search(r"(?:COOK\s*)?TIME\s*:\s*(\d+)", text, re.I)
    return int(match.group(1)) if match else None


def parse_nutrition(text: str) -> dict[str, int]:
    fields = {
        "calories": r"CALORIES?\s*[:.]\s*(\d+)",
        "fat_g": r"FAT\s*[:.]\s*(\d+)\s*g",
        "carbohydrates_g": r"CARBOHYDRATES?\s*[:.]\s*(\d+)\s*g",
        "protein_g": r"PROTEIN\s*[:.]\s*(\d+)\s*g",
    }
    output: dict[str, int] = {}
    for name, pattern in fields.items():
        match = re.search(pattern, text, re.I)
        if match:
            output[name] = int(match.group(1))
    return output


def modern_summary(page: dict[str, Any]) -> str:
    lines = page["ocr"]["lines"]
    header = next((line for line in lines if exact_line(line, "summary")), None)
    if not header:
        return ""
    keep_in_touch = next(
        (line for line in lines if exact_line(line, "keep in touch")), None
    )
    right_edge = keep_in_touch["box"]["x"] - 0.015 if keep_in_touch else 0.72
    body = [
        line["text"]
        for line in sorted(lines, key=lambda line: line["box"]["y"], reverse=True)
        if 0.19 <= line["box"]["x"] < right_edge
        and 0.045 < line["box"]["y"] < header["box"]["y"] + 0.015
        and not re.search(r"allergens?|processed and packaged", line["text"], re.I)
        and not BOILERPLATE.search(line["text"])
    ]
    return join_lines(body)


def matching_tags(text: str, rules: dict[str, str]) -> list[str]:
    return [label for label, pattern in rules.items() if re.search(pattern, text, re.I)]


def build_recipe(
    key: str,
    cover: dict[str, Any],
    method: dict[str, Any],
    standalone: bool,
    completeness: str = "complete",
) -> dict[str, Any]:
    title, subtitle = page_title(cover, key, standalone)
    if not subtitle and method is not cover:
        method_subtitle = next(
            (
                clean_text(line["text"])
                for line in method["ocr"]["lines"]
                if line["box"]["y"] > 0.84
                and re.match(r"(?i)^with\s+", line["text"])
            ),
            "",
        )
        subtitle = title_case(method_subtitle)
    ingredients = extract_ingredients(cover)
    if method is not cover:
        method_ingredients = extract_ingredients(method)
        if len(method_ingredients) > len(ingredients):
            ingredients = method_ingredients

    if standalone:
        steps = numbered_steps(method, standalone=True)
    elif method["recipe_number"] >= 84:
        steps = grid_steps(method)
    else:
        steps = numbered_steps(method)

    full_ocr = "\n".join([cover["ocr"]["fullText"], method["ocr"]["fullText"]])
    summary = modern_summary(cover) if cover["recipe_number"] >= 84 else subtitle
    searchable = " ".join([title, subtitle, summary, *ingredients]).lower()
    cuisine = ["Vegan", *matching_tags(searchable, CUISINE_RULES)]
    primary = matching_tags(searchable, PRIMARY_RULES)
    recipe_items = matching_tags(" ".join(ingredients).lower(), ITEM_RULES)

    source_pages = [cover]
    if method is not cover:
        source_pages.append(method)
    source_metadata = [
        {
            "side": page["side"],
            "image": page["image"],
            "uuid": page["metadata"]["uuid"],
            "original_filename": page["metadata"]["filename"],
            "captured_at": page["metadata"]["created_at"],
            "dimensions": [page["metadata"]["width"], page["metadata"]["height"]],
            "rotation_degrees_ccw": page["rotation_degrees_ccw"],
            "rotation_source": page["rotation_source"],
            "ocr_line_count": len(page["ocr"]["lines"]),
        }
        for page in source_pages
    ]

    recipe_number = cover["recipe_number"]
    return {
        "id": f"recipe-{key.lower()}",
        "collection_order": recipe_number,
        "title": clean_text(title),
        "subtitle": clean_text(subtitle),
        "summary": summary,
        "servings": parse_servings(full_ocr),
        "cook_time_minutes": parse_cook_time(full_ocr),
        "nutrition": parse_nutrition(full_ocr),
        "ingredients": ingredients,
        "steps": steps,
        "tags": {
            "cuisine": list(dict.fromkeys(cuisine)),
            "ingredients": primary,
            "recipe_items": recipe_items,
        },
        "images": {
            "dish": None if standalone else f"images/recipe-{recipe_number:03d}-dish.jpg",
            "cover": cover["image"],
            "method": method["image"],
        },
        "single_page_recipe": standalone,
        "completeness": completeness,
        "source_metadata": source_metadata,
        "ocr_text": full_ocr,
    }


def tag_counts(recipes: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    counts = Counter(tag for recipe in recipes for tag in recipe["tags"][group])
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def main() -> None:
    source = json.loads(SOURCE.read_text())
    pages = source["pages"]
    by_pair: dict[int, list[dict[str, Any]]] = {}
    for page in pages:
        by_pair.setdefault(page["recipe_number"], []).append(page)

    recipes: list[dict[str, Any]] = []
    for pair_number in sorted(by_pair):
        pair = sorted(by_pair[pair_number], key=lambda page: page["metadata"]["sequence"])
        if pair_number == 102:
            fabcake = build_recipe(
                "102a",
                pair[0],
                pair[0],
                standalone=False,
                completeness="missing_method",
            )
            fabcake["images"]["method"] = None
            recipes.append(fabcake)

            lasagna = build_recipe(
                "102b",
                pair[1],
                pair[1],
                standalone=False,
                completeness="missing_cover",
            )
            lasagna["images"]["cover"] = None
            lasagna["images"]["dish"] = None
            recipes.append(lasagna)
            continue
        if all(is_standalone(page) for page in pair):
            for index, page in enumerate(pair):
                key = f"{pair_number:03d}{chr(ord('a') + index)}"
                recipes.append(build_recipe(key, page, page, standalone=True))
        else:
            key = f"{pair_number:03d}"
            recipes.append(build_recipe(key, pair[0], pair[1], standalone=False))

    payload = {
        "collection": {
            "name": "The Purple Carrot Archive",
            "recipe_count": len(recipes),
            "image_count": len(pages),
            "source": "Photos on this Mac",
            "captured_on": "2026-06-05",
            "notes": "Images are ordered oldest-first within each capture run. Six complete Extras sheets are treated as single-page recipes. One adjacent pair contains a Fabcake Sliders cover and a Sweet Potato Lasagna method, so they are retained as two partial recipes.",
        },
        "filters": {
            "cuisine": tag_counts(recipes, "cuisine"),
            "ingredients": tag_counts(recipes, "ingredients"),
            "recipe_items": tag_counts(recipes, "recipe_items"),
        },
        "recipes": recipes,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print(f"Wrote {len(recipes)} recipes to {OUTPUT}")
    print(
        "Ingredients:",
        min(len(recipe["ingredients"]) for recipe in recipes),
        "-",
        max(len(recipe["ingredients"]) for recipe in recipes),
    )
    print(
        "Steps:",
        min(len(recipe["steps"]) for recipe in recipes),
        "-",
        max(len(recipe["steps"]) for recipe in recipes),
    )


if __name__ == "__main__":
    main()
