# altium-monkey 2026.06.08 Release Notes

Package version: `2026.6.8`

`2026.06.08` is represented in Python package metadata as the PEP 440
canonical form `2026.6.8`.

This release expands PcbDoc/PcbLib writer parity for downstream board
generation and transcode workflows. The changes are additive and preserve
existing documented APIs.

## PcbLib Via Feature Authoring

PcbLib now reads, preserves, and authors footprint-level
`PrimitiveParameters`, via IPC-4761 side tables, propagation delay,
fabrication and assembly testpoint flags, and mixed-footprint via-structure
links through the public `add_via(...)` API.

Via-structure links in PcbLib footprints now resolve against the full native
`Data` record order, not the via-only list. This fixes IPC-4761 feature rows
for footprints that contain pads, tracks, arcs, text, fills, regions, or
component bodies before linked vias. Footprints also expose a read-only
`primitives` aggregate view in native record order for workflows that need to
replay mixed primitive ordering.

## PcbDoc And PcbLib Writer Parity

PcbDoc and PcbLib pad authoring now expose matching fabrication and assembly
testpoint flags on the clean public `add_pad(...)` APIs.

PcbDoc and PcbLib via authoring now accepts explicit IPC-4761 feature rows on
the clean public `add_via(...)` APIs, so callers can author non-default side
and material rows without mutating returned records.

PcbDoc custom-pad authoring is now available through
`AltiumPcbDoc.add_custom_pad(...)` and `PcbDocBuilder.add_custom_pad(...)`,
including custom bodies, custom holes, pad-center offsets, net assignment, and
custom-pad footprint placement without duplicate region replay. Direct PcbDoc
custom-pad authoring also accepts additional per-layer custom bodies and holes
through Python `PcbCustomPadLayerShapeSpec` / `layer_shapes=...` and native
C++ `PcbCustomPadOptions.layer_shapes`.

Placing PcbLib footprints into PcbDoc now preserves footprint-local via
identity through PcbDoc-to-PcbLib extraction, including IPC-4761 feature side
tables, feature materials, propagation delay, hole tolerances, mask and tenting
flags, and fabrication/assembly testpoint flags.

PcbDoc authoring now includes Python and native C++ helpers for mechanical
layer display names, enabled-state registry fields, and mechanical mirror
pairing used by component side flipping.

PcbDoc user-union creation now supports explicit native union-id replay in
Python and native C++, enabling deterministic read/mutate/write recreation of
named user unions while preserving auto-allocation for normal use.

Layer-stack document authoring now includes via-span and backdrill-span
helpers, with native C++ parity for the same `LAYERPAIR*` Board6/Data contract.
Direct via-tail and counterhole mutation remains intentionally outside this
API.

## PcbDoc Long Text Fix

PcbDoc text writing now emits long authored text through a wide-safe
`Texts6/Data` fallback payload instead of the legacy one-byte Pascal-length
payload. This fixes downstream PcbDoc transcodes that generate PCB text longer
than 255 bytes.

The release includes fixture-backed coverage for an AD-authored board with one
ordinary PCB text object and one text-frame object containing the same
292-byte string. The tests verify no-op preservation of Altium's legacy
256-byte fallback payload and fresh authoring through the long-safe writer
path.

## Public Examples

New and updated public examples demonstrate direct via IPC-4761 feature-row
authoring, footprint primitive parameters, and fixture-backed PcbLib
via-feature recreation through the public `add_footprint(...)`, `add_via(...)`,
and primitive authoring helpers.

## Validation

The release diff was audited against `altium-monkey/v2026.6.7`. The
user-facing changed surfaces are: layer-stack document authoring and
interchange docs, PcbDoc/PcbLib writer parity, PcbLib via feature and
`PrimitiveParameters` support, public PcbLib examples, long PcbDoc text
serialization, generated public example docs, and native C++ parity for the
promoted writer controls.

The release candidate was tested with focused package authoring tests, public
example tests, private PcbDoc/PcbLib fixture lanes, native C++
parity lanes for the promoted writer surfaces, AD26 interop open/save smoke for
the generated PcbLib samples, and strict package Pyright with zero diagnostics.

## Public API Compatibility

Existing documented APIs remain compatible. The new writer controls,
metadata-preservation paths, and text serialization fix are additive.

---

# altium-monkey 2026.06.07 Release Notes

Package version: `2026.6.7`

`2026.06.07` is represented in Python package metadata as the PEP 440
canonical form `2026.6.7`.

This release is the first public Altium Monkey release with comprehensive
Layer Stack Manager reading, writing, interchange, and new-board authoring
support. The stackup work is additive and keeps existing documented APIs
compatible.

## Layer Stack Document

`AltiumLayerStackDocument` is now the source-aware layer-stack model for PcbDoc
workflows. Use it to read native PcbDoc stack data, import or export
`.stackup` and `.stackupx`, inspect rigid-flex topology, query native substack
and board-region joins, and author new rigid or fixture-backed rigid-flex
boards through `PcbDocBuilder.set_layer_stack_document(...)`.

`ResolvedLayerStack` remains the read-only convenience view for consumer
reports, layer display names, enabled-layer checks, and existing examples such
as `pcbdoc_stats`. It is intentionally derived data and is not the source model
for writing stack data.

## PcbDoc Stack Authoring

The new writer surface covers canonical empty-board synthesis, two-layer and
four-layer template compatibility, custom rigid stacks, controlled-impedance
rigid stacks, flex/stiffener stacks, Rigid-Flex 1.0 split-line stacks,
flex-in-cutout stacks, branch-based rigid-flex topologies, nested branch
topologies, and impedance/backdrill evidence for the promoted fixture-backed
rigid-flex shapes.

Stackup export/import now preserves the promoted Layer Stack Manager semantics
across native PcbDoc, `.stackup`, and `.stackupx` readbacks, including
substack-local layer enablement, bend-line radii, branch topology, selected
surface-finish rows, adhesive/stiffener rows, realistic-ratio display metadata,
impedance profiles, transmission lines, via spans, and backdrill spans.

## Public Examples

New and updated examples show the supported authoring and inspection workflow:

1. `pcbdoc_create_custom_rigid_stack`
2. `pcbdoc_create_impedance_rigid_stack`
3. `pcbdoc_inspect_layer_stack`
4. `pcbdoc_flex_topology_report`
5. `pcbdoc_create_flex_stiffener`
6. `pcbdoc_create_rigid_flex_split_lines`
7. `pcbdoc_create_flex_in_cutout`
8. `pcbdoc_create_rigid_flex_branch`
9. `pcbdoc_create_rigid_flex_branch_intrusion`
10. `pcbdoc_create_rigid_flex_two_branch`
11. `pcbdoc_create_rigid_flex_multibranch`
12. `pcbdoc_create_rigid_flex_impedance_backdrill`
13. `pcbdoc_create_cavity_placements`

The rigid-board `pcbdoc_stats` example still uses `ResolvedLayerStack` and
continues to report the packaged `loz-old-man` board statistics.

## PcbDoc And PcbLib Cavity Regions

PcbLib and PcbDoc region authoring now support cavity-definition regions
through `PcbRegionKind.CAVITY_DEFINITION` and `cavity_height_mils`, mapping the
public enum to the native cavity region kind. The public cavity examples create
a footprint cavity and board-side cavity placements, insert the packaged
footprint and embedded STEP payload, and verify save/reparse semantics.

## Schematic SVG Fix

SchDoc SVG rendering keeps pin names and designators rotated for vertical pins.
The public proof sample creates a SchLib symbol, inserts it into a SchDoc, and
renders SVG to verify the saved library-backed path.

## Validation

The release was tested through:

1. public example tests that execute the stackup examples and verify native
   PcbDoc readback with `AltiumPcbDoc` and `AltiumLayerStackDocument`
2. `.stackup` and `.stackupx` interchange round-trip checks for the promoted
   stack shapes
3. a supported local corpus regeneration gate covering synthesized,
   real-world, and canonical empty PcbDoc files
4. focused private release signoff checks for public packaging hygiene and
   format-contract synchronization
5. a strict package Pyright gate with zero diagnostics for
   `src/py/altium_monkey`

