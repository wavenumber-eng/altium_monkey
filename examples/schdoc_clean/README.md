# schdoc_clean

Open every schematic in an existing project asset, iterate through common
schematic object collections, normalize visual style in place, and save cleaned
copies.

This sample uses the Hydroscope project asset to demonstrate bulk schematic
mutation across multiple `.SchDoc` files.

## What It Shows

1. iterating project schematic files from an asset folder
2. walking components, pins, component parameters, and component graphics
3. mutating document-level wires, ports, net labels, power ports, harnesses, and sheet symbols
4. using `schdoc.objects.of_type(...)` for generic type-based object queries
5. using `ColorValue`, `SchFontSpec`, `LineWidth`, and record collections instead of raw Altium field names
6. saving modified schematics with `AltiumSchDoc.save(...)`

## Run

From the package root:

```powershell
uv run python examples\schdoc_clean\schdoc_clean.py
```

## Input

This sample reads every `.SchDoc` in:

```text
examples/assets/projects/hydroscope/
```

## Output

The script writes:

```text
examples/schdoc_clean/output/hydroscope_clean/
examples/schdoc_clean/output/hydroscope_clean/Hydroscope.PrjPcb
examples/schdoc_clean/output/clean_manifest.json
```

Open the copied `Hydroscope.PrjPcb` from the output folder to load the cleaned
schematics through the original project-relative document names.

## Clean Rules

The example applies these style rules:

1. component pin names and pin designators use Courier New 10 in black
2. document sheet backgrounds become pure white
3. the document system font becomes Courier New 10
4. component body rectangles with either dimension larger than 100 mil become solid white with a small black border
5. component line, polyline, and arc graphics become black
6. small component polygons get black border and black fill
7. filled component circles get black border and black fill
8. component designators use black Courier New, bold, preserving the existing size
9. visible component parameters use black Courier New 10
10. schematic wires become `#434343`
11. power ports become black with Courier New 10 bold text
12. dotted voltage-style power names normalize from forms like `+3.3V` to `+3v3`
13. net labels use black Courier New 9 bold
14. page ports get black border, black fill, white Courier New 10 bold text
15. free text strings use Courier New, preserving the existing size
16. note text uses Courier New 10
17. note fill becomes `#F3F3F3`
18. no-ERC directives use the black Small Cross symbol
19. signal harnesses become black
20. harness connector borders become black and fills become `#D9D9D9`
21. harness entries use Courier New 10 bold in black
22. harness type labels use black Courier New 10 bold
23. sheet symbols use pure white fill and small borders
24. sheet-symbol entries get black border, black fill, black Courier New 10 bold text, and Triangle shape
25. underscores in net labels, page ports, power ports, and sheet entries are replaced with hyphens
