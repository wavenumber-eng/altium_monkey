# altium-monkey

```text
          ▓▓▓▓▓▓▓▓▓▓
        ▓▓▓▓▓▓▓▓▓▓▓▓▓▓
      ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    ▓▓▓▓░░░░░░▓▓░░░░░░▓▓▓▓
░░░░▓▓░░░░░░░░░░░░░░░░░░▓▓░░░░
░░░░▓▓░░    ░░░░░░    ░░▓▓░░░░
  ░░▓▓░░██  ░░░░░░██  ░░▓▓░░
    ▓▓░░░░░░░░░░░░░░░░░░▓▓
      ▓▓░░░░░░░░░░░░░░▓▓
        ▓▓▓▓░░░░░░▓▓▓▓
            ▓▓▓▓▓▓        ░░
          ▓▓▓▓▓▓▓▓▓▓      ▓▓
          ▓▓▓▓▓▓▓▓▓▓    ▓▓▓▓
        ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
        ▓▓▓▓░░▓▓░░▓▓▓▓
```

`altium-monkey` is a Python toolkit for reading, writing, analyzing, and
rendering Altium files directly from automation.

It is designed for engineers who want to build their own command-line tools,
CI/CD checks, visualization pipelines, library generators, BOM workflows, and
design-review helpers without driving the Altium GUI for every operation.

## What It Supports

Core file types:

1. `.SchDoc`
2. `.SchLib`
3. `.PcbDoc`
4. `.PcbLib`
5. `.PrjPcb`
6. `.OutJob`
7. `.IntLib` extraction
8. `.PCBDwf` Draftsman files, currently experimental

Common workflows:

1. create and mutate schematic documents
2. create schematic symbols and PCB footprints
3. insert SchLib symbols and PcbLib footprints
4. extract SchLib and PcbLib data from projects
5. render logical schematic SVGs, compiled physical schematic SVGs, and PCB SVGs
6. inspect PCB layers, drills, board outlines, nets, and net classes
7. author and mutate PCB vias, including IPC-4761 protection metadata
8. extract embedded fonts and 3D models
9. generate project containers and run associated OutJobs
10. create experimental Draftsman pages with notes, text, pictures, and
    generated board-assembly-view highlight artwork

## Install

Normal GIL-enabled CPython 3.12 through Python 3.14 are supported. Free-threaded
Python builds are not currently part of the support contract.

```powershell
pip install altium-monkey
```

or with `uv`:

```powershell
uv add altium-monkey
```

For running the examples, prefer `uv run ...`. It is the highest-probability
path for using the expected interpreter and dependencies without local
environment drift.

The package includes dependencies for SVG text shaping and STEP-model bounds.
STEP bounds use `wn-geometer`, with published wheels currently available for
Windows amd64, macOS arm64, and Linux x86_64 tagged `manylinux_2_39`. See
[RELEASE_NOTES.md](RELEASE_NOTES.md) for platform and Python-version
boundaries. The CadQuery dependency is only needed for the public example that
synthesizes new STEP models.

## Public API Compatibility

We strive to maintain compatibility for documented public APIs between
releases. The API surface may still change as more Altium capabilities are
modeled, especially in areas that are currently marked as release boundaries or
advanced usage. Compatibility-affecting changes and migration notes will be
documented in release notes.

## Quick Start

Parse a project and emit the public design JSON contract:

```python
from altium_monkey import AltiumDesign

design = AltiumDesign.from_prjpcb("example.PrjPcb")
payload = design.to_json()
```

Create or modify a schematic, then save it:

```python
from altium_monkey import AltiumSchDoc, SchFontSpec, SchRectMils, make_sch_note

schdoc = AltiumSchDoc("input.SchDoc")
note = make_sch_note(
    bounds_mils=SchRectMils.from_corners_mils(1000, 3000, 2600, 2400),
    text="Added by altium-monkey",
    font=SchFontSpec(name="Arial", size=10),
)
schdoc.add_object(note)
schdoc.save("output.SchDoc")
```

Create a simple PCB primitive:

```python
from altium_monkey import AltiumPcbDoc, PcbLayer

pcbdoc = AltiumPcbDoc()
pcbdoc.add_track(
    (1000, 1000),
    (2500, 1000),
    width_mils=8,
    layer=PcbLayer.TOP,
    net="GND",
)
pcbdoc.save("output.PcbDoc")
```

## Documentation

The public docs are Markdown-first for this release:

1. [SchDoc](docs/schdoc.md)
2. [SchLib](docs/schlib.md)
3. [PcbDoc](docs/pcbdoc.md)
4. [PcbLib](docs/pcblib.md)
5. [PrjPcb](docs/prjpcb.md)
6. [AltiumDesign](docs/altium_design.md)
7. [IntLib](docs/intlib.md)
8. [API patterns](docs/api_patterns/index.md)
9. [Schema contracts](docs/schemas/index.md)
10. [Format contracts](docs/format_contracts/index.md)
11. [Docs style foundation](docs/style.md)
12. [Examples](docs/examples/index.md)

