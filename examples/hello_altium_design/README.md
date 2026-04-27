# hello_altium_design

Load an existing Altium project with `AltiumDesign`, print the available
variants and document paths, and write the full design JSON plus BOM/netlist
artifacts to disk.

This example is intentionally small. It is meant to show the public
project-loading and design-analysis API, not internal netlist plumbing.

## What It Shows

1. `AltiumDesign.from_prjpcb(...)`
2. `AltiumDesign.to_json(...)`
3. `AltiumDesign.to_netlist(...)`
4. `AltiumDesign.to_wirelist(...)`
5. `AltiumDesign.to_bom(...)`
6. `AltiumDesign.get_variants(...)`

## Run

From the repository root:

```powershell
uv run python examples\hello_altium_design\hello_altium_design.py
```

## Input Project

This sample uses the redistributable project staged at:

```text
examples/assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb
```

## Output

The script writes:

```text
examples/hello_altium_design/output/project_summary.json
examples/hello_altium_design/output/altium_design.json
examples/hello_altium_design/output/netlist.json
examples/hello_altium_design/output/wirelist.txt
examples/hello_altium_design/output/bom_all.json
examples/hello_altium_design/output/variant_boms/<variant>.json
```

`altium_design.json` uses the `altium_monkey.design.a1` schema. `netlist.json`
uses the `altium_monkey.netlist.a0` schema.

When the project references a PcbDoc, `altium_design.json` also includes the
optional root `pnp` block with pick-and-place placements in millimeters. See
[`pcbdoc_pick_n_place`](../pcbdoc_pick_n_place/README.md) for a
focused CSV/JSON pick-and-place example.
