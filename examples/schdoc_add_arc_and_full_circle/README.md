# schdoc_add_arc_and_full_circle

Open a blank schematic, add a small gallery of circular arcs and full circles,
and save the modified `.SchDoc`.

This example keeps the two together because Altium stores both with the same
schematic arc record. A full circle is just a 360-degree arc.

1. load an existing `SchDoc`
2. create several detached full circles with `make_sch_full_circle(...)`
3. create several detached partial arcs with `make_sch_arc(...)`
4. insert each object with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. using the public mil-facing API for circular geometry without dealing with
   raw internal coord fractions
2. creating detached full circles with ascending radii, including fractional
   mil radius values
3. creating detached partial arcs with different start and end angles
4. using `SchPointMils` for public center coordinates
5. using `LineWidth` and `ColorValue` to vary stroke thickness and color
6. reopening the written file and inspecting the created arc objects

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_arc_and_full_circle\schdoc_add_arc_and_full_circle.py
```

## Input

This sample uses:

```text
examples/schdoc_add_arc_and_full_circle/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_arc_and_full_circle/output/blank_with_arc_and_full_circle.SchDoc
```

## Expected Result

The output file should contain:

1. a top row of black full circles with ascending radii:
   `150.0 mil`, `250.5 mil`, `350.25 mil`, and `450.75 mil`
2. a middle row of partial arcs with different start/end angle sweeps
3. a bottom row of full circles showing line color and `LineWidth` variation,
   including `SMALLEST`, `SMALL`, `MEDIUM`, and `LARGE`
4. all geometry defined through the public mil-facing API, not raw internal
   schematic coord values

The script also prints the reopened centers, radii, angles, widths, and colors
so the example is easy to sanity-check without inspecting the binary file
directly.
