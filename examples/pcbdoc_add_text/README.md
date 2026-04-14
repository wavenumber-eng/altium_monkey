# pcbdoc_add_text

Add PCB text primitives to an existing PcbDoc.

The sample creates a rectangular board outline, sets the origin to the
lower-left outline corner, and places stroke text, TrueType text, mirrored
bottom-side text, inverted TrueType text, inverted framed text, multiline text
frame text, and Code 39 / Code 128 barcode text inside the board.

## What It Shows

1. `AltiumPcbDoc.from_file(...)`
2. `AltiumPcbDoc.set_outline_rectangle_mils(...)`
3. `AltiumPcbDoc.add_text(...)`
4. `PcbTextKind`, `PcbBarcodeKind`, `PcbBarcodeRenderMode`, and `PcbTextJustification`
5. `AltiumPcbDoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_add_text\pcbdoc_add_text.py
```

## Output

```text
examples/pcbdoc_add_text/output/pcbdoc_add_text.PcbDoc
```
