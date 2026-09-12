# PcbDoc Contract

`AltiumPcbDoc` is the public board model for PCB documents.

Ordinary `from_file(...)` and `from_bytes(...)` loading parses the requested
board without constructing a second whole-document normalized-state snapshot.
Loading establishes parseability of supported structures, not compilation,
electrical validity, or complete semantic certification. Output workflows are
expected to start from a caller-selected saved input and validate the facts
needed by that output.

## Stable Surface

- Parse existing `.PcbDoc` files.
- Preserve unknown streams and unsupported fields during normal read/write
  flows.
- Create blank PCB documents.
- Add common board primitives with high-level helper methods.
- Add nets, net classes, differential pairs, components, footprints, vias,
  tracks, arcs, regions, pads, text, and component bodies.
- Embed STEP models and infer component-body projection bounds through the core
  `wn-geometer` dependency, with explicit projection overrides available for
  deterministic authored geometry.
- Read and write promoted via metadata such as IPC-4761 type, via feature
  rows, solder-mask tenting, hole tolerance, fabrication/assembly testpoint
  flags, and propagation delay.
- Author shape-based region outlines with line/arc extended vertices through
  `add_region(..., outline_vertices=...)`.
- Author custom pads through the native board custom-shape contract, including
  primary and additional per-layer custom bodies, holes, net assignment,
  component ownership, and pad-center offsets.
- Preserve imported PCB dimension records and re-author raw `Dimensions6/Data`
  records through `add_dimension_record(...)`; this is not a full
  object-oriented dimension model.
- Author round, square, and slotted pad drill-hole shapes. Slotted holes
  require a positive slot length; square holes require a positive drill size.
- Inspect and change a component pad's stored removed-copper-land state in
  strict legacy Top/Mid1..Mid30/Bottom order while retaining its per-layer
  geometry and full-stack tail.
- Inspect user-defined PCB unions through union-name records, typed smart-union
  records, and computed user-union member summaries.
- Inspect and author semantic mechanical layer kind assignments through
  `mechanical_layer_kinds`, `get_mechanical_layer_kind(...)`, and
  `set_mechanical_layer_kind(...)`. The mapping is stored in
  `LayerKindMapping/Data` and synchronized into the Board6 `MECHKIND`
  layer-table/cache fields used by Altium.
- Author mechanical layer display names, enabled flags, and component mirror
  pairs through `set_mechanical_layer(...)` and
  `set_mechanical_layer_pair(...)`.
- Render PCB SVG and PCB layer SVGs.

## Object Model

PcbDoc is helper-oriented rather than `ObjectCollection`-based. Prefer
document-owned helpers such as `add_track(...)`, `add_via(...)`,
`add_component(...)`, `add_differential_pair(...)`, and related APIs.

Direct record-list mutation remains an advanced escape hatch for narrow edits
or preservation work.

For every counted section that the writer owns, `save(...)` treats `Header`
and `Data` as one serialization unit. `Header` is the current little-endian
32-bit record count and `Data` is the matching serialized collection. A
transition from one record to none writes a zero Header and empty Data instead
of falling back to the original source streams. A transition from none to one
creates both streams. Unsupported and otherwise unowned sections remain raw
passthrough. A source-backed save may still regenerate an owned section that
the caller did not edit; streams outside the selected regenerated set remain
byte-identical.

This is a serialization-integrity rule, not a generic object-management API.
Direct list mutation does not imply cascading removal or repair of component
ownership, primitive indexes, custom-shape relationships, model references,
or other companion state. Use a document-owned helper when one exists. The
current direct-list behavior remains available for narrow compatibility
workflows, but callers must coordinate related lists and references themselves.

Some owned streams are deliberately coupled: text serialization owns its wide
string table, embedded and non-embedded model catalogs have different counts,
and several region, component-body, corner/chamfer, custom-shape, and
via-structure streams have companion relationships. Count-changing
shape-based-region serialization fails closed when the source contains opaque
bytes that cannot be proven safe to regenerate.

## Custom Pads

PcbDoc custom pads are authored as an anchor pad plus one or more custom-pad
region shapes. The board writer emits `CustomShapes/Header` and
`CustomShapes/Data`; each `CustomShapes/Data` record uses zero-based
`PRIMITIVEINDEX` to reference the pad record. The paired `Regions6` and
`ShapeBasedRegions6` records carry native one-based `PADINDEX` metadata.