## Public API Compatibility

Existing documented APIs remain compatible. The new layer-stack, cavity, and
SVG behaviors are additive.

---

# altium-monkey 2026.06.01-2 Release Notes

Package version: `2026.6.1.post1`

`2026.06.01-2` is represented in Python package metadata as the PEP 440
canonical form `2026.6.1.post1`.

This second 2026.06.01 release expands public PcbDoc/PcbLib authoring parity
for downstream board generation workflows. The changes are additive and
preserve existing documented APIs.

## PcbDoc Writer API

`AltiumPcbDoc.add_via(...)` now mirrors PcbLib via surface controls by
accepting independent signed top/front and bottom/back solder-mask expansion
values in addition to top/bottom tenting flags.

`AltiumPcbDoc.add_pad(...)` and `PcbDocBuilder.add_pad(...)` now support
local-stack pad body geometry. Callers can provide top/mid/bottom shape and
size overrides; the writer emits native `pad_mode=1` and preserves the parsed
per-layer fields on round trip.

`AltiumPcbDoc.add_pad(...)` now also accepts the structured
`PcbMaskExpansion` / `PcbMaskExpansionMode` solder/paste mask-expansion
contract while preserving existing manual mil aliases.

PcbDoc region authoring exposes region kind, board-cutout, shape-based,
keepout, and subpoly metadata.

## PcbLib Writer API

PcbLib track, arc, and fill authoring now accepts solder-mask and paste-mask
expansion values.

PcbLib text authoring accepts frame options for authored non-barcode text,
matching the PcbDoc text surface where the native format supports it.

## Validation

Focused Python package tests, generated-public-package tests, and native C++
tests cover the shared primitive option cleanup, including component-body option
parity and R082-style local stack pad geometry.

## Public API Compatibility

Existing documented APIs remain compatible. The new writer controls are
additive.

---

# altium-monkey 2026.06.01 Release Notes

Package version: `2026.6.1`

`2026.06.01` is represented in Python package metadata as the PEP 440
canonical form `2026.6.1`.

This release adds PcbLib writer controls needed by downstream footprint and
library-generation workflows, so callers no longer need to patch native records
after using the public authoring API.

## PcbLib Writer API

`AltiumPcbFootprint.add_via(...)` now accepts top/bottom tenting flags and
independent top/bottom solder-mask expansion values.

`AltiumPcbFootprint.add_custom_pad(...)` now exposes custom-pad anchor width,
height, rotation, and shape. Generated custom-pad regions now use the authored
anchor pad's native 1-based `PADINDEX`, including when the custom pad is not
the first pad in a footprint.

`AltiumPcbFootprint.add_pad(...)` and `AltiumPcbDoc.add_pad(...)` now expose
the public `PadHoleShape` enum for round, square, and slotted drill holes.

## Public API Compatibility

Existing documented APIs remain compatible. The new writer controls are
additive.

---

# altium-monkey 2026.05.29 Release Notes

Package version: `2026.5.29`

`2026.05.29` is represented in Python package metadata as the PEP 440
canonical form `2026.5.29`.

This release publishes the PcbDoc user-union API and sample work. It also
carries release-validation cleanup that keeps the public packaging lane aligned
with the current private test and type-checking baselines.

## PcbDoc User Unions

PcbDoc now includes public user-union authoring APIs for creating, renaming,
mutating, and deleting named user-defined PCB unions. The release adds a small
`pcbdoc_user_union` public example that creates a named union containing
ordinary board objects and writes a board that can be inspected in Altium
Designer.

Track user-union encoding is corrected so authored track unions are visible
when the saved board is opened in Altium Designer.

## Release Validation Maintenance

Internal type-checking diagnostics were reduced across collection query views,
PCB drawing metadata, and IPC-2581 export paths without changing public APIs.

Stale release and test metadata were cleaned so release validation no longer
depends on obsolete fixtures or optional direct dependencies.

## Public API Compatibility

Existing documented APIs remain compatible. The PcbDoc user-union APIs are
additive.

---

# altium-monkey 2026.05.28 Release Notes

Package version: `2026.5.28`

`2026.05.28` is represented in Python package metadata as the PEP 440
canonical form `2026.5.28`.

This release tightens public PcbLib/PcbDoc authoring APIs, makes normal parser
logging quieter for downstream CLIs, and fixes public issue #3 so empty SchLib
designators stay empty when read back.

## Parser Logging

Parser/status chatter such as `Parsing SchDoc file`, embedded image discovery,
lazy `Loading PcbDoc`, and embedded font/model discovery messages now uses
DEBUG logging. Downstream command-line tools can keep normal INFO output
concise while still exposing parser diagnostics when they enable verbose or
debug logging.

## PcbLib Authoring

PcbLib pad authoring now exposes explicit solder-mask and paste-mask expansion
modes. `PcbMaskExpansion` and `PcbMaskExpansionMode` support `none`, `rule`,
and `manual`, and footprint `add_pad(...)` / `add_custom_pad(...)` accept
signed manual expansion values in mils. `add_custom_pad(...)` still accepts the
older rule-expansion booleans as compatibility aliases.

PcbLib and PcbDoc text authoring now share the public stroke-font contract.
Stroke text accepts `stroke_font_type="default"`, `"sans-serif"`, or `"serif"`,
and PcbLib `font_kind="stroke"` writes native stroke text encoding rather than
TrueType encoding.

PcbLib footprint `add_text(...)` now also supports first-class barcode text
with the same option names as PcbDoc text authoring: `font_kind="barcode"`,
`barcode_kind`, `barcode_render_mode`, `barcode_full_size_mils`,
`barcode_margin_mils`, `barcode_min_width_mils`, `barcode_show_text`, and
`barcode_inverted`. Save/readback preserves barcode sizing, symbology,
human-readable text, inversion, layer, v7 layer, placement, rotation, and
mirroring metadata.

## SchLib Empty Designators

