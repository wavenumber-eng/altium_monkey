# PcbDoc

`AltiumPcbDoc` is the public container for PCB documents. The current release
supports parsing, extraction, SVG rendering, statistics, high-level
helper-oriented authoring, and footprint insertion.

Use it when you need to:

1. parse `.PcbDoc` files
2. inspect board geometry, layers, drills, nets, and resolved components
3. render PCB layers to SVG
4. extract embedded fonts, 3D models, or footprints
5. add board outlines, nets, PCB primitives, routes, pads, vias, and regions
6. place footprints from `.PcbLib`
7. add component bodies and embedded 3D model payloads
8. inspect and author user-defined PCB unions
9. inventory extractable footprints and embedded payloads before selecting one

## Loading And Validation Boundary

`AltiumPcbDoc.from_file(...)` and `from_bytes(...)` parse the requested board
without building an additional whole-document record-state snapshot. This keeps
ordinary inspection, rendering, and authoring proportional to the work the
caller requested.

Loading proves that the supported document structures can be parsed; it does
not certify that the source is a compiled, electrically valid, or otherwise
known-good Altium design. Manufacturing and release workflows should begin
from a caller-selected saved input and apply the validation required by their
specific output.

## Object Model

PcbDoc does not yet use the generic `ObjectCollection` API used by SchDoc and
SchLib. SchDoc/SchLib typed views are live filtered query views with explicit
structural APIs such as `add_object(...)`, `insert_object(...)`, and
`remove_object(...)`. PcbDoc instead exposes parsed records as typed lists such
as `pcbdoc.tracks`, `pcbdoc.arcs`, `pcbdoc.pads`, `pcbdoc.vias`,
`pcbdoc.regions`, `pcbdoc.texts`, and `pcbdoc.components`.

For authoring, prefer high-level helpers:

```python
pcbdoc.add_track((1000, 1000), (2000, 1000), width_mils=8, net="GND")
pcbdoc.add_pad(
    designator="1",
    position_mils=(1500, 1500),
    width_mils=60,
    height_mils=80,
)
pcbdoc.add_via(position_mils=(1750, 1500), diameter_mils=24, hole_size_mils=12)
pcbdoc.save("updated.PcbDoc")
```

Direct edits to typed lists are advanced usage. They can be appropriate for
read-preserving mutation, but callers are responsible for keeping indexes,
ownership, stream order, and related binary state valid.

When a supported typed list is part of the document writer's owned state,
`save(...)` writes its counted section atomically: the current record count in
`Header` and the serialized records in `Data` are regenerated together. This
also applies when removing the final record, where both the zero count and an
empty Data stream must replace the source streams. Unsupported and otherwise
unowned sections remain lossless passthrough. A source-backed save may still
regenerate an owned section that the caller did not edit; streams outside the
selected regenerated set remain byte-identical.

This count synchronization makes narrow direct-list insertion and deletion
safe from stale-header corruption; it is not a generic PCB deletion or
relationship-management API. Removing a component, pad, model, or another
record can require coordinated updates to owner indexes, companion sections,
or references. Prefer a high-level helper when one exists, and reopen advanced
direct-list output in Altium Designer as an interoperability check.

## User Unions

`pcbdoc.union_name_records` exposes the decoded union-name catalog.
`pcbdoc.smart_unions` exposes read-only typed smart-union records.
`pcbdoc.user_unions` returns named user-defined unions with member references.

Use `create_user_union(...)`, `rename_user_union(...)`,
`add_user_union_member(...)`, `remove_user_union_member(...)`, and
`delete_user_union(...)` for explicit user-union authoring. Typed smart unions,
including drill tables, layer-stack tables, via stitching, via shielding,
OLE/object unions, rectangles, and length tuning, are read-only.

`create_user_union(...)` auto-allocates a native union id by default. Use the
optional `union_index=...` argument only when recreating an existing PcbDoc and
preserving deterministic native union ids matters.

Passing a component to `create_user_union(...)` includes the component record
and its authorable child primitives. Shape-based region membership is kept in
sync with the paired standard region record when that pair exists.