Ordinary region authoring and PcbDoc custom-pad body authoring share the same
outline normalization path for point lists, holes, and optional
`PcbExtendedVertex` line/arc outlines. Custom pads remain a composed workflow:
they add the anchor pad and native `CustomShapes/*` attachment records around
the shared region body.

`PcbExtendedVertex.start_angle` and `PcbExtendedVertex.end_angle` are degrees.
This matches the shape-based-region SVG renderer and the board-outline
conversion path, which convert these values with degree-based trigonometry.

`add_custom_pad(...)` takes the primary layer body through
`outline_points_mils` and optional primary holes through `hole_points_mils`.
Pass `outline_vertices` when the primary body needs native line/arc segment
semantics rather than a point-only polygon. Use `PcbCustomPadLayerShapeSpec`
entries in `layer_shapes` when a board pad needs additional layer-specific
custom bodies and holes that share the same anchor pad; layer-shape entries can
also carry `outline_vertices`.

Custom-pad anchors may carry ordinary pad drill data through `hole_size_mils`,
`plated`, `hole_shape`, `slot_length_mils`, `slot_rotation_degrees`, and drill
tolerance parameters. This is for source documents where the custom copper body
and the drill are both owned by one Altium pad record.

This differs from PcbLib, where footprint custom pads use
`ExtendedPrimitiveInformation` rather than board `CustomShapes/*`. Public
PcbDoc and PcbLib APIs intentionally expose the same semantic
`add_custom_pad(...)` shape while preserving the container-specific native
storage contracts.

## Pad Corner Radius

Rounded-rectangle pad corner radius uses Altium's native dual-lane storage.
The pad record (SubRecord 6) carries a rounded integer percent per layer.
Exact fractional percents are stored separately in the board-level
`CornerRadiusChamfer` section as text blocks of the form
`|SCR0.LAYER=<layer>|SCR0.CRPCTEX=<percent>|PRIMITIVEINDEX=<n>`, where
`PRIMITIVEINDEX` is the zero-based pad position in board pad-list order.

Read accessors on `AltiumPcbPad`:

- `corner_radius_percentage` is the legacy rounded integer lane.
- `corner_radius_percent_exact` is the exact fractional percent when present.
- `exact_corner_radius_percent_by_layer` and
  `exact_corner_radius_percent_on_layer(layer)` expose per-layer exact values.
- `corner_radius_mils_on_layer(layer)` resolves the effective radius, preferring
  the exact lane; the radius is `percent / 200 * min(width, height)`, capped at
  half the smaller pad dimension.

Authoring uses `add_pad(..., corner_radius_percent=...)`. Whole-number
percents write only the legacy integer lane, so existing whole-number output
stays byte-identical. Fractional percents write both lanes and are supported
for simple top- or bottom-layer pads; they are not combined with per-layer pad
body overrides. Round-tripped documents preserve both lanes.

## Component-pad removed copper lands

`AltiumPcbPad.removed_copper_layers` is an immutable 32-value snapshot in
legacy copper-layer order. Use `is_copper_land_removed(layer)` for one strict
`PcbLayer` query and `set_copper_land_removed(layer, removed)` for an explicit
Boolean mutation. `has_removed_copper_layer_map` distinguishes a persisted
complete map from an absent map; `removed_copper_layer_map_is_canonical`
reports whether every persisted byte is zero or one.

`has_stored_copper_land_on_layer(layer)` is a structural query. It combines
the stored removed state with the pad's owning layer, but it does not render,
delete geometry, recompute connectivity, or decide manufacturing output.

Save patches only the owned map byte when a valid full-stack table is already
present. The first true state on a valid absent, geometry-only, or all-false
map-only pad materializes Altium's 651-byte base form. Original per-layer
geometry, optional map state, full-stack entries, unknown entry suffixes, and
residual bytes remain lossless. Ambiguous or malformed layouts fail closed.
An all-false map-only pad permits one pending first-true change (or its
reversion) before save; stage additional removals after saving and reopening
the proven framed form.
Via removed-layer behavior and storage are separate and unchanged.

## Dimensions

