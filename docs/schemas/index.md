# Schema Contracts

`altium-monkey` emits a few JSON-shaped public contracts. The schema string is
the payload contract version. The Python package version is release metadata and
is intentionally separate from these schema IDs.

Any payload-shape change requires a new schema ID unless the existing schema
already permits it without changing field meaning.

## Revision Scheme

The leading letter is the major revision and changes for a breaking contract
change. Moving from `a` to `b` therefore marks an incompatible root shape. The
trailing number identifies an additive minor revision within that major family:
existing fields retain their meaning and required shape. Consumers should still
match a supported schema ID exactly unless they implement an explicit migration
or compatibility range.

1. Moving from `a0` to `a1` added project-analysis fields without removing the
   established Design fields.
2. Moving from `a1` to `a2` introduced the compiled physical-page projection.
3. Moving from `a2` to `b0` removes that duplicated projection and requires the
   source-neutral compiled schematic graph. Because this is breaking, the major
   revision advances from `a` to `b`.
4. Current public contracts use `a0`, `a1`, `a2`, and `b0` depending on payload
   family.

## Contract Files

The explicit contract bundle is maintained under
[`docs/schemas/altium_monkey`](altium_monkey/SPEC.md).

Machine-readable entry points:

1. [`design_b0.schema.json`](altium_monkey/design_b0.schema.json)
2. [`design_a2.schema.json`](altium_monkey/design_a2.schema.json)
3. [`design_a1.schema.json`](altium_monkey/design_a1.schema.json)
4. [`design_a0.schema.json`](altium_monkey/design_a0.schema.json)
5. [`netlist_a0.schema.json`](altium_monkey/netlist_a0.schema.json)
6. [`pcb_svg_enrichment_a0.schema.json`](altium_monkey/pcb_svg_enrichment_a0.schema.json)
7. [`embedded_assets_a0.schema.json`](altium_monkey/embedded_assets_a0.schema.json)
8. [`extractable_assets_a0.schema.json`](altium_monkey/extractable_assets_a0.schema.json)

`design_b0.schema.json` is self-contained for strict validation. Older sibling
schemas remain bundled for consumers pinned to earlier contracts or validating
other payload families directly.

## `altium_monkey.design.b0`

Emitter: `AltiumDesign.to_json(...)`

Generator: `altium_monkey`

This is the full project/design analysis contract. It combines project metadata,
schematic sheet metadata, variant metadata, enriched schematic components,
compiled nets, the source-neutral compiled schematic graph, narrow Altium page
presentation metadata, and resolved hierarchy data.

Root field order:

```text
schema
generator
project
variants
options
compile
diagnostics
sheets
components
schematic_hierarchy
compiled_schematic_graph
physical_page_metadata
pnp
nets
indexes
```

`indexes` is optional and is controlled by
`AltiumDesign.to_json(include_indexes=...)`.

`pnp` is optional and appears only when the design has a referenced PcbDoc that
can provide pick-and-place placements.

`compile` and `diagnostics` are optional and appear only when
`AltiumDesign.to_json(include_compile_metadata=True)` is requested.

`compiled_schematic_graph` and `physical_page_metadata` are always present.

Important fields:

1. `schema`: always `altium_monkey.design.b0`.
2. `generator`: always `altium_monkey`.
3. `project`: project name, path-derived metadata, document paths, and project parameters.
4. `variants`: project variant definitions, including DNP lists and parameter overrides when available.
5. `options`: netlist and hierarchy-resolution options used to generate the payload.
6. `sheets`: reachable schematic documents and sheet-level metadata. Canonical
   decimal `SheetNumber` values are emitted as JSON numbers for compatibility;
   non-canonical Altium values such as part-number strings are emitted exactly
   as JSON strings.
7. `components`: schematic components enriched with sheet, pin-count, parameters, and `svg_id` where available.
8. `schematic_hierarchy`: resolved documents, sheet symbols, channels, hierarchy paths, sheet-entry links, harness bundle links, and unresolved hierarchy diagnostics.
9. `pnp`: optional PCB-backed pick-and-place data in millimeters.
10. `compile`: optional compact compiled-model metadata, including compiled schema,
    summary, compile options, annotation metadata, and statistics.
11. `diagnostics`: optional compile, annotation, document, sheet-symbol,
    component, and net diagnostics.
12. `compiled_schematic_graph`: authoritative variant-neutral graph with ten
    source-neutral definition, occurrence, topology, binding, and drawing-link
    collections.
13. `physical_page_metadata`: Altium channel, room, path, and document facts
    keyed by canonical graph page occurrence ids; it does not repeat graph
    components or nets.
14. `nets`: compiled net records from the netlist contract, enriched with
    aliases and optional name-source provenance when available.
15. `indexes`: optional compatibility lookup maps for components, nets, pins,
    and unambiguous SVG IDs.

