# SchDoc

`AltiumSchDoc` is the public container for schematic documents. It is the most
complete document object model in the current release.

Use it when you need to:

1. create or modify `.SchDoc` files
2. add schematic primitives, ports, notes, templates, and images
3. insert components from `.SchLib`
4. iterate and normalize existing schematic objects
5. render schematic pages to SVG

## Object Model

`AltiumSchDoc` owns a single `ObjectCollection`. Typed properties such as
`schdoc.notes`, `schdoc.ports`, `schdoc.net_labels`, `schdoc.components`,
`schdoc.sheet_symbols`, and `schdoc.harness_connectors` are live filtered views
over that collection.

Use filtered views for query and traversal. Do not append to filtered views.
Change membership through the document:

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

## Examples

Start with:

1. [`hello_schdoc`](../examples/hello_schdoc/README.md)
2. [`schdoc_add_note`](../examples/schdoc_add_note/README.md)
3. [`schdoc_note_command`](../examples/schdoc_note_command/README.md)
4. [`schdoc_move_note`](../examples/schdoc_move_note/README.md)
5. [`schdoc_add_harness_connector`](../examples/schdoc_add_harness_connector/README.md)
6. [`schdoc_mutate_harness_connector`](../examples/schdoc_mutate_harness_connector/README.md)
7. [`schdoc_add_sheet_symbol`](../examples/schdoc_add_sheet_symbol/README.md)
8. [`schdoc_mutate_sheet_symbol`](../examples/schdoc_mutate_sheet_symbol/README.md)
9. [`schdoc_insert_dblib_style`](../examples/schdoc_insert_dblib_style/README.md)
10. [`schdoc_clean`](../examples/schdoc_clean/README.md)
11. [`schdoc_svg`](../examples/schdoc_svg/README.md)

See [API patterns](api_patterns/index.md) for cross-cutting mutation and
ownership guidance.
