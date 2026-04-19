# altium-monkey Release Notes

## 2026.04.19

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

## 2026.04.15

Package version: `2026.4.15`

`2026.04.15` was the first published release target. Python package metadata
uses the PEP 440 canonical form `2026.4.15`.

### Public API Compatibility

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas listed as known functional gaps. Compatibility
notes and migration guidance will be documented in release notes.

### Supported Python Versions

This release supports Python 3.11 and Python 3.12.

Python 3.13 is not advertised yet. The core package may work on Python 3.13, but
the CadQuery/OCCT/VTK dependency path used for STEP model bounds has not been
validated through the full release pipeline on Python 3.13.

### Functional Gaps

#### PcbDoc Mutation API

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

#### IntLib Support

Integrated libraries are extract-only in this release.

Supported:

1. Extract source files from an existing IntLib.
2. Split extracted SchLib/PcbLib files when they contain multiple symbols or
   footprints.

Not supported:

1. Compile or build a new IntLib.
2. Repackage modified sources back into an IntLib.

#### Hierarchical Designs And Annotation Files

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

#### Variant Processing

Variant processing is limited to DNP handling for this release.

Other variant behaviors, such as alternate fitted components, parameter
overrides, and variant-aware SVG presentation, are not part of the core public
API yet.

#### Platform Coverage

Primary release validation has been on Windows.

Linux and macOS testing is minimal for this release. The SVG font substitution
path may need additional platform-specific validation because available system
fonts and font fallback behavior vary by machine.
