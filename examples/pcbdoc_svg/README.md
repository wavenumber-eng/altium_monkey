# pcbdoc_svg

Load the RT Super C1 project with `AltiumDesign`, open its PcbDoc, and render
individual PCB SVG files for each visible layer plus a separate board-outline SVG.
Each layer SVG keeps the board outline enabled by default.
For review clarity, copper layers render as black geometry on a white/transparent
SVG background, and drill holes render as green overlay geometry.

This example is read-only. It demonstrates the core PcbDoc SVG API rather than
variant-aware or web-view rendering.

## What It Shows

1. `AltiumDesign.from_prjpcb(...)`
2. `AltiumDesign.load_pcbdoc(...)`
3. Passing project parameters into PCB text substitution
4. `PcbSvgRenderOptions(svg_display_scale=10.0)`
5. `pcbdoc.to_layer_svgs(...)`
6. `pcbdoc.to_board_outline_svg(...)`
7. Resolving layer selectors from enum, legacy layer id, JSON token, or resolved
   friendly layer name before passing `visible_layers={...}` to the SVG options
8. Overriding copper layer colors with `layer_colors={...}`
9. Rendering drill holes in overlay mode with a green review color

## Layer Selection

The core SVG option currently uses `PcbLayer` values:

```python
options = PcbSvgRenderOptions(visible_layers={PcbLayer.TOP})
```

This example includes `resolve_pcb_svg_layer(...)` to show the intended public
shape for friendlier callers. It accepts selectors such as:

```python
PcbLayer.TOP
1
"TOP"
"Top Layer"
"[8] Board Outline"
```

The RT Super C1 board includes keepout-layer tracks, so the automatic per-layer
output emits a `KEEPOUT` SVG. The resolver also resolves `"Keep-Out Layer"` to
`PcbLayer.KEEPOUT` for explicit layer views.

## Scale

`svg_display_scale` changes the SVG `width` and `height` attributes. The SVG
`viewBox` and geometry remain in millimeters so downstream CAD or viewer code can
continue to reason in physical units.

## Run

From the repo root:

```powershell
uv run python examples\pcbdoc_svg\pcbdoc_svg.py
```

## Output

```text
examples/pcbdoc_svg/output/board_outline.svg
examples/pcbdoc_svg/output/layers/
examples/pcbdoc_svg/output/pcbdoc_svg_manifest.json
```
