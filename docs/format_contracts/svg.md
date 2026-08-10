# SVG Contract

altium-monkey emits SVG for schematic documents, schematic library symbols,
PCB documents, and PCB library footprints. SVG is a review and integration
format, not a full replacement for the source Altium files.

## Common Rules

- Normal SVG output includes a root `viewBox`.
- `include_view_box=False` omits only the root `viewBox` attribute.
- Omitting the `viewBox` does not change geometry, metadata, filenames, layer
  keys, or element identifiers.
- Strict schematic oracle modes may omit the root `viewBox` by default so they
  preserve the comparison surface used by renderer tests.
- SVG DOM `id` values are render-output identifiers. Downstream tools should
  prefer documented `data-*` metadata when semantic identity is needed.

## Schematic SVG

SchDoc and SchLib SVG output uses schematic pixel-canvas coordinates. The
renderer converts source schematic coordinates into the emitted SVG coordinate
space before writing geometry.

The normal schematic SVG root carries document identity attributes:

- `data-doc-id`: the schematic document id used for this render. For parsed
  SchDoc output this is normally the sheet file UniqueID; for SchLib symbol
  output it is a synthesized symbol render id.
- `data-doc-ver`: the schematic SVG renderer contract version for this root
  shape.

The normal schematic SVG group shape is:

- `<g id="scene">`: top-level scene group
- `<g id="DocumentMainGroup">`: document-level drawing group
- `<g id="<data-doc-id>">`: drawing group for the rendered sheet or symbol
- optional background and mask groups when the selected render options require
  them
- source-record groups such as `<g id="<record UniqueID>">` for rendered
  records that have a source UniqueID

Most schematic graphical records render inside a group whose SVG `id` is the
source record UniqueID. Component groups use the component UniqueID. Pin,
wire, label, port, sheet-entry, harness, and power-port records use their own
record UniqueIDs when available. Some synthetic or helper geometry may not have
a stable source-owned id and should not be treated as semantic identity.

Logical-sheet SVG does not embed a document-level JSON metadata payload. The
project-level `AltiumDesign.to_physical_svg(page_occurrence_ref)` boundary does
embed graph scope as attributes: the root carries
`data-page-occurrence-ref` and `data-artifact-key="sch.dwg_scene"`; a retained
record group selected by a graphical link additionally carries
`data-element-id`, `data-graph-target-type`, and `data-graph-target-ref`.
The authoritative relationship sidecar remains the
`AltiumDesign.to_json(...)` and `Netlist.to_json(...)` payload:

- `components[].svg_id` points to the component SVG group id, normally the
  component record UniqueID.
- optional `indexes.svg_to_component` maps component SVG ids back to
  designators when indexes are requested.
- for repeated sheets and instantiated channels,
  `compiled_schematic_graph.graphical_artifact_links[]` disambiguates a
  logical element id with its canonical `page_occurrence_ref` and semantic
  target.
- `nets[].graphical` groups related schematic SVG ids by record type:
  `wires`, `junctions`, `labels`, `power_ports`, `ports`, `sheet_entries`, and
  `pins`.
- `nets[].graphical.pins[]` contains `{designator, pin, svg_id}` objects so a
  viewer can highlight the actual pin SVG element.
- `nets[].endpoints[]` provides semantic trace endpoints. `element_id` is the
  current SVG render target, while `object_id` is the source electrical object
  id when it differs from the rendered element. Endpoint connection points use
  source schematic coordinates, not SVG coordinates.

For schematic visualization, use the SVG as the drawing surface and the design
or netlist JSON as the semantic lookup table. Do not infer electrical meaning
from rendered text strings or group nesting alone.

In repeated/channel projects, a schematic SVG still renders the logical source
sheet. The physical review selector is the tuple of canonical page occurrence
ref, artifact key `sch.dwg_scene`, and source record element id from
`AltiumDesign.to_json()["compiled_schematic_graph"]`. A consumer that renders
or annotates an instantiated page should select the page occurrence first,
then resolve its scoped graphical links to component, terminal, local-net,
hierarchy-occurrence, or page targets.

`AltiumDesign.to_physical_svg(page_occurrence_ref)` is the project-level API for
that physical-page rendering. It renders the selected logical SchDoc geometry
with compiled physical designator text before IR/SVG emission, and the SVG root
uses the canonical page occurrence id as both `data-doc-id` and
`data-page-occurrence-ref`. `AltiumDesign.to_physical_ir()` is
the corresponding IR boundary for consumers that need geometry JSON instead of
SVG.

