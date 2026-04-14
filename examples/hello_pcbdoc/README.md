# hello_pcbdoc

Create a PCB document from scratch, place silkscreen text, and author a
rectangular board outline from explicit polygon vertices.

This example is intentionally small. It is meant to show the public PcbDoc API,
not internal stream assembly.

## What It Shows

1. Creating a rectangular board outline as explicit polygon vertices
2. `AltiumBoardOutline` and `BoardOutlineVertex`
3. `AltiumPcbDoc.set_board_outline(...)`
4. `AltiumPcbDoc.set_origin_to_outline_lower_left(...)`
5. `AltiumPcbDoc.add_text(..., layer=PcbLayer.TOP_OVERLAY)`
6. `AltiumPcbDoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\hello_pcbdoc\hello_pcbdoc.py
```

## Output

The script writes:

```text
examples/hello_pcbdoc/output/hello_pcbdoc.PcbDoc
```