Compiled graph and drawing identity:

1. The graph schema is `altium_monkey.compiled_schematic_graph.a0` and the
   identity namespace is `sch.compiled_schematic_graph.a0`.
2. Its ten collections are `unit_definitions`, `page_definitions`,
   `unit_occurrences`, `page_occurrences`, `hierarchy_occurrences`,
   `component_occurrences`, `local_net_occurrences`, `terminal_occurrences`,
   `hierarchy_terminal_bindings`, and `graphical_artifact_links`.
3. A review-safe graphical identity is the tuple
   `(page_occurrence_ref, artifact_key, element_id)`.
4. Current schematic SVG/IR links use `artifact_key="sch.dwg_scene"`.
5. A bare SVG element id is not a realized identity in reused hierarchy.

The predecessor `altium_monkey.design.a2`
[`design_a2.schema.json`](altium_monkey/design_a2.schema.json) is retained for
archived physical-page payloads. `altium_monkey.design.a1`
[`design_a1.schema.json`](altium_monkey/design_a1.schema.json) is still bundled
for strict validators pinned to the pre-compiled-design project contract. The
first public design contract, `altium_monkey.design.a0`
[`design_a0.schema.json`](altium_monkey/design_a0.schema.json), is also
bundled. Current `AltiumDesign.to_json(...)` output uses
`altium_monkey.design.b0`. Graph-absent or unsupported-graph-schema schematic
payloads must be rejected with a migration error rather than reconstructed
from Design a2 `physical_pages`.

PNP fields:

1. `units`: currently `mm` in the design JSON contract.
2. `position_mode`: selected coordinate algorithm. Current design JSON emits
   `altium-pick-place`.
3. `source_pcbdoc`: source PcbDoc filename used for placements.
4. `placements`: list of component placements.

`altium-pick-place` matches Altium's Pick Place export by using the center of
the bounding box of component-owned pad anchor points, with component-origin
fallback for components that have no owned pads. Direct API callers can request
`component-origin` from `AltiumDesign.to_pnp(...)` when they need the footprint
placement origin instead.

PNP placement fields:

1. `designator`: component designator.
2. `comment`: schematic value/comment when available.
3. `layer`: normalized PCB layer, usually `top` or `bottom`.
4. `footprint`: PCB footprint name.
5. `center_x`: selected PnP position X in `pnp.units`.
6. `center_y`: selected PnP position Y in `pnp.units`.
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
8. `endpoints`: source-owned semantic trace endpoints for visualization.
9. `hierarchy_paths`: optional hierarchy provenance for hierarchical or repeated-channel designs.

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

When `endpoints[].connection_point` is produced for sheet entries or harness
entries, the point uses the composed basic-entry offset from `DistanceFromTop`
and `DistanceFromTop_Frac1`. The fractional field is millionths of one
100-mil entry step, and the netlist projection rounds the composed native
10-mil-unit offset half-away-from-zero before exact endpoint matching.

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
5. `view.included_layer_ids`: renderer layer IDs for layer groups actually
   emitted in this SVG. These are legacy source IDs for native legacy layers
   and renderer-assigned IDs for derived layers, not serialized V7 saved layer
   IDs.
6. `view.includes_board_outline`: true when board-outline geometry is actually
   emitted.
7. `layers.all_layer_ids`: all known renderer layer IDs in the rendered board
   context; this is a discovery registry, not proof that each layer is emitted
   in the current SVG. These IDs use the same legacy/native or
   renderer-assigned derived-layer convention as `view.included_layer_ids`.
8. `layers.layer_id_to_key`: stable layer keys such as `L1`, `L32`, or `DRILLS`.
9. `layers.layer_id_to_name`: stable layer tokens such as `TOP`, `BOTTOM`, or `DRILLS`.
10. `layers.render_layers`: optional render-layer registry emitted when
    `include_render_layer_metadata=True`; entries classify native Altium layers
    and renderer-derived layers such as `DRILLS`.
11. `lookup.net_index_to_name`: net-index lookup table.
12. `lookup.net_name_to_classes`: net-class membership by net name.
13. `lookup.component_index_to_designator`: component-index lookup table.
14. `lookup.component_index_to_uid`: component unique IDs by component index.
15. `components`: component placement and parameter summaries used by SVG viewers.

`layers.render_layers` entries contain `id`, `key`, `name`, `display_name`,
`role`, `kind`, and `source`. Native source-backed entries use
`kind="native"` and `source="altium"`. Renderer-derived entries use
`kind="derived"` and `source="renderer"` and may include `derived_from`; the
current derived entry is `DRILLS` with id `9001`, role `drill`, and
`derived_from=["pad-hole", "via-hole"]`.

Element-level SVG metadata uses ordinary `data-*` attributes. Common attributes
include:

