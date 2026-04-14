# Extract SchLibs From A SchDoc

Open a schematic and extract the placed component symbols into schematic
libraries.

This example writes three extraction forms:

- A combined SchLib with all extracted symbols in one file.
- Split SchLibs with component parameters and implementation/model records
  removed. This is the form you would normally prepare before attaching symbols
  to a database library workflow.
- Split SchLibs with component parameters and implementation/model records
  preserved.

Run from the package root:

```powershell
uv run python examples\schdoc_extract_schlib\schdoc_extract_schlib.py
```

The example uses:

```text
examples/assets/projects/rt_super_c1/RT_SUPER_C1.SchDoc
```

It writes:

```text
examples/schdoc_extract_schlib/output/combined/RT_SUPER_C1.SchLib
examples/schdoc_extract_schlib/output/split_dblib_ready/
examples/schdoc_extract_schlib/output/split_preserved/
examples/schdoc_extract_schlib/output/schlib_extraction_manifest.json
```

The important pattern is:

```python
schdoc = AltiumSchDoc(INPUT_SCHDOC)

schdoc.extract_symbols(
    OUTPUT_DIR,
    combined_schlib=True,
    split_schlibs=False,
    strip_parameters=False,
    strip_implementations=False,
)

schdoc.extract_symbols(
    OUTPUT_DIR,
    combined_schlib=False,
    split_schlibs=True,
    strip_parameters=True,
    strip_implementations=True,
)
```

`strip_parameters=True` and `strip_implementations=True` produce the lean
DBLib-ready split libraries. Set both to `False` when you want extracted SchLibs
that retain source component metadata and model links.
