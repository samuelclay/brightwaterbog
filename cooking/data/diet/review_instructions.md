# Vegan review

The cookbook is all vegetarian. The site's Vegetarian / Vegan toggle needs to know which
recipes are vegan. `tools/classify_diet.py` settles clear cases from the ingredient lists
and leaves the rest for review: recipes whose only animal ingredients are optional or
come with an alternative, or that use an ambiguous store-bought item.

## Input

`data/diet/review_input/batch_NN.json`: a list of recipes, each with `pair` (id),
`title`, `source`, `flagged` (the ingredient lines that sent it to review),
`ingredients` (the full list), and `nyt_keywords` (any NYT diet keywords).

## Decide each recipe

**Vegan** means no animal products: no dairy (milk, butter, cream, cheese, yogurt,
ghee, buttermilk, whey), no eggs, no honey, no gelatin, and no fish or meat products
(fish sauce, anchovy, Worcestershire, dashi, chicken stock).

Mark a recipe **vegan** when every animal-product ingredient is either:

- explicitly optional ("feta, optional", "(optional) grated Parmesan"), or
- offered with a plant alternative in the same line ("butter or olive oil", "honey or
  maple syrup", "milk (any kind)", "dairy or nondairy yogurt", "vegan or regular
  mayonnaise"). The cook takes the plant option.

Mark it **not vegan** when any animal product is required. That includes:

- a garnish or "for serving" line that isn't marked optional ("Parmesan, for serving")
- a line whose alternatives are all animal ("Parmesan or pecorino", "butter or ghee")

Read "to taste" as a quantity, not as permission to leave something out.

Store-bought items, unless the line says otherwise:

- **Vegan:** dried pasta, bread, tortillas, phyllo, pizza dough, store-bought puff
  pastry (common brands are vegan), dark or semisweet chocolate and chocolate chips,
  sprinkles
- **Not vegan:** milk chocolate, all-butter pastry, homemade pie crust or biscuits made
  with butter, graham crackers (honey), caramel and toffee (dairy), pesto (Parmesan),
  kimchi (fish sauce), ravioli/tortellini/pierogi (cheese, egg), wonton and dumpling
  wrappers (egg), store-bought gnocchi (often egg)
- **Cookies:** judge by type. Shortbread, ladyfingers, and amaretti are not vegan;
  Oreo-style chocolate sandwich cookies are.

An item labeled vegan, dairy-free, egg-free, or vegetarian (for Worcestershire and fish
sauce) is vegan. If a line names a homemade component ("pesto, see recipe"), judge it by
the ingredients listed for that component in the same recipe.

## Output

Write exactly one JSON object to `data/diet/reviewed/batch_NN.json` (same NN as the
input), keyed by every `pair` in the batch:

```json
{"1012408": {"vegan": false, "why": "Parmesan, for serving (not optional)"},
 "1023570": {"vegan": true, "why": "only animal item is optional fish sauce"}}
```

Keep `why` short and name the deciding ingredient. Cover every recipe in the batch.
