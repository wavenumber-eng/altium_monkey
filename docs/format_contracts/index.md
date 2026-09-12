# Format Contracts

These pages define the file, API, and rendering contracts that altium-monkey
intends downstream users to rely on. They are higher level than the detailed
binary notes under `docs/canonical_format/` and are written so the same text can
ship with the public package docs.

The contracts cover stable behavior, known boundaries, and the test gates that
protect the behavior. Detailed record layout notes, reverse-engineering logs,
and case-specific discoveries stay in canonical-format or historical plan docs.
For PCB primitive authoring and rendering, the public PCB layers guide defines
the distinction between legacy `PcbLayer` values, V7 saved layer ids, stack
rows, and mechanical-kind metadata.

## Contracts

1. [SchDoc](schdoc.md)
2. [SchLib](schlib.md)
3. [Schematic JSON interoperability](schematic_interop.md)
4. [PcbDoc](pcbdoc.md)
5. [PcbLib](pcblib.md)
6. [PrjPcb](prjpcb.md)
7. [AltiumDesign](altium_design.md)
8. [IntLib](intlib.md)
9. [SVG](svg.md)
10. [Draftsman](draftsman.md)

## Publication Rule

These contract files are copied into the released documentation during release
preparation. The release signoff check fails if the shipped contract copies
drift from this source set.
