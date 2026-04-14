# schdoc_add_off_sheet_connector

Open a blank schematic, add left and right off-sheet connectors, and save the modified
`.SchDoc`.

This example uses the common built-in off-sheet connector pattern:

1. load an existing `SchDoc`
2. describe connector locations with `SchPointMils`
3. call `make_sch_off_sheet_connector(...)` with `OffSheetConnectorStyle.LEFT` and `OffSheetConnectorStyle.RIGHT`
4. insert the objects with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating detached off-sheet connectors with `make_sch_off_sheet_connector(...)`
2. using `SchPointMils` for the public placement coordinate
3. using `OffSheetConnectorStyle` for the native left/right connector direction
4. using `SchFontSpec` and `TextOrientation` for the visible connector name
5. using `ColorValue` helpers instead of raw Win32 color integers
6. reopening the written file and inspecting the created objects

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_off_sheet_connector\schdoc_add_off_sheet_connector.py
```

## Input

This sample uses:

```text
examples/schdoc_add_off_sheet_connector/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_off_sheet_connector/output/blank_with_off_sheet_connector.SchDoc
```

## Expected Result

The output file should contain:

1. one visible left-style off-sheet connector named `OFF_LEFT`
2. one visible right-style off-sheet connector named `OFF_RIGHT`
3. the common horizontal connector orientation
4. visible text using `Arial 10`

The script also prints the reopened connector text, style, orientation, font,
and location so the example is easy to sanity-check without inspecting the
binary file directly.
