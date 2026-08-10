# Altium Monkey Contract Specification

Version family: `a0`/`a1`/`a2`/`b0`

This directory documents the JSON-shaped contracts emitted directly by
`altium-monkey`. These contracts are Altium-oriented API payloads. They are not
the generic cross-CAD `design_a0` data-model contract.

## Bundled Entry Points

- `design_b0.schema.json`: schema for current `altium_monkey.design.b0`
- `design_a2.schema.json`: schema for predecessor `altium_monkey.design.a2`
- `design_a1.schema.json`: schema for predecessor `altium_monkey.design.a1`
- `design_a0.schema.json`: schema for predecessor `altium_monkey.design.a0`
- `netlist_a0.schema.json`: schema for `altium_monkey.netlist.a0`
- `pcb_svg_enrichment_a0.schema.json`: schema for
  `altium_monkey.pcb.svg.enrichment.a0`
- `embedded_assets_a0.schema.json`: schema for
  `altium_monkey.pcb.embedded_assets.a0`
- `extractable_assets_a0.schema.json`: schema for
  `altium_monkey.extractable_assets.a0`

The prose specification is intentionally kept next to the machine-readable
schemas so downstream tools and AI agents can discover the contract intent
without reading Python source.

`design_b0.schema.json` is self-contained for strict validation. Older sibling
schemas remain bundled for consumers pinned to earlier contracts or validating
other payload families directly.

## Revision Scheme

The leading letter is the major revision and changes for a breaking contract
change. The trailing number identifies an additive minor revision within that
major family: existing fields retain their meaning and required shape.
Consumers should still match a supported schema ID exactly unless they
implement an explicit migration or compatibility range.

- Moving from Design a2 to Design b0 removes the duplicated physical-page
  projection and requires the source-neutral compiled schematic graph. That is
  a breaking change, so the major revision advances from `a` to `b`. Strict
  predecessor validators reject this new root shape by design.
- The Python package version is release metadata. The serialized payload
  contract version is the `schema` string.

## `altium_monkey.design.b0`

Emitter: `AltiumDesign.to_json(...)`

Generator: `altium_monkey`

This is the full Altium project/design analysis contract. It combines project
metadata, schematic sheet metadata, variants, enriched schematic components,
compiled nets, resolved schematic hierarchy metadata, the compiled schematic
graph, narrow physical-page presentation metadata, optional PCB pick-and-place
data, and optional lookup indexes.

Required root fields:

- `schema`: always `altium_monkey.design.b0`
- `generator`: always `altium_monkey`
- `project`: project identity and project parameters
- `variants`: project variant definitions, including DNP lists and parameter overrides when available
- `options`: netlist and hierarchy-resolution options used for generation
- `sheets`: reachable schematic documents and sheet metadata
- `components`: schematic components enriched for downstream consumers
- `schematic_hierarchy`: resolved schematic hierarchy metadata for visualizers
- `compiled_schematic_graph`: required variant-neutral source-neutral graph
- `physical_page_metadata`: required Altium presentation facts keyed by graph
  page occurrence
- `nets`: compiled net records

Optional root fields:

- `pnp`: PCB-backed pick-and-place data when a PcbDoc is available
- `compile`: compact compiled-design metadata emitted when
  `include_compile_metadata=True`
- `diagnostics`: compile, annotation, document, sheet-symbol, component, and
  net diagnostics emitted when `include_compile_metadata=True`
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
- optional `variations`: raw project variation rows
- optional `parameters`: variant-level parameter rows
- optional `param_variations`: raw per-designator parameter-variation rows
- optional `parameter_overrides`: grouped designator -> parameter -> value map

`parameter_overrides` is the normalized form consumed by
`AltiumDesign.to_bom(variant=...)`. Raw alternate fitted component rows may
appear in `variations`, but alternate fitted component replacement is not
applied semantically by the design contract yet.

### Sheets

Each sheet contains:

