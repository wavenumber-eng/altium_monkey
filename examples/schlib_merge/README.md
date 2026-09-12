# schlib_merge

Merge a folder of SchLib files into one combined SchLib.

This sample scans the shared schematic-library assets, inspects every SchLib,
and merges the inputs that pass full parsing. The manifest reports advertised
source symbols, mergeable counts, and any parser-rejected legacy libraries with
their error messages. Duplicate source names in mergeable libraries are handled
with the default rename behavior.

## What It Shows

1. Discovering SchLib files by case-insensitive `.SchLib` extension
2. `AltiumSchLib.get_symbol_names(...)`
3. Separating fast-index discovery from full parseability
4. `AltiumSchLib.merge(input_paths, output_path, handle_conflicts="rename")`
5. Reopening the merged library through `AltiumSchLib`

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
