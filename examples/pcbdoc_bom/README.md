# pcbdoc_bom

Load an existing Altium project, iterate the resolved PCB component records, and
write PCB-backed BOM JSON.

This example is intentionally PCB-centric. It uses `AltiumDesign` only to find
and load the project PcbDoc, then reads `pcbdoc.components`. Those component
records contain resolved designator strings, placement, footprint names,
component kind, and parameters parsed from the PcbDoc component parameter
stream.

## What It Shows

1. Loading a project with `AltiumDesign.from_prjpcb(...)`
2. Loading the project PcbDoc with `design.load_pcbdoc()`
3. Iterating `pcbdoc.components`
4. Reading resolved designators, placement, footprint data, and component
   parameters
5. Filtering component kinds that should not be included in BOM output
6. Grouping component rows into a simple BOM view

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_bom\pcbdoc_bom.py
```

## Input Project

This sample uses:

```text
examples/assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb
```

## Output

The script writes:

```text
examples/pcbdoc_bom/output/pcbdoc_components.json
examples/pcbdoc_bom/output/pcbdoc_bom.json
```

`pcbdoc_components.json` contains every PCB component row, including components
marked as no-BOM. `pcbdoc_bom.json` contains only BOM-included rows plus a
grouped BOM section.

For schematic-driven BOMs with variant DNP handling, use `AltiumDesign.to_bom`.
For placement-centric output, see `pcbdoc_pick_n_place`.
