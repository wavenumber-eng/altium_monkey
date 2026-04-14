# pcbdoc_stats

Load the `loz-old-man` project with `AltiumDesign`, open the referenced PcbDoc,
and print a compact PCB statistics report.

This example is read-only. It is intended to show how project-level loading,
PcbDoc parsing, board-outline geometry, primitive inspection, and resolved
layer-stack metadata fit together.

## What It Shows

1. `AltiumDesign.from_prjpcb(...)`
2. `AltiumDesign.load_pcbdoc(...)`
3. Reading `pcbdoc.board.outline.bounding_box`
4. Counting plated pad holes, top-to-bottom vias, and non-plated pad holes
5. Counting plated and non-plated slotted pad holes
6. Building a drill-size table in mils and millimeters
7. Reading minimum copper-layer track and arc widths in mils
8. `resolved_layer_stack_from_pcbdoc(...)`
9. Printing a simple ASCII layer-stack table with Dk, Df, copper weight,
   thickness, material, and total stack thickness

## Run

From the repo root:

```powershell
uv run python examples\pcbdoc_stats\pcbdoc_stats.py
```

## Output

The script prints the board statistics to the terminal and writes the same data
as structured JSON plus a plain-text report:

```text
examples/pcbdoc_stats/output/pcbdoc_stats.json
examples/pcbdoc_stats/output/pcbdoc_stats.txt
```