- `filename`: schematic document filename
- `sheet_number`: canonical decimal `SheetNumber` values are emitted as JSON
  numbers for compatibility; non-canonical Altium values such as leading-zero
  or part-number strings are emitted exactly as JSON strings

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
- optional compiled identity fields such as `compiled_component_id`,
  `physical_document_id`, `physical_page_id`, `source_unique_id`, and
  `source_unique_id_path`
- optional `dnp` and `fitted` for active-variant population state
- optional `ambiguous_physical_designator` when a flat scalar component row
  cannot safely carry one SVG/source identity for every physical occurrence

`hierarchy` contains:

- `base_designator`
- `channel`
- `channel_index`
- `sheet`

`classification` contains:

- `prefix`
- `type`
- `pin_count`

### Schematic Hierarchy

`schematic_hierarchy.schema` is always
`altium_monkey.schematic_hierarchy.a1`.

The hierarchy block contains:

- `requested_scope`: requested net identifier scope
- `effective_scope`: compiler-resolved scope
- `documents`: source sheet documents and top-level classification
- `sheet_symbols`: parent sheet symbols, child sheet indices, repeat metadata,
  and entries
- `hierarchy_paths`: compiled hierarchy paths for nested or repeated sheets
- `channels`: repeated-channel instances and path references
- `links`: sheet-entry to child-port relationships
- `harness_bundle_links`: flat or hierarchical harness bundle relationships
- `unresolved`: hierarchy diagnostics such as missing child sheets or unmatched
  ports

### Compile Metadata

The optional root `compile` object is emitted when
`AltiumDesign.to_json(include_compile_metadata=True)` is requested. It is a
compact projection of
`AltiumDesign.compile()` for user-facing design JSON consumers. It contains the
compiled model schema, summary counts, compile options, annotation metadata, and
compile statistics. It intentionally does not embed the full compiled model;
call `design.compile().to_dict()` when a lower-level diagnostic or oracle
payload is needed.

The optional root `diagnostics` array is emitted by the same flag. It
aggregates public diagnostics from the compile owner, annotation parser,
logical documents, physical documents, physical sheet symbols, compiled
components, and compiled nets.

### Compiled Schematic Graph and Drawing Identity

`compiled_schematic_graph` is always present and uses schema
`altium_monkey.compiled_schematic_graph.a0` with identity namespace
`sch.compiled_schematic_graph.a0`. Its ten collections are:

- `unit_definitions`
- `page_definitions`
- `unit_occurrences`
- `page_occurrences`
- `hierarchy_occurrences`
- `component_occurrences`
- `local_net_occurrences`
- `terminal_occurrences`
- `hierarchy_terminal_bindings`
- `graphical_artifact_links`

Definitions describe reusable logical source material. Occurrences describe
the realized compiled design. Local scalar nets belong to page occurrences,
and hierarchy terminal bindings explicitly connect parent sheet entries to
child ports. Aggregate bus and harness carriers remain drawing evidence and do
not become fake scalar terminals or local nets.

The review-safe identity for rendered schematic graphics is:

```text
page_occurrence_ref + artifact_key + element_id
```

Current schematic SVG/IR links use `artifact_key="sch.dwg_scene"`.
`graphical_artifact_links` resolve each scoped selector to a semantic target.
This matters because one logical `.SchDoc` object and element id can occur in
several realized pages with different resolved designators.

`physical_page_metadata` is keyed by `page_occurrence_ref` and carries only
Altium presentation facts: physical instance path, channel index/prefix/alpha,
logical and physical room names, and document number. It does not repeat
components or nets.

When `include_indexes=True`, compatibility indexes may include
`svg_to_component`, `svg_to_components`, `component_to_nets`, and
`net_to_components`. Design a2 page-derived indexes are not emitted.

### Design Nets

Design JSON net rows follow the public netlist shape and may add compiled
name-source provenance:

- `aliases`: alternate net names discovered while merging compiled
  connectivity
- `name_sources`: optional source records for the winning name and other
  candidate names, such as labels, ports, sheet entries, power ports, and
  compiled aliases

### PNP

`pnp.units` is currently `mm`.
`pnp.position_mode` is currently `altium-pick-place` for design JSON.