PCB components are available through `pcbdoc.components`. Each
`AltiumPcbComponent` row exposes the resolved designator, footprint, placement,
rotation, side, component kind, and parsed PcbDoc component parameters. Use this
surface when a PCB-backed BOM or placement list should reflect what is actually
placed on the board.

Component source metadata is also exposed for boards produced from schematic
compile/ECO flows. Use fields such as `channel_offset`, `source_designator`,
`source_unique_id_segments`, `source_hierarchy_segments`,
`source_component_library`, `source_lib_reference`, and
`footprint_description` when repeated-sheet, channel, or library provenance is
needed. Designator/comment autoposition uses the `PcbTextAutoposition` enum
through `name_auto_position` and `comment_auto_position`; absent fields remain
`None` rather than being invented by the writer.

PCB classes are available through `pcbdoc.net_classes`. The historical
`AltiumPcbNetClass` name is retained for compatibility, but `Classes6/Data`
also stores component classes, pad classes, layer classes, polygon classes,
from-to classes, and differential-pair classes. A differential-pair class has
`kind == PcbNetClassKind.DIFF_PAIR`; its `members` are differential-pair names
such as `TX0` or `RX0`, not the positive/negative net names.

Concrete `DifferentialPairs6/Data` pair objects are available through
`pcbdoc.differential_pairs`. Each `AltiumPcbDifferentialPair` exposes `name`,
`positive_net_name`, `negative_net_name`, `gather_control`, and `unique_id`.
Use `pcbdoc.get_differential_pair(name)`,
`pcbdoc.differential_pairs_by_net_name`, and
`pcbdoc.differential_pair_classes` for common lookup paths.

```python
pair = pcbdoc.get_differential_pair("USB_D")
if pair is not None:
    print(pair.positive_net_name, pair.negative_net_name)
```

New pair objects can be authored explicitly:

```python
pcbdoc.add_differential_pair(
    name="USB_D",
    positive_net_name="USB_D_P",
    negative_net_name="USB_D_N",
)
pcbdoc.save("updated.PcbDoc")
```

`gather_control` is Altium's raw pair-level gather-control flag used around
uncoupled differential-pair fanout handling. It is preserved and writable, but
callers should keep the raw boolean meaning until their workflow has been
verified in Altium Designer.

## Units

Public PcbDoc authoring helpers use explicit `*_mils` parameter names. PCB
workflows are often metric, so convert metric source data before calling these
methods until metric helper functions are added.

Low-level PCB record fields may expose Altium internal integer units. Prefer
public helper methods for authored geometry.

## Layer Arguments

PcbDoc primitive authoring helpers keep `PcbLayer` as the legacy enum and use
`PcbLayerRef` for V7-aware layer identity. Arguments named `layer`,
`layer_start`, and `layer_end` accept documented `PcbLayer` values and
supported layer-name tokens such as `"Top Layer"`.

`PcbLayer` mirrors Altium's compact legacy/TV6 enum. It includes Top, Mid1
through Mid30, Bottom, and Mechanical 1 through Mechanical 16 only; values 73,
74, and 75 are Drill Drawing, Multi-Layer, and Connect. Do not pass serialized
V7 saved layer ids such as `16908305` for Mechanical 17 or `16777248` for
Mid31 as `layer=` unless a method explicitly documents a V7 override.

For extended mechanical and AD 26.8 128-signal-layer workflows, pass
`PcbLayerRef` values or documented semantic tokens. Track, arc, fill, text, and
region helpers can author ordinary numbered mechanical layers through
Mechanical53.
They can also author V7-only signal refs such as `PcbLayerRef.mid_layer(126)`
when a matching enabled layer stack has been attached with
`set_layer_stack_document(...)`, normally from `.stackupx`.

See [PCB layers](api_patterns/pcb_layers.md) for the current public boundary,
including the still-gated pad/via and PcbLib signal-layer cases.

## Pads

`AltiumPcbDoc.add_pad(...)` accepts `hole_shape="round"`, `"square"`, or
`"slot"` through `PadHoleShape`. Square holes require a positive drill size.
Slotted holes require `slot_length_mils`.

