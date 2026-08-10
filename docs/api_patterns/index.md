# API Patterns

This page describes the public authoring and mutation patterns for the current
`altium-monkey` release. It intentionally does not present historical fluent or
builder-style schematic APIs as the recommended path.

The preferred rule is simple: use the high-level container classes and their
public methods, then call `save(...)`. Drop to raw records only when you are
preserving existing data or handling a file feature that does not yet have a
stable public helper.

The project strives to keep documented public APIs compatible between releases.
As more Altium file capabilities are modeled, some APIs may still change.
Compatibility-affecting changes and migration notes are documented in release
notes.

For domain-specific starting points, use [SchDoc](../schdoc.md),
[SchLib](../schlib.md), [PcbDoc](../pcbdoc.md), [PcbLib](../pcblib.md),
[PrjPcb](../prjpcb.md), [AltiumDesign](../altium_design.md), and
[IntLib](../intlib.md).

## Main Containers

`AltiumSchDoc` is the schematic document container. It owns a single
`ObjectCollection`, exposes typed query views such as `schdoc.notes`,
`schdoc.ports`, `schdoc.components`, and `schdoc.sheet_symbols`, and provides
container-owned mutation with `add_object(...)`, `insert_object(...)`,
`remove_object(...)`, `add_component(...)`, and
`add_component_from_library(...)`.

`AltiumSchLib` is the schematic library container. Each `AltiumSymbol` owns an
`ObjectCollection` for pins, graphics, labels, parameters, and other symbol
records. Use `AltiumSchLib.add_symbol(...)`, `AltiumSchLib.get_symbol(...)`,
`AltiumSymbol.add_object(...)`, and the symbol helper methods for normal symbol
authoring.

`AltiumPcbDoc` is the PCB document container. The current public PCB API is
helper-oriented rather than `ObjectCollection`-oriented. Use methods such as
`add_track(...)`, `add_arc(...)`, `add_pad(...)`, `add_region(...)`,
`add_via(...)`, `add_text(...)`, `add_component_from_pcblib(...)`,
`add_extruded_3d_body(...)`, and `add_embedded_3d_model(...)`. Parsed PCB
records are available through typed lists such as `pcbdoc.tracks`,
`pcbdoc.pads`, `pcbdoc.components`, and `pcbdoc.component_bodies`.

`AltiumPcbLib` is the PCB footprint-library container. Use
`AltiumPcbLib.add_footprint(...)`, `AltiumPcbLib.add_existing_footprint(...)`,
`AltiumPcbLib.add_embedded_model(...)`, `AltiumPcbLib.split(...)`, and
footprint methods such as `footprint.add_pad(...)`,
`footprint.add_track(...)`, `footprint.add_region(...)`,
`footprint.add_extruded_3d_body(...)`, and
`footprint.add_embedded_3d_model(...)`.

Project-level helpers such as `AltiumPrjPcb`, `AltiumPrjPcbBuilder`,
`AltiumOutJob`, and `AltiumIntLib` are separate from the document object
systems. They handle project metadata, output jobs, and integrated-library
source extraction. `AltiumPrjPcbBuilder` remains a narrow public helper for
simple project-file generation; it should not be confused with the historical
schematic fluent builder style.

For integrated-library migration, `AltiumIntLib.component_parse_error` reports
malformed component cross-reference metadata while still allowing embedded
source streams to be discovered and extracted when they are present.

## Preferred Public Pattern

Import release APIs from `altium_monkey` or from documented public modules. For
normal authored output, prefer container methods over constructing raw stream
data yourself.

For SchDoc creation, create records with `make_sch_*` factory functions and add
them with `schdoc.add_object(...)`. The factories translate public parameters
such as `SchPointMils`, `SchRectMils`, `SchFontSpec`, `ColorValue`, and enums
into record objects. The document resolves font table entries, sheet indexes,
owner indexes, and serialization details when objects are added and saved.

For SchDoc mutation, query objects through `ObjectCollection` views, mutate the
returned record objects in place, and save the document. Use
`schdoc.remove_object(...)` for deletion so owned children and cached indexes
are cleaned up by the document.

