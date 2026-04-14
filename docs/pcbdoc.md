# PcbDoc

`AltiumPcbDoc` is the public container for PCB documents. The current release
focuses on parsing, extraction, SVG rendering, statistics, targeted authoring,
and footprint insertion.

Use it when you need to:

1. parse `.PcbDoc` files
2. inspect board geometry, layers, drills, nets, and resolved components
3. render PCB layers to SVG
4. extract embedded fonts, 3D models, or footprints
5. add simple PCB primitives and routes
6. place footprints from `.PcbLib`

## Object Model

PcbDoc does not yet use the generic `ObjectCollection` API used by SchDoc.
Instead, parsed records are exposed as typed lists such as `pcbdoc.tracks`,
`pcbdoc.arcs`, `pcbdoc.pads`, `pcbdoc.vias`, `pcbdoc.regions`,
`pcbdoc.texts`, and `pcbdoc.components`.

For authoring, prefer high-level helpers:

```python
pcbdoc.add_track((1000, 1000), (2000, 1000), width_mils=8, net="GND")
pcbdoc.add_pad(
    designator="1",
    position_mils=(1500, 1500),
    width_mils=60,
    height_mils=80,
)
pcbdoc.save("updated.PcbDoc")
```

Direct edits to typed lists are advanced usage. They can be appropriate for
read-preserving mutation, but callers are responsible for keeping indexes,
ownership, stream order, and related binary state valid.

PCB components are available through `pcbdoc.components`. Each
`AltiumPcbComponent` row exposes the resolved designator, footprint, placement,
rotation, side, component kind, and parsed PcbDoc component parameters. Use this
surface when a PCB-backed BOM or placement list should reflect what is actually
placed on the board.

## Units

Public PcbDoc authoring helpers use explicit `*_mils` parameter names. PCB
workflows are often metric, so convert metric source data before calling these
methods until metric helper functions are added.

Low-level PCB record fields may expose Altium internal integer units. Prefer
public helper methods for authored geometry.

## Current Gaps

There is no public PcbDoc object deletion API in this release.

There is no generic `ObjectCollection`-style query API for PcbDoc yet.

Mutations outside the high-level helper methods generally require direct
record-list edits and should be validated carefully.

## Examples

Start with:

1. [`hello_pcbdoc`](../examples/hello_pcbdoc/README.md)
2. [`pcbdoc_stats`](../examples/pcbdoc_stats/README.md)
3. [`pcbdoc_bom`](../examples/pcbdoc_bom/README.md)
4. [`pcbdoc_pick_n_place`](../examples/pcbdoc_pick_n_place/README.md)
5. [`pcbdoc_svg`](../examples/pcbdoc_svg/README.md)
6. [`pcbdoc_netclass_svg`](../examples/pcbdoc_netclass_svg/README.md)
7. [`pcbdoc_add_track`](../examples/pcbdoc_add_track/README.md)
8. [`pcbdoc_add_arc`](../examples/pcbdoc_add_arc/README.md)
9. [`pcbdoc_add_pad`](../examples/pcbdoc_add_pad/README.md)
10. [`pcbdoc_add_text`](../examples/pcbdoc_add_text/README.md)
11. [`pcbdoc_add_filled_region`](../examples/pcbdoc_add_filled_region/README.md)
12. [`pcbdoc_insert_nets_route`](../examples/pcbdoc_insert_nets_route/README.md)
13. [`pcbdoc_insert_footprint_from_pcblib`](../examples/pcbdoc_insert_footprint_from_pcblib/README.md)
14. [`pcbdoc_extract_pcblib`](../examples/pcbdoc_extract_pcblib/README.md)
15. [`pcbdoc_extract_embedded_3d_models`](../examples/pcbdoc_extract_embedded_3d_models/README.md)
16. [`pcbdoc_extract_embedded_fonts`](../examples/pcbdoc_extract_embedded_fonts/README.md)

See [API patterns](api_patterns/index.md) for public vs careful mutation
guidance.
