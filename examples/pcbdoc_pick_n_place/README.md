# pcbdoc_pick_n_place

Load an existing Altium project with `AltiumDesign`, generate PCB-backed
pick-and-place data, and write JSON outputs.

This example shows two related paths:

1. `AltiumDesign.to_json(...)` includes a root `pnp` block when the project has
   a referenced PcbDoc.
2. `AltiumDesign.to_pnp(...)` is the direct API for pick-and-place placements.

## What It Shows

1. Loading a project with `AltiumDesign.from_prjpcb(...)`
2. Reading the design JSON `pnp` contract
3. Calling `AltiumDesign.to_pnp(exclude_no_bom=True)` to skip components marked
   as no-BOM, including standard no-BOM components
4. Preserving JSON placement data for downstream automation

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_pick_n_place\pcbdoc_pick_n_place.py
```

## Input Project

This sample uses:

```text
examples/assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb
```

## Output

The script writes:

```text
examples/pcbdoc_pick_n_place/output/altium_design.json
examples/pcbdoc_pick_n_place/output/pick_and_place.json
examples/pcbdoc_pick_n_place/output/pick_and_place_exclude_no_bom.json
```

The standalone `pick_and_place.json` file is the same `pnp` object embedded in
`altium_design.json`. The `pick_and_place_exclude_no_bom.json` file shows the
direct `to_pnp(..., exclude_no_bom=True)` path.
