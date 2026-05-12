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