Embedded schematic images preserve the best available payload. Native PNG,
JPEG, GIF, SVG, and WebP payloads are embedded with their natural media type.
Plain BMP payloads are decoded to PNG for browser-compatible SVG output.
Alpha data is preserved when it exists in the stored image payload.

Schematic text is measured through the font resolver before SVG is emitted.
Installed system fonts are preferred, and callers can add search roots through
`ALTIUM_FONT_DIRS`. Common Altium/Windows font families can fall back to
bundled open-source fonts when unavailable: Arimo for Arial and Microsoft Sans
Serif-style families, Tinos for Times New Roman-style families, and Cousine for
Courier New or monospace families. When bundled fallback fonts are used,
schematic SVG embeds those font faces so browser layout follows the same
metrics used by the renderer.

## PCB SVG

PcbDoc and PcbLib SVG output uses millimeter coordinates. PCB SVG filenames and
dictionary keys use stable layer tokens such as `TOP`, `BOTTOM`, and
`TOPOVERLAY`.

Human-facing PCB layer labels are separate from stable layer tokens.
`data-layer-name` and JSON layer maps carry token-oriented names.
`data-layer-display-name` carries a user-facing label. Parsed PcbDoc output
uses the resolved board layer stack when one is available and otherwise falls
back to the default layer display label.

PcbLib footprints do not own a board signal stack, but PcbLib mechanical layer
registry metadata can provide custom mechanical display labels. When no
library registry label is available, footprint SVG output falls back to the
default display label.

When PCB metadata is enabled, the PCB SVG root carries render-context
attributes:

- `data-stage`: render stage such as `viz`, `validation`, or `export`
- `data-group-mode`: grouping mode requested by render options
- `data-enrichment-schema`: the PCB enrichment schema id
- `data-view-kind`: view type such as `board`, `layer_set`, or
  `board_outline_only`
- `data-mirror-x`: whether the SVG scene is mirrored around X
- `data-source`: source PcbDoc filename when known
- `data-board-centroid-*-mils`: board centroid values when known

PCB layer groups use ids such as `layer-TOP`, `layer-BOTTOM`, and
`layer-DRILLS`. When metadata is enabled, layer groups and layer-owned
primitives carry:

- `data-layer-id`: legacy source layer id for native layers; renderer-assigned
  id for derived layers. V7-only native layers omit this attribute rather than
  inventing a fake legacy id.
- `data-layer-key`: stable short key such as `L1`, `L32`, `DRILLS`, or
  `V7_16908321`
- `data-layer-name`: stable token such as `TOP`, `BOTTOM`, or `DRILLS`
- `data-layer-display-name`: human-facing label
- `data-layer-token`: V7-aware stable token when a layer reference is resolved,
  such as `MECHANICAL33` or `MID126`
- `data-layer-family`: normalized layer family such as `signal` or
  `mechanical`
- `data-layer-role`: normalized role such as `copper`, `silkscreen`,
  `soldermask`, `paste`, `mechanical`, `drill`, or `other`
- `data-layer-v7-saved-id`: serialized V7 saved-layer id when the source layer
  is represented by that identity family

Layer groups are classified by origin:

- **Native layers** are backed by source Altium layer data and use stable layer
  tokens such as `TOP`, `BOTTOM`, `TOPOVERLAY`, and mechanical-layer tokens.
- **Derived layers** are renderer-created layer-like outputs derived from
  source geometry. The current derived PCB layer is `DRILLS`, which aggregates
  rendered pad/via hole geometry. Derived layers use the same layer metadata
  attributes so consumers can discover them without hard-coding filenames.
- **Overlay groups** are reviewer/consumer aids emitted by renderer options.
  They are not native board layers and should not be interpreted as fabrication
  data.

The current public SVG layer contract is V7-layer-token oriented while keeping
legacy `PcbLayer` selectors working. Native groups use stable tokens such as
`TOP`, `BOTTOM`, `TOPOVERLAY`, `MECHANICAL33`, and `MID126`, plus documented
renderer-derived groups such as `DRILLS`. `PcbLayer` is still the legacy/TV6
enum and contains Top, Mid1 through Mid30, Bottom, and Mechanical 1 through
Mechanical 16 only.