For SchLib creation and mutation, work through `AltiumSchLib` and
`AltiumSymbol`. Add a symbol to the library, add pins and graphics to the
symbol, then save the library. Symbol object order matters visually because
schematic library graphics can cover pins or text, so preserve intentional
ordering when changing existing symbols. `AltiumSchDoc.add_component_from_library(...)`
preserves that order when placing a symbol into a schematic.

For PcbDoc and PcbLib authoring, use the high-level `add_*` methods. They keep
the record lists, net tables, embedded model streams, layer references, and
authoring metadata synchronized. PCB `layer=` arguments accept legacy
`PcbLayer` values and documented V7-aware `PcbLayerRef` values or semantic
tokens; see [PCB layers](pcb_layers.md) before working with V7 saved layer ids,
Mechanical 17+ metadata, or AD 26.8.1 extended signal-layer metadata. Save with
`save(...)` when finished.

For single-item inspection and extraction, use
[`asset_inventory(...)`](extractable_assets.md). `AltiumPcbDoc`,
`AltiumPcbLib`, `AltiumSchDoc`, and `AltiumSchLib` can return typed selectable
assets and stable `AltiumAssetRef` handles for one footprint, symbol, embedded
model, embedded font, or opaque embedded payload.

## ObjectCollection

`ObjectCollection` is used by SchDoc and SchLib symbol containers. It is the
authoritative mutable store for container membership.

Filtered views such as `schdoc.notes`, `schdoc.objects.of_type(...)`, and
`schdoc.objects.where(...)` are live query views. They update as the owning
collection changes, but they are intentionally read-only for membership
changes. Mutate the returned objects in place, or use the owning container's
`add_object(...)`, `insert_object(...)`, or `remove_object(...)` methods.

Do not append to a filtered view. For example, do not call
`schdoc.notes.append(note)`. Use `schdoc.add_object(note)`.

Objects that need parent context are bound to their owner when added or parsed.
That parent context is what allows higher-level properties, font changes, and
ownership-sensitive serialization to resolve back through the containing
document.

## Ownership Rules

Some Altium schematic records are not valid as top-level objects. Use the
container or owner-specific API so the document can write the correct owner
relationships.

Harness entries and harness type labels are owned by a harness connector. Add
entries to the connector and then add the connector to the document. See
[`schdoc_add_harness_connector`](../../examples/schdoc_add_harness_connector/README.md)
and
[`schdoc_mutate_harness_connector`](../../examples/schdoc_mutate_harness_connector/README.md).

Sheet entries, sheet-name labels, and file-name labels are owned by a sheet
symbol. Add those records through the sheet symbol and then add the sheet symbol
to the document. See
[`schdoc_add_sheet_symbol`](../../examples/schdoc_add_sheet_symbol/README.md)
and
[`schdoc_mutate_sheet_symbol`](../../examples/schdoc_mutate_sheet_symbol/README.md).

Component pins, designators, parameters, and implementation-list records are
component-owned. Prefer `schdoc.add_component_from_library(...)` when placing a
library symbol into a schematic. For database-library-style placement, see
[`schdoc_insert_dblib_style`](../../examples/schdoc_insert_dblib_style/README.md).

For PCB documents, component primitives, nets, pads, tracks, vias, models, and
component bodies have index and stream relationships. Use PCB container helpers
where they exist. Direct record-list edits are possible but are an advanced
mutation path.

## SchDoc Vs PcbDoc

SchDoc and SchLib currently have the more uniform object system. They use
`ObjectCollection`, typed live views, public `make_sch_*` factories, and
container-owned add/remove operations. This is the preferred pattern for
schematic examples and future schematic API work.

PcbDoc and PcbLib currently expose high-level helper methods plus typed record
lists. They do not yet expose the same generic `ObjectCollection` ownership
model or generic deletion API used by SchDoc. This means PCB creation is
straightforward through `add_*` helpers, while arbitrary PCB mutation is more
record-list oriented and should be handled with more care.

