# altium-monkey

```text
..........▓▓▓▓▓▓▓▓▓▓..............
........▓▓▓▓▓▓▓▓▓▓▓▓▓▓............
......▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓..........
....▓▓▓▓░░░░░░▓▓░░░░░░▓▓▓▓........
░░░░▓▓░░░░░░░░░░░░░░░░░░▓▓░░░░....
░░░░▓▓░░....░░░░░░....░░▓▓░░░░....
..░░▓▓░░██..░░░░░░██..░░▓▓░░......
....▓▓░░░░░░░░░░░░░░░░░░▓▓........
......▓▓░░░░░░░░░░░░░░▓▓..........
........▓▓▓▓░░░░░░▓▓▓▓............
............▓▓▓▓▓▓..........░░....
..........▓▓▓▓▓▓▓▓▓▓......▓▓......
..........▓▓▓▓▓▓▓▓▓▓....▓▓▓▓......
........▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓........
........▓▓▓▓░░▓▓░░▓▓▓▓............
```

`ALTIUM MONKEY`

This is the documentation entry point for `altium-monkey`.

The docs are intended to be:

1. capability-first
2. example-heavy
3. easy for humans to scan
4. easy for LLM tooling to parse

Near-term focus:

1. keep the release/export flow simple
2. ship a first small example set
3. keep the public Markdown documentation accurate and easy to scan

Domain guides:

1. [SchDoc](schdoc.md)
2. [SchLib](schlib.md)
3. [PcbDoc](pcbdoc.md)
4. [PcbLib](pcblib.md)
5. [PrjPcb](prjpcb.md)
6. [AltiumDesign](altium_design.md)

See the [examples index](examples/index.md) for the implemented sample set.

See the [schemas](schemas/index.md) page for the public JSON and SVG metadata
contracts emitted by `AltiumDesign`, `Netlist`, and PCB SVG rendering.

See the [API patterns](api_patterns/index.md) page for units, object mutation
patterns, and higher-level API conventions.

See the [release notes](../RELEASE_NOTES.md) for the current support boundary
and known functional gaps.
