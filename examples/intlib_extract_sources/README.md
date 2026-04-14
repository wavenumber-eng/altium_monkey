# intlib_extract_sources

Extract editable source libraries from an Altium integrated library.

An `.IntLib` is a package that contains compiled component metadata plus
embedded source libraries such as `.SchLib`, `.PcbLib`, and sometimes
`.PCB3DLib`. This example opens an integrated library, prints a short summary,
extracts the embedded source files, and writes a simple `.LibPkg` that points at
the extracted files. When the extracted `.SchLib` or `.PcbLib` contains more
than one symbol or footprint, the example also splits it into one library file
per symbol or footprint.

## What It Shows

1. `AltiumIntLib(...)`
2. Reading component and source-entry metadata
3. Extracting embedded source streams with `extract_sources(...)`
4. Opening extracted `.SchLib` and `.PcbLib` files with the normal library APIs
5. Splitting multi-symbol `.SchLib` and multi-footprint `.PcbLib` files
6. Writing an extraction manifest without reusing machine-local source paths

## Run

From the package root:

```powershell
uv run python examples\intlib_extract_sources\intlib_extract_sources.py
```

## Output

The script writes:

```text
examples/intlib_extract_sources/output/extracted_sources/
examples/intlib_extract_sources/output/split_sources/
examples/intlib_extract_sources/output/intlib_extract_sources_manifest.json
```

This is extraction-only. It does not rebuild or compile a new `.IntLib`.
