# Purple Carrot recipe card extraction instructions

You are extracting structured recipe data from photos of Purple Carrot (plant-based meal kit) recipe cards.

## Files

- Images live in `/Users/sclay/projects/brightwaterbog/purple-carrot-fable/images/full/`
- Each recipe is a PAIR: `NNNa_IMG_XXXX.jpg` (front card) and `NNNb_IMG_XXXX.jpg` (back card), where NNN is the zero-padded pair number you were assigned. Run `ls` on the directory to get exact filenames for your assigned pairs.
- Write one JSON file per pair to `/Users/sclay/projects/brightwaterbog/purple-carrot-fable/data/extracted/NNN.json` (create nothing else there).

## Card formats (two eras)

**Portrait pages (roughly pairs 1–83):** Front = hero photo, a banner with "2 (4) SERVINGS" and "COOK TIME: XX (XX) MIN", title, subtitle ("with ..."), INGREDIENTS list, fine print with nutrition. Back = "Directions" with numbered steps (1–5 or so), each with a step photo; TIP paragraphs interspersed.

**Landscape cards (roughly pairs 84–127):** Front = hero photo, title, SUMMARY (servings, cook time), NUTRITION PER SERVING, a descriptive paragraph. Back = title + subtitle, FROM YOUR KITCHEN list, INGREDIENTS list, TOOLS list, and 6 titled steps (e.g., "PREPARE THE CAULIFLOWER") each with text.

## JSON schema per pair

```json
{
  "pair": 84,
  "title": "Greek-Style Cauliflower Steaks",
  "subtitle": "with Tzatziki Sauce and Mashed Yams",
  "servings": "2",
  "cook_time": "35 minutes",
  "nutrition": {"calories": "910", "fat": "69 g", "carbohydrates": "63 g", "protein": "21 g"},
  "description": "Descriptive paragraph from the front card, if present, else null",
  "from_your_kitchen": ["1 tbsp olive oil", "Salt and pepper"],
  "ingredients": ["1 head cauliflower", "8 oz Japanese yam", "..."],
  "tools": ["Baking sheet", "Box grater"],
  "steps": [
    {"n": 1, "title": "PREPARE THE CAULIFLOWER", "text": "Set your oven to broil on high. ..."},
    {"n": 2, "title": null, "text": "... (portrait-era steps have no titles: use null)"}
  ],
  "tips": ["TIP: Dill fronds are the wispy dark green leaves on the thick stems."],
  "cuisine_tags": ["Greek", "Mediterranean"],
  "ingredient_tags": ["cauliflower", "yam", "cucumber"],
  "item_tags": ["kalamata olives", "pumpkin seeds", "hemp seeds", "white vinegar"],
  "issues": null
}
```

Field rules:

- **Transcribe faithfully.** OCR the actual text, including quantities, unicode fractions (¾), and asterisk footnotes (keep `*` and put the footnote text in `tips`). Do not paraphrase steps — copy them fully.
- `title`: title case as printed (landscape cards print ALL CAPS — convert to Title Case).
- `subtitle`: the "with ..." line, without leading "with" capitalization changes (keep as printed, e.g., "with Tzatziki Sauce and Mashed Yams" → store "With Tzatziki Sauce and Mashed Yams" as printed casing but lowercase 'with' is fine). Null if none.
- `servings` / `cook_time`: as printed, e.g. "2 (4)" and "35 (45) min".
- `from_your_kitchen`, `tools`: null if the card era doesn't have them.
- `tips`: any TIP paragraphs from either card, plus ingredient footnotes. Empty array if none.
- `cuisine_tags`: 1–3 tags. Prefer this vocabulary when it fits: Italian, Mexican, Indian, Thai, Chinese, Japanese, Korean, Vietnamese, Mediterranean, Greek, Middle Eastern, French, Spanish, American, Southern, Cajun/Creole, Caribbean, African, Ethiopian, Moroccan, Latin American, Fusion, Comfort Food. Judge from the dish itself (e.g., tacos → Mexican). If nothing fits, choose your own sensible tag.
- `ingredient_tags`: 2–6 lowercase tags for the MAIN components someone would filter by: legumes (chickpeas, lentils, black beans...), proteins (tofu, tempeh, seitan), primary vegetables, starches (pasta, rice, potatoes, tortillas). Use singular/base names: "chickpeas", "tofu", "sweet potato", "pasta", "rice", "mushrooms", "cauliflower".
- `item_tags`: 2–8 lowercase tags for notable spices, condiments, and pantry items: "cumin", "tahini", "harissa", "miso", "chipotle", "coconut milk", "curry powder", "za'atar", etc. Skip universal basics (salt, pepper, olive oil, sugar, flour, water).
- `issues`: null normally. Otherwise a short string describing any problem: image appears rotated/sideways or upside down, front/back don't seem to be the same recipe, image is blurry/unreadable, the pair ordering seems swapped (front is a directions card), etc.

## Process

For each assigned pair: Read the `a` image, Read the `b` image, transcribe, write `NNN.json`. Then move to the next pair.

## Return value

Return ONLY a compact summary: one line per pair `NNN: Title [issues: ...]`, nothing else.
