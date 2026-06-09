# PcbDoc Contract

`AltiumPcbDoc` is the public board model for PCB documents.

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
- Author custom pads through the native board custom-shape contract, including
  primary and additional per-layer custom bodies, holes, net assignment,
  component ownership, and pad-center offsets.
- Author round, square, and slotted pad drill-hole shapes. Slotted holes
  require a positive slot length; square holes require a positive drill size.
- Inspect user-defined PCB unions through union-name records, typed smart-union
  records, and computed user-union member summaries.
- Render PCB SVG and PCB layer SVGs.

## Object Model

PcbDoc is helper-oriented rather than `ObjectCollection`-based. Prefer
document-owned helpers such as `add_track(...)`, `add_via(...)`,
`add_component(...)`, `add_differential_pair(...)`, and related APIs.

Direct record-list mutation remains an advanced escape hatch for narrow edits
or preservation work.

## Custom Pads

PcbDoc custom pads are authored as an anchor pad plus one or more custom-pad
region shapes. The board writer emits `CustomShapes/Header` and
`CustomShapes/Data`; each `CustomShapes/Data` record uses zero-based
`PRIMITIVEINDEX` to reference the pad record. The paired `Regions6` and
`ShapeBasedRegions6` records carry native one-based `PADINDEX` metadata.

`add_custom_pad(...)` takes the primary layer body through
`outline_points_mils` and optional primary holes through `hole_points_mils`.
Use `PcbCustomPadLayerShapeSpec` entries in `layer_shapes` when a board pad
needs additional layer-specific custom bodies and holes that share the same
anchor pad.

This differs from PcbLib, where footprint custom pads use
`ExtendedPrimitiveInformation` rather than board `CustomShapes/*`. Public
PcbDoc and PcbLib APIs intentionally expose the same semantic
`add_custom_pad(...)` shape while preserving the container-specific native
storage contracts.

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

## Layer Names

Stable layer keys use token names such as `TOP`, `BOTTOM`, and `TOPOVERLAY`.
Use the resolved layer stack when board-specific user-facing names are needed.
Default display labels are fallback labels, not stable identifiers.
`ResolvedLayerStack` is a derived read-only consumer view; new PcbDoc authoring
uses `AltiumLayerStackDocument`.

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

External `.stackup`, `.stackupx`, `.csv`, and `.esx` files are currently
interchange artifacts, not the native writer contract. `.stackup` and
`.stackupx` exports are useful for Layer Stack Manager inspection and branch
interchange, but rigid-flex samples gate correctness on generated PcbDoc
readback because the interchange views normalize rows differently from native
PcbDoc `Board6/Data` plus `BoardRegions/Data`.

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

