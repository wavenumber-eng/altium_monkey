# pcblib_extract_3d_models

Load an existing PcbLib and extract its embedded 3D model payloads.

This example is intentionally separate from `hello_pcblib`. Use this when you
want to inspect or recover STEP models that are embedded inside an existing PCB
footprint library.

## What It Shows

1. `AltiumPcbLib.from_file(...)`
2. `AltiumPcbLib.extract_embedded_models(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcblib_extract_3d_models\pcblib_extract_3d_models.py
```

## Output

The script writes:

```text
examples/pcblib_extract_3d_models/output/extracted_models/
examples/pcblib_extract_3d_models/output/models.json
```
