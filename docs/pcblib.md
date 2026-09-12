# PcbLib

`AltiumPcbLib` is the public container for PCB footprint libraries. Each library
contains one or more `AltiumPcbFootprint` objects.

Use it when you need to:

1. create footprints programmatically
2. add pads, tracks, arcs, regions, text, and 3D bodies to footprints
3. embed STEP models
4. extract embedded 3D models
5. find, split, or render footprints
6. inventory footprints and embedded payloads before selecting one

## Loading And Creation

Pass an existing PcbLib path to the constructor or use `from_file(...)` to
parse it. Both forms populate footprints, primitives, and embedded models:

```python
pcblib = AltiumPcbLib("vendor.PcbLib")
# Equivalent explicit factory:
pcblib = AltiumPcbLib.from_file("vendor.PcbLib")
```

`AltiumPcbLib()` creates a new in-memory library. A nonexistent path passed to
the constructor is retained as destination metadata for compatibility, but new
authoring code should normally use the no-argument form and pass the output
path to `save(...)`. Passing an existing invalid file raises a parse error;
passing a directory raises `IsADirectoryError`.

## Object Model

PcbLib uses a footprint-oriented API. `AltiumPcbLib` owns embedded models and
footprints. `AltiumPcbFootprint` owns its primitive lists:
`footprint.pads`, `footprint.tracks`, `footprint.arcs`, `footprint.regions`,
`footprint.texts`, `footprint.vias`, and `footprint.component_bodies`.

Attach a footprint to a library before adding primitives so the library can
manage model streams and authoring metadata:

```python
pcblib = AltiumPcbLib()
footprint = pcblib.add_footprint("R0603")
footprint.add_pad(...)
pcblib.save("footprints.PcbLib")
```

## Units

Public PcbLib helper methods use explicit mil-unit parameter names. Metric
package data is common for footprints, so convert millimeters to mils at the
call site for now.

## Layer Arguments

PcbLib footprint primitive helpers keep `PcbLayer` for legacy-compatible layers
and use `PcbLayerRef` for V7-aware layer identity where supported. Arguments
named `layer`, `layer_start`, and `layer_end` accept documented `PcbLayer`
values and supported layer-name tokens. `PcbLayer` mirrors Altium's compact
legacy/TV6 enum and contains Top, Mid1 through Mid30, Bottom, and Mechanical 1
through Mechanical 16 only; values 73, 74, and 75 are Drill Drawing,
Multi-Layer, and Connect.

Some parsed footprint records expose V7 side metadata such as `v7_layer_id`,
`layer_v7_save_id`, or `V7_LAYER` property text. Track, arc, fill, text,
region, and component-body helpers can author ordinary numbered mechanical
layers through Mechanical53 with `PcbLayerRef` or semantic tokens. Do not pass
serialized V7 saved layer ids as ordinary `layer=` values unless a method
explicitly documents that override. See [PCB layers](api_patterns/pcb_layers.md)
for the current public boundary, including the still-gated PcbLib V7-only
signal-layer cases.

## Pads, Mask, And Paste

`AltiumPcbFootprint.add_pad(...)` and `add_custom_pad(...)` can author Altium's
pad solder-mask and paste-mask expansion modes. Use the explicit mode/value
arguments for new code:

```python
footprint.add_pad(
    designator="1",
    position_mils=(0, 0),
    width_mils=40,
    height_mils=30,
    paste_mask_expansion_mode="manual",
    paste_mask_expansion_mils=-40,
    solder_mask_expansion_mode="rule",
)
```

Accepted modes are `"none"`, `"rule"`, and `"manual"`, matching the native
record values 0, 1, and 2. Manual mode requires signed `*_mils` values; `none`
and `rule` should leave the manual value omitted. `PcbMaskExpansion` and
`PcbMaskExpansionMode` are available when callers prefer a structured value.

`add_custom_pad(...)` still accepts `paste_rule_expansion` and
`solder_rule_expansion` for compatibility. Those booleans map to `rule` when
true and `none` when false. New code should prefer the explicit expansion API.

`add_pad(...)` also accepts `hole_shape="round"`, `"square"`, or `"slot"`
through `PadHoleShape`. Square holes require a positive drill size. Slotted
holes require `slot_length_mils`.

`add_pad(...)` accepts fractional `corner_radius_percent` values for
rounded-rectangle pads on the top or bottom layer, matching Altium's exact
percent storage. Read the exact value back with
`pad.corner_radius_percent_exact` or resolve the effective radius with
`pad.corner_radius_mils_on_layer(layer)`. Whole-number percents keep the
legacy integer-only storage, so existing output is unchanged. See the
[PcbLib format contract](format_contracts/pcblib.md) for the storage details.

