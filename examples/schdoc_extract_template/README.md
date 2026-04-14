# Extract a SchDoc template

This example opens an existing schematic with an embedded template, then
extracts the template graphics back out to a standalone `.SchDot` file.

Run from the package root:

```powershell
uv run python examples\schdoc_extract_template\schdoc_extract_template.py
```

The example uses:

- `examples/assets/projects/m2_emmc/m2_emmc.SchDoc`

The outputs are written to:

```text
examples/schdoc_extract_template/output/m2_emmc_extracted_template.SchDot
```

The important extraction API call is:

```python
schdoc = AltiumSchDoc(INPUT_SCHDOC)
schdoc.extract_template(OUTPUT_SCHDOT)
```

The extracted `.SchDot` is written as a plain schematic-template document. It
does not contain an embedded `Template` record; the template graphics are
top-level records in the `.SchDot`, which matches the normal file shape used by
Altium schematic template files.
