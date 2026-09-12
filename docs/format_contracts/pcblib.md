# PcbLib Contract

`AltiumPcbLib` is the public model for PCB footprint libraries.

## Stable Surface

- Parse existing `.PcbLib` files.
- Preserve unknown or unsupported data during normal read/write flows.
- Create new footprint libraries.
- Add, find, split, and render footprints.
- Add pads, tracks, arcs, regions, text, vias, component bodies, and embedded
  STEP models to footprints. STEP-derived component-body bounds use
  `wn-geometer`; explicit bounds remain supported.
- Author pad solder-mask and paste-mask expansion modes with the stable
  vocabulary `none`, `rule`, and `manual`. Manual expansion values are signed
  mil-unit values.
- Author round, square, and slotted drill-hole pads. Slotted holes require a
  positive slot length; square holes require a positive drill size.
- Author footprint vias with top/bottom tenting flags and independent
  top/bottom solder-mask expansion values.
- Author footprint via IPC-4761 protection metadata, propagation delay, and
  fabrication/assembly testpoint flags.
- Author footprint-level primitive parameters through the PcbLib
  `PrimitiveParameters` stream.
- Inspect and author semantic mechanical layer kind assignments through
  `mechanical_layer_kinds`, `get_mechanical_layer_kind(...)`, and
  `set_mechanical_layer_kind(...)`. The mapping is stored in
  `Library/LayerKindMapping/Data` and synchronized into the PcbLib
  `Library/Data` `MECHKIND` layer-table/cache fields used by Altium.
- Author mechanical layer display names, enabled flags, and component mirror
  pairs through `set_mechanical_layer(...)` and
  `set_mechanical_layer_pair(...)`. These update the PcbLib `Library/Data`
  layer table and `MECHPAIR*` entries.
- Author custom-pad anchor geometry with explicit anchor width, height,
  rotation, and shape. Custom-pad regions use the native 1-based `PADINDEX`
  that corresponds to the authored anchor pad.
- Author footprint text as stroke, TrueType, or barcode text. Stroke fonts use
  the stable vocabulary `default`, `sans-serif`, and `serif`. Barcode text
  supports Code 39 and Code 128 option sets shared with PcbDoc text authoring.
- Extract embedded 3D model payloads.

## Loading And Creation

`AltiumPcbLib(existing_file)` and `AltiumPcbLib.from_file(existing_file)` both
parse the complete library. `AltiumPcbLib()` creates an empty library. For
the established destination-metadata behavior, a nonexistent path also creates an
empty library associated with that path; normal new authoring should prefer
the no-argument constructor followed by `save(output_path)`.

An existing invalid file raises its parse error instead of silently producing
an empty library, and a directory path raises `IsADirectoryError`.
`from_bytes(data, filename=...)` always parses only `data`: `filename` is
metadata even when it names a different existing file on disk.

## Object Model

`AltiumPcbLib` owns footprints and embedded model streams.
`AltiumPcbFootprint` owns its primitive lists and helper methods. Attach a
footprint to a library before adding primitives that need library-owned streams
or metadata.

Footprint primitive parameters are exposed as
`footprint.footprint_primitive_parameters` and authored with
`set_footprint_primitive_parameter(...)`. They are separate from the ordinary
footprint `parameters` dictionary.

## Units

High-level footprint helper methods use explicit mil-unit parameter names.
Low-level record fields may expose source integer storage units.

## Pad Corner Radius

Rounded-rectangle pad corner radius uses Altium's native dual-lane storage.
The pad record carries a rounded integer percent per layer. Exact fractional
percents are stored in a per-footprint `CornerRadiusChamfer` stream of text
blocks `|SCR0.LAYER=<layer>|SCR0.CRPCTEX=<percent>|PRIMITIVEINDEX=<n>`, where
`PRIMITIVEINDEX` is the zero-based primitive position in footprint record
order.

`AltiumPcbPad` read accessors are shared with PcbDoc:
`corner_radius_percentage` (legacy rounded lane),
`corner_radius_percent_exact`, `exact_corner_radius_percent_by_layer`,
`exact_corner_radius_percent_on_layer(layer)`, and
`corner_radius_mils_on_layer(layer)`.

Authoring uses `add_pad(..., corner_radius_percent=...)`. Whole-number
percents write only the legacy integer lane, so existing whole-number output
stays byte-identical. Fractional percents write both lanes and are supported
for simple top- or bottom-layer pads. Round-tripped libraries preserve both
lanes.

## Embedded 3D Models

If STEP bounds cannot be computed on the current host, footprint authoring may
fall back to an axis-aligned rectangle around available SMD/through-hole pads as
a recovery projection. This fallback is not a geometry-equivalent STEP import.

## Embedded Asset Inventory

`AltiumPcbLib.embedded_asset_inventory(...)` is the focused read-only contract
for PcbLib embedded binary payloads. It lists embedded 3D models without
writing files, and `get_embedded_model_payload(index)` returns one selected
available model payload.

Model summaries report `payload_available` from actual payload accessibility.
Corrupt supported model payloads stay visible for review but report unavailable
state, no SHA-256 hash, and no decompressed size. Hashes are emitted only when
requested and only for available payload bytes.

PcbLib `Library/EmbeddedFonts` data is currently preserved as an opaque
embedded stream rather than parsed as typed font summaries. Consumers should
use `support_status`, `reason`, and `payload_available` when deciding whether
to display, import, or extract opaque bytes.

`EmbeddedAssetInventory.to_dict()` emits
`altium_monkey.pcb.embedded_assets.a0`. Use the broader
`AltiumAssetInventory` / `altium_monkey.extractable_assets.a0` contract when a
consumer also needs selectable footprints or schematic symbols.

## Layer Names

Footprint primitive authoring APIs keep `PcbLayer` as the legacy/TV6 enum and
use `PcbLayerRef` for V7-aware layer identity where supported. `PcbLayer`
contains Top, Mid1 through Mid30, Bottom, and Mechanical 1 through Mechanical
16 only. Serialized V7 saved layer ids and LayerKindMapping ids are separate
identity systems; see the public PCB layers guide.

PcbLib footprints do not have a board layer stack. Stable layer keys use token
names, and default display labels are used only for human-facing labels.
Mechanical layer display names, enabled flags, and mirror pairs are library
registry metadata. Mechanical layer kind assignments are semantic metadata and
do not by themselves rename or enable mechanical layers.

Track, arc, fill, text, region, and component-body helpers can author ordinary
numbered mechanical layers through Mechanical53 with `PcbLayerRef` or semantic
tokens. PcbLib footprints do not have a board signal stack, so V7-only signal
authoring remains gated until an Altium-authored PcbLib fixture proves the
required context. Pads and vias remain conservative and use documented
legacy-compatible layers.

## SVG

`AltiumPcbFootprint.to_svg(...)` and `to_layer_svgs(...)` accept
`PcbSvgRenderOptions`. Normal output includes a root `viewBox` in millimeter
coordinates.

See [SVG](svg.md) for the shared rendering contract.

## Test Gates

The PcbLib contract is covered by footprint parsing, split/extract, authoring,
primitive-parameter and via-structure fixture coverage, 3D model, SVG, public
examples, and release signoff.
Embedded asset inventory behavior is covered by `L6_056`, the public
`embedded_assets_a0` schema tests, and the `embedded_asset_inventory` example.
