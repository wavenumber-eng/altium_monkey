# SchDoc

`AltiumSchDoc` is the public container for schematic documents. It is the most
complete document object model in the current release.

Use it when you need to:

1. create or modify `.SchDoc` files
2. add schematic primitives, ports, notes, templates, and images
3. insert components from `.SchLib`
4. iterate and normalize existing schematic objects
5. render schematic pages to SVG
6. inventory placed symbols and extract one selected or complete `.SchLib`

## Object Model

`AltiumSchDoc` owns a single `ObjectCollection`. Typed properties such as
`schdoc.notes`, `schdoc.ports`, `schdoc.net_labels`, `schdoc.components`,
`schdoc.sheet_symbols`, and `schdoc.harness_connectors` are live filtered views
over that collection. `schdoc.objects` and `schdoc.all_objects` are also
read-only live views.

Use these views for query and traversal. Do not append to a view or assign
`all_objects`. Change membership through the document:

```python
note = make_sch_note(...)
schdoc.add_object(note)

for note in schdoc.notes:
    note.font = SchFontSpec(name="Courier New", size=10)

schdoc.remove_object(note)
schdoc.save("updated.SchDoc")
```

The document resolves `IndexInSheet`, owner indexes, font indexes, and related
serialization details when objects are added, removed, and saved.

## Units

Public SchDoc authoring APIs use mils. Prefer `SchPointMils`, `SchRectMils`,
`SchFontSpec`, `ColorValue`, and public enums instead of raw integer fields.

Low-level record fields may expose native Altium storage units. Use those fields
only when preserving parsed data or when no high-level property exists yet.

## Ownership

Some schematic records are invalid as top-level objects. Add them through their
owner object so ownership and indexes stay valid.

Harness entries and harness type labels belong to harness connectors.

Sheet entries, sheet-name labels, and file-name labels belong to sheet symbols.

Component pins, designators, parameters, and implementation records belong to
components.

Use `schdoc.add_component_from_library(...)` for normal component insertion from
SchLib.

When placing a SchLib symbol, `add_component_from_library(...)` preserves the
symbol's child record order. This keeps intentional schematic draw order intact
for cases where pins, rounded rectangles, designators, and other visible child
records overlap.

## Symbol Extraction

Use `AltiumSchDoc.extract_schlib(...)` when a caller needs an in-memory
`AltiumSchLib` from the placed component symbols in a schematic.

Use `AltiumSchDoc.extract_symbols(...)` when writing split or combined
`.SchLib` files:

```python
schdoc = AltiumSchDoc("input.SchDoc")
schlib = schdoc.extract_schlib()

schdoc.extract_symbols(
    "output_symbols",
    split_schlibs=True,
    combined_schlib=False,
)
```

Both paths share the same extraction model. Symbols are grouped by design item,
component-owned child records are transformed back into symbol coordinates, and
implementation/model records can be preserved with
`strip_implementations=False`. The default file-writing behavior remains
compatible with earlier releases.

## Templates

Use `clear_template()`, `apply_template(...)`, and `extract_template(...)` for
schematic title blocks and border graphics stored as Altium `.SchDot`
templates.

By default, `apply_template(...)` applies template-owned drawing objects,
embedded images, fonts, missing template parameters, and template metadata. It
does not change the target sheet size or page setup unless requested.

If the `.SchDot` should also control the target page setup, pass
`apply_visual_sheet_settings=True`:

```python
schdoc.apply_template(
    "title_block.SchDot",
    template_filename="title_block.SchDot",
    apply_visual_sheet_settings=True,
)
```

That option copies visual sheet context such as sheet style, custom sheet
dimensions, border and reference-zone settings, display unit, grid settings,
sheet colors, sheet-number spacing, and the sheet system font. It does not copy
template identity, vault/release GUIDs, sheet number, or project/page
parameters.

## Embedded Images

`AltiumSchDoc` preserves embedded IMAGE payloads in the schematic Storage
stream. When Altium stores an image as a BMP preview plus a native payload such
as `TdxPNGImage`, SVG rendering and `extract_embedded_images(...)` prefer the
native payload so PNG alpha is preserved. Plain 32-bit BMP alpha is preserved
when present; plain 24-bit BMP remains opaque.

The Storage reader also accepts Altium-tolerated legacy streams whose physical
rows extend beyond the effective declared count. Only rows selected by the
managed header are interpreted; selected framing, compression, and payloads
remain strictly validated and charged to decompression limits. If selected
rows repeat a case-insensitive name, image lookup retains the first row. When
the effective Storage count, duplicate names, or header termination needs
repair, saving regenerates a canonical count and header from the selected live
entries.