Parsed PcbDoc dimensions are exposed as typed `AltiumPcbDimension` records with
raw-payload-preserving serialization. `add_dimension_record(...)` appends one
native Dimensions6 record from `record_type`, `record_leader`, and the raw
payload bytes. This API is for preservation and transcode workflows; it does
not yet define a full object-oriented semantic dimension-construction model.

## User Unions

`union_name_records` exposes the decoded `UnionNames/Data` catalog.
`smart_unions` exposes read-only typed smart-union records. `user_unions`
computes named user-defined unions and member references from parsed public
primitive fields.

Use explicit mutation helpers such as `create_user_union(...)`,
`rename_user_union(...)`, `add_user_union_member(...)`,
`remove_user_union_member(...)`, and `delete_user_union(...)` for user-defined
union authoring. Typed smart unions such as drill tables, layer-stack tables,
via stitching, via shielding, OLE/object unions, rectangles, and length tuning
are read-only in this contract.

`create_user_union(...)` auto-allocates a native union id by default. Pass
`union_index=...` only for deterministic replay/recreation workflows that need
to preserve an existing native union id.

Passing a component to `create_user_union(...)` includes the component record
and its authorable child primitives. Shape-based region membership is kept in
sync with the paired standard region record when that pair exists.

## Units

High-level PCB helper methods use explicit `*_mils` parameter names. Low-level
record fields may expose source integer storage units.

## Embedded 3D Models

STEP-derived component-body bounds use `wn-geometer`. If STEP bounds cannot be
computed on the current host, authoring helpers may use an axis-aligned
rectangle around available SMD/through-hole pads as a recovery projection. This
fallback is not a replacement for STEP-derived model geometry.

## Embedded Asset Inventory

`AltiumPcbDoc.embedded_asset_inventory(...)` is the focused read-only contract
for embedded PCB binary payloads. It lists embedded 3D models and embedded
TrueType fonts without writing files. Payload bytes remain explicit direct
calls through `get_embedded_model_payload(index)` and
`get_embedded_font_payload(index)`.

Summaries report `payload_available` from actual payload accessibility. Corrupt
supported payloads stay visible for review but report unavailable state, no
SHA-256 hash, and no decompressed size. Hashes are emitted only when requested
and only for available payload bytes.

`EmbeddedAssetInventory.to_dict()` emits
`altium_monkey.pcb.embedded_assets.a0`. Use the broader
`AltiumAssetInventory` / `altium_monkey.extractable_assets.a0` contract when a
consumer also needs selectable footprints or schematic symbols.

## Layer Names

Primitive authoring APIs keep `PcbLayer` as the legacy/TV6 enum and use
`PcbLayerRef` for V7-aware layer identity. `PcbLayer` contains Top, Mid1
through Mid30, Bottom, and Mechanical 1 through Mechanical 16 only. Serialized
V7 saved layer ids, V8 Layer Stack Manager rows, V9 Board6 cache rows, and
LayerKindMapping ids are separate identity systems; see the public PCB layers
guide.

Stable layer keys use token names such as `TOP`, `BOTTOM`, and `TOPOVERLAY`.
Mechanical layer display names, enabled flags, and mirror pairs are board
registry metadata. Mechanical layer kind assignments are semantic metadata and
do not by themselves rename or enable mechanical layers.
Use the resolved layer stack when board-specific user-facing names are needed.
Default display labels are fallback labels, not stable identifiers.
`ResolvedLayerStack` is a derived read-only consumer view; new PcbDoc authoring
uses `AltiumLayerStackDocument`.

Use `PcbLayerRef` or documented semantic tokens for layers outside the legacy
enum. Track, arc, fill, text, region, and component-body helpers can author
ordinary numbered mechanical layers through Mechanical53. Track, arc, fill,
text, and region helpers can author V7-only signal refs such as Mid31 through
Mid126 when `set_layer_stack_document(...)` supplies matching enabled
physical-stack evidence, normally from `.stackupx`.

Pads and vias remain conservative. Legacy signal-layer pads and vias are
supported. V7-only signal pads/vias and non-signal via span endpoints reject
until the native storage and stack/span semantics are fixture-proven.

## Layer Stack And Interchange