The examples are the best starting point for public API usage. They are kept in
[`examples/`](examples/) and are indexed from `examples/manifest.toml`.

## Schematic SVG Fonts

Schematic SVG rendering uses installed fonts when it can resolve the requested
Altium font family. On macOS, the resolver searches the standard system font
locations, including Supplemental fonts. Callers can also set
`ALTIUM_FONT_DIRS` to one or more additional font directories.

When a common Altium/Windows family is unavailable, schematic rendering can use
bundled open-source fallback fonts. Arial and Microsoft Sans Serif-style
families substitute Arimo, Times New Roman-style families substitute Tinos, and
Courier New or monospace families substitute Cousine. SVG output embeds bundled
fallback faces when they are used so browser rendering follows the same metrics
used to place text.

gotIR carries font-resolution diagnostics for substitutions and fallbacks so
downstream tools can surface a warning instead of silently using a hard
default.

## Contributing

This repository is a published mirror. Issues, minimal reproduction cases,
documentation fixes, API feedback, and focused pull requests are welcome, but
PRs may be adapted or reimplemented in the upstream development workspace before
they are mirrored back here. See [CONTRIBUTING.md](CONTRIBUTING.md).

## API Shape

The schematic side uses a higher-level object system:

1. `AltiumSchDoc` and `AltiumSymbol` own `ObjectCollection` instances.
2. Typed views such as `schdoc.notes` and `symbol.pins` are live query views.
3. Structural mutations should go through `add_object(...)`,
   `insert_object(...)`, or `remove_object(...)`.

The PCB document side is helper-oriented rather than `ObjectCollection`-based:

1. `AltiumPcbDoc` and `AltiumPcbFootprint` expose high-level `add_*` methods.
2. `AltiumPcbDoc` covers common authoring workflows including board setup,
   nets, primitives, components, footprint insertion, component bodies, and
   embedded 3D model placement. Via support includes IPC-4761 type metadata,
   feature rows, propagation delay, tenting, and testpoint flags.
3. Parsed primitives are available through typed record lists such as
   `pcbdoc.tracks`, `pcbdoc.pads`, `pcbdoc.vias`, and `pcbdoc.components`.
4. Direct record-list mutation is possible but should be treated as advanced
   usage until PcbDoc grows a generic object API.

See [API patterns](docs/api_patterns/index.md) for units, object ownership,
public vs careful APIs, and internal Altium unit guidance.

## Testing And Interoperability

`altium-monkey` is developed against a large private corpus and
real-world Altium files spanning multiple Altium eras from "Summer '08" until present day. Interoperability checks include round-trip parsing, binary serialization, SVG rendering, and native
Altium oracle comparisons where practical. The test corpus is not included in the public package.

No tool can prove perfect compatibility with every historical Altium file. If
you find a parsing, serialization, SVG, or interoperability issue, please file
an issue with the smallest representative `.SchDoc`, `.SchLib`, `.PcbDoc`, or
`.PcbLib` that reproduces the problem.

## Release Boundaries

Known release boundaries include:

1. PcbDoc does not yet use `ObjectCollection`; it remains a typed-list plus
   helper-oriented API.
2. PcbDoc does not yet have a public generic object deletion API.
3. IntLib support is extract-only, with fallback source-stream extraction when
   component cross-reference metadata cannot be parsed.
4. Variant processing supports DNP handling and parameter overrides; alternate
   fitted component replacement is not applied semantically yet.
5. Complex hierarchical channels route through the compiled design model for
   design JSON, netlist JSON, and physical schematic SVG/IR output. Rich
   consumers should use the required Design b0 `compiled_schematic_graph` and
   select pages by canonical page occurrence id instead of assuming one source
   SVG ID maps to one physical component.
6. Project design JSON emits `altium_monkey.design.b0`. Strict validators
   pinned to Design a2 should refresh to `design_b0.schema.json`; Design b0
   requires `altium_monkey.compiled_schematic_graph.a0` and intentionally does
   not emit the duplicated Design a2 `physical_pages` projection. Compile
   metadata and diagnostics remain opt-in through
   `to_json(include_compile_metadata=True)`.
7. Windows remains the primary validation platform. macOS font discovery and
   bundled schematic font substitution have focused coverage; Linux coverage
   remains limited and may rely more heavily on bundled substitutions.

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the full current support boundary.

## License

`altium-monkey` is licensed under the GNU Affero General Public License v3.0 or
later. See [LICENSE](LICENSE).
