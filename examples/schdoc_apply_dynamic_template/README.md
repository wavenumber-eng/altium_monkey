# Apply a dynamic SchDoc template

This example builds lower-right title-block graphics from a Python function
instead of loading a fixed `.SchDot` file. The title block is anchored from the
lower-right sheet corner, so the same geometry can be applied to different ANSI
sheet sizes while staying clear of Altium's border graphics.

The generated title block uses Altium parameter expressions such as
`=PCB_CODENAME`, `=SCH_DATE`, and `=SheetNumber of  =SheetTotal` in the text
frames. The example does not write those values as SchDoc document parameters.
Instead, it writes them as project parameters in the generated `.PrjPcb` and
sets the current project variant to `A` for `=VariantName` substitution. The
generated documents use Arial 10 as the document system font.

Run from the package root:

```powershell
uv run python examples\schdoc_apply_dynamic_template\schdoc_apply_dynamic_template.py
```

The example uses:

- `examples/assets/schdoc/blank.SchDoc`
- `examples/schdoc_apply_dynamic_template/assets/logo.png`

It writes two schematic outputs and a project container:

```text
examples/schdoc_apply_dynamic_template/output/blank_with_dynamic_ansi_B.SchDoc
examples/schdoc_apply_dynamic_template/output/blank_with_dynamic_ansi_D.SchDoc
examples/schdoc_apply_dynamic_template/output/schdoc_apply_dynamic_template.PrjPcb
```

The important pattern is:

```python
template_doc = build_dynamic_template_doc(
    sheet_size="D",
    title_block_image=TITLE_BLOCK_IMAGE,
)
template_doc.save(dynamic_template_path)

schdoc = AltiumSchDoc(INPUT_SCHDOC)
set_ansi_sheet(schdoc, "D")
schdoc.apply_template(dynamic_template_path)
schdoc.save(output_path)

project = AltiumPrjPcb.create_minimal(PROJECT_NAME)
project.set_parameters(PROJECT_PARAMETERS)
project.add_variant("A", current=True)
project.add_document(output_path.name)
project.save(OUTPUT_PROJECT)
```

`make_title_block(...)` is intentionally parameterized with
`title_block_image`, `right_offset_mils`, and `bottom_offset_mils`. That keeps
the image and sheet-border clearance explicit instead of hardcoding them into
the drawing routine.
