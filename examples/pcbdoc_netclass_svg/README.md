# pcbdoc_netclass_svg

Load the RT Super C1 project with `AltiumDesign`, open its PcbDoc, and build
net-class review SVGs. The SVG renderer already embeds relationship metadata such
as `data-net`, `data-net-class`, and `data-net-classes`; this example uses those
attributes as enrichment data to recolor existing SVG output.

Document-level PCB SVG metadata uses the
`altium_monkey.pcb.svg.enrichment.a0` schema when enrichment metadata is enabled.

For each routed net class, the script writes one SVG per routed copper layer and
one HTML review page with the SVGs embedded inline.

## What It Shows

1. Loading a PcbDoc through `AltiumDesign`
2. Reading PCB net classes from the parsed PcbDoc
3. Selecting only copper layers with real routed track or arc length for that net
   class
4. Rendering per-layer SVGs with `PcbSvgRenderOptions(svg_display_scale=10.0)`
5. Using SVG `data-*` enrichment attributes to highlight a net class
6. Writing simple standalone HTML review pages with inline SVG

## Styling

The output uses a fixed review palette:

1. Tracks and arcs in the active net class are red.
2. Via drill holes in the active net class are red, while the via annular rings
   stay black.
3. Pads and via annular rings are black.
4. Other tracks, arcs, fills, and polygon-region copper are grey.
5. The board outline is black.

The review SVGs intentionally draw tracks and arcs first, then copper
fills/polygon regions, then pads and via annular rings. This keeps copper areas,
pads, and vias visually above the routed line geometry in the HTML review pages.

The example intentionally skips via-only inner layers. A layer is included only
when the target net class has at least one track or arc on that layer whose
length is at least 10 mils.

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_netclass_svg\pcbdoc_netclass_svg.py
```

## Output

```text
examples/pcbdoc_netclass_svg/output/html/
examples/pcbdoc_netclass_svg/output/svgs/
examples/pcbdoc_netclass_svg/output/pcbdoc_netclass_svg_manifest.json
```
