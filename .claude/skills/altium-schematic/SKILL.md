---
name: altium-schematic
description: Read Altium schematics and projects (.PrjPcb / .SchDoc) so Claude can answer questions about the circuit — components, nets, pin-level connectivity, BOM, and sheet hierarchy. Use whenever the user asks about an Altium design in this workspace.
---

# altium-schematic

Lets Claude reason about Altium designs by calling a small Python helper backed
by [`altium-monkey`](https://github.com/wavenumber-eng/altium_monkey). The
helper exposes targeted subcommands so you load only the slice of design data
relevant to the user's question into context — not the whole 400KB+ design
JSON.

## When to use

Trigger this skill when the user asks anything about an Altium design in the
workspace, for example:

- "what's connected to U7 pin C9?"
- "list every IC on the codec sheet"
- "which nets cross between sheets?"
- "what's the BOM for the PCBA_Build variant?"
- "what does this schematic do?"

If you don't know which `.PrjPcb` they mean, run `find . -name '*.PrjPcb'` and
ask if there's more than one.

## Prerequisite

`altium-monkey` must be importable. In an `altium-monkey` repo clone the
provided `uv` env satisfies this. Elsewhere, install with `pip install
altium-monkey` or `uv add altium-monkey`. The helper invocation below uses
`uv run` so it picks up whichever env is active.

## How to invoke

All commands print JSON to stdout. Errors print `{"error": "..."}` to stderr
with exit code 1. Run from the repository root:

```bash
uv run --quiet python .claude/skills/altium-schematic/read_schematic.py <subcommand> [args]
```

### Recommended starting point: `summary`

Always run `summary` first when you encounter a project for the first time in
a session. It's small and gives you sheet names, component/net counts,
component-type breakdown, power nets, and variants — everything you need to
plan follow-up queries.

```bash
uv run --quiet python .claude/skills/altium-schematic/read_schematic.py summary path/to/Foo.PrjPcb
```

### Subcommands

| Subcommand    | What it returns                                              | Typical question it answers                |
| ------------- | ------------------------------------------------------------ | ------------------------------------------ |
| `summary`     | Project overview: sheets, counts, power nets, variants       | "What's in this project?"                  |
| `components`  | Filtered component list with `--designator`/`--sheet`/`--type`/`--value-contains`/`--brief` | "List the ICs on the regulator sheet."     |
| `nets`        | Net listing, or full terminal list with `--name NET`. Use `--contains` for substring match. | "What's on the SDA net?" / "Find P5V*."    |
| `connections` | Per-pin connectivity for `--designator U7` (optionally `--pin C9`) | "What's wired to U7 pin C9?"               |
| `bom`         | BOM, optionally `--variant <name>`                           | "Give me the PCBA_Build BOM."              |
| `sheet`       | Single `.SchDoc` inspection (no project needed)              | "What does this one sheet contain?"        |
| `raw design`  | Full design JSON (large — last resort)                       | "I need everything."                       |
| `raw netlist` | Full netlist JSON (large — last resort)                      | Bulk netlist analysis.                     |

### Notes

- **Pin identifiers can be alphanumeric** (`C9`, `D8`, `1`, `A1`). Pass them
  as strings to `--pin`.
- **Designators come from project-level annotation.** A bare `.SchDoc`
  inspected via `sheet` may show empty designators; use `summary`/`components`
  on the parent `.PrjPcb` instead.
- **Hierarchical designs work** — the netlist resolves nets across sheets,
  and `components` reports `sheet` per component so you can scope by sheet.
- **The summary's `power_and_ground_nets`** is heuristic (named nets touching
  POWER-type pins, plus GND/VSS substrings). Use `nets --contains P5V` etc.
  to find rails it missed.

## Strategy for circuit reasoning

For "explain what this circuit does" type questions, do not dump `raw design`.
Instead:

1. `summary` to learn sheet names and counts.
2. `components --sheet <name> --brief` per sheet to see what's on each one.
3. `nets` to see all named signals.
4. `connections --designator <main IC>` to see how the central part is wired.
5. Pull individual nets with `nets --name <X>` only when needed.

This keeps your context window small and your answers grounded in actual
design data rather than guesses from filenames.

## Example session

User: "What does the dongle do? It's in DONGLE_V2_RELEASED_DESIGN_FILES."

```bash
# 1. Orient yourself
uv run --quiet python .claude/skills/altium-schematic/read_schematic.py \
    summary DONGLE_V2_RELEASED_DESIGN_FILES/Dongle_PRJ.PrjPcb
# → 4 sheets (top + tap_off, codec, regulators), 103 parts, 63 nets,
#   power: P5V0, P3V3_CODEC, P2V5_MIC, P2V5_SPK, GND
# → top-level sheet symbols: CODEC, LINE_TAP, PSU

# 2. What's on the codec sheet?
uv run --quiet python .claude/skills/altium-schematic/read_schematic.py \
    components DONGLE_V2_RELEASED_DESIGN_FILES/Dongle_PRJ.PrjPcb \
    --sheet Dongle_Sheet2_Codec.SchDoc --brief
# → DA7212-01UM2 (audio codec) + I²C pull-ups + decoupling

# 3. How is the codec wired?
uv run --quiet python .claude/skills/altium-schematic/read_schematic.py \
    connections DONGLE_V2_RELEASED_DESIGN_FILES/Dongle_PRJ.PrjPcb \
    --designator U7
# → SDA/SCL out to J4 with R34/R35 pull-ups, audio I/O to LINE_TAP, etc.
```

Synthesize that into a circuit explanation. Cite designators and net names so
the user can verify against the schematic.
