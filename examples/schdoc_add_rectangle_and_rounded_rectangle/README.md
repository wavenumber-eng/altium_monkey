# schdoc_add_rectangle_and_rounded_rectangle

This example opens an existing schematic, creates detached rectangle and
rounded-rectangle objects, adds them to the document, and saves the result.

It demonstrates:

- plain rectangles with different `LineStyle` values
- rectangle border width, fill, and transparent-fill variation
- rounded rectangles with different X/Y corner radii
- rounded rectangles with different border widths and fill states

The public API uses mil-based helpers throughout:

- `SchRectMils` for bounds
- `ColorValue` for colors
- `LineWidth` for border thickness
- `LineStyle` for plain rectangle dash patterns

Rounded rectangles intentionally have a narrower public surface than plain
rectangles. Native Altium V5 rounded-rectangle records do not expose the same
`line_style` and `transparent_fill` options as plain rectangles, so those
options are omitted from `make_sch_rounded_rectangle(...)`.

## Run

```powershell
uv run python examples\schdoc_add_rectangle_and_rounded_rectangle\schdoc_add_rectangle_and_rounded_rectangle.py
```

## Output

The script writes:

- `output/blank_with_rectangles.SchDoc`

After saving, it reopens the output and prints the rectangle count, rounded
rectangle count, bounds, corner radii, and border style details.
