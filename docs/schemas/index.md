# Schema Contracts

`altium-monkey` emits a few JSON-shaped public contracts. The schema string is
the payload contract version. The Python package version is release metadata and
is intentionally separate from these schema IDs.

Breaking payload changes require a new schema ID. Additive fields may appear in
the same `a0` contract when older readers can safely ignore them.

## Revision Scheme

Schema suffixes use a stepping-style revision scheme:

1. The leading letter is the major revision. Moving from `a` to `b` indicates a
   potentially breaking contract change.
2. The trailing number is the minor revision. Moving from `a0` to `a1`
   indicates the contract may have added fields while preserving the existing
   `a` major-revision shape.
3. The first public contracts use `a0`.

This is intentionally similar to semiconductor stepping names: compact, stable,
and easy to compare in filenames, generated artifacts, and downstream tooling.

## Contract Files

The explicit contract bundle is maintained under
[`docs/schemas/altium_monkey`](altium_monkey/SPEC.md).

Machine-readable entry points:

1. [`design_a0.schema.json`](altium_monkey/design_a0.schema.json)
2. [`netlist_a0.schema.json`](altium_monkey/netlist_a0.schema.json)
3. [`pcb_svg_enrichment_a0.schema.json`](altium_monkey/pcb_svg_enrichment_a0.schema.json)

## `altium_monkey.design.a0`

Emitter: `AltiumDesign.to_json(...)`

Generator: `altium_monkey`

This is the full project/design analysis contract. It combines project metadata,
schematic sheet metadata, variant metadata, enriched schematic components, and
compiled nets.

Root field order:

```text
schema
generator
project
variants
options
sheets
components
pnp
nets
indexes
```

`indexes` is optional and is controlled by
`AltiumDesign.to_json(include_indexes=...)`.

`pnp` is optional and appears only when the design has a referenced PcbDoc that
can provide pick-and-place placements.

Important fields:

1. `schema`: always `altium_monkey.design.a0`.
2. `generator`: always `altium_monkey`.
3. `project`: project name, path-derived metadata, document paths, and project parameters.
4. `variants`: project variant definitions, including DNP component lists when available.
5. `options`: netlist and hierarchy-resolution options used to generate the payload.
6. `sheets`: reachable schematic documents and sheet-level metadata.
7. `components`: schematic components enriched with sheet, pin-count, parameters, and `svg_id` where available.
8. `pnp`: optional PCB-backed pick-and-place data in millimeters.
9. `nets`: compiled net records from the netlist contract.
10. `indexes`: optional lookup maps for components, nets, pins, and SVG IDs.

PNP fields:

1. `units`: currently `mm` in the design JSON contract.
2. `source_pcbdoc`: source PcbDoc filename used for placements.
3. `placements`: list of component placements.

PNP placement fields:

1. `designator`: component designator.
2. `comment`: schematic value/comment when available.
3. `layer`: normalized PCB layer, usually `top` or `bottom`.
4. `footprint`: PCB footprint name.
5. `center_x`: X placement in `pnp.units`.
6. `center_y`: Y placement in `pnp.units`.
7. `rotation`: rotation in degrees.
8. `description`: schematic or PCB component description.
9. `parameters`: component parameters.

The design contract does not contain a root `version` field. The `schema` field
is the contract version.

The design contract also does not contain `components_enriched`. The enriched
component list is the canonical `components` field.

## `altium_monkey.netlist.a0`

Emitter: `Netlist.to_json(...)` and `AltiumDesign.to_netlist().to_json(...)`

Generator: `altium_monkey`

This is the raw compiled schematic netlist contract. It is smaller than the full
design payload and is meant for electrical connectivity consumers.

Root fields:

```text
schema
generator
components
nets
```

Component fields:

1. `designator`: schematic designator.
2. `value`: component value/comment text.
3. `footprint`: footprint/model name when available.
4. `library_ref`: source library reference.
5. `description`: component description.
6. `parameters`: schematic component parameters copied into the compiled netlist.

Net fields:

1. `uid`: stable net identity within the emitted payload.
2. `name`: compiled net name.
3. `auto_named`: true when the compiler generated the net name.
4. `source_sheets`: schematic filenames that contributed to the net.
5. `terminals`: connected component pins.
6. `graphical`: related schematic SVG IDs grouped by record type.
7. `aliases`: alternate names discovered while merging connectivity.
8. `hierarchy_paths`: optional hierarchy provenance for hierarchical or repeated-channel designs.

Terminal fields:

1. `designator`: owning component designator.
2. `pin`: pin designator.
3. `pin_name`: pin display name.
4. `pin_type`: electrical pin type enum name.

Graphical net fields:

1. `wires`
2. `junctions`
3. `labels`
4. `power_ports`
5. `ports`
6. `sheet_entries`
7. `pins`

## `altium_monkey.pcb.svg.enrichment.a0`

Emitter: PCB SVG rendering when `PcbSvgRenderOptions(include_metadata=True)`.

This is document-level PCB SVG enrichment metadata. It exists so downstream
tools can inspect layer, net, net-class, component, board-outline, and drill
relationships without reparsing the PcbDoc.

The schema appears in three places:

1. root SVG attribute `data-enrichment-schema="altium_monkey.pcb.svg.enrichment.a0"`
2. metadata attribute `<metadata id="pcb-enrichment-a0" data-schema="altium_monkey.pcb.svg.enrichment.a0">`
3. JSON payload field `"schema": "altium_monkey.pcb.svg.enrichment.a0"`

The metadata element id remains `pcb-enrichment-a0`. That id is a DOM lookup
anchor, not the schema namespace.

Payload root fields:

```text
schema
source
board
view
layers
lookup
components
```

Important fields:

1. `source.pcbdoc_file`: source PcbDoc filename when known.
2. `board.centroid_mils`: board centroid in mils when known.
3. `board.centroid_relative_to_origin_mils`: centroid relative to the board origin in mils when known.
4. `view.kind`: view type such as `board`, `layer_set`, or `board_outline_only`.
5. `view.included_layer_ids`: layer IDs included in this SVG.
6. `view.includes_board_outline`: true when board-outline geometry is present.
7. `layers.all_layer_ids`: all known layer IDs in the rendered board context.
8. `layers.layer_id_to_key`: stable layer keys such as `L1`, `L32`, or `DRILLS`.
9. `layers.layer_id_to_name`: friendly layer names such as `TOP`, `BOTTOM`, or `DRILLS`.
10. `lookup.net_index_to_name`: net-index lookup table.
11. `lookup.net_name_to_classes`: net-class membership by net name.
12. `lookup.component_index_to_designator`: component-index lookup table.
13. `lookup.component_index_to_uid`: component unique IDs by component index.
14. `components`: component placement and parameter summaries used by SVG viewers.

Element-level SVG metadata uses ordinary `data-*` attributes. Common attributes
include:

1. `data-layer-id`, `data-layer-key`, `data-layer-name`, and `data-layer-role`
2. `data-net-index`, `data-net`, `data-net-class`, and `data-net-classes`
3. `data-component-index`, `data-component`, and `data-component-uid`
4. `data-feature="board-outline"` and `data-feature="board-cutout"`
5. `data-primitive="pad-hole"` or `data-primitive="via-hole"` for drill geometry
6. `data-hole-kind`, `data-hole-plating`, and `data-hole-render`
