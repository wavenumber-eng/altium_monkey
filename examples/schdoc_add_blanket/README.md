# schdoc_add_blanket

This example opens an existing schematic, creates detached blanket directives,
adds them to the document, and saves the result.

It demonstrates:

- blanket border styles such as dashed, dotted, and solid
- blanket border thickness variation
- transparent vs opaque blanket fill behavior
- collapsed blanket state
- rectangular and non-rectangular blanket regions

The public API uses:

- `SchPointMils` lists for blanket vertices
- `ColorValue` for border and fill colors
- `LineWidth` for blanket border thickness
- `LineStyle` for blanket border pattern

## Run

```powershell
uv run python examples\schdoc_add_blanket\schdoc_add_blanket.py
```

## Output

The script writes:

- `output/blank_with_blankets.SchDoc`

After saving, it reopens the output and prints the blanket count, point lists,
and directive display state.
