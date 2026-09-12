# SchLib Contract

`AltiumSchLib` is the public model for schematic symbol libraries.

## Stable Surface

- Parse existing `.SchLib` files.
- Preserve unknown or unsupported data during normal read/write flows.
- Create new schematic libraries.
- Add, remove, merge, split, and find symbols.
- Extract symbols from placed schematic components.
- Render symbol SVG.

## Object Ownership

SchLib follows the same `ObjectCollection` ownership rules as SchDoc at the
symbol level. `AltiumSchLib` owns symbols, and each symbol owns its records.
`AltiumSchLib.symbols`, `AltiumSymbol.objects`, and the symbol's typed accessors
are read-only live query views. Do not append to or replace those views; use
`AltiumSchLib.add_symbol()` / `remove_symbol()` and
`AltiumSymbol.add_object()` / `remove_object()` for structural changes.
Typed views are live filtered views, not independent mutable lists.

Use symbol-owned mutation APIs such as `add_object(...)` for pins, component
graphics, parameters, and related child records.

Symbol child order is part of the visible contract because Altium draws
overlapping symbol children in record order. SchDoc component insertion from
SchLib preserves the source symbol order, and SchDoc symbol extraction writes
children back in the placed component order.

## Extraction From SchDoc

`AltiumSchDoc.extract_schlib(...)` returns an in-memory `AltiumSchLib` from
placed schematic components. `AltiumSchDoc.extract_symbols(...)` is the
file-oriented companion for split or combined `.SchLib` output and uses the
same extraction model.

## Units

High-level public symbol-authoring APIs use mils. Low-level records may expose
source storage units.

## Editor Display Options

`AltiumSchLib(show_comments_designators=True)` and the
`show_comments_designators` property write `AlwaysShowCD=T` in the SchLib
`FileHeader`. This asks Altium's library editor to show symbol comments and
designators by default. The default remains `False`, which omits the field for
newly authored libraries and preserves the previous output.

## SVG

`AltiumSchLib.symbol_to_svg(...)` and symbol `to_svg(...)` accept
`SchSvgRenderOptions`. Normal output includes a root `viewBox`.

See [SVG](svg.md) for the shared rendering contract.

## Test Gates

The SchLib contract is covered by symbol parsing, split/merge, extraction,
authoring, SVG, public examples, and release signoff.
