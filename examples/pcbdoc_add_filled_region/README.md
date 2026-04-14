# pcbdoc_add_filled_region

Add filled polygon regions to an existing PcbDoc.

The sample creates a rectangular board outline, sets the origin to the
lower-left outline corner, and places top and bottom copper regions, a region
with a hole, and a keepout region inside the board. The public method is
`AltiumPcbDoc.add_region(...)` because Altium represents filled PCB polygons
as region primitives.

## What It Shows

1. `AltiumPcbDoc.from_file(...)`
2. `AltiumPcbDoc.set_outline_rectangle_mils(...)`
3. `AltiumPcbDoc.add_region(...)`
4. Region holes through `hole_points_mils`
5. Keepout regions through `is_keepout`
6. `AltiumPcbDoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_add_filled_region\pcbdoc_add_filled_region.py
```

## Output

```text
examples/pcbdoc_add_filled_region/output/pcbdoc_add_filled_region.PcbDoc
```