The practical difference is ownership maturity. Schematic ownership rules are
centralized in the document object model. PCB ownership and binary stream state
are still partly managed by the high-level helpers and internal authoring
machinery. Prefer public PCB helpers for authored output, and validate direct
PCB record-list mutation by opening the result in Altium or by running the
relevant tests.

## Public APIs And Careful APIs

Stable public patterns:

- `AltiumSchDoc.from_file(...)`, `schdoc.add_object(...)`,
  `schdoc.remove_object(...)`, `schdoc.add_component_from_library(...)`,
  `schdoc.clear_template()`, `schdoc.apply_template(...)`,
  `schdoc.extract_template(...)`, `schdoc.extract_embedded_images(...)`,
  `schdoc.asset_inventory(...)`, `schdoc.extract_asset(...)`, and
  `schdoc.save(...)`.
- `make_sch_*` factory functions, schematic enums, `SchPointMils`,
  `SchRectMils`, `SchFontSpec`, and `ColorValue`.
- `AltiumSchLib`, `AltiumSymbol`, symbol helper methods, `split(...)`,
  `merge(...)`, `asset_inventory(...)`, `extract_asset(...)`, and `save(...)`.
- `AltiumPcbDoc` high-level `add_*`, extraction, rendering, and `save(...)`
  methods.
- `AltiumPcbVia` fields for via geometry, IPC-4761 type, IPC-4761 feature
  rows, propagation delay in picoseconds, tenting, fabrication testpoint, and
  assembly testpoint metadata.
- `AltiumPcbLib`, `AltiumPcbFootprint`, footprint high-level `add_*`,
  embedded-model, SVG, split, asset-inventory, single-asset extraction, and
  save methods.
- `AltiumPrjPcb` for project parameters, variants, document references, and
  associated OutJob resolution.
- `AltiumPrjPcbBuilder` for minimal new `.PrjPcb` generation.
- `AltiumDesign` for project-level design extraction and JSON contracts.

Use with care:

- Direct edits to raw record fields when a higher-level property exists.
- Direct use of `AltiumSchImage.image_data` as a standalone image file. It is
  the raw SchDoc Storage payload and may contain Altium wrapper bytes; use
  `schdoc.extract_embedded_images(...)` for normal image export.
- Direct edits to PCB typed lists such as `pcbdoc.tracks` or `pcbdoc.pads`.
- Passing serialized V7 saved layer ids as PCB `layer=` arguments unless the
  method explicitly documents a V7 override. Current ordinary layer arguments
  use the legacy/TV6 `PcbLayer` enum, which is limited to Top, Mid1..Mid30,
  Bottom, Mechanical 1..16, and the documented legacy non-copper layers.
- Manual management of `IndexInSheet`, `OwnerIndex`, font indexes, component
  indexes, net indexes, model checksums, stream names, or binary stream order.
- Low-level serializer fields that expose Altium's native units or native enum
  encodings.
- Builder classes that still exist for internal compatibility or project-file
  helpers. Do not use old SchDoc/SchLib fluent builder patterns as the public
  authoring style. For PCB, prefer `AltiumPcbDoc` and `AltiumPcbLib` methods
  even though internal builder machinery may still be used by those methods.

Direct record manipulation is reasonable when preserving parsed data, doing a
small targeted mutation that does not affect ownership or indexes, or working
around a feature that does not yet have a public helper. If the mutation changes
membership, ownership, fonts, nets, component indexes, embedded models, or
binary stream layout, use a container method or add a dedicated helper.

## Units

The public API strives to make units explicit.

Schematic authoring APIs are primarily mil-based because Altium schematic
workflows are normally grid-oriented and mil-based. Use `SchPointMils` and
`SchRectMils` for public schematic coordinates and bounds. Use `SchFontSpec`
for fonts rather than manually editing font indexes.

PCB and PcbLib authoring are metric-friendly in real workflows, but the current
high-level methods use explicit `*_mils` parameter names. Footprint dimensions,
package drawings, drills, body geometry, and STEP-derived dimensions are often
specified in millimeters, so convert metric source data before passing it to
these methods. The intended public direction is small metric helpers such as
`mm_to_mils(...)`, `point_mm(...)`, `bounds_mm(...)`, and `points_mm(...)`
rather than duplicating every PCB method as a separate `*_mm` API.

