# pcblib_add_free_3d_extruded

Create a PcbLib with one sample footprint and add a generic extruded 3D body.
This is the PcbLib equivalent of the free extruded PcbDoc 3D body workflow.

## What It Shows

1. `AltiumPcbLib.add_footprint(...)`
2. `AltiumPcbFootprint.add_pad(...)`
3. `AltiumPcbFootprint.add_track(...)`
4. `AltiumPcbFootprint.add_extruded_3d_body(...)`
5. Saving the library through `pcblib.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcblib_add_free_3d_extruded\pcblib_add_free_3d_extruded.py
```

## Output

```text
examples/pcblib_add_free_3d_extruded/output/pcblib_add_free_3d_extruded.PcbLib
```
