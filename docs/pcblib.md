# PcbLib

`AltiumPcbLib` is the public container for PCB footprint libraries. Each library
contains one or more `AltiumPcbFootprint` objects.

Use it when you need to:

1. create footprints programmatically
2. add pads, tracks, arcs, regions, text, and 3D bodies to footprints
3. embed STEP models
4. extract embedded 3D models
5. find, split, or render footprints

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

## SVG Rendering

`AltiumPcbFootprint.to_svg(...)` and `to_layer_svgs(...)` accept
`PcbSvgRenderOptions`. Footprint SVG output includes a root `viewBox` by
default, computed from the footprint primitives in millimeter coordinates.
Set `PcbSvgRenderOptions(include_view_box=False)` to omit only that root
attribute.

Layer keys and SVG filenames use stable `PcbLayer.to_json_name()` tokens.
Use `PcbLayer.to_display_name()` only for default UI labels; PcbLib footprints
do not have a board layer stack, so there is no board-specific rename source.

## Direct Record Edits

Directly editing footprint primitive lists is an advanced escape hatch. It can
be useful when preserving parsed libraries or performing a narrow mutation, but
high-level helper methods should be preferred for authored output.

## Examples

Start with:

1. [`hello_pcblib`](../examples/hello_pcblib/README.md)
2. [`pcblib_add_via_ipc4761_matrix`](../examples/pcblib_add_via_ipc4761_matrix/README.md)
3. [`pcblib_find_footprint`](../examples/pcblib_find_footprint/README.md)
4. [`pcblib_split`](../examples/pcblib_split/README.md)
5. [`pcblib_footprint_svg`](../examples/pcblib_footprint_svg/README.md)
6. [`pcblib_extract_3d_models`](../examples/pcblib_extract_3d_models/README.md)
7. [`pcblib_add_free_3d_extruded`](../examples/pcblib_add_free_3d_extruded/README.md)
8. [`pcblib_synthesize_power_resistor_lib`](../examples/pcblib_synthesize_power_resistor_lib/README.md)

See [API patterns](api_patterns/index.md) for the differences between schematic
and PCB object systems.

