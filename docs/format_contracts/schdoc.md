# SchDoc Contract

`AltiumSchDoc` is the public document model for schematic sheets.

## Stable Surface

- Parse existing `.SchDoc` files.
- Preserve unknown or unsupported data during normal read/write flows.
- Create blank schematic documents.
- Add, insert, remove, and mutate schematic objects through document-owned
  structural APIs.
- Render schematic SVG.
- Extract placed component symbols into an in-memory SchLib or split/combined
  SchLib files.
- Extract and apply schematic templates.
- Preserve and extract embedded images.

## Object Ownership

SchDoc uses the `ObjectCollection` model. Typed views such as `components`,
`wires`, and `notes`, plus `objects` and `all_objects`, are read-only live views
over the owned object collection. Do not append to a view or assign
`all_objects`. Use `add_object(...)`, `insert_object(...)`,
`remove_object(...)`, or typed high-level helpers for structural mutation.

Owned child records must be added through their owner:

- component pins, designators, parameters, graphics, and implementation records
  belong to components
- sheet entries, sheet-name labels, and file-name labels belong to sheet
  symbols
- harness entries and harness type labels belong to harness connectors

## Units

High-level public schematic authoring APIs use mils and expose helpers such as
`SchPointMils`, `SchRectMils`, `SchFontSpec`, and public enums.

Low-level record fields may expose source storage units when direct record
access is required.

## Netlist Hotspots

Sheet-entry and harness-entry connectivity uses the composed Altium basic-entry
distance when projecting entry tabs onto wires and ports. `DistanceFromTop`
stores whole 100-mil basic-entry steps, and `DistanceFromTop_Frac1` stores
millionths of one basic-entry step.

For integer netlist endpoint matching, altium-monkey projects entry offsets to
native 10-mil schematic units as:

```text
DistanceFromTop * 10 + DistanceFromTop_Frac1 / 100000
```

The projected integer hotspot uses half-away-from-zero rounding. This is a
netlist/hierarchy connectivity contract; SVG geometry and low-level record
round-tripping continue to preserve the source fields independently.

## SVG

`AltiumSchDoc.to_svg(...)` accepts `SchSvgRenderOptions`. Normal review output
includes a root `viewBox`; strict native/oracle output may omit it by default.

See [SVG](svg.md) for the shared rendering contract.

## Symbol Extraction

`AltiumSchDoc.extract_schlib(...)` returns an in-memory `AltiumSchLib` for the
placed component symbols in the schematic. `AltiumSchDoc.extract_symbols(...)`
writes split and/or combined `.SchLib` files from the same extraction model.

Extraction groups placed components by design item, transforms component-owned
child geometry back into symbol coordinates, and preserves existing
split/combined output behavior.

## Test Gates

The SchDoc contract is covered by schematic authoring tests, round-trip tests,
SVG lanes, public examples, and release signoff.
