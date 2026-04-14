# schlib_split

Load an existing multi-symbol SchLib and split it into one SchLib file per
symbol.

This sample uses the RT Super C1 SchLib asset. It is the direct SchLib-only
version of the split flow; it does not extract symbols from a SchDoc first.

## What It Shows

1. `AltiumSchLib(...)`
2. `schlib.split(output_dir)`
3. Iterating the original `schlib.symbols`
4. Writing a JSON manifest that maps symbol names to split library files

## Run

From the package root:

```powershell
uv run python examples\schlib_split\schlib_split.py
```

## Input

```text
examples/assets/schlib/RT_SUPER_C1.SCHLIB
```

## Output

```text
examples/schlib_split/output/split/
examples/schlib_split/output/schlib_split_manifest.json
```