`add_pad(...)` accepts fractional `corner_radius_percent` values for
rounded-rectangle pads on the top or bottom layer, matching Altium's exact
percent storage. Read the exact value back with
`pad.corner_radius_percent_exact` or resolve the effective radius with
`pad.corner_radius_mils_on_layer(layer)`. Whole-number percents keep the
legacy integer-only storage, so existing output is unchanged. See the
[PcbDoc format contract](format_contracts/pcbdoc.md) for the storage details.

Parsed component pads expose their stored removed-copper-land state through
`pad.removed_copper_layers` and `pad.is_copper_land_removed(layer)`. Change one
legacy Top/Mid1..Mid30/Bottom state explicitly with:

```python
pad.set_copper_land_removed(PcbLayer.MID1, True)
pcbdoc.save("updated.PcbDoc")
```

The setter requires an actual legacy copper `PcbLayer` and a `bool`. Saving a
valid record preserves its per-layer geometry and full-stack data while
updating the map. Ambiguous, partial, noncanonical, or malformed source maps
reject mutation rather than guessing. Use
`pad.has_stored_copper_land_on_layer(layer)` when you need the structural
stored-land applicability; it does not alter geometry or replace rendering or
manufacturing policy. Pad and via removed-layer storage are independent.
For an all-false map-only legacy pad, save and reopen after the first removal
before applying another layer removal.

`AltiumPcbDoc.add_custom_pad(...)` authors a board custom pad as an anchor pad
plus native custom-shape region records. `outline_points_mils` and
`hole_points_mils` describe the primary layer body and holes. Pass
`outline_vertices` for line/arc segment semantics, and use
`PcbCustomPadLayerShapeSpec` entries in `layer_shapes` for additional
layer-specific bodies and holes that share the same anchor pad. Custom-pad
anchors can also carry ordinary pad drill fields such as `hole_size_mils`,
`plated`, `hole_shape`, and slot/tolerance parameters.

`AltiumPcbDoc.add_region(...)` also accepts `outline_vertices` for
line/arc-preserving shape-based-region authoring. Region and PcbDoc custom-pad
body helpers share the same outline normalization path; custom pads add the
anchor pad and native `CustomShapes/*` attachment records around that region
body.

## Keepout Restrictions

Object-specific PCB keepouts preserve Altium's raw `keepout_restrictions`
integer. Use `PcbKeepoutRestriction`,
`decode_pcb_keepout_restrictions(...)`,
`encode_pcb_keepout_restrictions(...)`,
`pcb_keepout_restriction_names(...)`, and
`pcb_keepout_restriction_unknown_bits(...)` when you need named access to the
confirmed mask bits for via, track, copper, SMD pad, and through-hole pad
restrictions. Keep the raw integer as the stored field for round-trip
preservation.

## Dimensions

`AltiumPcbDoc.add_dimension_record(...)` and
`PcbDocBuilder.add_dimension_record(...)` append raw native `Dimensions6/Data`
records from `record_type`, `record_leader`, and payload bytes. This is a
preservation/transcode API for imported dimensions, not a high-level dimension
construction API or full object-oriented dimension model.

## Text

`AltiumPcbDoc.add_text(...)` accepts `font_kind="stroke"`, `"truetype"`, or
`"barcode"`. Stroke text accepts `stroke_font_type="default"`,
`"sans-serif"`, or `"serif"` (native ids 1, 2, and 3). This is the same
stroke-font vocabulary used by PcbLib footprint text helpers.

## Embedded 3D Models

`AltiumPcbDoc.add_embedded_3d_model(...)` can embed a STEP payload and create
the matching component-body projection. When callers omit explicit placement
geometry, STEP-derived rectangular bounds are inferred through `wn-geometer`.

If STEP bounds cannot be computed on the current host, the helper can fall back
to an axis-aligned rectangle around available SMD/through-hole pads. That
fallback is a recovery projection for authoring a usable board body; it is not a
geometry-equivalent STEP import.

Use explicit `bounds_mils`, `projection_outline_mils`, and
`overall_height_mils` when the package projection or height is known.