`altium-pick-place` matches Altium's Pick Place export by using the center of
the bounding box of component-owned pad anchor points, with component-origin
fallback for components that have no owned pads. Direct API callers can request
`component-origin` from `AltiumDesign.to_pnp(...)` when they need the footprint
placement origin instead.

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

`design_a2.schema.json` remains bundled for archived physical-page payloads.
`design_a1.schema.json` and `design_a0.schema.json` remain bundled for earlier
contracts. Current `AltiumDesign.to_json(...)` output uses
`altium_monkey.design.b0`. A graph-absent or unknown-graph-schema schematic
payload must be rejected with a clear migration error rather than synthesized
from Design a2 `physical_pages`.

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
- `endpoints`
- `hierarchy_paths`

Terminal fields:

- `designator`
- `pin`
- `pin_name`
- `pin_type`

`endpoints` contains source-owned semantic trace endpoints for downstream
schematic visualization. Unlike `graphical`, endpoint `role` values are not
inferred from SVG ids or rendered text. Endpoint records contain:

- `endpoint_id`
- `role`
- `element_id`: current render target id
- `object_id`: source electrical object id when it differs from the render id
- `name`
- `source_sheet`
- optional pin fields (`designator`, `pin`, `pin_name`, `pin_type`)
- optional `sheet_index` and `compiled_sheet_index`
- optional `connection_point` in `altium_coord` source schematic units

`graphical` groups related schematic SVG IDs by record type:

- `wires`
- `junctions`
- `labels`
- `power_ports`
- `ports`
- `sheet_entries`
- `pins`

For sheet-entry and harness-entry endpoints, `connection_point` is computed
from the composed basic-entry distance. `DistanceFromTop` contributes whole
100-mil steps and `DistanceFromTop_Frac1` contributes millionths of one step;
the netlist compiler rounds the resulting native 10-mil-unit hotspot
half-away-from-zero before exact endpoint matching.

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

The `view` fields describe the emitted SVG artifact. `included_layer_ids`
mirrors emitted `layer-*` groups, and `includes_board_outline` is true only
when outline geometry is present. These IDs are renderer layer IDs: legacy
source IDs for native legacy layers and renderer-assigned IDs for derived
layers, not serialized V7 saved layer IDs. Use `layers.all_layer_ids` and
optional `layers.render_layers` for layer discovery; use
`view.included_layer_ids` for the current SVG view.

`layers` contains:

- `all_layer_ids`: known renderer layer IDs for this board context, using the
  same legacy/native or renderer-assigned derived-layer convention as
  `view.included_layer_ids`
- `layer_id_to_key`
- `layer_id_to_name`: stable layer tokens such as `TOP`, `BOTTOM`, or `DRILLS`
- optional `render_layers` when `include_render_layer_metadata=True`; this is
  the consumer-facing registry for native Altium layers and renderer-derived
  layers.

Each `render_layers` entry contains:

- `id`
- `key`
- `name`
- `display_name`
- `role`
- `kind`: `native` or `derived`
- `source`: `altium` or `renderer`
- optional `derived_from` for renderer-derived entries

Native source-backed entries use `kind="native"` and `source="altium"`.
Renderer-derived entries use `kind="derived"` and `source="renderer"`. The
current derived entry is `DRILLS` with id `9001`, display name `Drill Holes`,
role `drill`, and `derived_from=["pad-hole", "via-hole"]`.

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
include `data-layer-*`, `data-net-*`, `data-component-*`, `data-feature`,
drill-specific `data-hole-*` attributes, and optional review-overlay attributes
such as `data-overlay-kind="pad-designator"` and
`data-overlay-kind="origin-datum"`.

Layer element metadata keeps stable tokens and display labels separate:
`data-layer-name` is token-oriented, while `data-layer-display-name` is the
human-facing label. Parsed PcbDoc output uses resolved board layer-stack names
when available and otherwise falls back to the default `PcbLayer` display label.

## `altium_monkey.extractable_assets.a0`

Emitter: `AltiumAssetInventory.to_dict(...)`

