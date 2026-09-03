# NYT recipe tagging instructions

You are assigning filter tags to recipes saved from NYT Cooking so they sit in the same
grid, with the same three filter menus, as the family's Purple Carrot recipe cards.

## Files

- Your batch: `/Users/sclay/projects/brightwaterbog/cooking/data/nyt/tagging_input/batch_NN.json`
  (you are told which NN). It is a JSON list; each entry has `id`, `title`, `description`,
  `nyt_tags` (NYT's own tags, grouped by type — `cuisine`, `ingredient`, `dish`, `meal`,
  `diet`, ...; treat them as hints, not answers) and `ingredients` (the full ingredient lines).
- The vocabulary already in use, with counts:
  `/Users/sclay/projects/brightwaterbog/cooking/data/nyt/tagging_input/vocab.txt`.
  Read it first. Reuse an existing tag whenever it fits — matching the existing spelling
  exactly — so the filters merge instead of fragmenting. Invent a new tag only when nothing
  in the list fits.
- Write one file per recipe to
  `/Users/sclay/projects/brightwaterbog/cooking/data/nyt/tags/<id>.json`
  (create nothing else there):

```json
{"cuisine_tags": ["Italian", "Mediterranean"], "ingredient_tags": ["chickpeas", "tomato", "pasta"], "item_tags": ["parmesan", "capers", "lemon", "red chile flakes"]}
```

## Rules

- `cuisine_tags`: 1–3 tags, Title Case. Prefer this vocabulary when it fits: Italian, Mexican,
  Indian, Thai, Chinese, Japanese, Korean, Vietnamese, Mediterranean, Greek, Middle Eastern,
  French, Spanish, American, Southern, Cajun/Creole, Caribbean, African, Ethiopian, Moroccan,
  Latin American, Fusion, Comfort Food, plus any others already in vocab.txt (New England,
  Eastern European, Jewish, German...). Judge from the dish itself: tacos → Mexican, dal →
  Indian, lasagna → Italian, chocolate chip cookies → American. Desserts and drinks with no
  clear origin → American. Use Fusion for cross-cuisine dishes, Comfort Food for casseroles,
  mac and cheese, potpie, etc. (usually alongside a cuisine).
- `ingredient_tags`: 2–6 lowercase tags for the MAIN components someone would filter by —
  legumes (chickpeas, lentils, white beans, black beans), proteins (tofu, eggs, tempeh,
  chorizo, scallops), primary vegetables, starches (pasta, rice, potato, farro, tortilla,
  bread). Singular/base names in the vocabulary's style: "chickpeas", "tofu", "sweet potato",
  "pasta", "rice", "mushrooms", "cauliflower", "eggs", "white beans". For a cookie or cake,
  the defining component (chocolate chips, cranberries, beets). All pasta shapes → "pasta"
  (add the shape as a pantry item only if notable, e.g. "orzo", "soba noodles" are fine as
  main ingredients since vocab has them). Cheese that IS the dish (ricotta dumplings, baked
  feta) can be a main ingredient; otherwise cheese is a pantry item.
- `item_tags`: 2–8 lowercase tags for notable spices, condiments, cheeses, herbs, and pantry
  items: "cumin", "tahini", "harissa", "miso", "parmesan", "feta", "capers", "lemon", "ginger",
  "coconut milk", "curry powder", "za'atar", "chile crisp", "brown butter", "vanilla".
  Skip universal basics: salt, pepper, olive oil, vegetable oil, sugar, flour, water, butter,
  garlic, onion (unless the dish is about them: onion tart, garlic bread crumbs).
- Keep tags short: no quantities, no adjectives like "fresh"/"chopped"; "parsley" not
  "flat-leaf parsley"; "red chile flakes" not "red-pepper flakes"; "scallions" not "scallion".
- These recipes are not all plant-based — tag meat, fish, eggs and dairy honestly.

## Process

Read vocab.txt, then your batch file, then write every `<id>.json` for the batch.

## Return value

Return ONLY a compact summary, one line per recipe: `<id>: <title> — <cuisine tags>`.