## Embedded Asset Inventory

`AltiumPcbDoc.embedded_asset_inventory(include_hashes=False)` lists embedded
3D models and embedded TrueType fonts without writing files. The focused
inventory returns typed model/font summaries and can emit the
`altium_monkey.pcb.embedded_assets.a0` JSON shape through `to_dict()`.

Use direct indexed payload helpers when a consumer wants one embedded payload:

```python
inventory = pcbdoc.embedded_asset_inventory(include_hashes=True)
model = next((item for item in inventory.models if item.payload_available), None)
if model is None:
    raise RuntimeError("no available embedded model payload found")
payload = pcbdoc.get_embedded_model_payload(model.index)
```

Corrupt supported payloads remain visible in the inventory with
`payload_available=False`, no hash, and no decompressed size. Selecting that
payload raises a clear decompression error. For the focused JSON contract, see
[embedded PCB assets](api_patterns/embedded_assets.md).

## Extractable Assets

`AltiumPcbDoc.asset_inventory(include_hashes=False)` lists selectable board
assets, including embedded 3D models, embedded fonts, and footprints extracted
from placed components. Use the returned `AltiumAssetRef` with
`extract_asset(...)` to extract exactly one payload or one footprint.

```python
inventory = pcbdoc.asset_inventory(include_hashes=True)
footprint = next(
    (item for item in inventory.by_kind("pcb_footprint") if item.can_extract),
    None,
)
if footprint is None:
    raise RuntimeError("no extractable footprint found")
selected = pcbdoc.extract_asset(footprint.ref)
selected.pcblib.save("selected_footprint.PcbLib")
```

Embedded payload selections return `payload` bytes and a suggested
`extraction_filename`. Footprint selections return a single-footprint
`AltiumPcbLib`. The JSON-ready inventory uses
`altium_monkey.extractable_assets.a0`. For the shared reference and JSON
contract, see [extractable assets](api_patterns/extractable_assets.md).

## SVG Rendering

`AltiumPcbDoc.to_svg(...)`, `to_layer_svgs(...)`, and
`to_board_outline_svg(...)` accept `PcbSvgRenderOptions`.

Normal PCB SVG output includes a root `viewBox` in millimeter coordinates.
Set `PcbSvgRenderOptions(include_view_box=False)` when a downstream consumer
needs width and height without a root viewBox. This does not change geometry,
layer keys, filenames, or metadata identifiers.

Layer identifiers remain token-based for the current public SVG contract.
`PcbLayer.to_json_name()` returns legacy tokens such as `TOP`, `BOTTOM`, and
`TOPOVERLAY`; `PcbLayerRef` covers V7 tokens such as `MECHANICAL33` and
`MID126`. `PcbLayer.to_display_name()` returns default user-facing labels such
as `Top Layer` and `Top Overlay`.
For parsed PcbDoc files, prefer `ResolvedLayerStack` when actual board-specific
layer names are required; SVG `data-layer-display-name` uses resolved names
when available and falls back to `PcbLayer.to_display_name()`.
Supported simple primitives on Mechanical17 through Mechanical53 and
StackUpX-backed Mid31 through Mid126 render/select by V7 tokens and do not
invent legacy layer ids.

## Layer Stack Inspection

`AltiumLayerStackDocument` is the source-aware layer-stack model for PcbDoc
inspection and canonical empty-board stack synthesis. It preserves native stack
source evidence while exposing deterministic objects for physical stacks,
registry entries, substacks, board regions, bend lines, and layer pairs.

Use `AltiumLayerStackDocument.from_pcbdoc(...)` for read-only inspection and
`AltiumLayerStackDocument.canonical_empty()` plus
`to_canonical_empty_board_data()` when creating a canonical empty PcbDoc through
`PcbDocBuilder`.

For new rigid-board documents, `AltiumLayerStackDocument.from_rigid_stack(...)`
accepts typed `AltiumRigidCopperLayerSpec` and
`AltiumRigidDielectricLayerSpec` rows for copper names/thicknesses and
dielectric names, thicknesses, material, dielectric constant, dielectric type,
and loss tangent. Emit the stack into a new builder with
`PcbDocBuilder.set_layer_stack_document(...)`.

