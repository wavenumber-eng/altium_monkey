# Apply a SchDoc template

This example opens an existing schematic, removes the embedded title-block
template graphics, applies a `.SchDot` template, and saves the result as a new
`.SchDoc`.

Run from the package root:

```powershell
uv run python examples\schdoc_apply_template\schdoc_apply_template.py
```

The example uses:

- `examples/assets/projects/bunny_brain/bunny_brain_D.SchDoc`
- `examples/assets/templates/Wavenumber__ANSI_D.SchDot`

The output is written to:

```text
examples/schdoc_apply_template/output/bunny_brain_D_with_applied_template.SchDoc
```

The important API calls are:

```python
schdoc = AltiumSchDoc(INPUT_SCHDOC)
schdoc.clear_template()
schdoc.apply_template(
    TEMPLATE_SCHDOT,
    clear_existing=False,
    template_filename=TEMPLATE_REF,
)
schdoc.save(OUTPUT_SCHDOC)
```

`apply_template()` can also clear the existing template itself by using the
default `clear_existing=True`. This example calls `clear_template()` explicitly
first so the two operations are easy to inspect independently. It passes
`template_filename=TEMPLATE_REF` so the saved schematic stores a relocatable
template reference instead of the absolute asset path used to load the file.