`AltiumLayerStackDocument` is the source-aware model for PcbDoc layer stacks.
The stable contract is PcbDoc inspection, preservation during normal
read/write flows, canonical empty-board synthesis, and controlled new
rigid-board stack construction. New-document rigid-flex authoring is limited
to typed `AltiumLayerStackDocument` models that explicitly provide physical
layers, substacks, board regions, and optional branch topology through the
public `AltiumStackLayer`, `AltiumStackSubstack`, `AltiumStackRegion`,
`AltiumStackBendLine`, and `AltiumStackBranch*` dataclasses. The writer emits
native `Board6/Data`, `BoardRegions/Data`, and embedded StackupX branch data,
then callers should re-open the generated PcbDoc to verify the intended
topology.

The source-aware topology query contract uses native ids, not display names,
for joins. `AltiumStackSubstack.source_stackup_ref` and
`AltiumStackRegion.layerstack_id` are the stable substack/region join. Branch
section stacks, impedance transmission lines, and via/backdrill spans can
reference the same ids with bare GUID spelling; public lookup helpers normalize
refs with or without braces. Use `substack_by_source_ref(...)`,
`board_regions_for_layerstack_id(...)`, `layers_for_substack(...)`,
`layers_for_board_region(...)`, and `branches_for_stack_ref(...)` for read-only
topology queries. Display names remain labels and are not unique ids.

External `.stackup` and `.stackupx` files can be parsed into
`AltiumLayerStackDocument` and applied to a fresh `PcbDocBuilder` for simple
rigid-board authoring. The writer regenerates native `Board6/Data` stack rows
from the semantic model and preserves copper, dielectric, solder-mask,
overlay, layer-pair, and supported material/thickness fields on generated
PcbDoc readback. `.csv` and `.esx` remain inspection/export artifacts rather
than native writer inputs.

Programmatic rigid-board authoring uses the same document model. Use
`AltiumLayerStackDocument.from_rigid_layer_rows(...)` with ordered
`AltiumRigidStackRowSpec` rows when the exact physical sequence matters.
Normal public authoring should use semantic constructors such as
`AltiumRigidStackRowSpec.copper(...)`, `.prepreg(...)`, `.core(...)`,
`.solder_mask(...)`, and `.overlay(...)` with `AltiumCopperMaterialSpec`,
`AltiumDielectricMaterialSpec`, `AltiumComponentPlacement`, and
`AltiumLayerPair`. Stackup-wide electrical settings such as roughness model,
copper resistance, via plating thickness, realistic ratio, and temperatures
use `AltiumStackupSettings`, `AltiumStackupType`, and
`AltiumStackupRoughnessModel`. Raw StackupX type IDs, property tuples, and
serialized stackup attributes are compatibility escape hatches for unsupported
source metadata, not the preferred API. This supports code-authored `.stackup`
/ `.stackupx` export and fresh PcbDoc creation, but does not imply arbitrary
in-place mutation of an existing populated board.

Rigid-flex correctness is still gated on generated PcbDoc readback because
interchange views normalize rows differently from native PcbDoc `Board6/Data`
plus `BoardRegions/Data`, and `.stackup`/`.stackupx` do not carry board-region
outline and bend-line geometry. For rigid-flex authoring, use typed
`AltiumLayerStackDocument` region/substack/branch inputs or a native PcbDoc
source model backed by fixture evidence.

`ResolvedLayerStack` remains the public convenience view for read-only
consumer reports, layer display names, and enabled-layer checks. It must not be
used as the source model for writing stack data.

## SVG

`AltiumPcbDoc.to_svg(...)`, `to_layer_svgs(...)`, and
`to_board_outline_svg(...)` accept `PcbSvgRenderOptions`. Normal output
includes a root `viewBox` in millimeter coordinates.

See [SVG](svg.md) for the shared rendering and enrichment contract.

## Test Gates

The PcbDoc contract is covered by foundation parsing, authoring, round-trip,
SVG, public examples, and release signoff. Promoted layer-stack writer
features require generated native PcbDoc readback through `AltiumPcbDoc` and
`AltiumLayerStackDocument`; `.stackup` and `.stackupx` comparisons are
supporting evidence, not substitutes for native PcbDoc verification.
Embedded asset inventory behavior is covered by `L6_056`, the public
`embedded_assets_a0` schema tests, and the `embedded_asset_inventory` example.
