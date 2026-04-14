# schdoc_add_bus_and_bus_entry

Open a blank schematic, add one bus and several bus entries, and save the
modified `.SchDoc`.

This example keeps the related connectivity objects together because bus
entries are normally used with a bus:

1. load an existing `SchDoc`
2. describe the bus path with `SchPointMils`
3. call `make_sch_bus(...)`
4. call `make_sch_bus_entry(...)` for each entry segment
5. insert every object with `AltiumSchDoc.add_object(...)`
6. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating a detached bus path with `make_sch_bus(...)`
2. creating detached bus entries with `make_sch_bus_entry(...)`
3. using `SchPointMils` for all public connectivity coordinates
4. using `LineWidth` for bus and bus-entry thickness
5. using `ColorValue` helpers instead of raw Win32 color integers
6. reopening the written file and inspecting the created objects

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_bus_and_bus_entry\schdoc_add_bus_and_bus_entry.py
```

## Input

This sample uses:

```text
examples/schdoc_add_bus_and_bus_entry/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_bus_and_bus_entry/output/blank_with_bus_and_entries.SchDoc
```

## Expected Result

The output file should contain:

1. one visible bus with a three-point path and `LineWidth.LARGE`
2. three visible 100 mil by 100 mil bus entries connected to that bus
3. all connectivity objects colored `#000000`

The script also prints the reopened bus point list and the start/end points for
each bus entry so the example is easy to sanity-check without inspecting the
binary file directly.
