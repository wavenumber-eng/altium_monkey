# schlib_merge

Merge a folder of SchLib files into one combined SchLib.

This sample scans the shared schematic-library assets, merges every SchLib it
finds, and writes a manifest showing the input libraries, source symbols, and
the final merged symbol names. Duplicate source names are handled with the
default rename behavior.

## What It Shows

1. Discovering SchLib files by case-insensitive `.SchLib` extension
2. `AltiumSchLib.get_symbol_names(...)`
3. `AltiumSchLib.merge(input_paths, output_path, handle_conflicts="rename")`
4. Reopening the merged library through `AltiumSchLib`

## Run

From the package root:

```powershell
uv run python examples\schlib_merge\schlib_merge.py
```

## Input

```text
examples/assets/schlib/
```

## Output

```text
examples/schlib_merge/output/merged/merged_schlib_assets.SchLib
examples/schlib_merge/output/schlib_merge_manifest.json
```
