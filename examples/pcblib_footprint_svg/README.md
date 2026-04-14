# pcblib_footprint_svg

Load an existing PcbLib, iterate its footprints, and render footprint-local SVG
files.

This sample uses the RT Super C1 PcbLib asset because it contains many footprint
shapes. It writes one composed SVG for each footprint and one SVG per visible
footprint layer. The output is useful for
quick visual review of pad, overlay, solder-mask, paste-mask, and drill geometry
without placing the footprint on a board first.

The SVG enrichment metadata is disabled in this sample so the files stay focused
on review artwork instead of downstream PCB data enrichment.

## What It Shows

1. `AltiumPcbLib.from_file(...)`
2. iterating `pcblib.footprints`
3. `footprint.to_svg(...)`
4. `footprint.to_layer_svgs(...)`
5. using `PcbSvgRenderOptions(svg_display_scale=10.0, include_metadata=False)`
6. writing a JSON manifest that records generated SVG paths

## Run

From the package root:

```powershell
uv run python examples\pcblib_footprint_svg\pcblib_footprint_svg.py
```

## Input

```text
examples/assets/pcblib/RT_SUPER_C1.PcbLib
```

## Output

```text
examples/pcblib_footprint_svg/output/footprints/
examples/pcblib_footprint_svg/output/layers/
examples/pcblib_footprint_svg/output/pcblib_footprint_svg_manifest.json
```