Use `PcbLayerRef` values or semantic tokens for layers outside the legacy enum.
Serialized V7 saved layer ids such as `0x01020011` for Mechanical 17 or
`0x01000020` for Mid31 are source diagnostics, not public selectors for
`visible_layers` or `layer_render_order`.

For migrated primitive families, SVG output preserves V7 side-field identity.
Tracks, arcs, fills, texts, regions, and component-body projections whose real
layer is recoverable only from V7 side fields render under their resolved token
when that layer is supported by the board or library registry. V7-only signal
rendering depends on stack-backed registry evidence, normally from a
`.stackupx`-backed PcbDoc. Pads, vias, and unsupported native primitive
families remain on their documented conservative paths.

When `PcbSvgRenderOptions(include_render_layer_metadata=True)` is set, the
embedded enrichment JSON also includes `layers.render_layers`. This is the
canonical layer registry for consumers that need to discover what the renderer
can emit without scanning SVG group ids. Each entry has this shape:

```json
{
  "id": 1,
  "key": "L1",
  "name": "TOP",
  "display_name": "Top Layer",
  "role": "copper",
  "kind": "native",
  "source": "altium"
}
```

V7-only native layers omit `id` and use a V7 layer key plus saved-layer
diagnostics:

```json
{
  "key": "V7_16908321",
  "name": "MECHANICAL33",
  "token": "MECHANICAL33",
  "display_name": "Sample Mechanical 33",
  "custom_name": "Sample Mechanical 33",
  "family": "mechanical",
  "role": "mechanical",
  "kind": "native",
  "source": "altium",
  "v7_saved_layer_id": 16908321
}
```

Derived layers use the same base fields and add derivation metadata:

```json
{
  "id": 9001,
  "key": "DRILLS",
  "name": "DRILLS",
  "display_name": "Drill Holes",
  "role": "drill",
  "kind": "derived",
  "source": "renderer",
  "derived_from": ["pad-hole", "via-hole"]
}
```

The stable consumer rules are:

- `name` and `token` are stable tokens used in SVG group ids, filenames, and
  `to_layer_svgs(...)` dictionary keys.
- `display_name` is a human-facing label and may come from the board layer
  stack or mechanical-layer registry.
- `role` is a normalized broad category for filtering and UI grouping.
- `kind="native"` means the entry is backed by source Altium layer data.
- `kind="derived"` means the entry is synthesized by the renderer from source
  primitives.
- `source="renderer"` means consumers should not look for a matching Altium
  source layer.

Use `PcbSvgRenderOptions.visible_layers` to select rendered layers. Ordered
sequences preserve the requested order. Use `layer_render_order` when selection
and draw order must be specified separately. Layers not mentioned in
`layer_render_order` are appended deterministically. Per-layer SVG output
returns one SVG per selected native/derived layer; top-level overlay groups may
be promoted by review tooling for visibility but remain identifiable by their
overlay metadata.

`visible_layers` and `layer_render_order` accept legacy `PcbLayer` values,
`PcbLayerRef` values, registry entries, and semantic layer tokens or unique
display names that resolve through the current registry.
Derived layers such as `DRILLS` are controlled by renderer options rather than
by inventing public `PcbLayer` enum values. With the default
`drill_holes_as_layer_group=True`, drill holes are emitted in the derived
`DRILLS` layer group instead of being interleaved inside copper layer groups.

Mechanical 17 through Mechanical53 and StackUpX-backed Mid31 through Mid126
selection use `PcbLayerRef` or semantic tokens. They are not selected by adding
enum values after `PcbLayer.MECHANICAL_16` or `PcbLayer.MID30`.

PCB primitive metadata uses `data-primitive` values such as `track`, `arc`,
`pad`, `via`, `region`, `text`, `pad-hole`, and `via-hole`. Relationship
attributes are emitted when the source primitive carries the linkage:

- `data-net-index`, `data-net`, `data-net-uid`
- `data-net-class` and `data-net-classes`
- `data-component-index`, `data-component`, `data-component-uid`
- `data-pad-designator` and `data-pad-number` for pad geometry
- `data-text-role` for PCB text (`designator`, `comment`, or `free`)

Where deterministic primitive identity is assigned, the SVG element has both
`id` and `data-element-key`. The current key form is
`pcb-<primitive-kind>-<index>` with optional layer and role suffixes. Treat the
exact string as stable within one emitted SVG and as a lookup key for that
rendered artifact. Use semantic `data-*` attributes for cross-render matching.

