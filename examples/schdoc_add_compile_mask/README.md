# schdoc_add_compile_mask

This example opens an existing schematic, creates detached compile-mask
directives, adds them to the document, and saves the result.

It demonstrates:

- compile masks with different border widths
- compile masks with different fill colors
- collapsed and expanded compile-mask state
- absolute bounds placement through `SchRectMils`

The public API uses:

- `SchRectMils` for compile-mask bounds
- `ColorValue` for border and fill colors
- `LineWidth` for compile-mask border thickness

## Run

```powershell
uv run python examples\schdoc_add_compile_mask\schdoc_add_compile_mask.py
```

## Output

The script writes:

- `output/blank_with_compile_masks.SchDoc`

After saving, it reopens the output and prints the compile-mask count, bounds,
and collapsed state.
