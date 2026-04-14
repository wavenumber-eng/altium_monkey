# schdoc_add_wire_and_net_label

Open a blank schematic, add one wire plus one matching net label, and save the
modified `.SchDoc`.

This example keeps the related connectivity objects together because net labels
are normally attached to wires:

1. load an existing `SchDoc`
2. describe the wire path with `SchPointMils`
3. call `make_sch_wire(...)`
4. call `make_sch_net_label(...)`
5. insert every object with `AltiumSchDoc.add_object(...)`
6. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating a detached wire path with `make_sch_wire(...)`
2. creating a detached net label with `make_sch_net_label(...)`
3. using `SchPointMils` for all public connectivity coordinates
4. using `LineWidth` for wire thickness
5. using `SchFontSpec`, `TextOrientation`, and `TextJustification` for label
   formatting
6. using `ColorValue` helpers instead of raw Win32 color integers
7. reopening the written file and inspecting the created objects

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_wire_and_net_label\schdoc_add_wire_and_net_label.py
```

## Input

This sample uses:

```text
examples/schdoc_add_wire_and_net_label/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_wire_and_net_label/output/blank_with_wires_and_net_labels.SchDoc
```

## Expected Result

The output file should contain:

1. one visible horizontal wire named `CLK_MAIN`
2. one visible net label using `TextJustification.BOTTOM_LEFT`
3. the common horizontal, non-mirrored net-label orientation pattern

The script also prints the reopened wire point list plus the net label's text,
orientation, justification, and location so the example is easy to sanity-check
without inspecting the binary file directly.
