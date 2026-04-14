# schdoc_add_polyline

Open a blank schematic, add a small gallery of detached polylines, and save
the modified `.SchDoc`.

This example focuses on the native schematic `Polyline` object:

1. load an existing `SchDoc`
2. create detached polylines with `make_sch_polyline(...)`
3. insert each object with `AltiumSchDoc.add_object(...)`
4. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. using the public mil-facing API for polyline vertices through `points_mils`
2. creating detached polylines with different start and end marker shapes
3. creating detached polylines with different endpoint marker sizes
4. creating detached polylines with different line styles and colors
5. reopening the written file and inspecting the created polyline objects

## Important Note

`line_shape_size` uses the same four-step schematic size enum as `line_width`.
That matches Altium's native `TSize` model for polyline endpoint markers.

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_polyline\schdoc_add_polyline.py
```

## Input

This sample uses:

```text
examples/schdoc_add_polyline/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_polyline/output/blank_with_polylines.SchDoc
```

## Expected Result

The output file should contain:

1. a top row showing common end-marker shapes
2. a second row showing the same marker shapes on the start of the path
3. a third row showing `SMALLEST`, `SMALL`, `MEDIUM`, and `LARGE` endpoint sizes
4. a bottom row showing `SOLID`, `DASHED`, `DOTTED`, and `DASH_DOT` polyline styles

The script also prints the reopened polyline points, widths, styles, marker
shapes, and marker sizes so the example is easy to sanity-check without
inspecting the binary file directly.
