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

Common workflows:

1. create and mutate schematic documents
2. create schematic symbols and PCB footprints
3. insert SchLib symbols and PcbLib footprints
4. extract SchLib and PcbLib data from projects
5. render schematic and PCB SVGs
6. inspect PCB layers, drills, board outlines, nets, and net classes
7. extract embedded fonts and 3D models
8. generate project containers and run associated OutJobs

## Install

Python 3.11 and Python 3.12 are supported for this release.

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
See [RELEASE_NOTES.md](RELEASE_NOTES.md) for platform and Python-version
boundaries.

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
7. [API patterns](docs/api_patterns/index.md)
8. [Schema contracts](docs/schemas/index.md)
9. [Examples](docs/examples/index.md)

The examples are the best starting point for public API usage. They are kept in
[`examples/`](examples/) and are indexed from `examples/manifest.toml`.

## API Shape

The schematic side uses a higher-level object system:

1. `AltiumSchDoc` and `AltiumSymbol` own `ObjectCollection` instances.
2. Typed views such as `schdoc.notes` and `symbol.pins` are live query views.
3. Structural mutations should go through `add_object(...)`,
   `insert_object(...)`, or `remove_object(...)`.

The PCB side is currently helper-oriented:

1. `AltiumPcbDoc` and `AltiumPcbFootprint` expose high-level `add_*` methods.
2. Parsed primitives are available through typed record lists.
3. Direct record-list mutation is possible but should be treated as advanced
   usage until the PcbDoc object API is expanded.

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

1. PcbDoc does not yet have a generic `ObjectCollection`-style mutation API.
2. PcbDoc does not yet have a public object deletion API.
3. IntLib support is extract-only.
4. Variant processing is limited to DNP handling.
5. Complex hierarchical channels and `.Annotation` file handling may need
   additional validation.
6. Linux and macOS coverage is still minimal.

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the full current support boundary.

## License

`altium-monkey` is licensed under the GNU Affero General Public License v3.0 or
later. See [LICENSE](LICENSE).