Board-outline geometry uses `data-feature`:

- `data-feature="board-outline"` for the outer profile
- `data-feature="board-cutout"` plus `data-feature-index` for board-profile
  voids

Drill geometry uses:

- `data-primitive="pad-hole"` or `data-primitive="via-hole"`
- `data-hole-owner`
- `data-hole-kind`
- `data-hole-plating`
- `data-hole-render`

### PCB Review Overlays

PCB review overlays are opt-in. Existing default `PcbDoc` and `PcbLib` SVG
output does not include these groups unless the corresponding render option is
enabled.

The shared PCB renderer options apply to both whole-board and footprint output:

- `PcbDoc.to_svg(...)` and `PcbDoc.to_layer_svgs(...)` render whole-board
  documents.
- `PcbFootprint.to_svg(...)` and `PcbFootprint.to_layer_svgs(...)` render
  PcbLib footprints through a transient board-shaped adapter so they use the
  same renderer contract.

Overlay metadata attributes are emitted only when `include_metadata=True`.
Visible overlay geometry still renders when metadata is disabled, but
nonessential `data-*` overlay attributes are omitted.

Pad-designator overlays are enabled with
`PcbSvgRenderOptions(show_pad_designators=True)`. The renderer emits SVG text
labels for pads on rendered copper layers:

```xml
<g id="pcb-pad-designator-overlays"
   class="pcb-pad-designator-overlays"
   data-overlay-container="pad-designator"
   data-overlay-z-order="top">
<g class="pcb-pad-designator-overlay"
   data-overlay-kind="pad-designator"
   data-overlay-role="review-annotation"
   data-layer-id="1"
   data-layer-name="TOP"
   data-layer-role="copper">
  <text class="pcb-pad-designator-label"
        data-primitive="pad-designator-label"
        data-overlay-kind="pad-designator"
        data-pad-designator="A1"
        data-pad-number="A1"
        data-layer-id="1"
        data-layer-name="TOP"
        data-layer-role="copper"
        fill="#111111"
        font-family="monospace"
        font-size="0.42">A1</text>
</g>
</g>
```

The pad-label renderer contract is:

- labels are emitted only for pads that render on the target copper layer;
- label text is the source pad designator;
- label SVG uses screen-horizontal text by default, independent of pad
  rotation;
- label sizing is derived from the resolved pad body size and
  `pad_designator_padding_ratio`;
- `pad_designator_padding_ratio` is unitless and clamped by the renderer to a
  conservative range;
- there is no lower font-size floor; reviewers can zoom into small pads;
- `pad_designator_max_font_size_mm` can cap label size;
- labels are fill-only in renderer output and do not emit `stroke`,
  `stroke-width`, or pad-rotation `transform` attributes;
- `text_as_polygons` does not convert pad-label overlays into polygons;
- `include_view_box=False` does not change pad-label placement.

Consumers that need outlines, shadows, or other decoration should style
`.pcb-pad-designator-label`, `.pcb-pad-designator-overlay`, or
`[data-overlay-kind="pad-designator"]` downstream. The renderer does not expose
outline width as a public API knob because fixed absolute outlines become
visually heavy on small pads.

The footprint/board origin overlay is enabled with
`PcbSvgRenderOptions(show_origin_datum=True)`. It projects document coordinate
`(0,0)` into the SVG surface:

```xml
<g id="pcb-origin-datum-overlay"
   class="pcb-origin-datum-overlay"
   data-overlay-kind="origin-datum"
   data-overlay-role="reference-datum"
   data-overlay-z-order="top"
   data-origin-x-mils="0"
   data-origin-y-mils="0"
   data-origin-svg-x-mm="12.7"
   data-origin-svg-y-mm="6.35"
   data-marker-style="circle_cross"
   data-marker-size-mm="1.2">
  <circle class="pcb-origin-datum-circle"
          data-primitive="origin-datum-circle"
          data-overlay-kind="origin-datum"
          data-marker-part="circle"/>
  <line class="pcb-origin-datum-cross pcb-origin-datum-cross-horizontal"
        data-primitive="origin-datum-cross"
        data-overlay-kind="origin-datum"
        data-marker-part="cross-horizontal"/>
  <line class="pcb-origin-datum-cross pcb-origin-datum-cross-vertical"
        data-primitive="origin-datum-cross"
        data-overlay-kind="origin-datum"
        data-marker-part="cross-vertical"/>
</g>
```

