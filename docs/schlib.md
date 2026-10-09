# SchLib

`AltiumSchLib` is the public container for schematic libraries. Each library
contains one or more `AltiumSymbol` objects.

Use it when you need to:

1. create schematic symbols
2. mutate existing symbols
3. split or merge schematic libraries
4. extract symbols from schematic documents
5. render symbols and multipart symbols to SVG
6. inventory symbols and extract one selected symbol

## Object Model

`AltiumSymbol` uses the same `ObjectCollection` pattern as `AltiumSchDoc`.
Symbol properties such as `symbol.pins`, `symbol.parameters`,
`symbol.rectangles`, `symbol.lines`, and `symbol.arcs` are typed query views.
`AltiumSchLib.symbols`, `AltiumSymbol.objects`, and these typed accessors are
read-only live views. Use `AltiumSchLib.add_symbol()` / `remove_symbol()` and
`AltiumSymbol.add_object()` / `remove_object()` for structural changes instead
of appending to or replacing a returned view.

Rename a library-owned symbol with
`schlib.rename_symbol(symbol_or_storage_name, storage_name,
original_name=None)`. String selection is exact and case-sensitive and uses
the current OLE storage key, not the semantic library reference. Omitting
`original_name` preserves that semantic reference. The operation validates and
serializes a candidate before changing live state, moves the complete symbol
storage subtree, and returns the same owned symbol object.

Add symbol records with `symbol.add_object(...)` or symbol helper methods. Keep
visual ordering in mind: body graphics should usually be behind pins and text.
That order is preserved when a symbol is inserted into a SchDoc through
`AltiumSchDoc.add_component_from_library(...)` and when placed symbols are
extracted back to SchLib.

## Units

Public SchLib authoring APIs use mils for geometry. Use public helpers and
enums for pins, fonts, and drawing styles. Avoid raw pin binary fields unless
you are doing serializer-level preservation work.

## Public Pattern

Create or load a library, add or find a symbol, mutate the symbol, then save:

```python
schlib = AltiumSchLib()
symbol = schlib.add_symbol("MY_SYMBOL")
symbol.add_object(make_sch_pin(...))
schlib.save("my_symbols.SchLib")
```

Pass `show_comments_designators=True` when creating a library if Altium should
open the SchLib editor with symbol comments and designators visible:

```python
schlib = AltiumSchLib(show_comments_designators=True)
```

You can also set `schlib.show_comments_designators = True` before saving.

## Alternate pin functions

Pins can carry the alternate functions used by multifunctional MCU symbols.
Pass `defined_functions` and, when needed, `selected_functions` to
`make_sch_pin(...)`:

```python
pin = make_sch_pin(
    designator="15",
    name="PB0",
    location_mils=SchPointMils.from_mils(0, 0),
    defined_functions=["SPI1_NSS", "I2S1_WS", "TIM3_CH3"],
    selected_functions=["SPI1_NSS"],
)
symbol.add_pin(pin)
schlib.save("mcu.SchLib")
```

The library stores these values in Altium's per-pin `PinFunctionData` stream.
When modifying a parsed library, ordinary `save()` persists function changes
and removes stale function records. Unchanged function streams retain their bytes.
The function lists preserve their input order.

For parsed libraries, prefer `AltiumSchLib.get_symbol(...)` and symbol views
over scanning raw streams.

## Legacy Auxiliary-Stream Compatibility

`AltiumSchLib` accepts bounded legacy `PinTextData` and embedded-image Storage
streams that Altium itself imports even when their header termination, row
names, or declared count is noncanonical. Selected rows are still checked for
valid framing, compression, and payload shape; unrelated corruption remains an
error. PinTextData aliases apply in source order, including later default rows
that clear earlier custom settings. Storage validates and budgets every
selected row, then retains the first case-insensitive name for image lookup.
An unchanged PinTextData stream is preserved exactly. A semantic
PinTextData synchronization writes the current canonical zero-based form and
does not retain source rows that Altium ignored.

## Extraction From SchDoc

`AltiumSchDoc.extract_schlib(...)` returns an in-memory `AltiumSchLib` built
from the placed component symbols in a schematic. Use it when another API wants
to inspect, render, merge, or save the extracted library without first writing
split files.

`AltiumSchDoc.extract_symbols(...)` remains the file-oriented helper for split
or combined `.SchLib` output. It uses the same extraction model as
`extract_schlib(...)`.

## Extractable Assets

`AltiumSchLib.asset_inventory()` lists library symbols with typed
`SchSymbolAssetDetails`. Use the returned ref with `extract_asset(...)` when a
tool needs exactly one symbol as a new single-symbol `AltiumSchLib`.

```python
inventory = schlib.asset_inventory()
symbol = next(
    (item for item in inventory.by_kind("sch_symbol") if item.can_extract),
    None,
)
if symbol is None:
    raise RuntimeError("no extractable schematic symbol found")
selected = schlib.extract_asset(symbol.ref)
selected.schlib.save("selected_symbol.SchLib")
```

For the shared reference and JSON contract, see
[extractable assets](api_patterns/extractable_assets.md).

## SVG Rendering

`AltiumSchLib.symbol_to_svg(...)` and `to_svg(...)` accept
`SchSvgRenderOptions`. Normal symbol SVG output includes a root `viewBox` in
schematic pixel-canvas coordinates. Pass
`SchSvgRenderOptions(include_view_box=False)` to omit only that root attribute
while keeping the same geometry and symbol rendering path.

Bundled fallback font payloads are omitted by default. Pass
`SchSvgRenderOptions(embed_bundled_fallback_fonts=True)` for self-contained
symbol SVGs when package fallbacks are selected. The option embeds only used
package-owned fallback faces. Bulk `to_svg(output_dir=...)` still writes only
the requested SVG artifacts; it does not copy font sidecars.

## Examples

Start with:

1. [`hello_schlib`](../examples/hello_schlib/README.md)
2. [`schlib_find_symbol`](../examples/schlib_find_symbol/README.md)
3. [`schlib_split`](../examples/schlib_split/README.md)
4. [`schlib_merge`](../examples/schlib_merge/README.md)
5. [`schlib_svg`](../examples/schlib_svg/README.md)
6. [`schdoc_extract_schlib`](../examples/schdoc_extract_schlib/README.md)
7. [`extractable_asset_inventory`](../examples/extractable_asset_inventory/README.md)

See [API patterns](api_patterns/index.md) for the shared `ObjectCollection`
rules used by SchDoc and SchLib.
