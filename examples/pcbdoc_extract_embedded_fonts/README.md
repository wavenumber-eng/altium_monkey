# pcbdoc_extract_embedded_fonts

Load a project PcbDoc and write all embedded TrueType fonts to a folder.

PCB files can carry embedded fonts when board text uses non-standard font
faces. This example extracts those font payloads without modifying the board.

## What It Shows

1. `AltiumDesign.from_prjpcb(...)`
2. `AltiumDesign.load_pcbdoc(...)`
3. `AltiumPcbDoc.extract_embedded_fonts(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_extract_embedded_fonts\pcbdoc_extract_embedded_fonts.py
```

## Output

The script writes:

```text
examples/pcbdoc_extract_embedded_fonts/output/embedded_fonts/
examples/pcbdoc_extract_embedded_fonts/output/fonts.json
```