Use `AltiumLayerStackDocument.from_rigid_layer_rows(...)` when the physical
row sequence matters. It accepts ordered `AltiumRigidStackRowSpec` rows, or
existing parsed `AltiumStackLayer` rows, and preserves solder-mask, overlay,
surface-finish, adjacent dielectric/prepreg/core rows, StackupX type IDs,
StackupX properties, layer-pair spans, typed stackup settings, and impedance
profiles/transmission lines for rigid new-board authoring. This is the
programmatic equivalent of starting from a rigid `.stackup` or `.stackupx`
file, but keeps the stack definition in Python code.

Use the semantic row constructors for normal authoring. They fill the native
Layer Stack Manager type IDs and StackupX property type names internally. Use
`AltiumStackupSettings` for stackup-level electrical settings such as roughness
model, copper resistance, via plating thickness, and temperatures:

```python
from altium_monkey import (
    AltiumComponentPlacement,
    AltiumCopperMaterialSpec,
    AltiumDielectricMaterialSpec,
    AltiumLayerPair,
    AltiumLayerStackDocument,
    AltiumRigidStackRowSpec,
    AltiumStackupRoughnessModel,
    AltiumStackupSettings,
    PcbDocBuilder,
)

np_155f_1080 = AltiumDielectricMaterialSpec(
    name="NP-155F",
    construction="1080",
    resin="67%",
    frequency="1GHz",
    dielectric_constant=3.91,
    loss_tangent=0.02,
    glass_transition_temperature="150C",
    manufacturer="Nan Ya Plastics",
)
copper_foil = AltiumCopperMaterialSpec(
    weight="1oz",
    process="ED",
    manufacturer="Altium Designer",
    description="Copper Foil",
)

stack = AltiumLayerStackDocument.from_rigid_layer_rows(
    name="vendor-8-layer",
    rows=(
        AltiumRigidStackRowSpec.overlay("Top Overlay"),
        AltiumRigidStackRowSpec.solder_mask(
            "Top Solder",
            thickness_mils=0.6,
            material="Solder Resist",
            dielectric_constant=3.8,
        ),
        AltiumRigidStackRowSpec.copper(
            "Top Layer",
            thickness_mm=0.035,
            component_placement=AltiumComponentPlacement.BODY_UP,
            material=copper_foil,
        ),
        AltiumRigidStackRowSpec.prepreg(
            "Dielectric 1",
            thickness_mm=0.069,
            material=np_155f_1080,
        ),
        AltiumRigidStackRowSpec.copper(
            "Inner Layer 1",
            thickness_mm=0.03,
            component_placement=AltiumComponentPlacement.NONE,
            material=copper_foil,
        ),
        # Add the remaining rows in the exact physical sequence.
    ),
    layer_pairs=(AltiumLayerPair(0, "TOP", "BOTTOM"),),
    stackup_settings=AltiumStackupSettings(
        roughness_model=AltiumStackupRoughnessModel.MODIFIED_HAMMERSTAD,
        roughness_factor_model_sr="1um",
        roughness_factor_model_rf="2%",
        via_plating_thickness="18um",
    ),
)
builder = PcbDocBuilder()
builder.set_layer_stack_document(stack)
builder.save("vendor_stack.PcbDoc")
stack.to_stackup().write("vendor_stack.stackup")
stack.to_stackupx().write("vendor_stack.stackupx")
```

The direct `AltiumRigidStackRowSpec(...)` constructor and
`stackupx_properties` tuples are still available as advanced escape hatches
for preserving unsupported source metadata, but they should not be needed for
ordinary rigid stack creation.

See
[`pcbdoc_create_jlcpcb_rigid_stack`](../examples/pcbdoc_create_jlcpcb_rigid_stack/README.md)
for a complete JLCPCB eight-layer stack authored entirely from Python row and
material constants.

For simple rigid boards, exported `.stackup` and `.stackupx` files can also be
used as new-board inputs:

