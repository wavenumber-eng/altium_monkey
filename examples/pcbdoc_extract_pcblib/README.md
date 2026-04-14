# pcbdoc_extract_pcblib

Load a project PcbDoc and extract its placed footprints back into PcbLib files.

This example shows both forms supported by the public API:

1. a combined PcbLib containing one footprint definition per extracted component pattern
2. split one-footprint PcbLib files that are convenient for DBLib-style library workflows

## What It Shows

1. `AltiumDesign.from_prjpcb(...)`
2. `AltiumDesign.load_pcbdoc(...)`
3. `AltiumPcbDoc.extract_pcblib(...)`
4. `AltiumPcbLib.split(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_extract_pcblib\pcbdoc_extract_pcblib.py
```

## Output

The script writes:

```text
examples/pcbdoc_extract_pcblib/output/combined/RT_SUPER_C1_extracted.PcbLib
examples/pcbdoc_extract_pcblib/output/split/
examples/pcbdoc_extract_pcblib/output/pcblib_extraction_manifest.json
```
