# Altium Monkey Contract Specification

Version family: `a0`

This directory documents the JSON-shaped contracts emitted directly by
`altium-monkey`. These contracts are Altium-oriented API payloads. They are not
the generic cross-CAD `wn.design.a0` data-model contract.

## Bundled Entry Points

- `design_a0.schema.json`: schema for `altium_monkey.design.a0`
- `netlist_a0.schema.json`: schema for `altium_monkey.netlist.a0`
- `pcb_svg_enrichment_a0.schema.json`: schema for
  `altium_monkey.pcb.svg.enrichment.a0`

The prose specification is intentionally kept next to the machine-readable
schemas so downstream tools and AI agents can discover the contract intent
without reading Python source.

## Revision Scheme

Schema suffixes use a stepping-style revision scheme:

- The leading letter is the major revision. Moving from `a` to `b` indicates a
  potentially breaking contract change.
- The trailing number is the minor revision. Moving from `a0` to `a1` indicates
  fields may have been added while preserving the existing `a` major-revision
  shape.
- The Python package version is release metadata. The serialized payload
  contract version is the `schema` string.

## `altium_monkey.design.a0`

Emitter: `AltiumDesign.to_json(...)`

Generator: `altium_monkey`

This is the full Altium project/design analysis contract. It combines project
metadata, schematic sheet metadata, variants, enriched schematic components,
compiled nets, optional PCB pick-and-place data, and optional lookup indexes.

Required root fields:

- `schema`: always `altium_monkey.design.a0`
- `generator`: always `altium_monkey`
- `project`: project identity and project parameters
- `variants`: project variant definitions; currently DNP lists only
- `options`: netlist and hierarchy-resolution options used for generation
- `sheets`: reachable schematic documents and sheet metadata
- `components`: schematic components enriched for downstream consumers
- `nets`: compiled net records

Optional root fields:

- `pnp`: PCB-backed pick-and-place data when a PcbDoc is available
- `indexes`: lookup maps emitted when `include_indexes=True`

The design contract does not contain a root `version` field. The `schema` field
is the version. The contract also does not contain `components_enriched`; the
enriched component list is the canonical `components` field.

### Project

Fields:

- `name`: project stem when a PrjPcb is present, otherwise null
- `filename`: project filename when a PrjPcb is present, otherwise null
- `parameters`: project parameter map

### Variants

Each variant contains:

- `name`: variant name
- `dnp`: list of designators omitted from population for that variant

Variant processing is currently DNP-oriented. More advanced variant semantics
may be added in a later minor or major contract.

### Components

Each component contains:

- `designator`
- `svg_id`
- `value`
- `footprint`
- `library_ref`
- `description`
- `hierarchy`
- `classification`
- `parameters`

`hierarchy` contains:

- `base_designator`
- `channel`
- `channel_index`
- `sheet`

`classification` contains:

- `prefix`
- `type`
- `pin_count`

### PNP

`pnp.units` is currently `mm`.

Each placement contains:

- `designator`
- `comment`
- `layer`
- `footprint`
- `center_x`
- `center_y`
- `rotation`
- `description`
- `parameters`

## `altium_monkey.netlist.a0`

Emitter: `Netlist.to_json(...)` and `AltiumDesign.to_netlist().to_json(...)`

Generator: `altium_monkey`

This is the compiled schematic connectivity contract. It is intentionally
smaller than the design contract and is meant for electrical-connectivity
consumers.

Required root fields:

- `schema`: always `altium_monkey.netlist.a0`
- `generator`: always `altium_monkey`
- `components`: component summaries copied into the compiled netlist
- `nets`: compiled nets

Component fields:

- `designator`
- `value`
- `footprint`
- `library_ref`
- `description`
- `parameters`

Net fields:

- `uid`
- `name`
- `auto_named`
- `source_sheets`
- `terminals`
- `graphical`
- `aliases`
- `hierarchy_paths`

Terminal fields:

- `designator`
- `pin`
- `pin_name`
- `pin_type`

`graphical` groups related schematic SVG IDs by record type:

- `wires`
- `junctions`
- `labels`
- `power_ports`
- `ports`
- `sheet_entries`
- `pins`

The netlist contract does not classify nets as power or ground. Those are
analysis heuristics and belong in downstream applications.

## `altium_monkey.pcb.svg.enrichment.a0`

Emitter: PCB SVG rendering when metadata is enabled.

This is the metadata payload embedded into PCB SVG output. It lets downstream
viewers inspect layer, net, net-class, component, board-outline, and drill
relationships without reparsing the PcbDoc.

The schema appears in three places:

- root SVG attribute `data-enrichment-schema`
- metadata attribute `<metadata id="pcb-enrichment-a0" data-schema="...">`
- JSON payload field `schema`

The metadata element id `pcb-enrichment-a0` is a DOM lookup anchor, not the
schema namespace.

Required root fields:

- `schema`: always `altium_monkey.pcb.svg.enrichment.a0`
- `source`
- `board`
- `view`
- `layers`
- `lookup`
- `components`

`source` contains:

- `pcbdoc_file`

`board` contains:

- `centroid_mils`
- `centroid_relative_to_origin_mils`

`view` contains:

- `kind`
- `included_layer_ids`
- `includes_board_outline`

`layers` contains:

- `all_layer_ids`
- `layer_id_to_key`
- `layer_id_to_name`

`lookup` contains:

- `net_index_to_name`
- `net_name_to_classes`
- `component_index_to_designator`
- `component_index_to_uid`

Each component summary contains:

- `index`
- `designator`
- `unique_id`
- `footprint`
- `description`
- `layer`
- `x_mils`
- `y_mils`
- `rotation_deg`
- `parameters`

Element-level SVG metadata uses ordinary `data-*` attributes. Common attributes
include `data-layer-*`, `data-net-*`, `data-component-*`, `data-feature`, and
drill-specific `data-hole-*` attributes.