```python
stack = AltiumLayerStackDocument.from_stackupx("source.stackupx")
builder = PcbDocBuilder()
builder.set_layer_stack_document(stack)
builder.save("from_stackupx.PcbDoc")
```

Use the same pattern with `from_stackup(...)` for `.stackup` text exports. The
writer regenerates native PcbDoc stack rows from the imported semantic model;
reopen the generated PcbDoc with `AltiumPcbDoc` and
`AltiumLayerStackDocument` to verify readback. See
[`pcbdoc_create_from_stackup_files`](../examples/pcbdoc_create_from_stackup_files/README.md)
for a complete `.stackup` and `.stackupx` input example.

For new rigid-flex documents, construct a typed `AltiumLayerStackDocument` with
physical stack rows, `AltiumStackSubstack` definitions, `AltiumStackRegion`
geometry, optional `AltiumStackBendLine` entries, and optional
`AltiumStackBranch` topology. Emit it with
`PcbDocBuilder.set_layer_stack_document(...)`, save the PcbDoc, and re-open it
with `AltiumPcbDoc` plus `AltiumLayerStackDocument` to verify the generated
native topology.

For rigid-flex and multi-stack inspection, use native ids for joins. A
substack's `source_stackup_ref` is the stable id; board regions point back to
it through `layerstack_id`. Altium stores the same GUIDs with mixed spelling
across sources, so helpers such as `substack_by_source_ref(...)`,
`board_regions_for_layerstack_id(...)`, `layers_for_substack(...)`,
`layers_for_board_region(...)`, and `branches_for_stack_ref(...)` accept refs
with or without braces. Treat substack and region names as display labels that
may collide or be renamed.

`ResolvedLayerStack` remains the read-only convenience view for consumer layer
names, enabled-layer checks, and reports such as `pcbdoc_stats`. Do not use it
as the source for new PcbDoc authoring. Use `AltiumLayerStackDocument` whenever
you need to write stack data, export `.stackup`/`.stackupx`, or inspect
source-aware topology, branch, or bend-line evidence. See
[`pcbdoc_flex_topology_report`](../examples/pcbdoc_flex_topology_report/README.md)
for a complete query report.

Arbitrary layer-stack editing is not part of the public writer contract yet.
Use `set_layer_stack_template(...)` for the current limited rigid-board
template helper. That helper is routed through the source-aware layer-stack
model and preserves the established two-layer/four-layer output semantics.

## Mechanical Layer Kinds

Mechanical layer display names, enabled flags, and mirror pairs are stored in
the Board6 layer registry. Semantic layer roles are stored separately in
`LayerKindMapping/Data` and are exposed through `MechanicalLayerKind`.
Authored output also synchronizes Altium's Board6 `MECHKIND` layer-table/cache
fields so the assignments are visible in Altium's layer manager.

Use `mechanical_layer_kinds` to inspect the parsed mapping, and use
`get_mechanical_layer_kind(...)` / `set_mechanical_layer_kind(...)` for common
lookup and authoring:

```python
from altium_monkey import AltiumPcbDoc, MechanicalLayerKind

pcbdoc = AltiumPcbDoc()
pcbdoc.set_mechanical_layer("MECHANICAL13", name="3D Bodies", enabled=True)
pcbdoc.set_mechanical_layer_kind("MECHANICAL13", MechanicalLayerKind.BODY_3D_TOP)
pcbdoc.save("mechanical_kind.PcbDoc")
```

Mechanical layers 1 through 16 use classic PCB layer ids in the mapping.
Mechanical layers 17 through 32 use Altium's extended
`0x04000000 | mechanical_number` id form. This is the mechanical-kind mapping
id family, not the primitive `PcbLayer` enum and not the serialized V7
saved-layer id family.

## Via Protection, Tenting, And Delay

`AltiumPcbDoc.add_via(...)` can author ordinary through vias and promoted via
metadata:

