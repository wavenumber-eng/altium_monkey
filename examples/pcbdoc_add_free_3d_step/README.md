# pcbdoc_add_free_3d_step

Open an existing blank PcbDoc, embed a STEP model from disk, and add it as a
free board-level 3D body. The PcbDoc API mirrors the PcbLib embedded-model
workflow.

## What It Shows

1. `AltiumPcbDoc.add_embedded_model(...)`
2. Native checksum generation for the embedded STEP payload
3. `AltiumPcbDoc.add_embedded_3d_model(...)`
4. Automatic STEP projection and height inference through OCCT/CadQuery
5. Saving the modified board through `pcbdoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_add_free_3d_step\pcbdoc_add_free_3d_step.py
```

## Output

```text
examples/pcbdoc_add_free_3d_step/output/pcbdoc_add_free_3d_step.PcbDoc
```