Datum styling options are expressed in millimeters:
`origin_datum_marker_size_mm`, `origin_datum_stroke_width_mm`,
`origin_datum_color`, and `origin_datum_marker_style`. Supported marker styles
are `circle`, `cross`, and `circle_cross`; unknown values fall back to
`circle_cross`.

In `PcbLib` footprint SVGs this is the footprint 0,0 datum. In whole-board
`PcbDoc` SVGs this is the board/document coordinate origin. It is not a
per-component footprint-origin overlay for placed components.

Overlay groups are emitted after normal layer geometry when
`pad_designator_overlay_z_order="top"` so soldermask or paste layers do not
obscure review labels. With `pad_designator_overlay_z_order="layer"`,
pad-designator groups stay inside the owning layer output.

Generated corpus review HTML may promote overlay groups into viewer-owned
overlay layers so reviewers can toggle labels and datum markers independently
from fabrication layers. That promotion is a review UI behavior; the raw SVG
contract remains the class and `data-*` metadata documented above.

## PCB Enrichment Metadata

When PCB metadata is enabled, the root SVG and the embedded metadata payload
use schema id `altium_monkey.pcb.svg.enrichment.a0`.

The PCB enrichment payload records document-level context such as:

- emitted view information
- included layer ids
- layer token mappings
- net and net-class summaries
- component placement summaries
- board-outline and drill relationships
- optional render-layer registry entries when
  `include_render_layer_metadata=True`

The payload is embedded as escaped JSON in:

```xml
<metadata id="pcb-enrichment-a0" data-schema="altium_monkey.pcb.svg.enrichment.a0">
  ...
</metadata>
```

The metadata element id is a DOM lookup anchor. The `data-schema` attribute and
JSON `schema` field are the payload contract identifiers.

At the top level, the payload has these fields:

- `schema`: schema id, currently `altium_monkey.pcb.svg.enrichment.a0`
- `source`: source-document context, including `pcbdoc_file` when known
- `board`: board centroid information in mils when known
- `view`: emitted view metadata
- `layers`: layer maps and optional render-layer registry
- `lookup`: net/component lookup tables
- `components`: component placement summaries used by SVG viewers

The `view` object describes emitted SVG structure, not merely requested render
options. Use these fields when a consumer needs to know what the current SVG
artifact actually contains:

- `kind`: `board`, `layer_set`, or `board_outline_only`, derived from emitted
  layer groups and board-outline geometry
- `included_layer_ids`: sorted renderer layer ids for `id="layer-*"` groups
  actually emitted in this SVG. These are legacy source ids for native legacy
  layers and renderer-assigned ids for derived layers such as `DRILLS`. V7-only
  native layers do not appear in this legacy-id list; use `render_layers` and
  element `data-layer-token`/`data-layer-v7-saved-id` attributes for those
  identities. Requested layers filtered out as empty are not listed unless
  `show_empty_layers=True` causes an empty group to be emitted.
- `includes_board_outline`: true only when board-outline geometry is actually
  emitted

The `layers` object is the discovery and lookup registry for the rendered board
context:

- `all_layer_ids`: known renderer layer ids for the rendered board context;
  entries can exist even when the current SVG view does not emit that layer
  group. These are legacy source ids for native legacy layers and
  renderer-assigned ids for derived layers, not serialized V7 saved layer ids.
- `layer_id_to_key`: stable short keys such as `L1`, `L32`, and `DRILLS`
- `layer_id_to_name`: stable tokens such as `TOP`, `BOTTOM`, and `DRILLS`
- `render_layers`: optional array described in the render-layer registry above

Human-facing layer names, normalized roles, native/derived classification, and
derivation details are available through the optional `render_layers` registry,
not through the legacy layer-id maps.
Use `layers.render_layers` for V7-aware discovery. Use `layers.all_layer_ids`
only for legacy-id and derived-id compatibility, and use
`view.included_layer_ids` for the legacy or derived layer groups present in the
current SVG.

The schema contract pages contain the machine-readable payload shape. The SVG
contract here documents how that payload relates to the rendered SVG elements.

## Test Gates

The SVG contract is protected by targeted unit tests, public example tests,
corpus SVG lanes, and release signoff checks. The signoff gate also checks that
these contract docs are synchronized into the released docs.
