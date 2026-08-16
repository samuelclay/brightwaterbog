# Fusion DXF conversion

Run the converter with the project virtual environment:

```sh
.side-glass-venv/bin/python inset_dxf_polygons.py "Sketches/Front Glass.dxf"
```

One run creates three physically sized files beside the Fusion export:

- `Front Glass.svg` — cleaned, non-inset cut lines
- `Front Glass inset.svg` — one inward-offset path per glass region
- `Front Glass Silhouette.dxf` — the cleaned profile using only plain `LINE`
  entities, avoiding Silhouette Studio's DXF arc/bulge rendering problems

The converter reads Fusion's DXF units, rebuilds closed regions, removes open
construction linework, flattens curves, and uses the same cleaned profile for all
three outputs.

## Per-sketch settings

Most clean Fusion exports need no config. To force a physical width:

```sh
.side-glass-venv/bin/python inset_dxf_polygons.py \
  "Sketches/New Panel.dxf" --target-width 9.5
```

If construction geometry extends beyond the intended panel, inspect it once and
save the decision in `New Panel.conversion.json` beside the DXF:

```json
{
  "target_width_inches": 9.5,
  "clip_x_bounds_inches": [-4.6875, 4.6875]
}
```

For a four-sided crop, use `clip_bounds_inches` with
`[min_x, min_y, max_x, max_y]`. Coordinates are inches in the Fusion export.
Command-line settings override the sidecar. Re-exporting the same Fusion sketch
then needs only the standard one-line command.