```python
from altium_monkey import (
    AltiumPcbDoc,
    PcbIpc4761ViaType,
    PcbViaStructureFeatureSide,
    PcbViaStructureFeatureType,
)

pcbdoc = AltiumPcbDoc()
via = pcbdoc.add_via(
    position_mils=(1000, 1000),
    diameter_mils=24,
    hole_size_mils=10,
    ipc4761_via_type=PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING,
    propagation_delay_ps=12.5,
    is_tent_top=True,
    is_tent_bottom=True,
)
via.set_ipc4761_feature_side(
    PcbViaStructureFeatureType.FILLING,
    PcbViaStructureFeatureSide.BOTH,
)
via.set_ipc4761_feature_material(PcbViaStructureFeatureType.FILLING, "EPOXY")
```

Parsed vias are available through `pcbdoc.vias`. Each `AltiumPcbVia` exposes
`ipc4761_via_type`, `via_structure`, `propagation_delay_ps`, ordinary
top/bottom tenting flags, fabrication testpoint flags, and assembly testpoint
flags. The feature-table helpers `get_ipc4761_feature(...)`,
`set_ipc4761_feature(...)`, `set_ipc4761_feature_side(...)`, and
`set_ipc4761_feature_material(...)` mirror the IPC-4761 feature rows shown by
Altium Designer.

The public propagation-delay unit is picoseconds. Altium stores this field as a
seconds value in the underlying VIA payload, but callers should use
`propagation_delay_ps`.

Solder-mask expansion fields on a via are low-level record fields in Altium
internal units. They remain available for careful mutation and round-trip
preservation; use the via examples below when authoring tenting or manual mask
expansion for Altium Designer review.

## Hole Tolerances

Pads and vias expose Altium's drill-hole tolerance fields as positive and
negative magnitudes. Use the `*_mils` helpers for normal public code:

```python
pad = pcbdoc.add_pad(
    designator="1",
    position_mils=(1000, 1000),
    width_mils=150,
    height_mils=150,
    layer=PcbLayer.MULTI_LAYER,
    hole_size_mils=50,
    hole_positive_tolerance_mils=3.0,
    hole_negative_tolerance_mils=2.0,
)

via = pcbdoc.add_via(
    position_mils=(1400, 1000),
    diameter_mils=28,
    hole_size_mils=12,
    hole_positive_tolerance_mils=1.5,
    hole_negative_tolerance_mils=0.5,
)
```

For mutation, assign `pad.hole_positive_tolerance_mils`,
`pad.hole_negative_tolerance_mils`, `via.hole_positive_tolerance_mils`, or
`via.hole_negative_tolerance_mils`. A value of `None` represents Altium's N/A
state; the raw fields remain available as internal-unit integers for advanced
round-trip work.

## Current Gaps

PcbDoc does not yet use `ObjectCollection`.

There is no public generic PcbDoc object deletion API in this release.

Mutations outside the high-level helper methods generally require direct
record-list edits and should be validated carefully. Counted Header/Data
synchronization is automatic for owned sections, but relationship cleanup and
cascading deletion remain the caller's responsibility.

## Examples

Start with:

