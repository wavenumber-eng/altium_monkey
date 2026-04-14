# schdoc_add_line

Open a blank schematic, add a small gallery of plain line segments, and save
the modified `.SchDoc`.

This example focuses on the native schematic `Line` object:

1. load an existing `SchDoc`
2. create several detached line objects with `make_sch_line(...)`
3. insert each object with `AltiumSchDoc.add_object(...)`
4. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. using the public mil-facing API for absolute line start/end coordinates
2. creating detached lines with different `LineWidth` values
3. creating detached lines with different `LineStyle` values
4. creating detached lines with different lengths, slopes, and colors
5. reopening the written file and inspecting the created line objects

## Important Note

Altium schematic endpoint decorations such as arrows, tails, circles, and
squares belong to the separate `Polyline` object, not the plain `Line` object.
See `schdoc_add_polyline`.

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_line\schdoc_add_line.py
```

## Input

This sample uses:

```text
examples/schdoc_add_line/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_line/output/blank_with_lines.SchDoc
```

## Expected Result

The output file should contain:

1. a top row of black solid lines showing `SMALLEST`, `SMALL`, `MEDIUM`, and
   `LARGE` line widths
2. a middle row of black `SOLID`, `DASHED`, `DOTTED`, and `DASH_DOT` lines
3. a bottom row of lines with different lengths, slopes, colors, widths, and
   styles

The script also prints the reopened start/end points, widths, styles, and
colors so the example is easy to sanity-check without inspecting the binary
file directly.
