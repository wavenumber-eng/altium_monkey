# AltiumDesign

`AltiumDesign` is the project-level analysis surface. Use it when you need a
compiled view across a `.PrjPcb`, its schematic documents, variants, component
metadata, and schematic connectivity.

Use it when you need to:

1. load a project from `.PrjPcb`
2. emit the public design JSON contract
3. emit the public netlist JSON contract
4. generate wirelist netlist format
5. generate PCB-backed pick-and-place data when a PcbDoc is referenced
6. inspect project parameters, variants, components, sheets, and nets

## Public Contracts

`AltiumDesign.to_json(...)` emits `altium_monkey.design.a0`.

`AltiumDesign.to_netlist().to_json(...)` emits `altium_monkey.netlist.a0`.

`AltiumDesign.to_pnp(...)` returns pick-and-place entries from the project
PcbDoc. When a project has a PcbDoc, `AltiumDesign.to_json(...)` also includes
the same data under the optional root `pnp` field.

The `schema` field is the contract version. These payloads do not use a root
`version` field.

The root `generator` field is `altium_monkey`.

See [schema contracts](schemas/index.md) for field-level contract notes.

## Current Boundaries

Variant processing is limited to DNP handling in this release.

Complex hierarchical sheets, repeated channels, and annotation-file driven
designator mapping may have edge cases. This release does not process
`*.Annotation` files.

Use schematic SVG rendering directly when you only need page-level drawings.
Use `AltiumDesign` when you need project context such as parameters, variants,
or netlist data.

Use `design.load_pcbdoc().components` when a PCB-backed BOM should reflect the
components that are actually placed on the board. The `pcbdoc_bom` example shows
that pattern.

## Examples

Start with:

1. [`hello_altium_design`](../examples/hello_altium_design/README.md)
2. [`pcbdoc_bom`](../examples/pcbdoc_bom/README.md)
3. [`pcbdoc_pick_n_place`](../examples/pcbdoc_pick_n_place/README.md)
4. [`schdoc_svg`](../examples/schdoc_svg/README.md)
5. [`pcbdoc_stats`](../examples/pcbdoc_stats/README.md)
6. [`prjpcb_make_project`](../examples/prjpcb_make_project/README.md)