Custom pads can set the anchor pad independently from the outline:
`anchor_width_mils`, `anchor_height_mils`, `anchor_rotation_degrees`, and
`anchor_shape` control the native anchor pad, while the generated custom-pad
region writes the correct 1-based native `PADINDEX`.

## Vias

`AltiumPcbFootprint.add_via(...)` supports top/bottom tenting, independent
manual solder-mask expansion values, propagation delay, fabrication/assembly
testpoint flags, and IPC-4761 via-protection types:

```python
footprint.add_via(
    position_mils=(100, 0),
    diameter_mils=24,
    hole_size_mils=10,
    ipc4761_via_type=PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING,
    propagation_delay_ps=12.5,
    is_tent_top=True,
    is_tent_bottom=False,
    solder_mask_expansion_top_mils=-3,
    solder_mask_expansion_bottom_mils=5,
)
```

Use `set_ipc4761_feature_side(...)` and
`set_ipc4761_feature_material(...)` on the returned via to customize IPC-4761
feature rows such as filling or capping material.

## Keepout Restrictions

Footprint tracks, arcs, fills, and regions can carry Altium's raw
`keepout_restrictions` mask when authored as object-specific keepouts. Use
`PcbKeepoutRestriction`, `decode_pcb_keepout_restrictions(...)`,
`encode_pcb_keepout_restrictions(...)`,
`pcb_keepout_restriction_names(...)`, and
`pcb_keepout_restriction_unknown_bits(...)` for named access to the confirmed
via, track, copper, SMD pad, and through-hole pad restriction bits while
preserving the raw integer for writeback.

## Footprint Primitive Parameters

`AltiumPcbFootprint.set_footprint_primitive_parameter(...)` writes
footprint-level user parameters to the PcbLib `PrimitiveParameters` side stream:

```python
footprint.set_footprint_primitive_parameter("TEST_PARAMETER", "sample")
```

These parameters are separate from the standard footprint `Parameters` stream
used for pattern, description, height, and item ids.

## Text

`AltiumPcbFootprint.add_text(...)` supports stroke, TrueType, and barcode text
through `font_kind`. Stroke text writes native stroke encoding and accepts
`stroke_font_type="default"`, `"sans-serif"`, or `"serif"` (native ids 1, 2,
and 3). TrueType text preserves `font_name`, `bold`, `italic`, inverted-text
flags, and inverted margins for save/readback and downstream transcode.

Barcode footprint text uses `font_kind="barcode"` or `PcbTextKind.BARCODE` and
accepts the same barcode option names as PcbDoc text authoring:
`barcode_kind`, `barcode_render_mode`, `barcode_full_size_mils`,
`barcode_margin_mils`, `barcode_min_width_mils`, `barcode_show_text`, and
`barcode_inverted`.

```python
footprint.add_text(
    text="FP096",
    position_mils=(10, 20),
    height_mils=60,
    layer=PcbLayer.TOP_OVERLAY,
    font_kind=PcbTextKind.BARCODE,
    barcode_kind=PcbBarcodeKind.CODE_39,
    barcode_render_mode=PcbBarcodeRenderMode.BY_FULL_WIDTH,
    barcode_full_size_mils=(600, 120),
    barcode_margin_mils=(12, 8),
    barcode_min_width_mils=5,
    barcode_show_text=False,
    barcode_inverted=False,
)
```

## Embedded 3D Models

`AltiumPcbFootprint.add_embedded_3d_model(...)` can infer rectangular STEP
projection bounds and overall height through `wn-geometer` when an embedded
STEP payload is available.

If STEP bounds cannot be computed on the current host, the helper can fall back
to an axis-aligned rectangle around available SMD/through-hole pads. That
fallback is intended to create a usable component-body projection; it is not a
geometry-equivalent STEP import.

Explicit `bounds_mils`, `projection_outline_mils`, and `overall_height_mils`
remain supported for deterministic authored output.

## Extractable Assets

`AltiumPcbLib.asset_inventory(include_hashes=False)` lists selectable
footprints, embedded 3D model payloads, and opaque embedded streams. Use the
returned `AltiumAssetRef` with `extract_asset(...)` to extract one model
payload or one footprint.

```python
inventory = pcblib.asset_inventory(include_hashes=True)
footprint = next(
    (item for item in inventory.by_kind("pcb_footprint") if item.can_extract),
    None,
)
if footprint is None:
    raise RuntimeError("no extractable footprint found")
selected = pcblib.extract_asset(footprint.ref)
selected.pcblib.save("selected_footprint.PcbLib")
```

For the shared reference and JSON contract, see
[extractable assets](api_patterns/extractable_assets.md).

## Embedded Asset Inventory

