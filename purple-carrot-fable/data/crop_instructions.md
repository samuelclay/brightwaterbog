# Photo-region mapping instructions

You are locating photo regions on photographed Purple Carrot recipe cards so they can be cropped out programmatically.

## Files

- Images: `/Users/sclay/projects/brightwaterbog/purple-carrot-fable/images/full/`
- Each pair NNN has `NNNa_IMG_XXXX.jpg` (front) and `NNNb_IMG_XXXX.jpg` (back). `ls` the directory for exact names.
- Write one JSON file per pair: `/Users/sclay/projects/brightwaterbog/purple-carrot-fable/data/crops/NNN.json`

## What to locate

**Front (`a` image) — the hero food photo.** The single large glamour photograph of the finished dish.
- Portrait binder pages (pairs 1–83): the photo fills the top ~half of the card, ending at the purple SERVINGS/COOK TIME banner. Exclude the banner, the white margins, and anything outside the card (binder rings, table).
- Landscape cards (pairs 84–127): the photo is the large image occupying the top of the card (the title text may overlay it — that's fine, include the title overlay if it sits on the photo). Exclude the white summary/ingredients panel below.

**Back (`b` image) — one box per numbered step photo.** The small process photos (wood cutting board shots etc.).
- Portrait era: a horizontal strip of ~5 photos, each with a numbered circle badge.
- Landscape era: 6 photos in a grid, each above its titled step text.
- Match each box to its step number (the number in the purple/green badge). If a step has no photo, skip it.

## Output format (per pair)

```json
{
  "id": 84,
  "hero": [0.062, 0.081, 0.938, 0.522],
  "steps": [
    {"n": 1, "box": [0.238, 0.118, 0.428, 0.268]},
    {"n": 2, "box": [0.443, 0.118, 0.633, 0.268]}
  ]
}
```

- Boxes are `[x0, y0, x1, y1]` as FRACTIONS of the image's full width/height (0.0 = left/top edge, 1.0 = right/bottom edge), 3 decimals.
- **Bias the box 1–2% INSIDE the photo's true edges** — a slightly tight crop looks fine; including white margin or neighboring content looks broken.
- The cards were photographed by hand, so they sit at slight angles within the frame — box the photo region as it appears in the image, not the idealized card.
- Look carefully at the actual image before writing coordinates. Double-check that y0 < y1 and x0 < x1 and that the box contains the photo, not the text.
- `hero`: null if the page has no clear hero photo. `steps`: [] if no step photos.

## Special cases

Pairs 15, 25, 59, 102: the `b` image is NOT a back card — it's a standalone recipe page (recipe ids 128, 129, 130, 131 respectively). For those, ADDITIONALLY write a separate file (128.json/129.json/130.json/131.json) describing that single image: its `hero` box if the page has a clear hero/food photo (else null) and its `steps` boxes if it has numbered step photos (131 is a landscape directions side — it has 6 step photos). In the main NNN.json for those pairs, set `steps: []`.

## Return value

Return ONLY one line per JSON file written: `NNN: hero=yes/no, N step boxes`.
