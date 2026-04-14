# schlib_svg

Extract schematic symbols from a schematic, then render the generated schematic
library symbols to standalone SVG files.

This sample uses the `rt_super_c1` project asset. It first extracts the placed
schematic components from `RT_SUPER_C1.SchDoc` into one combined `.SchLib`, then
loads that library and explicitly iterates through every symbol and symbol part.
The RT685 symbol in this design is multipart, so the sample writes one SVG for
each subcomponent part.

## What It Shows

1. opening an existing `.SchDoc` with `AltiumSchDoc`
2. calling `AltiumSchDoc.extract_symbols(..., combined_schlib=True)`
3. loading the generated `.SchLib` with `AltiumSchLib`
4. iterating `schlib.symbols`
5. reading each symbol's `part_count`
6. calling `AltiumSchLib.symbol_to_svg(symbol.name, part_id=...)`
7. writing one SVG per symbol part
8. writing a JSON manifest that records the generated SchLib and SVG paths

## Run

From the package root:

```powershell
uv run python examples\schlib_svg\schlib_svg.py
```

## Input

This sample reads:

```text
examples/assets/projects/rt_super_c1/RT_SUPER_C1.SchDoc
```

## Output

The script writes:

```text
examples/schlib_svg/output/combined/RT_SUPER_C1.SchLib
examples/schlib_svg/output/symbol_svgs/
examples/schlib_svg/output/schlib_svg_manifest.json
```

Single-part symbols are written as `{symbol_name}.svg`. Multipart symbols are
written as `{symbol_name}_part{n}.svg`; for example, the RT685 symbol is emitted
as `MIMXRT685SFVKB_part1.svg` through `MIMXRT685SFVKB_part8.svg`.
