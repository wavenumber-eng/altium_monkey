# schdoc_svg

Render every reachable schematic sheet in a `.PrjPcb` to SVG.

This sample uses the Hydroscope project asset. It demonstrates loading project
metadata, collecting schematic documents in project order, and passing
project-level parameters into `AltiumSchDoc.to_svg(...)` so title-block
parameter expressions can resolve during rendering.

This is a core schematic SVG rendering example only. It does not apply variant
DNP/alternate-part handling or hierarchical channel expansion. Those workflows
require additional project-level information from `AltiumDesign` or a downstream
application layer.

## What It Shows

1. loading an existing `.PrjPcb` with `AltiumPrjPcb`
2. using `project.get_reachable_schdoc_paths()` instead of hardcoding sheet names
3. reading `project.parameters`
4. calling `AltiumSchDoc.to_svg(project_parameters=...)`
5. writing one SVG per schematic page

## Run

From the package root:

```powershell
uv run python examples\schdoc_svg\schdoc_svg.py
```

## Input

This sample reads:

```text
examples/assets/projects/hydroscope/Hydroscope.PrjPcb
```

The schematic documents are discovered from the project file.

## Output

The script writes SVGs under:

```text
examples/schdoc_svg/output/
```

For the Hydroscope asset, the output contains one SVG per reachable schematic
page:

```text
examples/schdoc_svg/output/CPU.svg
examples/schdoc_svg/output/POWER_SUPPLY.svg
examples/schdoc_svg/output/TOP_LEVEL.svg
examples/schdoc_svg/output/US_IF.svg
examples/schdoc_svg/output/svg_manifest.json
```

The manifest records the source project, source schematic pages, project
parameters passed to the renderer, and the generated SVG paths.