1. `data-layer-id`, `data-layer-key`, `data-layer-name`,
   `data-layer-display-name`, and `data-layer-role`
2. `data-net-index`, `data-net`, `data-net-class`, and `data-net-classes`
3. `data-component-index`, `data-component`, and `data-component-uid`
4. `data-feature="board-outline"` and `data-feature="board-cutout"`
5. `data-primitive="pad-hole"` or `data-primitive="via-hole"` for drill geometry
6. `data-hole-kind`, `data-hole-plating`, and `data-hole-render`
7. `data-overlay-kind="pad-designator"` and
   `data-overlay-kind="origin-datum"` for optional review overlays

## `altium_monkey.extractable_assets.a0`

Emitter: `AltiumAssetInventory.to_dict(...)`

This is the JSON-ready inventory contract for assets that can be selected from
one source document or library. The public Python containers that currently
emit it are `AltiumPcbDoc.asset_inventory(...)`,
`AltiumPcbLib.asset_inventory(...)`, `AltiumSchDoc.asset_inventory(...)`, and
`AltiumSchLib.asset_inventory(...)`.

Payload root fields:

```text
schema
source_kind
source_path
assets
```

Important fields:

1. `schema`: always `altium_monkey.extractable_assets.a0`.
2. `source_kind`: source container kind such as `pcbdoc`, `pcblib`, `schdoc`,
   or `schlib`.
3. `source_path`: source file path when the inventory came from a file-backed
   container; live unsaved containers may emit `null`.
4. `assets`: selectable asset summaries.

Each asset contains:

1. `ref`: the `AltiumAssetRef` handle to pass back to `extract_asset(...)` or a
   kind-specific extraction helper.
2. `kind`: A0 accepts `embedded_model`, `embedded_font`, `opaque_embedded`,
   `pcb_footprint`, and `sch_symbol`. The in-process API reserves `sch_image`
   for future image inventory work, but it is not valid in this A0 JSON schema
   until a future schema revision adds a matching typed details branch.
3. `name`: public display and exact-name selection value.
4. `extraction_filename`: suggested output filename, or `null` when no stable
   filename is available.
5. `native_extension`: native file extension without a leading dot, or `null`.
6. `can_extract`: true when this API can extract the asset from the current
   source.
7. `payload_available`: true when the selected asset has direct byte payload
   access. Footprint and symbol assets extract as library objects instead.
8. `payload_sha256`: optional SHA-256 over extracted payload bytes when hashes
   were requested and a byte payload is available.
9. `details`: typed per-kind detail object. Numeric counts remain JSON numbers,
   booleans remain booleans, and designator/index lists remain arrays.
10. `extras`: reserved extension object for non-contract annotations. A0
    requires `{}`.

File-backed refs are durable through normalized `source_path` identity. Refs
from live unsaved containers carry an opaque process-local
`source_instance_id`; they are valid only for that live source instance in the
current process and are not durable across reloads or serialization
boundaries.

## `altium_monkey.pcb.embedded_assets.a0`

Emitter: `EmbeddedAssetInventory.to_dict(...)` from
`AltiumPcbDoc.embedded_asset_inventory(...)` and
`AltiumPcbLib.embedded_asset_inventory(...)`.

This is the focused PCB/PcbLib embedded-binary inventory contract for preview,
dedupe, import dry-run, and extraction-planning consumers. Use it when the
consumer only needs embedded model/font/opaque payload metadata. Use
`altium_monkey.extractable_assets.a0` when the consumer also needs selectable
logical assets such as footprints or schematic symbols.

Payload root fields:

```text
schema
source_kind
source_path
models
fonts
opaque_assets
```

Important fields:

1. `schema`: always `altium_monkey.pcb.embedded_assets.a0`.
2. `source_kind`: `pcbdoc` or `pcblib`.
3. `source_path`: source file path when file-backed, otherwise `null` for live
   unsaved containers.
4. `models`: embedded 3D model summaries.
5. `fonts`: embedded TrueType font summaries. A0 currently emits typed fonts
   for PcbDoc only.
6. `opaque_assets`: preserved embedded streams that are not parsed as typed
   assets. A0 currently uses this for PcbLib `Library/EmbeddedFonts`.

Model summaries include `index`, `name`, `id`, `extraction_filename`,
`model_format`, `is_embedded`, compressed/decompressed sizes, payload
availability, optional SHA-256, optional Altium checksum, and PCB object
references. Font summaries include family/style names, extraction filename,
sizes, payload availability, optional SHA-256, and support status. Opaque
summaries include stream name, raw size, payload availability, optional SHA-256,
support status, and reason.

Hashes are only present when requested and when a payload is available. Corrupt
or unsupported payloads report `payload_available=false` and
`payload_sha256=null`.
