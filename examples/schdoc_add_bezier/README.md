# schdoc_add_bezier

This example opens an existing schematic, creates detached bezier curves,
adds them to the document, and saves the result.

It demonstrates:

- single-segment cubic bezier curves
- chained bezier segments using native `4 + 3n` control-point groups
- stroke color variation
- bezier stroke thickness variation

The public API uses:

- `SchPointMils` lists for bezier control points
- `ColorValue` for stroke color
- `LineWidth` for bezier stroke thickness

Native schematic V5 bezier serialization does not carry a line-style field, so
the public factory intentionally focuses on control points, color, and stroke
thickness.

## Run

```powershell
uv run python examples\schdoc_add_bezier\schdoc_add_bezier.py
```

## Output

The script writes:

- `output/blank_with_beziers.SchDoc`

After saving, it reopens the output and prints the bezier count, control-point
lists, and stroke widths.
