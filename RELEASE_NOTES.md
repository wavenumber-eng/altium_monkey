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
validated through the full release pipeline on Python 3.13.

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