1. [`hello_pcbdoc`](../examples/hello_pcbdoc/README.md)
2. [`pcbdoc_stats`](../examples/pcbdoc_stats/README.md)
3. [`pcbdoc_inspect_layer_stack`](../examples/pcbdoc_inspect_layer_stack/README.md)
4. [`pcbdoc_create_layer_stack`](../examples/pcbdoc_create_layer_stack/README.md)
5. [`pcbdoc_create_mechanical_layer_kinds`](../examples/pcbdoc_create_mechanical_layer_kinds/README.md)
6. [`pcbdoc_create_custom_rigid_stack`](../examples/pcbdoc_create_custom_rigid_stack/README.md)
7. [`pcbdoc_create_jlcpcb_rigid_stack`](../examples/pcbdoc_create_jlcpcb_rigid_stack/README.md)
8. [`pcbdoc_create_impedance_rigid_stack`](../examples/pcbdoc_create_impedance_rigid_stack/README.md)
9. [`pcbdoc_create_flex_stiffener`](../examples/pcbdoc_create_flex_stiffener/README.md)
10. [`pcbdoc_create_rigid_flex_split_lines`](../examples/pcbdoc_create_rigid_flex_split_lines/README.md)
11. [`pcbdoc_create_flex_in_cutout`](../examples/pcbdoc_create_flex_in_cutout/README.md)
12. [`pcbdoc_create_rigid_flex_branch`](../examples/pcbdoc_create_rigid_flex_branch/README.md)
13. [`pcbdoc_create_rigid_flex_branch_intrusion`](../examples/pcbdoc_create_rigid_flex_branch_intrusion/README.md)
14. [`pcbdoc_create_rigid_flex_two_branch`](../examples/pcbdoc_create_rigid_flex_two_branch/README.md)
15. [`pcbdoc_create_rigid_flex_impedance_backdrill`](../examples/pcbdoc_create_rigid_flex_impedance_backdrill/README.md)
16. [`pcbdoc_create_cavity_placements`](../examples/pcbdoc_create_cavity_placements/README.md)
17. [`pcbdoc_create_rigid_flex_multibranch`](../examples/pcbdoc_create_rigid_flex_multibranch/README.md)
18. [`pcbdoc_flex_topology_report`](../examples/pcbdoc_flex_topology_report/README.md)
19. [`pcbdoc_bom`](../examples/pcbdoc_bom/README.md)
20. [`pcbdoc_pick_n_place`](../examples/pcbdoc_pick_n_place/README.md)
21. [`pcbdoc_svg`](../examples/pcbdoc_svg/README.md)
22. [`pcbdoc_netclass_svg`](../examples/pcbdoc_netclass_svg/README.md)
23. [`pcbdoc_add_track`](../examples/pcbdoc_add_track/README.md)
24. [`pcbdoc_user_union`](../examples/pcbdoc_user_union/README.md)
25. [`pcbdoc_add_arc`](../examples/pcbdoc_add_arc/README.md)
26. [`pcbdoc_add_pad`](../examples/pcbdoc_add_pad/README.md)
27. [`pcbdoc_add_hole_tolerances`](../examples/pcbdoc_add_hole_tolerances/README.md)
28. [`pcbdoc_add_via_ipc4761_matrix`](../examples/pcbdoc_add_via_ipc4761_matrix/README.md)
29. [`pcbdoc_add_differential_pairs`](../examples/pcbdoc_add_differential_pairs/README.md)
30. [`pcbdoc_diff_pair_report`](../examples/pcbdoc_diff_pair_report/README.md)
31. [`pcbdoc_mutate_via_ipc4761`](../examples/pcbdoc_mutate_via_ipc4761/README.md)
32. [`pcbdoc_add_text`](../examples/pcbdoc_add_text/README.md)
33. [`pcbdoc_add_filled_region`](../examples/pcbdoc_add_filled_region/README.md)
34. [`pcbdoc_add_custom_pad_region_outline`](../examples/pcbdoc_add_custom_pad_region_outline/README.md)
35. [`pcbdoc_insert_nets_route`](../examples/pcbdoc_insert_nets_route/README.md)
36. [`pcbdoc_insert_footprint_from_pcblib`](../examples/pcbdoc_insert_footprint_from_pcblib/README.md)
37. [`pcbdoc_add_free_3d_extruded`](../examples/pcbdoc_add_free_3d_extruded/README.md)
38. [`pcbdoc_add_free_3d_step`](../examples/pcbdoc_add_free_3d_step/README.md)
39. [`pcbdoc_extract_pcblib`](../examples/pcbdoc_extract_pcblib/README.md)
40. [`pcbdoc_extract_embedded_3d_models`](../examples/pcbdoc_extract_embedded_3d_models/README.md)
41. [`pcbdoc_extract_embedded_fonts`](../examples/pcbdoc_extract_embedded_fonts/README.md)
42. [`embedded_asset_inventory`](../examples/embedded_asset_inventory/README.md)
43. [`pcbdoc_v7_128_signal_track_row`](../examples/pcbdoc_v7_128_signal_track_row/README.md)
44. [`pcb_v7_mechanical_layer_track_rows`](../examples/pcb_v7_mechanical_layer_track_rows/README.md)

See [API patterns](api_patterns/index.md) for public vs careful mutation
guidance.
