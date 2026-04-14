# pcbdoc_add_free_3d_extruded

Open an existing blank PcbDoc and add a free board-level extruded 3D body. This
is the generic Altium 3D Body workflow where a 2D outline is extruded between a
standoff height and an overall height.

## What It Shows

1. `AltiumPcbDoc.add_extruded_3d_body(...)`
2. Supplying a polygon projection in mils
3. Setting standoff height, overall height, side, color, and opacity
4. Saving the modified board through `pcbdoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_add_free_3d_extruded\pcbdoc_add_free_3d_extruded.py
```

## Output

```text
examples/pcbdoc_add_free_3d_extruded/output/pcbdoc_add_free_3d_extruded.PcbDoc
```