This is the public JSON-ready inventory for selecting one asset from a source
document or library. The current source containers are `AltiumPcbDoc`,
`AltiumPcbLib`, `AltiumSchDoc`, and `AltiumSchLib`.

Required root fields:

- `schema`: always `altium_monkey.extractable_assets.a0`
- `source_kind`: `pcbdoc`, `pcblib`, `schdoc`, or `schlib` for current emitters
- `source_path`: source path for file-backed containers, otherwise null for
  live unsaved containers
- `assets`: selectable asset summaries

Each asset summary contains:

- `ref`: the `AltiumAssetRef` handle to pass back to `extract_asset(...)`
- `kind`: `embedded_model`, `embedded_font`, `opaque_embedded`,
  `pcb_footprint`, or `sch_symbol`; the in-process API reserves `sch_image`,
  but it is not valid in A0 until a future schema revision adds a typed branch
- `name`
- `extraction_filename`
- `native_extension`
- `can_extract`
- `payload_available`
- `payload_sha256`
- `details`: typed per-kind details
- `extras`: reserved extension object; A0 requires `{}`

`AltiumAssetRef.key` is semantic and source-local. File-backed refs are durable
through normalized `source_path` identity. Live unsaved refs carry an opaque
process-local `source_instance_id`; those refs are move-stable for the live
object but are not durable across reloads or process boundaries.

Current `details` variants:

- Embedded model details: `model_format`, `id`, compressed/decompressed sizes,
  `is_embedded`, and PCB object `references`.
- Embedded font details: style, compressed/decompressed/raw sizes, and support
  status.
- Opaque embedded details: raw size, support status, and reason.
- PCB footprint details: pattern, occurrence, component indexes/designators,
  component count, source footprint library, pad count, and primitive count.
- Schematic symbol details: display/safe/original names, component count,
  component designators, selected designator, library reference, design item id,
  description, pin count, object count, and optional part count.

## `altium_monkey.pcb.embedded_assets.a0`

Emitter: `EmbeddedAssetInventory.to_dict(...)`

This is the focused inventory contract for embedded PCB binary assets. It is
intended for preview, dedupe, import dry-run, and extraction-planning consumers
that only need PcbDoc/PcbLib model, font, and opaque payload metadata. Use the
broader `altium_monkey.extractable_assets.a0` contract when a consumer also
needs selectable footprints, schematic symbols, or other logical assets.

Required root fields:

- `schema`: always `altium_monkey.pcb.embedded_assets.a0`
- `source_kind`: `pcbdoc` or `pcblib`
- `source_path`: source path for file-backed containers, otherwise null for
  live unsaved containers
- `models`: embedded 3D model summaries
- `fonts`: typed embedded font summaries
- `opaque_assets`: preserved embedded streams that are not parsed as typed
  assets

Model summaries contain:

- `kind`: `model`
- `index`
- `source_kind`
- `source_path`
- `name`
- `id`
- `extraction_filename`
- `model_format`
- `is_embedded`
- `compressed_size`
- `decompressed_size`: null when payload decompression fails
- `payload_available`
- `payload_sha256`: SHA-256 over decompressed payload bytes when requested and
  available
- `altium_checksum`
- `references`: PCB object references to this embedded model. A0 references
  are emitted only under model summaries, so `asset_kind` is constrained to
  `model`.

Font summaries contain:

- `kind`: `font`
- `index`
- `source_kind`
- `source_path`
- `family_name`
- `style`
- `extraction_filename`
- `compressed_size`
- `decompressed_size`
- `raw_size`
- `payload_available`
- `payload_sha256`
- `support_status`

Opaque summaries contain:

- `kind`: `opaque`
- `index`
- `source_kind`
- `source_path`
- `stream_name`
- `extraction_filename`
- `raw_size`
- `payload_available`
- `payload_sha256`: SHA-256 over raw opaque bytes when requested
- `support_status`
- `reason`

A0 currently allows typed `fonts` for PcbDoc and `opaque_assets` for PcbLib
`Library/EmbeddedFonts`. PcbLib typed font parsing should move to a future
schema revision if that stream shape is proven compatible.
