# pcbdoc_add_track

Add PCB track primitives to an existing PcbDoc.

The sample creates a rectangular board outline, sets the origin to the
lower-left outline corner, and adds top copper, bottom copper, overlay,
mechanical-layer, and keepout track objects in neat rows with several widths
and net assignments.

## What It Shows

1. `AltiumPcbDoc.from_file(...)`
2. `AltiumPcbDoc.set_outline_rectangle_mils(...)`
3. `AltiumPcbDoc.add_track(...)`
4. `AltiumPcbDoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_add_track\pcbdoc_add_track.py
```

## Output

```text
examples/pcbdoc_add_track/output/pcbdoc_add_track.PcbDoc
```
