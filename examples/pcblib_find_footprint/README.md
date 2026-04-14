# pcblib_find_footprint

Build an index of footprint names across a folder of PcbLib files and run simple
fuzzy searches against that index.

This is useful when a project has many local footprint libraries and you need to
find which library contains a footprint before inserting it into a board or
splitting/combining libraries.

## What It Shows

1. `AltiumPcbLib.from_file(...)`
2. Iterating `pcblib.footprints`
3. Building a `{library_filename: [footprint_name, ...]}` dictionary
4. Searching footprint names with a lightweight Python fuzzy matcher

## Run

From the repo root:

```powershell
uv run python examples\pcblib_find_footprint\pcblib_find_footprint.py
```

To scan a different folder:

```powershell
uv run python examples\pcblib_find_footprint\pcblib_find_footprint.py --pcblib-dir path\to\pcblibs --query R0603 --query TC2030
```

The example uses queries such as `r0603`, `tc2030`, `msop`, and `tp smt` to
show exact, partial, and approximate matches.

## Output

The script prints the discovered footprints and writes:

```text
examples/pcblib_find_footprint/output/footprint_index.json
```