Low-level record fields may expose Altium internal units. Those fields are
useful for round-trip preservation and serializer work, but they should not be
the first choice for public authored geometry.

PCB via propagation delay is the exception where the public property is named
for its unit: use `via.propagation_delay_ps` and
`add_via(..., propagation_delay_ps=...)`. Altium stores the serialized value in
seconds, but the public API uses picoseconds to match the Via dialog.

Pad and via drill-hole tolerances use mil-unit helpers on top of raw
internal-unit fields. Prefer `hole_positive_tolerance_mils` and
`hole_negative_tolerance_mils` for reading, authoring, and mutation. `None`
represents Altium's N/A tolerance state.

## Examples To Study

Start with [`hello_schdoc`](../../examples/hello_schdoc/README.md),
[`hello_schlib`](../../examples/hello_schlib/README.md),
[`hello_pcbdoc`](../../examples/hello_pcbdoc/README.md), and
[`hello_pcblib`](../../examples/hello_pcblib/README.md) for basic document and
library creation.

For schematic object creation, see
[`schdoc_add_note`](../../examples/schdoc_add_note/README.md),
[`schdoc_add_text_frame`](../../examples/schdoc_add_text_frame/README.md),
[`schdoc_add_wire_and_net_label`](../../examples/schdoc_add_wire_and_net_label/README.md),
and the drawing-tool examples such as
[`schdoc_add_arc_and_full_circle`](../../examples/schdoc_add_arc_and_full_circle/README.md).

For schematic mutation, see
[`schdoc_note_command`](../../examples/schdoc_note_command/README.md),
[`schdoc_move_note`](../../examples/schdoc_move_note/README.md),
[`schdoc_clean`](../../examples/schdoc_clean/README.md), and the harness and
sheet-symbol mutation examples.

For PCB authoring, see
[`pcbdoc_add_track`](../../examples/pcbdoc_add_track/README.md),
[`pcbdoc_add_arc`](../../examples/pcbdoc_add_arc/README.md),
[`pcbdoc_add_pad`](../../examples/pcbdoc_add_pad/README.md),
[`pcbdoc_add_hole_tolerances`](../../examples/pcbdoc_add_hole_tolerances/README.md),
[`pcbdoc_add_via_ipc4761_matrix`](../../examples/pcbdoc_add_via_ipc4761_matrix/README.md),
[`pcbdoc_add_text`](../../examples/pcbdoc_add_text/README.md),
[`pcbdoc_add_filled_region`](../../examples/pcbdoc_add_filled_region/README.md),
and
[`pcbdoc_insert_nets_route`](../../examples/pcbdoc_insert_nets_route/README.md).

For targeted PCB mutation, see
[`pcbdoc_mutate_via_ipc4761`](../../examples/pcbdoc_mutate_via_ipc4761/README.md).

For PCB library workflows, see
[`pcblib_find_footprint`](../../examples/pcblib_find_footprint/README.md),
[`pcblib_split`](../../examples/pcblib_split/README.md),
[`pcblib_footprint_svg`](../../examples/pcblib_footprint_svg/README.md),
[`pcblib_extract_3d_models`](../../examples/pcblib_extract_3d_models/README.md),
and
[`pcblib_synthesize_power_resistor_lib`](../../examples/pcblib_synthesize_power_resistor_lib/README.md).

For integrated library source recovery, see
[`intlib_extract_sources`](../../examples/intlib_extract_sources/README.md).

For read-only embedded PCB/PcbLib model, font, and opaque payload inventory,
see [`embedded_assets`](embedded_assets.md).

For typed asset inventory and one-item extraction, see
[`extractable_asset_inventory`](../../examples/extractable_asset_inventory/README.md).

For project-level compiled schematic output, repeated sheets, resolved channel
designators, and Design a2 to Design b0 migration, see
[`compiled_design`](compiled_design.md).
