# pcblib_add_via_ipc4761_matrix

Create a PcbLib footprint with IPC-4761 via-structure examples and a
footprint-level primitive parameter.

## What It Shows

1. `AltiumPcbLib.add_footprint(...)`
2. `AltiumPcbFootprint.set_footprint_primitive_parameter(...)`
3. `AltiumPcbFootprint.add_via(...)`
4. `PcbIpc4761ViaType`
5. Passing explicit `AltiumPcbViaStructureFeature` rows into `add_via(...)`
   for a Type7 filling/capping via
6. Reopening the generated PcbLib to verify the primitive parameter and
   footprint-local via-structure side tables

## Run

From the repository root:

```powershell
uv run python examples\pcblib_add_via_ipc4761_matrix\pcblib_add_via_ipc4761_matrix.py
```

## Output

```text
examples/pcblib_add_via_ipc4761_matrix/output/pcblib_add_via_ipc4761_matrix.PcbLib
examples/pcblib_add_via_ipc4761_matrix/output/pcblib_add_via_ipc4761_matrix.json
```

Open the generated PcbLib in Altium Designer and inspect the labeled footprint.
The JSON file records the read-back primitive parameter, via type list, side
table counts, and custom Type7 feature material settings.
