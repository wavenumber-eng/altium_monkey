# pcblib_split

Load an existing multi-footprint PcbLib and split it into one PcbLib file per
footprint.

This sample uses the RT Super C1 PcbLib asset. It is the direct PcbLib-only
version of the split flow; it does not extract footprints from a board first.

## What It Shows

1. `AltiumPcbLib.from_file(...)`
2. `pcblib.split(output_dir)`
3. iterating the original `pcblib.footprints`
4. writing a JSON manifest that maps footprint names to split library files

## Run

From the package root:

```powershell
uv run python examples\pcblib_split\pcblib_split.py
```

## Input

```text
examples/assets/pcblib/RT_SUPER_C1.PcbLib
```

## Output

```text
examples/pcblib_split/output/split/
examples/pcblib_split/output/pcblib_split_manifest.json
```