Use `schdoc.extract_embedded_images(output_dir)` when writing embedded images
as standalone files. Direct `image.image_data` access is a preservation API: it
returns the raw Storage payload and may include Altium wrapper bytes before the
native image. Code that needs image files should not hash or write
`image.image_data` directly unless it intentionally wants the exact stored
payload.

## Extractable Assets

`AltiumSchDoc.asset_inventory()` lists placed schematic symbols with typed
`SchSymbolAssetDetails`. Use the returned `AltiumAssetRef` with
`extract_asset(...)` to extract one selected symbol as a single-symbol
`AltiumSchLib`.

```python
inventory = schdoc.asset_inventory()
symbol = next(
    (item for item in inventory.by_kind("sch_symbol") if item.can_extract),
    None,
)
if symbol is None:
    raise RuntimeError("no extractable schematic symbol found")
extracted = schdoc.extract_asset(symbol.ref)
extracted.schlib.save("selected_symbol.SchLib")
```

For the shared reference and JSON contract, see
[extractable assets](api_patterns/extractable_assets.md).

## SVG Rendering

`AltiumSchDoc.to_svg(...)` accepts `SchSvgRenderOptions`. Normal review output
includes a root `viewBox` in schematic pixel-canvas coordinates. Strict
native/oracle output from `SchSvgRenderOptions.native_altium()` omits the root
viewBox by default so comparison lanes can preserve the native export shape.

Set `SchSvgRenderOptions(include_view_box=False)` when a caller needs the
normal renderer profile without a root viewBox.

`SchSvgRenderOptions.paint_color_mode` accepts the public `SchPaintColorMode`
values `COLOR`, `GRAYSCALE`, and `MONOCHROME` for managed painter color policy.

Schematic SVG rendering resolves text fonts before measuring and placing text.
Installed system fonts are preferred. On macOS, the resolver searches the
standard system font directories, including Supplemental fonts; callers can add
directories through `ALTIUM_FONT_DIRS`.

When a requested Altium/Windows family is unavailable, common families fall
back to bundled open-source fonts: Arimo for Arial and Microsoft Sans
Serif-style fonts, Tinos for Times New Roman-style fonts, and Cousine for
Courier New or monospace fonts. The fallback remains automatic for resolution
and measurement, but its font bytes are not embedded by default. Use
`SchSvgRenderOptions(embed_bundled_fallback_fonts=True)` when the output must be
self-contained. Only package-owned Arimo, Tinos, or Cousine faces actually used
by painted SVG text are eligible; system and configured fonts are never
embedded, and no font sidecars are written. Without embedding, a viewer that
lacks the fallback face may lay out text differently.

`AltiumSchDoc.to_ir(profile="onscreen")` includes font-resolution diagnostics
for substitutions and fallbacks. Exact system matches are intentionally quiet.

## Examples

Start with:

1. [`hello_schdoc`](../examples/hello_schdoc/README.md)
2. [`schdoc_vertical_pin_svg`](../examples/schdoc_vertical_pin_svg/README.md)
3. [`schdoc_add_note`](../examples/schdoc_add_note/README.md)
4. [`schdoc_note_command`](../examples/schdoc_note_command/README.md)
5. [`schdoc_move_note`](../examples/schdoc_move_note/README.md)
6. [`schdoc_add_harness_connector`](../examples/schdoc_add_harness_connector/README.md)
7. [`schdoc_mutate_harness_connector`](../examples/schdoc_mutate_harness_connector/README.md)
8. [`schdoc_add_sheet_symbol`](../examples/schdoc_add_sheet_symbol/README.md)
9. [`schdoc_mutate_sheet_symbol`](../examples/schdoc_mutate_sheet_symbol/README.md)
10. [`schdoc_insert_dblib_style`](../examples/schdoc_insert_dblib_style/README.md)
11. [`schdoc_clean`](../examples/schdoc_clean/README.md)
12. [`schdoc_svg`](../examples/schdoc_svg/README.md)
13. [`schdoc_apply_dynamic_template`](../examples/schdoc_apply_dynamic_template/README.md)
14. [`extractable_asset_inventory`](../examples/extractable_asset_inventory/README.md)

See [API patterns](api_patterns/index.md) for cross-cutting mutation and
ownership guidance.
