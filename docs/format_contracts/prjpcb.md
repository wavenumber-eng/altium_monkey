# PrjPcb Contract

`AltiumPrjPcb` is the public model for Altium PCB project files.

## Stable Surface

- Parse existing `.PrjPcb` project files.
- Preserve raw project configuration when a setting does not yet have a typed
  property.
- Read and write project documents, variants, parameters, output-job links, and
  class-generation settings exposed by the public API.
- Create simple project containers programmatically.

## Configuration Model

Project files are INI-like configuration documents. Typed properties are public
convenience accessors over the preserved raw configuration. Unknown keys should
survive normal read/write flows.

## Document Paths

Altium stores project document paths with Windows-style separators. Relative
paths are resolved as nested host-filesystem paths on Windows, Linux, macOS,
and WSL, while their Altium-facing representation remains unchanged when the
project is written. This applies to schematic, PCB, output-job, and managed
device-sheet documents.

Reachability is matched by the normalized project-relative document path, not
only by the final filename. Projects may therefore contain identically named
sheets in different folders without one sheet being selected for another.
Managed `[DeviceSheetN]` sections retain that section identity during normal
read/write round trips and are not duplicated as `[DocumentN]` entries.

Drive-qualified and UNC paths are host-specific. They are usable on compatible
Windows hosts, but are not a portable cross-platform project-reference
contract; projects intended for multiple hosts should use relative paths.

## Design Integration

`AltiumDesign` uses `AltiumPrjPcb` as the project-level entry point for loading
schematic sheets, PCB documents, variants, compiled netlists, and project
metadata.

## Test Gates

The PrjPcb contract is covered by project parsing, project authoring, design
loading, public examples, and release signoff.
