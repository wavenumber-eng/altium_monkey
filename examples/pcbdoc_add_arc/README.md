# pcbdoc_add_arc

Add PCB arc primitives to an existing PcbDoc.

The sample creates a rectangular board outline, sets the origin to the
lower-left outline corner, and places full circles and partial arcs in uniform
rows with different radii, angles, widths, layers, and net assignments.

## What It Shows

1. `AltiumPcbDoc.from_file(...)`
2. `AltiumPcbDoc.set_outline_rectangle_mils(...)`
3. `AltiumPcbDoc.add_arc(...)`
4. `AltiumPcbDoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_add_arc\pcbdoc_add_arc.py
```

## Output

```text
examples/pcbdoc_add_arc/output/pcbdoc_add_arc.PcbDoc
```
