# schdoc_add_polygon

This example opens an existing schematic, creates detached polygon objects,
adds them to the document, and saves the result.

It demonstrates:

- polygons with different point counts and outlines
- filled polygons with different fill colors
- polygon border thickness variation
- transparent-fill vs opaque-fill behavior

The public API uses:

- `SchPointMils` lists for polygon vertices
- `ColorValue` for stroke and fill colors
- `LineWidth` for polygon border thickness

Native schematic V5 polygon serialization does not carry a line-style field, so
the public factory intentionally focuses on vertices, fill state, transparency,
and border thickness.

## Run

```powershell
uv run python examples\schdoc_add_polygon\schdoc_add_polygon.py
```

## Output

The script writes:

- `output/blank_with_polygons.SchDoc`

After saving, it reopens the output and prints the polygon count, point lists,
and fill/border state.
