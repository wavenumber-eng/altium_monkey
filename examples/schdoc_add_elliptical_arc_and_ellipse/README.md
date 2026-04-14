# schdoc_add_elliptical_arc_and_ellipse

Open a blank schematic, add a small gallery of ellipses and elliptical arcs,
and save the modified `.SchDoc`.

This example keeps the two together because both use the same public mil-facing
center-plus-radii model, and both rely on the same internal coord-style radius
serialization under the hood.

1. load an existing `SchDoc`
2. create several detached ellipses with `make_sch_ellipse(...)`
3. create several detached elliptical arcs with `make_sch_elliptical_arc(...)`
4. insert each object with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. using the public mil-facing API for two-axis ellipse geometry without
   dealing with raw internal coord fractions
2. creating detached ellipses with ascending primary and secondary radii,
   including fractional mil radius values
3. creating detached elliptical arcs with different start and end angle sweeps
4. using `SchPointMils` for public center coordinates
5. using `LineWidth` and `ColorValue` to vary stroke thickness, stroke color,
   and fill color
6. reopening the written file and inspecting the created ellipse objects

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_elliptical_arc_and_ellipse\schdoc_add_elliptical_arc_and_ellipse.py
```

## Input

This sample uses:

```text
examples/schdoc_add_elliptical_arc_and_ellipse/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_elliptical_arc_and_ellipse/output/blank_with_elliptical_arc_and_ellipse.SchDoc
```

## Expected Result

The output file should contain:

1. a top row of filled black-stroke ellipses with ascending radii pairs
2. a middle row of black elliptical arcs with different start/end angle sweeps
3. a bottom row of ellipses showing line width, stroke color, and fill style
   variation
4. all geometry defined through the public mil-facing API, not raw internal
   schematic coord values

The script also prints the reopened centers, radii, angles, widths, and fill
flags so the example is easy to sanity-check without inspecting the binary file
directly.