Reading a SchLib designator record with omitted `Text` now preserves the empty
designator instead of substituting `U?`. This fixes
[#3](https://github.com/wavenumber-eng/altium_monkey/issues/3). Synthetic
authoring helpers still default new designator objects to `U?` unless callers
explicitly set the text to an empty string.

## Public API Compatibility

Existing documented APIs remain compatible. The new PcbLib authoring options
are additive, legacy custom-pad rule-expansion booleans still work, and parser
diagnostics remain available through DEBUG logging.

---

# altium-monkey 2026.05.26 Release Notes

Package version: `2026.5.26`

`2026.05.26` is represented in Python package metadata as the PEP 440
canonical form `2026.5.26`.

This release makes Pick-and-Place coordinate generation explicit and
documented after validating Altium PNP-METRIC parity on hierarchical board
fixtures.

## Pick-And-Place Position Modes

`AltiumDesign.to_pnp(...)` now accepts `position_mode`. The default
`altium-pick-place` mode matches Altium's Pick Place export by using the center
of the bounding box of component-owned pad anchor points, with component-origin
fallback when a component has no owned pads.

Use `position_mode="component-origin"` when callers need the raw footprint
placement origin instead.

Design JSON now emits `pnp.position_mode` next to `pnp.units` and
`pnp.source_pcbdoc`, and the public `design.a1` schema documents the allowed
mode names. `center_x` and `center_y` should be read as the selected PnP
position, not as a generic geometric centroid.

## Validation

The Pick-and-Place mode contract is covered by regression tests, including a
fixture whose component origin differs from the Altium-compatible Pick Place
position.

Existing documented APIs remain compatible. The default PnP behavior remains
the Altium-compatible mode introduced by the 2026.5.26 fix; the new argument is
an opt-in override for callers that intentionally need component origins.

---

# altium-monkey 2026.05.25 Release Notes

Package version: `2026.5.25`

`2026.05.25` is represented in Python package metadata as the PEP 440
canonical form `2026.5.25`.

This release moves core PcbDoc/PcbLib STEP bounds inference from CadQuery to
`wn-geometer`, removes unused direct runtime dependencies, and fixes
`altium_cruncher megamaid` schematic embedded-image extraction edge cases.

## STEP Bounds Dependency Cleanup

PcbDoc and PcbLib embedded STEP model bounds now use
`wn-geometer==2026.5.25`. Core `altium-monkey` no longer depends on CadQuery for
embedded STEP model bounds. CadQuery remains an optional dependency for public
examples that synthesize new STEP geometry, such as the power-resistor PcbLib
sample.

The current Geometer wheel coverage used by this release is Windows amd64,
macOS arm64, and Linux x86_64 tagged `manylinux_2_39`. Older Linux glibc
compatibility is not claimed for this release.

`AltiumPcbDoc.add_embedded_3d_model(...)` and
`AltiumPcbFootprint.add_embedded_3d_model(...)` still prefer STEP-derived
bounds when callers omit explicit placement geometry. If STEP bounds cannot be
computed on the current host, those helpers can now fall back to an
axis-aligned rectangle around available SMD/through-hole pads. This fallback is
for producing a usable component-body projection; it is not a replacement for
STEP-derived model geometry.

Explicit `bounds_mils`, `projection_outline_mils`, and `overall_height_mils`
remain the deterministic override path when package geometry is known.

## Runtime Dependency Cleanup

The unused direct NumPy runtime dependency has been removed from
`altium-monkey`. NumPy may still appear in developer workspaces, optional
examples, or test environments through other packages, but it is no longer part
of the core package install contract.

## Embedded Image And CLI Fixes

`altium_cruncher megamaid` schematic embedded-image extraction now handles
Altium wrapper payloads without relying on a missing private
`AltiumSchDoc` helper. The command writes the preferred native image bytes when
they are available.

The public schematic image boundary is now documented: use
`AltiumSchDoc.extract_embedded_images(...)` for standalone image files, and
treat `AltiumSchImage.image_data` as raw Storage payload for preservation.

`altium_cruncher` CLI logging on Windows now avoids Unicode logging tracebacks
when project filenames contain characters unsupported by a legacy console
encoding.

## Public API Compatibility

Existing documented APIs remain compatible. The STEP inference implementation
changed internally, and the runtime dependency set is smaller, but callers that
already use explicit embedded-model placement geometry or normal inferred
placement flows should not need code changes.

Draftsman remains experimental as described in the 2026.05.24 release notes.

---

# altium-monkey 2026.05.24 Release Notes

Package version: `2026.5.24`

`2026.05.24` is represented in Python package metadata as the PEP 440
canonical form `2026.5.24`.

This release is a focused Draftsman follow-up after `2026.05.23`. It keeps
Draftsman support experimental, adds multi-page and object-id workflows, adds a
JSON-driven controlled-impedance Draftsman sample, standardizes note/text/picture
placement around `DraftsmanRect`, and carries the release-integration fix for
Altium polygon-pour cutout classification in the toolz data-model writer path.

## Draftsman API Follow-Up

Draftsman documents now expose page and item lookup helpers for scan-and-mutate
workflows:

1. `AltiumDraftsmanDocument.page_by_id(...)`
2. `AltiumDraftsmanDocument.item_by_id(...)`
3. `AltiumDraftsmanDocument.note_by_id(...)`
4. `AltiumDraftsmanPage.item_by_id(...)`
5. `AltiumDraftsmanPage.items_by_type(...)`
6. `AltiumDraftsmanPage.note_by_title(...)`

`AltiumDraftsmanDocument.add_page(...)`, `remove_page(...)`, and
`move_page(...)` support conservative multi-page document editing. New pages can
copy sheet setup from an existing page while starting with an empty item list;
item-preserving page cloning remains deferred until Draftsman item-reference
remapping is fixture-proven.

`page.add_note(...)` now accepts `rect=DraftsmanRect(...)`, matching
`page.add_text(...)` and `page.add_picture(...)`. Existing
`x_mm`/`y_mm`/`width_mm` note arguments are still accepted for compatibility.
Because Draftsman note XML stores a start point plus width, `rect.height_mm` is
accepted as a layout hint but is not serialized.

The new `draftsman_multipage_notes` example creates a minimal project with an
empty `.SchDoc`, empty `.PcbDoc`, linked `.PCBDwf`, and two Draftsman pages. It
demonstrates page-id lookup, note-id lookup, page-scoped title lookup, and
document-wide note iteration.

## Experimental Net-Class Draftsman Autodoc

The new `draftsman_netclass_autodoc` example reads JSON configs for Bunny Brain,
RT Super C1, and loz-old-man, then synthesizes Draftsman board-assembly-view
pages that highlight routed net classes, differential-pair classes,
differential-pair names, or explicit scalar nets.

Each configured group can define:

1. selectors for net classes, differential-pair classes, differential pairs, or
   nets
2. a page title and notes
3. highlight and context colors
4. per-group view scale, auto-fit behavior, target fill ratio, and tile spacing
5. a minimum routed-length threshold and connected-route highlight filtering

For multi-layer routes, the sample tiles the relevant copper layers onto one
ANSI B sheet per group. Top layer views are placed first, notes stay in the
upper-left area, and the routed-layer cluster is centered in the remaining page
area.

This sample intentionally uses experimental support modules such as
`altium_pcb_drawing_geometry` and `altium_draftsman_pcb_geometry_xml`. They are
importable so advanced users can experiment, but the `PcbDrawing*` and
`DraftsmanPcb*` dataclasses are not package-root public API and may change
before a stable `page.add_board_assembly_view(...)` style API is promoted.

## Draftsman Geometry And Rendering Fixes

The shared PCB drawing-geometry path used by the Draftsman experiment now keeps
legacy TC2030-style pads visible when negative paste expansion intentionally
removes the paste opening. This mirrors the existing PCB SVG workaround and
keeps the public SVG renderer path intact.

The Draftsman autodoc geometry now also handles:

1. free/componentless pads such as mounting holes
2. configurable non-plated hole coloring
3. visible drill and slot overlays above pad/via helper geometry
4. IPC-4761 filled/capped vias rendered as copper without an open drill overlay
5. crossing-zero Draftsman cache arcs using Altium-style unwrapped end angles
6. connected-route highlight filtering so short segments remain highlighted
   when they belong to a longer selected route
7. internal-layer draw ordering with context copper underneath highlighted
   routes, pad/via helpers, and drill overlays

## Related Polygon-Pour Cutout Writer Fix

The release integration includes the toolz data-model Altium PcbDoc writer fix
for polygon-pour cutout classification. Legacy `REGION` raw `KIND=2` records are
no longer treated as cutouts, while legacy `KIND=1`, parsed polygon cutouts, and
SDK-style cutout enum names are accepted. Regression coverage verifies both
classification behavior and realized polygon-hole preservation.

## Public Repo Hygiene

The published GitHub mirror now includes contribution guidance plus GitHub issue
and pull-request templates. The templates explain the generated mirror workflow
and ask for minimal Altium reproduction files when users report file format
issues.

## Compatibility Notes

Existing documented APIs remain compatible. Draftsman remains experimental:
unsupported objects are preserved as raw XML, board-derived cache synthesis is a
sample-level capability, and broader board-view/callout/dimension APIs are still
future work.

# altium-monkey 2026.05.23 Release Notes

Package version: `2026.5.23`

`2026.05.23` is represented in Python package metadata as the PEP 440
canonical form `2026.5.23`.

This release adds the first experimental Python Draftsman `.PCBDwf` API, promotes
explicit PcbDoc differential-pair objects and component source metadata into the
Python public API, improves schematic embedded-image payload handling, adds
root `viewBox` controls for SVG output, and restores cumulative release-note
history back to the first 2026.04 package version.

## Initial Experimental Draftsman Support

`AltiumDraftsmanDocument` now exposes a conservative Python API for Draftsman
files. It can load raw XML and legacy LZ4-compressed `.PCBDwf` containers, write
raw XML outputs, create a blank AD25-profile document, set the linked source
PcbDoc filename, inspect pages/items, and preserve unsupported XML subtrees.

The initial typed model includes:

1. `AltiumDraftsmanPage`
2. `AltiumDraftsmanItem`
3. `AltiumDraftsmanNote`
4. `AltiumDraftsmanNoteElement`
5. `AltiumDraftsmanText`
6. `AltiumDraftsmanPicture`
7. `AltiumDraftsmanDocumentOptions`
8. `DraftsmanColor`, `DraftsmanPoint`, `DraftsmanSize`, `DraftsmanRect`, and
   `DraftsmanMargin`
9. `DraftsmanFontStyle` and `DraftsmanFontDecoration`
10. `DraftsmanStandardSheetSize`, `DraftsmanNoteBorderStyle`,
    `DraftsmanHorizontalAlignment`, and `DraftsmanVerticalAlignment`

New authored paths include `page.add_note(...)`, `page.add_text(...)`,
`page.add_picture(...)`, document font-style lookup/reuse, page-size helpers,
and visual placement helpers such as `page.point_from_top_left(...)` and
`page.rect_centered(...)`.

Three public examples were added:

1. `draftsman_create_blank_project`
2. `hello_draftsman`
3. `draftsman_add_image`

Draftsman support is experimental in this release. Unsupported objects remain
raw XML and the API can change as more object families are promoted.

## SVG Output Contract Updates

Schematic, schematic-library, PcbDoc, and PcbLib SVG output now have documented
root `viewBox` behavior. Normal output includes a root `viewBox`; render options
expose `include_view_box=False` for strict comparison lanes or downstream
compatibility paths that need width and height without a root `viewBox`.

The public SVG contract now documents group structure, semantic `data-*`
attributes, relationship JSON linkage for schematic renders, PCB layer metadata,
and the PCB enrichment metadata payload.

## PCB Layer Display Labels

`PcbLayer.to_display_name()` now returns default human-facing PCB layer labels
such as `Top Layer`, `Bottom Layer`, `Top Overlay`, and `Top Solder`, while
`to_json_name()` remains the stable token API for machine-readable output.

Parsed PcbDoc SVG output uses resolved board layer-stack display names when the
board provides them and falls back to `PcbLayer.to_display_name()` otherwise.
PcbLib footprints do not own a board layer stack, so footprint SVG output uses
the default display labels.

## PcbDoc Polygon Authoring Notes

PcbDoc region authoring can carry optional polygon realization linkage through
`polygon_index`, `subpoly_index`, and `union_index`. This keeps authored region
records able to preserve editable polygon relationships when callers know those
indexes.

The PcbDoc planning docs also separate typed polygon-pour field promotion from
raw record preservation and polygon realization/linkage work, so later polygon
modeling can proceed without overclaiming automatic repour behavior.

## Improved SchDoc And SchLib Embedded Images

Schematic image extraction and SVG rendering now prefer native payloads stored
inside Altium image wrappers such as `TdxPNGImage` instead of falling back to
BMP previews when a better original payload is available.

The image path now preserves PNG and 32-bit BMP alpha and no longer treats the
schematic background color as the normal transparency keying path. This improves
visual fidelity for embedded logos and transparent schematic graphics while
keeping extracted image assets closer to the original Altium payload.

## New PcbDoc Differential-Pair APIs

`AltiumPcbDoc` now parses `DifferentialPairs6/Data` into
`pcbdoc.differential_pairs`. Each `AltiumPcbDifferentialPair` exposes:

1. `name`
2. `positive_net_name`
3. `negative_net_name`
4. `gather_control`
5. `unique_id`

Common lookup and authoring helpers are now available:

1. `pcbdoc.get_differential_pair(name)`
2. `pcbdoc.differential_pair_classes`
3. `pcbdoc.differential_pairs_by_net_name`
4. `pcbdoc.add_differential_pair(...)`
5. `PcbDocBuilder.add_differential_pair(...)`

The model keeps concrete differential-pair objects separate from
differential-pair classes in `Classes6/Data`, signal classes, routing rules, and
project suffix policy.

## New PcbDoc Component Source Metadata APIs

`AltiumPcbComponent` now exposes source and ECO provenance fields that are
important for repeated sheets, channels, and board-to-schematic traceability:

1. `channel_offset`
2. `source_designator`
3. `source_unique_id`
4. `source_unique_id_segments`
5. `source_hierarchical_path`
6. `source_hierarchy_segments`
7. `source_component_library`
8. `source_component_library_identifier_kind`
9. `source_component_library_identifier`
10. `source_lib_reference`
11. `footprint_description`

Component designator/comment autoposition fields are now enum-backed through
`PcbTextAutoposition`:

1. `name_auto_position`
2. `comment_auto_position`

Optional component flags are also exposed where present:

1. `lock_strings`
2. `enable_pin_swapping`
3. `enable_part_swapping`
4. `jumpers_visible`

`AltiumPcbDoc.add_component(...)` and `PcbDocBuilder.add_component(...)` accept
these fields for authored components. Newly authored components no longer invent
`NAMEAUTOPOSITION` or `COMMENTAUTOPOSITION` fields when callers do not supply
explicit values.

## Examples

Two public PcbDoc examples were added:

1. `pcbdoc_add_differential_pairs` creates a PcbDoc from scratch, adds
   differential-pair objects with routed member nets, and saves a generated
   board plus JSON summary.
2. `pcbdoc_diff_pair_report` loads the RT Super C1 project, reads differential
   pair objects and classes, prints a table, and writes JSON plus text reports.

## Bug Fixes

### Schematic Embedded Images Preserve Native Alpha Payloads

Embedded schematic images with native alpha data now render and extract without
forcing background-color keying. PNG payloads and 32-bit BMP payloads keep their
alpha channel when the stored Altium wrapper provides it.

### Draftsman Explicit Fonts Clear Document-Font Flags

Draftsman note and text helper methods now clear the relevant `UseDocumentFont`
flag when callers supply an explicit font style. This makes generated notes and
text render with the requested font family, size, and decoration flags instead
of silently falling back to the document default font.

### Authored PcbDoc Components Do Not Invent Autoposition Fields

Component authoring no longer emits default `NAMEAUTOPOSITION` or
`COMMENTAUTOPOSITION` fields unless the caller supplies explicit enum values.
This better matches Altium files where those fields are absent and avoids
introducing board metadata that was not present in the source intent.

### Release Notes Preserve Public History

The public `RELEASE_NOTES.md` file now carries historical release sections back
to `2026.04.15`. The Git tags already preserved this history, but the current
branch's release-notes file only carried the latest two sections before this
release.

## Public API Compatibility

Existing documented APIs remain compatible. This release adds new optional
PcbDoc component and differential-pair fields and helpers. The component
autoposition write behavior is more conservative for new authored components:
fields are omitted unless explicitly supplied.

Differential-pair object support reads and writes explicit PcbDoc pair objects.
Naming-policy inference from `.PrjPcb` suffix declarations and broader PCB
object-class/room modeling remain future work.

Draftsman APIs are newly introduced and experimental. They are documented for
the supported objects above, but the shape may change as dimensions, board
views, generated tables, title blocks, and other Draftsman object families are
modeled.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

No new functional gaps were introduced in this release. The PcbDoc object-model
and authoring limitations described in the 2026.05.20 release notes still
apply.

---

# altium-monkey 2026.05.22 Release Notes

Package version: `2026.5.22`

`2026.05.22` is represented in Python package metadata as the PEP 440
canonical form `2026.5.22`.

This release completes public Python API coverage for PcbDoc and PcbLib
pad/via drill-hole tolerances, adds a public example for manual Altium review,
and fixes PcbDoc saves from source boards that do not contain Simbeor cache
streams.

## New PcbDoc And PcbLib Hole-Tolerance APIs

Pads and vias now expose Altium's drill-hole tolerance fields for reading,
mutation, and authoring:

1. `hole_positive_tolerance`
2. `hole_negative_tolerance`
3. `hole_positive_tolerance_mils`
4. `hole_negative_tolerance_mils`

The raw fields use Altium internal integer units for careful round-trip work.
Use the `*_mils` helpers for normal public code. `None` represents Altium's
N/A tolerance state.

`AltiumPcbDoc.add_pad(...)`, `AltiumPcbDoc.add_via(...)`,
`PcbDocBuilder.add_pad(...)`, `PcbDocBuilder.add_via(...)`,
`AltiumPcbLib.add_pad(...)`, and `AltiumPcbLib.add_via(...)` now accept
`hole_positive_tolerance_mils` and `hole_negative_tolerance_mils`.

When either tolerance side is supplied while authoring, an omitted side is
written as an explicit `0mil` tolerance, matching Altium Designer's dialog
model for enabled hole tolerances.

## Examples

One public PcbDoc example was added:

1. `pcbdoc_add_hole_tolerances` loads a blank PcbDoc, adds labeled pad and via
   drill-hole tolerance cases plus unset controls, saves the board, and writes
   a JSON manifest for manual review in Altium Designer.

## Bug Fixes

### PcbDoc Saves Preserve Absent Simbeor Cache Streams

PcbDoc saves from source files that do not contain `SimbeorCacheSection/*` now
preserve that absence. The builder no longer creates present-but-zero-byte
Simbeor cache streams for those boards, avoiding an Altium Designer stream-read
error on open.

## Public API Compatibility

Existing documented APIs remain compatible. This release adds optional keyword
arguments for pad/via authoring and new pad/via tolerance properties. Code that
does not use the new fields should continue to read, mutate, and save boards as
before.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

Draftsman support does not yet provide full typed coverage for dimensions,
callouts, board fabrication/assembly views, generated tables, title-block
templates, or LZ4 write output. Unsupported Draftsman XML is preserved rather
than normalized.

The PcbDoc object-model and authoring limitations described in the 2026.05.20
release notes still apply.

---

# altium-monkey 2026.05.20 Release Notes

Package version: `2026.5.20`

`2026.05.20` is represented in Python package metadata as the PEP 440
canonical form `2026.5.20`.

This release promotes PcbDoc via-protection metadata into the Python public API
and adds public examples for authoring and mutating IPC-4761 via settings. It
also carries forward the parser, rendering, deterministic-output, and PcbLib
metadata fixes from the previous package version.

## New PcbDoc Via APIs

### IPC-4761 Via Protection

`AltiumPcbVia` now exposes `ipc4761_via_type` for the IPC-4761 type shown in
Altium Designer's Via dialog. The public enum is `PcbIpc4761ViaType` and maps
directly to Altium's values from `NONE` through
`TYPE_7_FILLING_AND_CAPPING`.

The structured IPC-4761 feature rows are available through
`via.via_structure` and helper methods:

1. `via.get_ipc4761_feature(...)`
2. `via.set_ipc4761_feature(...)`
3. `via.set_ipc4761_feature_side(...)`
4. `via.set_ipc4761_feature_material(...)`

Feature row types and sides use `PcbViaStructureFeatureType` and
`PcbViaStructureFeatureSide`.

### Via Propagation Delay

`AltiumPcbVia.propagation_delay_ps` provides read/write access to the via
propagation-delay field in picoseconds. `AltiumPcbDoc.add_via(...)` and the
underlying PcbDoc builder accept `propagation_delay_ps=...` for new vias.

Altium stores this value as seconds in the binary VIA payload. The public API
uses picoseconds to match the Via dialog and to avoid exposing the serializer's
unit convention to normal callers.

### Tenting, Mask, And Testpoint Metadata

`AltiumPcbDoc.add_via(...)` now accepts:

1. `is_tent_top`
2. `is_tent_bottom`
3. `is_test_fab_top`
4. `is_test_fab_bottom`
5. `is_assy_testpoint_top`
6. `is_assy_testpoint_bottom`

Authored tented vias now emit the manual solder-mask state that Altium
Designer expects for ordinary via tenting to survive an open/save cycle.

## Examples

Two public examples were added:

1. `pcbdoc_add_via_ipc4761_matrix` creates a labeled PcbDoc via matrix covering
   IPC-4761 types, ordinary tenting, manual solder-mask expansion variants, and
   a Type7 epoxy-fill/copper-cap example.
2. `pcbdoc_mutate_via_ipc4761` copies the bundled RT Super C1 project, finds
   12 mil diameter / 6 mil hole vias, and marks them as IPC-4761 Type7 filling
   and capping with explicit feature-row metadata.

## Bug Fixes

### PcbDoc Via Propagation-Delay Units Are Consistent

The public `propagation_delay_ps` API consistently uses picoseconds while the
serializer reads and writes the underlying VIA payload float in seconds.

Freshly authored propagation-delay values now include the Altium-compatible VIA
tail marker/default bytes needed for values to survive an Altium Designer
open/save cycle.

### PcbDoc Ordinary Tenting Authors Altium-Compatible Mask State

`add_via(..., is_tent_top=True)` and
`add_via(..., is_tent_bottom=True)` now emit manual solder-mask expansion state
with compatible defaults. This allows ordinary tenting flags to persist through
Altium Designer rather than being canonicalized away.

### PCB SVG Skips Text Records With No Drawable Glyph Geometry

PCB text records whose resolved glyphs produce no drawable geometry now emit no
SVG path output. This prevents empty or placeholder text geometry from
appearing when a font cannot provide visible outlines for a record.

### Fixed-Width PCB UTF-16 Text Fields Decode Safely

Fixed-width UTF-16-LE PCB text fields now decode through a safer path that
handles truncated or partially populated buffers defensively.

### PcbLib Footprint Regions Handle Extended-Vertex Records

Some footprint `Data` streams store shape-based region geometry under the
standard `REGION` record discriminator while using the extended, arc-capable
vertex layout. PcbLib extraction now detects that layout and preserves the
shape-based region geometry instead of treating the payload as a simple region.

### PCB Metadata Follows Windows-1252 Text Semantics

PcbDoc and PcbLib pipe-text metadata now uses Windows-1252 encoding and
decoding to match Altium's native serializer. This fixes footprint extraction
and library authoring for real-world files that contain Windows-1252
punctuation bytes.

### Schematic And Design-Output Stability Fixes

This release carries forward the schematic rendering, symbol extraction,
deterministic design JSON/netlist/PNP output, and SchLib preview parity fixes
from the previous package version.

## Public API Compatibility

Existing documented APIs remain compatible. The release adds optional keyword
arguments to `AltiumPcbDoc.add_via(...)` and adds public enum/model surfaces
for via IPC-4761 metadata, feature rows, propagation delay, tenting, and
testpoint flags.

Exact serialized ordering for design JSON, netlist, and PNP data may change in
golden-file tests because output ordering is now more deterministic. PCB text
metadata now normalizes Windows-1252 byte streams to Unicode strings on read and
serializes those fields as Windows-1252 on write.

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Authoring And Object Model

The PcbDoc API includes high-level helper-oriented authoring for common board
workflows: outline/origin/layer-stack setup, nets, components, footprint
placement from PcbLib, tracks, arcs, fills, text, pads, vias, IPC-4761 via
metadata, regions, component bodies, embedded model payloads, and STEP-backed
3D body placement.

This is intentionally different from the current schematic object model.
SchDoc and SchLib use `ObjectCollection` with live filtered views and explicit
structural APIs such as `add_object(...)`, `insert_object(...)`, and
`remove_object(...)`. PcbDoc still exposes parsed board data through typed lists
plus PCB-specific high-level helpers. It does not yet use `ObjectCollection`.

Known gaps:

1. There is no generic `ObjectCollection`-style PcbDoc mutation/deletion API
   yet.
2. There is no public generic PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.
3. Continue source extraction when component cross-reference metadata is
   malformed but embedded source streams are still present.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.
3. Recover semantic component/model metadata when the source IntLib's
   cross-reference stream cannot be parsed.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Project variant support includes `ProjectVariantN` parsing, current-variant
selection, DNP/not-fitted designator lists, raw variation rows, variant-level
parameter rows, per-designator `ParamVariation` parameter overrides, and
variant metadata in design JSON.

`AltiumDesign.to_bom(variant=...)` applies parameter overrides to component
parameter maps, display values, and descriptions while retaining DNP rows with a
`dnp` flag. `AltiumDesign.to_pnp(variant=...)` omits DNP placements for the
selected variant.

Alternate fitted component rows are preserved in raw variant metadata but are
not applied as semantic component replacements in BOM, netlist, PNP, or SVG
output yet. Variant-aware schematic SVG presentation is also outside the core
public API for this release.

### Platform Coverage

Primary release validation remains on Windows.

Basic package operation has also been checked on macOS, including baseline
functional SVG font substitution. Linux coverage remains limited, and exact SVG
font metrics may still vary by installed system fonts and local fallback
behavior.

---

# altium-monkey 2026.05.18 Release Notes

Package version: `2026.5.18`

`2026.05.18` is represented in Python package metadata as the PEP 440
canonical form `2026.5.18`.

This release is a focused parser and rendering follow-up after `2026.5.12`. It
also carries forward the parser, extraction, rendering, and
deterministic-output fixes from that release.

## Bug Fixes

### PCB SVG skips text records with no drawable glyph geometry

PCB text records whose resolved glyphs produce no drawable geometry now emit no
SVG path output. This prevents empty or placeholder text geometry from
appearing when a font cannot provide visible outlines for a record.

### Fixed-width PCB UTF-16 text fields decode safely

Fixed-width UTF-16-LE PCB text fields now decode through a safer path that
handles truncated or partially populated buffers defensively. This improves
parsing robustness for board records that store text in fixed binary fields.

### PcbLib footprint regions handle extended-vertex records

Some footprint `Data` streams store shape-based region geometry under the
standard `REGION` record discriminator while using the extended, arc-capable
vertex layout. PcbLib extraction now detects that layout and preserves the
shape-based region geometry instead of treating the payload as a simple region.

### PCB metadata follows Windows-1252 text semantics

PcbDoc and PcbLib pipe-text metadata now uses Windows-1252 encoding and
decoding to match Altium's native serializer. This fixes footprint extraction
and library authoring for real-world files that contain Windows-1252
punctuation bytes such as `0x96` in footprint descriptions.

The shared fix covers length-prefixed PCB text streams, PcbDoc board and record
metadata, PcbLib footprint parameters, `ComponentParamsTOC`, `SectionKeys`,
`Library/Data`, and footprint catalog names. Invalid source bytes are decoded
with replacement, and write paths replace characters that cannot be represented
in Windows-1252.

### Schematic rendering handles template-owned parent-bound records

`SchDoc.to_geometry()` and `SchDoc.to_svg()` no longer crash when a template
contains parent-bound harness entries or sheet entries. These records are
positioned through their parent harness connector or sheet symbol, so the
generic template-child rendering path now skips them defensively instead of
calling their geometry methods without parent context.

### Schematic rendering respects component display modes

Schematic rendering now filters component body and child primitives by the
active Altium display mode. Multi-mode components no longer render inactive
mode graphics on top of the selected mode.

### Schematic image rendering uses stable runtime image keys

Image records without a stored `UniqueID` now get stable runtime image keys
during geometry and SVG rendering. This prevents collisions when multiple image
records are present and keeps generated image href maps aligned with rendered
geometry. The image pipeline also has a more stable PNG path for background
color to alpha conversion.

### Schematic symbol extraction preserves designators

`altium_schdoc_symbol_extractor` now preserves designator text when extracting
symbol definitions from placed schematic components. Extracted symbols restore
placed designators to their library-style prefix form, such as `R?` or `U?`,
instead of dropping the designator during conversion.

### Design and netlist JSON output is more deterministic

Design JSON, netlist, and pick-and-place related output now uses stronger
sorting and de-duplication for projects, components, variants, graphical
references, terminals, aliases, endpoints, hierarchy paths, and PNP parameter
maps. This reduces output jitter between runs and makes downstream diffs more
stable.

### SchLib preview parity improvements

SchLib bounds, geometry, and SVG helpers now support display-mode selection for
symbols with alternate graphics. SchLib SVG rendering also has an optional
`pin_text_follows_orientation` mode for editor-style symbol previews, and empty
symbol weighting is aligned with the canonical baseline used by the package.

## Public API Compatibility

Existing documented APIs remain compatible. The release adds optional keyword
arguments for SchLib display-mode and pin-text preview behavior, so existing
callers keep the previous defaults.

Exact serialized ordering for design JSON, netlist, and PNP data may change in
golden-file tests because output ordering is now more deterministic. PCB text
metadata now normalizes Windows-1252 byte streams to Unicode strings on read and
serializes those fields as Windows-1252 on write.

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Mutation API

The PcbDoc API is currently focused on parsing, extraction, rendering, and
targeted authoring helpers.

Known gaps:

1. There is no generic `ObjectCollection`-style query API for PcbDoc yet.
2. There is no public PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

The intended direction for a follow-up release is to bring the PcbDoc mutation
surface closer to the SchDoc/SchLib object model.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.
3. Continue source extraction when component cross-reference metadata is
   malformed but embedded source streams are still present.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.
3. Recover semantic component/model metadata when the source IntLib's
   cross-reference stream cannot be parsed.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Project variant support includes `ProjectVariantN` parsing, current-variant
selection, DNP/not-fitted designator lists, raw variation rows, variant-level
parameter rows, per-designator `ParamVariation` parameter overrides, and
variant metadata in design JSON.

`AltiumDesign.to_bom(variant=...)` applies parameter overrides to component
parameter maps, display values, and descriptions while retaining DNP rows with a
`dnp` flag. `AltiumDesign.to_pnp(variant=...)` omits DNP placements for the
selected variant. Native BOM and PNP CLI output is checked against the Python
variant behavior.

Alternate fitted component rows are preserved in raw variant metadata but are
not applied as semantic component replacements in BOM, netlist, PNP, or SVG
output yet. Variant-aware schematic SVG presentation is also outside the core
public API for this release.

### Platform Coverage

Primary release validation remains on Windows.

Basic package operation has also been checked on macOS, including baseline
functional SVG font substitution. Linux coverage remains limited, and exact SVG
font metrics may still vary by installed system fonts and local fallback
behavior.

---

# altium-monkey 2026.05.12 Release Notes

Package version: `2026.5.12`

`2026.05.12` is represented in Python package metadata as the PEP 440
canonical form `2026.5.12`.

This release focuses on parser, extraction, rendering, and deterministic-output
fixes that landed after `2026.5.8`.

## Bug Fixes

### PCB metadata follows Windows-1252 text semantics

PcbDoc and PcbLib pipe-text metadata now uses Windows-1252 encoding and
decoding to match Altium's native serializer. This fixes footprint extraction
and library authoring for real-world files that contain Windows-1252
punctuation bytes such as `0x96` in footprint descriptions.

The shared fix covers length-prefixed PCB text streams, PcbDoc board and record
metadata, PcbLib footprint parameters, `ComponentParamsTOC`, `SectionKeys`,
`Library/Data`, and footprint catalog names. Invalid source bytes are decoded
with replacement, and write paths replace characters that cannot be represented
in Windows-1252.

### Schematic rendering handles template-owned parent-bound records

`SchDoc.to_geometry()` and `SchDoc.to_svg()` no longer crash when a template
contains parent-bound harness entries or sheet entries. These records are
positioned through their parent harness connector or sheet symbol, so the
generic template-child rendering path now skips them defensively instead of
calling their geometry methods without parent context.

### Schematic rendering respects component display modes

Schematic rendering now filters component body and child primitives by the
active Altium display mode. Multi-mode components no longer render inactive
mode graphics on top of the selected mode.

### Schematic image rendering uses stable runtime image keys

Image records without a stored `UniqueID` now get stable runtime image keys
during geometry and SVG rendering. This prevents collisions when multiple image
records are present and keeps generated image href maps aligned with rendered
geometry. The image pipeline also has a more stable PNG path for background
color to alpha conversion.

### Schematic symbol extraction preserves designators

`altium_schdoc_symbol_extractor` now preserves designator text when extracting
symbol definitions from placed schematic components. Extracted symbols restore
placed designators to their library-style prefix form, such as `R?` or `U?`,
instead of dropping the designator during conversion.

### Design and netlist JSON output is more deterministic

Design JSON, netlist, and pick-and-place related output now uses stronger
sorting and de-duplication for projects, components, variants, graphical
references, terminals, aliases, endpoints, hierarchy paths, and PNP parameter
maps. This reduces output jitter between runs and makes downstream diffs more
stable.

### SchLib preview parity improvements

SchLib bounds, geometry, and SVG helpers now support display-mode selection for
symbols with alternate graphics. SchLib SVG rendering also has an optional
`pin_text_follows_orientation` mode for editor-style symbol previews, and empty
symbol weighting is aligned with the canonical baseline used by the package.

## Public API Compatibility

Existing documented APIs remain compatible. The release adds optional keyword
arguments for SchLib display-mode and pin-text preview behavior, so existing
callers keep the previous defaults.

Exact serialized ordering for design JSON, netlist, and PNP data may change in
golden-file tests because output ordering is now more deterministic. PCB text
metadata now normalizes Windows-1252 byte streams to Unicode strings on read and
serializes those fields as Windows-1252 on write.

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Mutation API

The PcbDoc API is currently focused on parsing, extraction, rendering, and
targeted authoring helpers.

Known gaps:

1. There is no generic `ObjectCollection`-style query API for PcbDoc yet.
2. There is no public PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

The intended direction for a follow-up release is to bring the PcbDoc mutation
surface closer to the SchDoc/SchLib object model.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.
3. Continue source extraction when component cross-reference metadata is
   malformed but embedded source streams are still present.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.
3. Recover semantic component/model metadata when the source IntLib's
   cross-reference stream cannot be parsed.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Project variant support includes `ProjectVariantN` parsing, current-variant
selection, DNP/not-fitted designator lists, raw variation rows, variant-level
parameter rows, per-designator `ParamVariation` parameter overrides, and
variant metadata in design JSON.

`AltiumDesign.to_bom(variant=...)` applies parameter overrides to component
parameter maps, display values, and descriptions while retaining DNP rows with a
`dnp` flag. `AltiumDesign.to_pnp(variant=...)` omits DNP placements for the
selected variant. Native BOM and PNP CLI output is checked against the Python
variant behavior.

Alternate fitted component rows are preserved in raw variant metadata but are
not applied as semantic component replacements in BOM, netlist, PNP, or SVG
output yet. Variant-aware schematic SVG presentation is also outside the core
public API for this release.

### Platform Coverage

Primary release validation remains on Windows.

Basic package operation has also been checked on macOS, including baseline
functional SVG font substitution. Linux coverage remains limited, and exact SVG
font metrics may still vary by installed system fonts and local fallback
behavior.

---

# altium-monkey 2026.05.08 Release Notes

Package version: `2026.5.8`

`2026.05.08` is represented in Python package metadata as the PEP 440
canonical form `2026.5.8`.

## Bug Fixes

IntLib source extraction is more tolerant of vendor-generated integrated
libraries with malformed `LibCrossRef.Txt` component metadata. `AltiumIntLib`
now records the cross-reference parse failure on `component_parse_error` and
continues to discover extractable `.SchLib`, `.PcbLib`, and `.PCB3DLib` source
streams by scanning the OLE stream tree.

PCB SVG rendering now keeps unlinked copper regions in the normal copper layer
color. This improves previews for vendor custom pad shapes that arrive as
unlinked `ShapeBasedRegion` or region primitives. Linked polygon pours still use
the configured polygon overlay color.

## Documentation

The public docs now include an IntLib guide covering source extraction,
metadata fallback behavior, and the extract-only support boundary.

## Public API Compatibility

The `AltiumIntLib.component_parse_error` property is additive. Existing IntLib
code that reads `components`, `get_source_entries()`, `read_stream(...)`, or
`extract_sources(...)` should continue to work.

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Mutation API

The PcbDoc API is currently focused on parsing, extraction, rendering, and
targeted authoring helpers.

Known gaps:

1. There is no generic `ObjectCollection`-style query API for PcbDoc yet.
2. There is no public PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

The intended direction for a follow-up release is to bring the PcbDoc mutation
surface closer to the SchDoc/SchLib object model.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.
3. Continue source extraction when component cross-reference metadata is
   malformed but embedded source streams are still present.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.
3. Recover semantic component/model metadata when the source IntLib's
   cross-reference stream cannot be parsed.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Variant processing includes DNP handling and parameter overrides for this
release.

Other variant behaviors, such as alternate fitted components and variant-aware
SVG presentation, are not part of the core public API yet.

### Platform Coverage

Primary release validation has been on Windows.

Linux and macOS testing is minimal for this release. The SVG font substitution
path may need additional platform-specific validation because available system
fonts and font fallback behavior vary by machine.

---

# altium-monkey 2026.05.07 Release Notes

Package version: `2026.5.7`

`2026.05.07` is represented in Python package metadata as the PEP 440
canonical form `2026.5.7`.

## Bug Fixes

IntLib source extraction is more tolerant of vendor-generated integrated
libraries with malformed `LibCrossRef.Txt` component metadata. `AltiumIntLib`
now records the cross-reference parse failure on `component_parse_error` and
continues to discover extractable `.SchLib`, `.PcbLib`, and `.PCB3DLib` source
streams by scanning the OLE stream tree.

PCB SVG rendering now keeps unlinked copper regions in the normal copper layer
color. This improves previews for vendor custom pad shapes that arrive as
unlinked `ShapeBasedRegion` or region primitives. Linked polygon pours still use
the configured polygon overlay color.

## Documentation

The public docs now include an IntLib guide covering source extraction,
metadata fallback behavior, and the extract-only support boundary.

Maintainer packaging notes were tightened without changing runtime APIs.

## Public API Compatibility

The `AltiumIntLib.component_parse_error` property is additive. Existing IntLib
code that reads `components`, `get_source_entries()`, `read_stream(...)`, or
`extract_sources(...)` should continue to work.

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Mutation API

The PcbDoc API is currently focused on parsing, extraction, rendering, and
targeted authoring helpers.

Known gaps:

1. There is no generic `ObjectCollection`-style query API for PcbDoc yet.
2. There is no public PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

The intended direction for a follow-up release is to bring the PcbDoc mutation
surface closer to the SchDoc/SchLib object model.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.
3. Continue source extraction when component cross-reference metadata is
   malformed but embedded source streams are still present.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.
3. Recover semantic component/model metadata when the source IntLib's
   cross-reference stream cannot be parsed.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Variant processing includes DNP handling and parameter overrides for this
release.

Other variant behaviors, such as alternate fitted components and variant-aware
SVG presentation, are not part of the core public API yet.

### Platform Coverage

Primary release validation has been on Windows.

Linux and macOS testing is minimal for this release. The SVG font substitution
path may need additional platform-specific validation because available system
fonts and font fallback behavior vary by machine.

---

# altium-monkey 2026.04.28 Release Notes

Package version: `2026.4.28`

`2026.04.28` is represented in Python package metadata as the PEP 440
canonical form `2026.4.28`.

## Bug Fixes

Schematic sheet-symbol child labels now parse and preserve `IsHidden` records.

This fixes hidden sheet names being emitted into schematic IR/SVG output when
an Altium `SHEET_NAME` child record persisted `IsHidden=T`. `FILE_NAME` child
records also preserve explicit `IsHidden` state during parse and serialization.

The fix is intentionally narrow: base schematic labels continue to drop stale
runtime-only hidden state, while sheet-symbol `SHEET_NAME` and `FILE_NAME`
records keep the persisted visibility flag that Altium stores on those child
records.

## Changed Examples

The dynamic template example now relies on the generated `.SchDot` for visual
sheet setup, uses the exported `SheetStyle` enum, and applies templates with
`apply_visual_sheet_settings=True`.

## Documentation

The README and docs index wording were refreshed to describe ongoing Linux and
macOS coverage boundaries and current example-maintenance expectations.

## Public API Compatibility

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Mutation API

The PcbDoc API is currently focused on parsing, extraction, rendering, and
targeted authoring helpers.

Known gaps:

1. There is no generic `ObjectCollection`-style query API for PcbDoc yet.
2. There is no public PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

The intended direction for a follow-up release is to bring the PcbDoc mutation
surface closer to the SchDoc/SchLib object model.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Variant processing includes DNP handling and parameter overrides for this
release.

Other variant behaviors, such as alternate fitted components and variant-aware
SVG presentation, are not part of the core public API yet.

### Platform Coverage

Primary release validation has been on Windows.

Linux and macOS testing is minimal for this release. The SVG font substitution
path may need additional platform-specific validation because available system
fonts and font fallback behavior vary by machine.

---

# altium-monkey 2026.04.27 Release Notes

Package version: `2026.4.27`

`2026.04.27` is represented in Python package metadata as the PEP 440
canonical form `2026.4.27`.

## Additions

`AltiumDesign.to_json()` now emits `altium_monkey.design.a1`.

The `a1` design payload adds schematic hierarchy data for downstream
visualizers and project analysis tools. The new root `schematic_hierarchy`
block includes:

1. resolved source and compiled sheet documents
2. sheet-symbol to child-sheet relationships
3. hierarchy paths for repeated-channel and nested designs
4. channel metadata, including repeat context when present
5. sheet-entry to child-port links
6. harness bundle links for flat and hierarchical harness traces
7. unresolved hierarchy diagnostics

Compiled net records now include source-owned semantic `endpoints` for
schematic trace and overlay tools. Endpoint records describe pins, ports,
sheet entries, power ports, and related electrical hotspots without requiring
downstream tools to infer connectivity from rendered SVG IDs or label text.

Project variants now expose variant parameter rows, per-designator parameter
variation rows, and a normalized `parameter_overrides` map. BOM generation uses
those overrides when resolving displayed component values.

Schematic component records expose display-body and full-body bounds helpers.
These are intended for renderers and hit-testers that need component body
geometry without treating pins as part of the display body.

`AltiumSchDoc.apply_template()` now accepts
`apply_visual_sheet_settings=True`.

Use this when a `.SchDot` should control the target schematic's visual page
setup, not just its template-owned drawing objects.

When enabled, the target sheet inherits these fields from the template sheet:

1. sheet style and custom sheet dimensions
2. custom zone and margin geometry
3. border, title-block, and reference-zone visibility
4. reference-zone style
5. document border style and workspace orientation
6. persisted display unit
7. snap, visible, and hot-spot grid settings
8. sheet line and area colors
9. sheet-number spacing
10. sheet system font, remapped into the target document font table

The package root now exports these schematic sheet enums:

1. `SheetStyle`
2. `DocumentBorderStyle`
3. `WorkspaceOrientation`

## Compatibility

`altium_monkey.design.a1` preserves the existing `a` family design payload
shape and adds hierarchy/variant data. Existing consumers that require the
exact `altium_monkey.design.a0` schema string should update their schema checks
before consuming this release.

`apply_visual_sheet_settings` defaults to `False`. Existing callers that
already configure the target sheet before applying a template keep the previous
behavior.

Template identity and document identity state are still target-owned. The new
visual sheet copy path does not copy template filename metadata, vault/release
GUIDs, file identity, sheet number, or project/page parameters.

## Changed Examples

The dynamic template examples now use the generated `.SchDot` as the source of
sheet context instead of duplicating sheet setup on the target document.

`schdoc_apply_dynamic_template` now:

1. builds generated ANSI B and ANSI D `.SchDot` templates
2. applies each template with `apply_visual_sheet_settings=True`
3. uses the exported `SheetStyle` enum instead of raw sheet-style integers

`prjpcb_make_project` now:

1. starts from a new `AltiumSchDoc()` instead of a shared blank SchDoc input
2. applies its generated D-size `.SchDot` with
   `apply_visual_sheet_settings=True`
3. writes a generated project named `ULTRA-MONKEY`
4. uses a grid-based title block with project and document parameter
   expressions
5. publishes only the schematic PDF through the OutJob publish medium
6. keeps fabrication, assembly, netlist, BOM, and STEP outputs in the
   generated-files medium

## Public API Compatibility

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Mutation API

The PcbDoc API is currently focused on parsing, extraction, rendering, and
targeted authoring helpers.

Known gaps:

1. There is no generic `ObjectCollection`-style query API for PcbDoc yet.
2. There is no public PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

The intended direction for a follow-up release is to bring the PcbDoc mutation
surface closer to the SchDoc/SchLib object model.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Variant processing includes DNP handling and parameter overrides for this
release.

Other variant behaviors, such as alternate fitted components and variant-aware
SVG presentation, are not part of the core public API yet.

### Platform Coverage

Primary release validation has been on Windows.

Linux and macOS testing is minimal for this release. The SVG font substitution
path may need additional platform-specific validation because available system
fonts and font fallback behavior vary by machine.

---

# altium-monkey 2026.04.19 Release Notes

Package version: `2026.4.19`

`2026.04.19` is represented in Python package metadata as the PEP 440
canonical form `2026.4.19`.

### Additions

`AltiumSchDoc.apply_template()` now accepts
`apply_visual_sheet_settings=True`.

Use this when a `.SchDot` should control the target schematic's visual page
setup, not just its template-owned drawing objects.

When enabled, the target sheet inherits these fields from the template sheet:

1. sheet style and custom sheet dimensions;
2. custom zone and margin geometry;
3. border, title-block, and reference-zone visibility;
4. reference-zone style;
5. document border style and workspace orientation;
6. persisted display unit;
7. snap, visible, and hot-spot grid settings;
8. sheet line and area colors;
9. sheet-number spacing;
10. sheet system font, remapped into the target document font table.

The package root now exports these schematic sheet enums:

1. `SheetStyle`
2. `DocumentBorderStyle`
3. `WorkspaceOrientation`

### Compatibility

`apply_visual_sheet_settings` defaults to `False`. Existing callers that
already configure the target sheet before applying a template keep the previous
behavior.

Template identity and document identity state are still target-owned. The new
visual sheet copy path does not copy template filename metadata, vault/release
GUIDs, file identity, sheet number, or project/page parameters.

### Changed Examples

The dynamic template examples now use the generated `.SchDot` as the source of
sheet context instead of duplicating sheet setup on the target document.

`schdoc_apply_dynamic_template` now:

1. builds generated ANSI B and ANSI D `.SchDot` templates;
2. applies each template with `apply_visual_sheet_settings=True`;
3. uses the exported `SheetStyle` enum instead of raw sheet-style integers.

`prjpcb_make_project` now:

1. starts from a new `AltiumSchDoc()` instead of a shared blank SchDoc input;
2. applies its generated D-size `.SchDot` with
   `apply_visual_sheet_settings=True`;
3. writes a generated project named `ultra-monkey`;
4. uses a grid-based title block with project and document parameter
   expressions;
5. publishes only the schematic PDF through the OutJob publish medium;
6. keeps fabrication, assembly, netlist, BOM, and STEP outputs in the
   generated-files medium.

---

# altium-monkey 2026.04.15 Release Notes

Package version: `2026.4.15`

`2026.04.15` is the first published release target. Python package metadata uses
the PEP 440 canonical form `2026.4.15`.

## Public API Compatibility

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

## Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated on Python 3.13.

## Functional Gaps

### PcbDoc Mutation API

The PcbDoc API is currently focused on parsing, extraction, rendering, and
targeted authoring helpers.

Known gaps:

1. There is no generic `ObjectCollection`-style query API for PcbDoc yet.
2. There is no public PcbDoc object deletion API yet.
3. Existing PcbDoc mutations outside the high-level helper methods generally
   require direct record-list edits. Treat those edits as advanced usage and
   validate outputs in Altium Designer.

The intended direction for a follow-up release is to bring the PcbDoc mutation
surface closer to the SchDoc/SchLib object model.

### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.

### Hierarchical Designs And Annotation Files

Complex hierarchical sheets, multi-channel designs, and designator resolution
may have edge cases in `altium_design.py`.

Altium Designer can store board-level annotation changes in `*.Annotation`
files for cases such as device sheets and multi-channel designs. This release
does not process those annotation files. Designs that depend on annotation-file
mapping may need additional validation.

Reference:

https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components#component-linking-with-unique-ids

Please file an issue with a minimal reproducible project if you find a
hierarchical design or annotation-resolution case that is not represented
correctly.

### Variant Processing

Variant processing is limited to DNP handling for this release.

Other variant behaviors, such as alternate fitted components, parameter
overrides, and variant-aware SVG presentation, are not part of the core public
API yet.

### Platform Coverage

Primary release validation has been on Windows.

Linux and macOS testing is minimal for this release. The SVG font substitution
path may need additional platform-specific validation because available system
fonts and font fallback behavior vary by machine.
