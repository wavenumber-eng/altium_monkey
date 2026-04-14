# schdoc_insert_dblib_style

Load a generic two-pin resistor symbol from an existing schematic library,
place several resistor components into a blank schematic, and inject the
parameters and footprint assignments that would normally be resolved by a
database-backed library flow.

This example shows the canonical public path for DBLib-style placement:

1. load a generic source symbol from `R_2P.Schlib`
2. load a blank schematic with `AltiumSchDoc(...)`
3. place each resistor with `AltiumSchDoc.add_component_from_library(...)`
4. add resolved value, manufacturer, part-number, and sourcing parameters
5. add the resolved PCB footprint implementation
6. create a `.PrjPcb` that includes the generated `.SchDoc` and `.PcbLib`
7. persist the schematic with `AltiumSchDoc.save(...)`

## What It Shows

1. reusing one generic `R_2P` resistor symbol for multiple components
2. keeping DB-resolved symbol, footprint model, and footprint library names in
   the application row data
3. passing schematic placement coordinates separately from DB row data
4. placing components from a `.Schlib` file with public mil coordinates
5. adding DB-resolved component parameters after placement
6. using `Description` to populate the component description field
7. adding a PCB footprint implementation through the placed component API
8. keeping resistor pin hotspots on the 100 mil schematic grid
9. using Arial 12 bold designators with `=Value` comments
10. creating a project with `AltiumPrjPcbBuilder.add_schdoc(...)` and
   `AltiumPrjPcbBuilder.add_pcblib(...)`

## Run

From the project root:

```powershell
uv run python examples\schdoc_insert_dblib_style\schdoc_insert_dblib_style.py
```

## Input

This sample uses:

```text
examples/assets/schdoc/blank.SchDoc
examples/assets/schlib/R_2P.Schlib
examples/assets/pcblib/R0603_0.55MM_MD.PcbLib
```

## Output

The script writes:

```text
examples/schdoc_insert_dblib_style/output/schdoc_insert_dblib_style.SchDoc
examples/schdoc_insert_dblib_style/output/R0603_0.55MM_MD.PcbLib
examples/schdoc_insert_dblib_style/output/schdoc_insert_dblib_style.PrjPcb
```

## Expected Result

The output schematic should contain three side-by-side `R_2P` resistor
placements. Each placement uses the same generic resistor symbol, has DB-style
resolved parameters such as `Value`, `Manufacturer`, and `Manufacturer Part
Number`, and references the `R0603_0.55MM_MD` footprint implementation.

Pin hotspots remain on the 100 mil schematic grid. Designators use Arial 12
bold, and the visible comment field is `=Value`, so Altium displays the resolved
values such as `10kÎ©`.
