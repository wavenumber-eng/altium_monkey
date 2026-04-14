# hello_schlib

Create a schematic library from scratch and author a tiny fictitious 6-pin IC
symbol.

This example is intentionally small. It is meant to show the public
`AltiumSchLib` and `make_sch_pin(...)` APIs, not internal library-stream
details.

## What It Shows

1. `AltiumSchLib.add_symbol(...)`
2. `AltiumSymbol.set_description(...)`
3. `make_sch_pin(...)`
4. `SchPointMils.from_mils(...)`
5. `AltiumSymbol.add_pin(...)`
6. `AltiumSymbol.add_rectangle(...)`
7. centering a `1000 x 600 mil` symbol body around `(0, 0)`
8. setting pin-name and pin-designator text to Arial 10 with `SchFontSpec`
9. rotating custom VCC/GND pin-name and pin-designator text with `PinTextRotation`
10. keeping pin lengths and pin hotspots on the 100 mil schematic grid
11. `AltiumSymbol.add_designator(...)`
12. `AltiumSymbol.add_parameter(...)`
13. `AltiumSchLib.save(...)`

## Run

From the repo root:

```powershell
uv run python examples\hello_schlib\hello_schlib.py
```

## Output

The script writes:

```text
examples/hello_schlib/output/hello_schlib.SchLib
```
