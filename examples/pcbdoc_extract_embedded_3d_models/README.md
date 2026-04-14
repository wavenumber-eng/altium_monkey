# pcbdoc_extract_embedded_3d_models

Load a project PcbDoc and write all embedded 3D model payloads to a folder.

This is useful when recovering STEP assets from a board file or auditing which
models are embedded directly inside the PcbDoc.

## What It Shows

1. `AltiumDesign.from_prjpcb(...)`
2. `AltiumDesign.load_pcbdoc(...)`
3. `AltiumPcbDoc.get_embedded_model_entries(...)`
4. `AltiumPcbDoc.extract_embedded_models(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_extract_embedded_3d_models\pcbdoc_extract_embedded_3d_models.py
```

## Output

The script writes:

```text
examples/pcbdoc_extract_embedded_3d_models/output/embedded_3d_models/
examples/pcbdoc_extract_embedded_3d_models/output/models.json
```