`AltiumPcbLib.embedded_asset_inventory(include_hashes=False)` lists embedded
3D models and preserved opaque embedded streams without writing files. PcbLib
`Library/EmbeddedFonts` bytes are currently reported as opaque summaries rather
than typed font records.

Use the direct embedded model helpers for one selected model payload:

```python
inventory = pcblib.embedded_asset_inventory(include_hashes=True)
model = next((item for item in inventory.models if item.payload_available), None)
if model is None:
    raise RuntimeError("no available embedded model payload found")
payload = pcblib.get_embedded_model_payload(model.index)
```

The focused inventory emits the `altium_monkey.pcb.embedded_assets.a0` JSON
shape through `to_dict()`. For the focused JSON contract, see
[embedded PCB assets](api_patterns/embedded_assets.md).

## SVG Rendering

`AltiumPcbFootprint.to_svg(...)` and `to_layer_svgs(...)` accept
`PcbSvgRenderOptions`. Footprint SVG output includes a root `viewBox` by
default, computed from the footprint primitives in millimeter coordinates.
Set `PcbSvgRenderOptions(include_view_box=False)` to omit only that root
attribute.

Layer keys and SVG filenames use stable layer tokens. Legacy layers use
`PcbLayer.to_json_name()` tokens; V7 mechanical layers use `PcbLayerRef` tokens
such as `MECHANICAL33`. Use `PcbLayer.to_display_name()` only for default UI
labels. PcbLib footprints do not have a board signal stack, but library
mechanical-layer registry names are used for custom mechanical display labels
when available.

## Mechanical Layer Kinds

PcbLib semantic mechanical layer roles are stored in
`Library/LayerKindMapping/Data` and exposed through `MechanicalLayerKind`.
Authored output also synchronizes Altium's `Library/Data` `MECHKIND`
layer-table/cache fields so the assignments are visible in Altium's layer
manager. Mechanical layer display names, enabled flags, and component mirror
pairs are stored separately in `Library/Data`.

Use `mechanical_layer_kinds` to inspect the parsed mapping, and use
`get_mechanical_layer_kind(...)` / `set_mechanical_layer_kind(...)` for common
lookup and authoring:

```python
from altium_monkey import AltiumPcbLib, MechanicalLayerKind

pcblib = AltiumPcbLib()
pcblib.add_footprint("MECH_KIND_DEMO")
pcblib.set_mechanical_layer("MECHANICAL14", name="Top Component Outline")
pcblib.set_mechanical_layer("MECHANICAL15", name="Bottom Component Outline")
pcblib.set_mechanical_layer_pair("MECHANICAL14", "MECHANICAL15", pair_index=0)
pcblib.set_mechanical_layer_kind("MECHANICAL14", MechanicalLayerKind.COMPONENT_OUTLINE_TOP)
pcblib.save("mechanical_kind.PcbLib")
```

Mechanical layers 1 through 16 use classic PCB layer ids in the mapping.
Mechanical layers 17 through 32 use Altium's extended
`0x04000000 | mechanical_number` id form. This is the mechanical-kind mapping
id family, not the primitive `PcbLayer` enum and not the serialized V7
saved-layer id family.

## Direct Record Edits

Directly editing footprint primitive lists is an advanced escape hatch. It can
be useful when preserving parsed libraries or performing a narrow mutation, but
high-level helper methods should be preferred for authored output.

## Examples

Start with:

1. [`hello_pcblib`](../examples/hello_pcblib/README.md)
2. [`pcblib_create_mechanical_layer_kinds`](../examples/pcblib_create_mechanical_layer_kinds/README.md)
3. [`pcblib_add_via_ipc4761_matrix`](../examples/pcblib_add_via_ipc4761_matrix/README.md)
4. [`pcblib_find_footprint`](../examples/pcblib_find_footprint/README.md)
5. [`pcblib_split`](../examples/pcblib_split/README.md)
6. [`pcblib_footprint_svg`](../examples/pcblib_footprint_svg/README.md)
7. [`pcblib_extract_3d_models`](../examples/pcblib_extract_3d_models/README.md)
8. [`pcblib_add_free_3d_extruded`](../examples/pcblib_add_free_3d_extruded/README.md)
9. [`pcblib_create_cavity_region`](../examples/pcblib_create_cavity_region/README.md)
10. [`pcblib_synthesize_power_resistor_lib`](../examples/pcblib_synthesize_power_resistor_lib/README.md)
11. [`extractable_asset_inventory`](../examples/extractable_asset_inventory/README.md)
12. [`embedded_asset_inventory`](../examples/embedded_asset_inventory/README.md)
13. [`pcb_v7_mechanical_layer_track_rows`](../examples/pcb_v7_mechanical_layer_track_rows/README.md)

See [API patterns](api_patterns/index.md) for the differences between schematic
and PCB object systems.
