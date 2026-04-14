# pcbdoc_insert_footprint_from_pcblib

Create a PCB document from scratch and place footprints from an existing PcbLib.

This example shows the preferred public container API for board assembly:
load a source library, select a footprint, place components with parameters and
pad-net assignments, then save the board.

## What It Shows

1. `AltiumPcbLib.from_file(...)`
2. `AltiumPcbDoc.set_outline_rectangle_mils(...)`
3. `AltiumPcbDoc.add_component_from_pcblib(...)`
4. `AltiumPcbDoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_insert_footprint_from_pcblib\pcbdoc_insert_footprint_from_pcblib.py
```

## Output

The script writes:

```text
examples/pcbdoc_insert_footprint_from_pcblib/output/pcbdoc_insert_footprint_from_pcblib.PcbDoc
```
