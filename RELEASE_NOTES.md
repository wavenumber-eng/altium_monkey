# altium-monkey 2026.09.04 Release Notes

Package version: `2026.9.4`

This release only updates the `wn-geometer` dependency from `2026.8.21` to
`2026.9.4`.

# altium-monkey 2026.08.21 Release Notes

Package version: `2026.8.21`

This release fixes PCB parameter text decoding for non-ASCII characters,
multichannel channel numbering and naming under non-default project options,
sheet-symbol child resolution for extension-less references, compiled-graph
projection, and SchDoc/SchLib SVG paint order.

## PCB Parameter Unicode

- Fixed PCB component parameter text decoding for non-ASCII characters such
  as the degree (U+00B0) and plus-minus (U+00B1) signs, which previously read
  back as mojibake like `A-circumflex + degree` pairs (GitHub issue #32).
  PcbDoc and PcbLib parameter readers now prefer Altium's authoritative
  `UNICODE__<FIELD>` UTF-16 code-unit sidebands over the plain fields, whose
  byte encoding depends on the writing machine's ANSI code page, so parameter
  text decodes correctly regardless of the system code page the file was
  saved under.
- The PcbDoc builder also emits `UNICODE=EXISTS` and `UNICODE__NAME` /
  `UNICODE__VALUE` sidebands when authoring non-ASCII parameters so Altium
  reads them back losslessly on any system code page.

## Multichannel Numbering And Naming

- Fixed multichannel `$ChannelIndex` / `$ChannelAlpha` numbering (GitHub
  issue #40; thanks to the PR #42 reporter for the diagnosis and verification
  data). When the same child schematic is instantiated by two or more
  discrete sheet symbols, channels were numbered by sheet-symbol record order
  in the parent document instead of Altium's hierarchy-rank order, swapping
  physical designators and channel-scoped net names relative to what Altium
  writes to the PcbDoc and its netlists. The compiler now assigns the
  channel index as the 1-based rank in Altium's hierarchy-path sort,
  unconditionally for every channel designator format. This matches Altium
  for reversed stored order, non-contiguous designators (for example
  `PSU_5`/`PSU_9` yield indices 1/2), nested multichannel designs, and
  `Repeat()` ranges starting above 1, all pinned by new Altium
  compile-oracle regression fixtures.
- Fixed three multichannel naming behaviors that diverged from Altium when
  non-default `[Design]` project options are set, pinned by a new 700-variant
  Altium compile-oracle matrix sweeping channel designator formats, room
  naming styles, and room-suffix options:
  - `Repeat()` channels now apply Altium's alpha room-name swap under the
    flat-alpha, alpha-name-path, and mixed-name-path room naming styles.
  - `$ChannelPrefix` now resolves to the `Repeat()` base name for repeat
    channels instead of the expanded channel designator.
  - Channel-expanded net-name detection now follows Altium's source-object
    priority, so a sheet-entry-named root net no longer replaces the
    channel-expanded net spellings under `$RoomName`-leading channel
    designator formats ([GitHub issue #47](https://github.com/wavenumber-eng/altium_monkey/issues/47)).
- Fixed compiled-design channel formatting for designators whose prefix
  contains punctuation, such as `Leaf_1`; `$ComponentPrefix` now preserves
  `Leaf_` and `$ComponentIndex` resolves to `1`, matching Altium Designer.
- Fixed compiled sheet/document numbering for `Repeat()` channels: each path
  level now uses the repeat value at that expansion position (or 1 for the
  first position when the project uses old sheet-symbol indexing) instead of
  the expansion ordinal, matching the sheet numbers Altium reports.
- Compiled physical sheet-symbol rows now report the resolved child
  document's file name, so extension-less sheet-symbol references surface
  the child's real file name instead of the raw reference text.

## Sheet-Symbol Child Resolution

- Fixed sheet-symbol child document resolution when the stored reference has
  no schematic extension (for example `Power` instead of `Power.SchDoc`),
  which previously raised `unresolved_sheet_symbol_child` and silently
  compiled only a subset of the design's sheets (GitHub issue #38).
  Extension-less references are first-class in Altium; both compiler
  resolution paths now match references to loaded documents by
  case-insensitive filename stem (stripping `.SchDoc`/`.SchDot`/`.sch`),
  and ambiguous stem matches are reported with the
  `ambiguous_sheet_symbol_child` diagnostic instead of resolving
  arbitrarily.

## Compiled Graph And SVG Rendering

- Fixed compiled-design and compiled-schematic graph projection for multipart
  shared-pin cardinality, duplicate loaded source paths, and repeated-page
  component SVG indexes.
- Fixed [GitHub issue #43](https://github.com/wavenumber-eng/altium_monkey/issues/43):
  SchDoc and SchLib IR/SVG output now follows Altium's owner-scoped paint order,
  including UID-less nested graphics and Altium's component/library
  transparency ordering rule.

## Import And Authoring Corrections

- Fixed schematic component-description parsing so MBCS pipe escapes are
  normalized consistently when Altium supplies a preferred UTF-8 sidecar.
- Aligned newly authored native harness connectors with Altium and Python's
  right-side default while preserving the left-side fallback for imported
  sparse legacy records.

## Dependencies

- The `wn-geometer` dependency is updated to `2026.8.21` (ABI `20260821`),
  including endpoint/radius arc support for planar regions.

# altium-monkey 2026.08.11-2 Release Notes

Package version: `2026.8.11.post1`

`2026.08.11-2` is represented in Python package metadata as the PEP 440
canonical form `2026.8.11.post1`.

This second 2026.08.11 release adds Python 3.14 support and fixes compiled
schematic identity for distinct unannotated components with the same displayed
designator.

## Python 3.14 Support

- Normal GIL-enabled CPython 3.12, 3.13, and 3.14 are supported. Free-threaded
  Python builds remain outside the current support contract.
- Pillow is updated to 12.3.0 so clean Python 3.14 installs resolve from wheels.
- CadQuery remains an optional example/test dependency; Trimesh and Cascadio
  are not core dependencies.
- Release validation now builds first and runs the complete public suite from
  the exact installed wheel in a clean Python 3.14 environment. A separate
  plain-wheel environment verifies optional geometry packages are absent.

## Duplicate Unannotated Components

- Distinct terminal-bearing components that share a displayed designator such
  as `R?` or `M?` retain their exact source component and pin ownership.
- Identity now survives single-sheet connectivity, hierarchy, repeated sheets,
  compiled-graph projection, and design-review serialization.
- Projection consumes exact source-body evidence without expanding candidates
  by displayed designator. Missing source identity omits only the affected
  semantic row and emits a compile warning rather than aborting the graph.

## Verification

The release was checked with clean wheel-only installs on CPython 3.12, 3.13,
and 3.14, the complete installed-wheel public suite on Python 3.14, targeted
image, text, STEP, Draftsman, and webfont tests, private signoff, exact
Python/native compiled-graph parity, and native CLI smoke tests.

# altium-monkey 2026.08.11 Release Notes

Package version: `2026.8.11`

`2026.08.11` is represented in Python package metadata as the PEP 440
canonical form `2026.8.11`.

This patch release fixes schematic parameter-stream Unicode compatibility for
SchDoc and SchLib files.

## Schematic Unicode Encoding

- Writers now follow Altium's native representation for non-ASCII text:
  a lossless `%UTF8%<Field>` sidecar plus a Windows-1252-safe ordinary field.
- Leading and trailing whitespace is preserved exactly in authoritative UTF-8
  sidecars.
- Readers recover legacy unmarked UTF-8 emitted by older Monkey versions when
  Windows-1252 cannot decode the record, and report the affected stream,
  record, pair, and field where that context is available.
- Malformed content that is neither valid Windows-1252 nor valid UTF-8 now
  fails closed consistently in Python and the native implementation.

## Verification

The release was checked with exact reported CJK byte sequences, byte-exact
Python/native writer parity, SchDoc and SchLib Unicode round trips, project
compilation, public package tests, clean wheel installation, and distribution
metadata checks.

# altium-monkey 2026.08.10 Release Notes

Package version: `2026.8.10`

`2026.08.10` is represented in Python package metadata as the PEP 440
canonical form `2026.8.10`.

This release publishes the consolidated schematic design compiler, the
source-neutral compiled schematic graph, cross-platform project discovery
fixes, and the complete three-family Altium stroke webfont bundle.

## Breaking Design b0 Contract

Project Design JSON advances from `altium_monkey.design.a2` to
`altium_monkey.design.b0`. Design b0 requires the embedded
`altium_monkey.compiled_schematic_graph.a0`, which retains realized hierarchy,
multipart component bodies, page-local scalar nets, terminals, hierarchy
bindings, and scoped drawing evidence with stable canonical identities.

Design b0 removes the duplicated Design a2 `physical_pages` projection. Its
replacement, `physical_page_metadata`, is narrow presentation metadata keyed by
canonical page-occurrence IDs. Project variants and DNP/fitted state remain in
the surrounding Design payload and are intentionally outside the
variant-neutral graph. The Design a2 schema remains bundled only for validating
archived payloads; consumers must explicitly migrate to Design b0.

Python and native C++ emit identical graph and page-metadata payloads.

## Consolidated Schematic Compilation

`AltiumDesign.to_netlist()` and the function-level `compile_netlist()` entry now
both compile an `AltiumCompiledDesign` and project its netlist. The superseded
Python multi-sheet project compiler and public WireList serialization path were
removed; WireList cannot represent repeated/channel hierarchy without losing
information.

The compiler now preserves Altium's fractional scalar connectivity, metric
endpoint tolerance, port-body electrical lines, bus/harness object dispatch,
sheet-entry fractional placement, repeated-channel naming and provenance,
physical sheet expansion, sheet/document numbering, device-sheet behavior, and
multipart duplicate-designator semantics. These fixes cover flat, hierarchical,
repeated-channel, metric, bus/harness, and multipart projects.

## Cross-Platform Project Discovery

Fixed `.PrjPcb` document discovery on Linux, macOS, and WSL when Altium stores
nested `SchDoc`, `PcbDoc`, or `OutJob` paths with Windows-style backslash
separators. Reachable sheets are distinguished by full project-relative paths
when folders contain duplicate filenames, and managed device-sheet sections
survive project save without duplicate document entries.

## Complete Altium Stroke Webfont Bundle

Expanded the packaged webfont bundle to all three native stroke styles.
`Altium Stroke`, `Altium Stroke Sans`, and `Altium Stroke Serif` each ship in
Light, Regular, and Bold weights with per-style stroke ratios tuned against
Altium rendering. Sans and serif include newly authored Greek, math, and
electronics symbols, corrected proportional symbol spacing, and family-native
e-grave glyphs. The bundled demo shows the same equation, BOM, symbol, weight,
and fabrication-note specimens in all three families.

# altium-monkey 2026.08.01 Release Notes

Package version: `2026.8.1`

`2026.08.01` is represented in Python package metadata as the PEP 440
canonical form `2026.8.1`.

This release introduces the V7-aware PCB layer-reference API covering extended
signal and mechanical layers, exact fractional pad corner-radius fidelity,
StackUpX authoring hardening, asset-inventory APIs, SVG review overlays, and a
published Altium stroke webfont bundle.

## V7-Aware PCB Layer API

Added the initial V7-aware PCB layer-reference API. `PcbLayer` remains the
legacy/TV6 enum, while `PcbLayerRef` and semantic tokens cover ordinary
numbered Mechanical17 through Mechanical53 authoring and StackUpX-backed
PcbDoc Mid31 through Mid126 signal authoring for track, arc, fill, text, and
region primitives. SVG layer selection and metadata now use V7-aware tokens
and saved-layer ids without inventing fake legacy `data-layer-id` values.

`write_ipc2581` keeps V7 layer identity in exports: extended mechanical layers
export under their custom display names, and StackUpX-backed extended signal
layers export as their own signal layers instead of collapsing onto legacy
layer slots. Pads, vias, PcbLib V7-only signal authoring, and reserved
mechanical-family layers remain gated pending dedicated native authoring
contracts.

Two new public samples exercise the surface:

- `pcbdoc_v7_128_signal_track_row` generates a local AD 26.8 style 128-signal
  `.stackupx` with deterministic GUID ids and native-style solder mask and
  silkscreen (overlay) layers, re-imports it, writes one vertical track on
  every signal layer through `PcbLayerRef` plus a rotated layer-number label
  on the same copper layer as each track, and records exact readback tokens
  and V7 saved-layer ids for downstream validation.
- `pcb_v7_mechanical_layer_track_rows` writes one track on each ordinary
  numbered Mechanical1 through Mechanical53 layer through
  `PcbLayerRef.mechanical(...)` and preserves custom display names through
  PcbDoc, PcbLib, and SVG output.

## StackUpX Authoring

StackUpX authoring now generates GUID ids by default and enforces the GUID id
contract. `AltiumStackupXDocument`, `StackupXStack`, `StackupXLayer`,
`StackupXSpan`, and related id-bearing types auto-generate GUID ids when the
id argument is omitted, and the PcbDoc-writing bridge
(`AltiumLayerStackDocument.from_stackupx` / `to_layer_stack_document`) rejects
non-GUID stack, layer, and span ids with a clear error instead of writing
board data that crashes Altium's Layer Stack Manager (`Invalid GUID string`).
`native_pcbdoc_write_support()` also reports non-GUID ids on StackUpX-sourced
stacks. Parsing existing `.stackupx` files keeps their ids verbatim.

Added `StackupXLayerType` and `StackupXFeatureId` enums to the public API.
These name the well-known Altium Layer Stack Manager layer `TypeId` GUIDs
(copper signal, prepreg, core, solder mask, overlay, and the rest) and feature
GUIDs (standard stackup, impedance calculator, rigid/flex, back drills), so
StackUpX authoring code never needs to hardcode raw GUID strings.

## Exact Fractional Pad Corner Radius

Fixed exact fractional pad corner-radius handling (issue #22).
`add_pad(..., corner_radius_percent=...)` now accepts float percents on all
PcbDoc, PcbDocBuilder, PcbLib, and footprint authoring surfaces instead of
silently truncating to an integer: authored files carry both the rounded
legacy percent and the exact value in Altium's native `CornerRadiusChamfer`
stream, so the exact percent (for example `18.181818` for a 0.05 mm radius on
a 21.6 mil pad) survives reopening in Altium.

Parsing and re-saving native files now preserves the `CornerRadiusChamfer`
data byte-faithfully instead of dropping it, parsed pads expose the exact
value through `exact_corner_radius_percent_by_layer`,
`corner_radius_percent_exact`, `exact_corner_radius_percent_on_layer(...)`,
and `corner_radius_mils_on_layer(...)`, and SVG, IPC-2581, and pad-state JSON
exports derive the corner radius from the exact percent when present.
Whole-number percents keep producing byte-identical output to previous
releases.

## Asset Inventory APIs

Added extractable-asset inventory APIs as an initial beta public surface.
`AltiumPcbDoc`, `AltiumPcbLib`, `AltiumSchDoc`, and `AltiumSchLib` can now
list selectable assets with typed per-kind details and `AltiumAssetRef`
handles, then extract one selected embedded payload, PCB footprint, or
schematic symbol without forcing callers through a bulk extraction workflow.
`AltiumAssetInventory.to_dict()` emits the documented
`altium_monkey.extractable_assets.a0` JSON contract for preview and
import-dry-run consumers.

Added focused embedded PCB asset inventory APIs as an initial beta public
surface. `AltiumPcbDoc` and `AltiumPcbLib` can list embedded models, embedded
PcbDoc fonts, and opaque PcbLib embedded-font streams without writing files.
The `EmbeddedAssetInventory.to_dict()` shape is documented as
`altium_monkey.pcb.embedded_assets.a0` for lower-level preview, dedupe, and
import dry-run consumers.

Optimized SchDoc symbol extraction for large placed symbols. Extraction now
builds SchLib records without broad child-object graph copies, adds
`AltiumSchDoc.extract_schlib(...)` for in-memory workflows, and preserves the
existing `extract_symbols(...)` split/combined output behavior.

## SVG Review Overlays

Added optional PCB/PcbLib SVG review overlays. `PcbSvgRenderOptions` can now
emit fill-only pad-designator labels and a document/footprint origin datum
marker without changing default rendered geometry. The SVG format contract now
documents native layers, derived layers such as `DRILLS`, overlay metadata,
layer ordering, consumer styling guidance, and the A0 metadata distinction
between layer discovery and layer groups actually emitted in the current SVG.

## Docs Assets And Stroke Webfont

Added the Altium stroke webfont bundle to the published docs assets. The
repository now ships `assets/fonts/` with Regular and Bold TTF/OTF/WOFF/WOFF2
builds generated from Altium's stroke-font tables, a shared `@font-face`
stylesheet, and an interactive `demo.html` specimen page. The fonts cover
Latin-1 plus engineering symbols (µ Ω ° ± ² ³ × ÷ Δ π ∇ and superscript
digits) so docs and web pages can render schematic-style stroke text. Docs
styling assets (`altium-monkey-docs.css` and theme files) moved to the same
top-level `assets/` folder.

## Fixes

Fixed region authoring to accept the two native replay layer shapes that
Altium writes for rigid-flex boards: board-cutout regions stored with the
legacy Keep-Out layer byte plus `V7_LAYER=MULTILAYER`, and board
region/split-line rows stored with legacy layer byte 0 plus reserved system
mechanical tokens (`MECHANICAL64530` and above). `add_region(...)` previously
rejected these combinations, which broke recreating rigid-flex boards from
captured layer-stack specs. Reserved system rows are never auto-registered as
user mechanical layers.

Fixed the `pcbdoc_svg` example for boards with content on V7-only promoted
mechanical layers (Mechanical17 and above): per-layer output and the manifest
now report these layers under their V7 tokens with `legacy_id: null` instead
of failing on `PcbLayer.from_json_name`.

## Validation

This release was checked with V7 layer-reference, StackUpX round-trip,
IPC-2581 export, corner-radius dual-lane round-trip, asset-inventory, and SVG
overlay test lanes, plus corpus-backed PcbDoc/PcbLib regression strata.
Release validation also covers package formatting/lint, release-note hygiene,
public export, wheel build, clean install, and public test execution.

---

# altium-monkey 2026.07.29 Release Notes

Package version: `2026.7.29`

`2026.07.29` is represented in Python package metadata as the PEP 440
canonical form `2026.7.29`.

This release promotes the project-level schematic compiler to the default
project netlisting and design-JSON path. It adds a new `design.a2` JSON
contract for compiled physical pages, repeated/channel sheet identity,
resolved physical designators, physical SVG/IR rendering, and net-name
provenance.

## Compiled Project Design Data

`AltiumDesign.compile()` now produces the project-level compiled schematic
model used by `AltiumDesign.to_netlist()` and `AltiumDesign.to_json()`. The old
multi-sheet clone/rewrite netlisting implementation has been removed from the
production path; compatibility entry points remain importable but route through
the compiled model for multi-sheet projects.

`AltiumDesign.to_json()` now emits `altium_monkey.design.a2` by default. The
new contract includes `physical_pages`, page-local components and nets,
resolved physical designators, active-variant `dnp` / `fitted` metadata,
net-name aliases, and optional name-source provenance. Compact compile
metadata and diagnostics remain opt-in through
`AltiumDesign.to_json(include_compile_metadata=True)`.

Net-name `aliases` are emitted in deterministic Altium-compatible total sort
order with the winning `name` excluded, so review tools can diff or cache
alternate-name lists reliably.

Public issue #26 nested multi-placement is resolved for the Python compiled
design path. The nested multi-placement fixture now expands the two-by-two
`Main -> Mid -> Leaf` hierarchy to `R1.1`, `R1.2`, `R1.3`, and `R1.4`; the
compatibility `compile_netlist(..., project=...)` facade also preserves the
project channel designator format when explicit options are not supplied.

Strict validators pinned to `altium_monkey.design.a1` should refresh to
`design.a2`. The `design_a2.schema.json` file is self-contained for strict
validation.

## Physical SVG and IR

Project-level rendering APIs were added for repeated and channelized designs:
`AltiumDesign.to_physical_ir(physical_page_id)` and
`AltiumDesign.to_physical_svg(physical_page_id)`. They render the source
logical schematic geometry with compiled physical designator substitutions,
so repeated pages show resolved names such as `R1.1`, `R1.2`, `R1A`, or `R1B`
without mutating the source schematic.

For review-safe graphical identity in repeated/channel projects, combine a
`physical_page.id` with an SVG element id or use the
`physical_svg_to_components` index.

## WireList API Removal

WireList serialization APIs are removed from the public output path:
`AltiumDesign.to_wirelist()` and `Netlist.to_wirelist()`. WireList can lose hierarchy,
zero-pin interface, long-name, alias, and repeated-channel information. Use
`AltiumDesign.to_json()`, `AltiumDesign.compile().to_dict()`, or
`AltiumDesign.to_netlist().to_json()` for programmatic consumers.

## Documentation and Examples

The `hello_altium_design` example now demonstrates project-aware design JSON,
physical pages, net-name winners/aliases/name_sources, and physical SVG output.
The public API docs include the new compiled-design migration guide and the
initial Altium Monkey docs theme assets, including the generated Altium Stroke
webfont assets.

## PCB and Library Updates

PCB/PcbLib SVG review overlays can now emit fill-only pad-designator labels and
a document/footprint origin datum marker without changing default rendered
geometry.

Extractable-asset inventory APIs were added for PCB, PCB library, schematic,
and schematic-library documents. Embedded PCB asset inventory APIs can list
embedded models, embedded PcbDoc fonts, and opaque PcbLib embedded-font streams
without writing files.

SchDoc symbol extraction was optimized for large placed symbols and now
supports `AltiumSchDoc.extract_schlib(...)` for in-memory workflows.

## Fixes and Clarifications

SchDoc connectivity now composes whole and fractional sheet-entry and harness
entry offsets before endpoint matching, avoiding off-by-one attachment to
nearby wires or ports.

PCB layer identity docs now distinguish the legacy `PcbLayer` enum from saved
layer ids, Layer Stack Manager rows, Board6 stack/cache rows, and layer-kind
mapping ids. Current authoring and SVG layer-selection APIs remain
legacy-layer-first while newer high-layer-count workflows remain future design
work.

## Validation

Release validation covers package formatting/lint, schema validation against
real design JSON payloads, generated-doc checks, example execution, public
export, wheel build, clean install, the Python L3/L4/L5 release rack, and a
compiled-design corpus performance baseline.

Performance closeout repaired the duplicate-SchDoc load regression and
optimized the RMEGA repeated-sheet stress path from the initial benchmark
matrix `19.8s` compile / `32.8s` total without DR to `6.7s` compile /
`18.0s` total without DR. Remaining `design.a2` cost is accepted as the
broader contract cost for physical pages, resolved designators, aliases,
name_sources, and repeated/channel-safe indexes.

---
# altium-monkey 2026.07.15 Release Notes

Package version: `2026.7.15`

`2026.07.15` is represented in Python package metadata as the PEP 440
canonical form `2026.7.15`.

This customer-delivery hotfix restores project-backed schematic title-block
substitution on the default onscreen IR/SVG path used by downstream schematic
viewers and exporters.

## Schematic Title-Block Parameters

Schematic `to_ir(profile="onscreen")` and `to_svg(...,
options=SchSvgRenderOptions.onscreen(), project_parameters=...)` now resolve
project-backed template/title-block labels such as `=PCB_PART_NUMBER`,
`=PCB_CODENAME`, `=PCB_MIXDOWN`, and schematic `*` placeholders such as
`=ENGINEER` when the matching project parameter is available.

If a schematic `*` placeholder has no matching project value, it remains a
literal `*`. Explicit schematic parameter values still take precedence over
project parameters, and the strict native/oracle profile remains available for
native-export comparison behavior.

This fixes missing title-block values in downstream onscreen consumers such as
`viz sch` and `altium_cruncher sc svg` without requiring viewer-side
workarounds.

## Validation

This release was checked with focused schematic parameter-substitution tests,
template/title-block geometry tests, native C++ parity checks, and a USB token
real-world corpus acceptance check covering onscreen IR and SVG output.
Release validation also covers package formatting/lint, release-note hygiene,
public export, wheel build, clean install, and public test execution.

---

# altium-monkey 2026.07.09 Release Notes

Package version: `2026.7.9`

`2026.07.09` is represented in Python package metadata as the PEP 440
canonical form `2026.7.9`.

This release speeds up reading of large libraries and documents through an
OLE reader cache and fixes schematic arc radius round-trip fidelity.

## OLE Reader Performance

The OLE reader now caches the compound-file root mini stream instead of
rebuilding it for every mini-sector read, dramatically speeding up parsing
of large libraries and documents with many small streams. The reporting
user measured a library load drop from ~17s to ~4s. No behavior change;
write paths are unaffected. Thanks to @reid-p for the report and proposed
fix (issue #19).

## Schematic Arc Radius Round-Trip

Schematic arc and elliptical-arc radii now round-trip exactly:

- Fractional radius storage (`Radius_Frac` / `SecondaryRadius_Frac`) is
  preserved on save instead of being dropped.
- Authored radii of exactly 100.0 mils, or 100.x mils via `add_arc` /
  `add_elliptical_arc` / the `radius_mils` properties, no longer reparse
  as 0 after save.
- The fix applies to arc, elliptical arc, and pie chart records.

## Schematic IR Diagnostics

Schematic IR `render_hints.font_resolution` diagnostics are now emitted in
a canonical sorted order (by requested family/style and resolution result)
instead of first-use order, so the hint is stable across render pipelines.

## Validation

This release was checked with cache rebuild-count and write-path safety
regression tests, arc radius round-trip regressions in Python and C++,
corpus-backed symbol-extraction and component round-trip lanes, and the
full L0/L3 strata. Release validation also covers package formatting/lint,
release-note hygiene, public export, wheel build, clean install, and
public test execution.

---

# altium-monkey 2026.07.07 Release Notes

Package version: `2026.7.7`

`2026.07.07` is represented in Python package metadata as the PEP 440
canonical form `2026.7.7`.

This release significantly speeds up schematic rendering on text-heavy
sheets and fixes schematic SVG text placement for styled and missing font
families to match Altium's native SVG export.

## Schematic Rendering Performance

Schematic `to_ir` and `to_svg` are significantly faster on text-heavy
sheets:

- Default-path font resolution results are now cached. The cache is
  invalidated automatically when the resolver configuration or the font
  environment changes.
- Embedded BMP images are converted to PNG through Pillow's native encoder
  instead of a pure-Python encoder. Decoded image pixels are unchanged;
  embedded PNG payload bytes inside SVG output may differ from previous
  releases.

Measured on a 77-sheet Windows corpus, `to_ir` went from 612.9s to 46.8s
and `to_svg` from 764.6s to 51.4s. A macOS project measured 19.9x faster
`to_ir` and 22.2x faster `to_svg` against the published 2026.7.6 wheel.

New module-level font resolver utilities:
`altium_monkey.altium_font_resolver.clear_font_resolution_result_cache()`,
`font_resolution_result_cache_stats()`, and `FontResolutionCacheStats`.

## Schematic SVG Text Fidelity For Styled And Missing Fonts

Styled text (bold/italic) in families that ship a single font file, such as
Arial Black and Bahnschrift, now measures with that family's own metrics
instead of a generic fallback. This removes a 2-7 px vertical text drift
against Altium's native SVG export for styled labels.

When a requested font family is not available at all, schematic SVG output
now emits `Microsoft Sans Serif` as the font family, matching how Altium's
own export resolves unknown families. Named substitutions such as Arial to
bundled Arimo keep reporting the substituted family.

## Validation

This release was checked against Altium native SVG export references for
141 schematic rendering cases, gotIR oracle positioning lanes, and
before/after corpus timing sweeps on Windows plus a macOS review pass
covering resolver behavior on macOS font directories. Release validation
also covers package formatting/lint, release-note hygiene, public export,
wheel build, clean install, and public test execution.

---

# altium-monkey 2026.07.06 Release Notes

Package version: `2026.7.6`

`2026.07.06` is represented in Python package metadata as the PEP 440
canonical form `2026.7.6`.

This release improves schematic font portability for SVG, gotIR, and
downstream viewers, fixes Altium-style parameter expression substitution, and
hardens schematic rendering against malformed parent-bound child records.

## Schematic Font Resolution

Schematic SVG and gotIR rendering now resolves common Altium/Windows font
families more reliably on macOS. The resolver searches standard macOS system
font locations, including Supplemental fonts, and also accepts explicit font
directories through `ALTIUM_FONT_DIRS`.

When a requested family is not available from the system, the package can use
bundled open-source fallback fonts:

- Arial and Microsoft Sans Serif-style families route to Arimo.
- Times New Roman-style families route to Tinos.
- Courier New and monospace families route to Cousine.

The fallback bundle includes regular, bold, italic, and bold-italic faces where
available. Heavy or styled family names such as Arial Black now route to the
closest bundled Arimo style instead of falling through to hard default metrics.

Schematic SVG output embeds bundled fallback font faces when they are used, so
browser rendering stays aligned with the font metrics used during geometry
generation.

## Font Diagnostics In gotIR

Schematic geometry IR now carries font-resolution diagnostics for substitutions
and fallbacks. Exact system matches remain quiet, while bundled substitutions,
generic fallbacks, and hard fallbacks are surfaced for downstream tooling.

This lets CLI tools and viewers warn when a rendered sheet used Arimo, Tinos,
or Cousine in place of the originally requested Altium font family.

## Special-String And Formula Substitution

PCB special-string substitution now handles quoted parameter expressions such
as `'.PartNumberPCB'-'.Revision'.'.RevisionMinor'` without emitting Altium
expression quote delimiters in resolved SVG or IPC text.

Schematic text formula substitution now handles Altium-style expressions such
as `=Revision+'.'+RevisionMinor` and
`=title+' pcb  Rev ' + PcbRevision+'.'+PcbRevisionMinor`, resolving project and
document parameters instead of emitting expression fragments.

## Schematic Rendering Robustness

Schematic SVG and gotIR rendering now skips malformed parent-bound harness or
sheet entries whose owner index collides with a component. This matches Altium
behavior for the public synthetic repro and avoids a `TypeError` from generic
component geometry dispatch.

## Public Example Fix

The `pcbdoc_add_custom_pad_region_outline` public example now computes
`PcbExtendedVertex.start_angle` and `PcbExtendedVertex.end_angle` in degrees,
matching Altium Monkey's shape-based-region renderer and PcbDoc board-outline
behavior.

## Validation

This release was checked with focused font-resolution, PCB special-string,
schematic parameter-substitution, public repro, and private schematic
dispatch tests. Release validation also covers package formatting/lint,
release-note hygiene, public export, wheel build, clean install, and public
test execution.

---

# altium-monkey 2026.07.01 Release Notes

Package version: `2026.7.1`

`2026.07.01` is represented in Python package metadata as the PEP 440
canonical form `2026.7.1`.

This release expands PCB stackup authoring, adds named keepout restriction
helpers, improves font portability, and tightens PcbDoc/PcbLib record
preservation for automated board generation.

## PCB Stackup Authoring

PcbDoc layer-stack authoring now supports rigid `.stackup` and `.stackupx`
files as inputs to `AltiumLayerStackDocument.from_stackup(...)` and
`AltiumLayerStackDocument.from_stackupx(...)`, followed by
`PcbDocBuilder.set_layer_stack_document(...)`.

Generated PcbDocs preserve copper, dielectric, solder-mask, solder-paste,
overlay, layer-pair, internal-plane ID, and layer type semantics on readback,
and can be exported again through `to_stackup(...)` and `to_stackupx(...)`.
The new public `pcbdoc_create_from_stackup_files` example demonstrates both
import paths. Rigid-flex StackupX authoring still requires explicit
board-region geometry and is not inferred from interchange files alone.

Programmatic rigid stack creation is now available through
`AltiumLayerStackDocument.from_rigid_layer_rows(...)` and
`AltiumRigidStackRowSpec`. Semantic row constructors cover common rigid
authoring cases through `AltiumComponentPlacement`,
`AltiumDielectricLayerKind`, `AltiumCopperMaterialSpec`, and
`AltiumDielectricMaterialSpec`, so callers do not need raw StackupX GUIDs or
property type tuples for ordinary copper, prepreg, core, solder-mask, and
overlay rows.

Stackup-level electrical settings such as roughness model, roughness factors,
copper resistance, via plating thickness, realistic ratio, and temperatures are
modeled by `AltiumStackupSettings`, `AltiumStackupType`, and
`AltiumStackupRoughnessModel`. `AltiumStackupDocument`,
`AltiumStackupXDocument`, and `AltiumLayerPair` are first-class public exports.
The `pcbdoc_create_custom_rigid_stack` example now writes PcbDoc, `.stackup`,
and `.stackupx` outputs from the same code-authored stack model, and the new
`pcbdoc_create_jlcpcb_rigid_stack` example demonstrates a JLCPCB-style
eight-layer rigid stack authored through the semantic API.

## PCB Keepout Restrictions

PCB keepout restriction helpers are now public. Python callers can use
`PcbKeepoutRestriction`, `decode_pcb_keepout_restrictions(...)`,
`encode_pcb_keepout_restrictions(...)`,
`pcb_keepout_restriction_names(...)`, and
`pcb_keepout_restriction_unknown_bits(...)` to decode and author the confirmed
Altium object-specific keepout mask bits: via `0x01`, track `0x02`, copper
`0x04`, SMD pad `0x08`, and through-hole pad `0x10`.

The raw `keepout_restrictions` integer remains the authoritative stored field
so unknown future bits can still round-trip.

## PCB Record Models And Preservation

PcbDoc VIA records now expose the parsed `drill_layer_pair_type` field used by
backdrill-aware board files.

PcbDoc rule records now expose parsed semantic rule data, including canonical
rule-kind aliases, semantic model names, parsed scope expressions, typed scalar
accessors, connect-style settings, per-layer width and differential-pair
metrics, routing-layer flags, room outlines, clearance object pairs, and
routing-neckdown layer lengths. Raw `extra_fields` remain preserved for
unsupported or pass-through fields.

Typed Rules6 write-back is now supported through semantic field setters on
rule objects. PcbDoc saves rewrite `Rules6/Header` and `Rules6/Data` while
preserving parsed record leaders.

Rigid-flex board-region bend-line edits, typed Dimensions6 helpers and
write-back, board-level ExtendedPrimitiveInformation summaries and write-back,
and read-only board-level CustomShapes summaries are now available through the
Python API. Parsed custom pads also expose their resolved custom layer set
through `PcbPadSummary.custom_shape_layers`.

## Font And SchDoc Image Compatibility

Font portability helpers now expose `FontReplacementRule`,
`portable_font_replacements()`, and
`ALTIUM_PORTABLE_FONT_REPLACEMENTS` from the `altium_monkey` package root and
`altium_monkey.altium_font_resolver`. These mirror the existing portable
replacement table while giving Python callers a typed rule API.

SchDoc IR image geometry now matches Altium for wrapped `TSVGImage` payloads:
`gotImage` source dimensions use the embedded BMP preview size while runtime
SVG rendering still uses the SVG payload.

## Compatibility Notes

The internal PcbDoc VIA authoring constant
`VIA_AD25_DEFAULT_SOLDER_MASK_EXPANSION_IU` was renamed to
`VIA_TENTING_DEFAULT_SOLDER_MASK_EXPANSION_IU` so the API wording describes the
tenting behavior rather than implying an AD25-only rule. The old name is not
retained.

## Validation

This release was checked with focused keepout helper tests, package
validation, public tests, wheel build and clean-install checks, private
signoff, and the affected source quality gates.

---

# altium-monkey 2026.06.21 Release Notes

Package version: `2026.6.21`

`2026.06.21` is represented in Python package metadata as the PEP 440
canonical form `2026.6.21`.

This release fixes focused schematic-library and PCB library/document behavior
reported from real Altium workflows.

## SchLib Comment And Designator Visibility

`AltiumSchLib` now supports `show_comments_designators=True` in the constructor
and a `show_comments_designators` property. When enabled, generated SchLib files
write `AlwaysShowCD=T` in the FileHeader so Altium's library editor opens with
symbol comments and designators visible.

The default remains unchanged for compatibility: newly authored libraries omit
the field unless the option is enabled. Parsed libraries hydrate the property
from existing FileHeader data when present.

The public `hello_schlib` example now opts into this setting so its generated
comment and designator are visible in Altium without manually changing document
options.

## SchDoc Vertical Port Rendering

SchDoc SVG rendering now matches Altium for vertically oriented page-level
ports. Port bodies, connection anchors, and text labels follow the native
on-screen geometry for vertical port styles instead of leaving port labels
horizontal.

## Legacy Rounded PcbLib Pads

PcbLib footprint SVG and placed PcbDoc SVG rendering now handles legacy pads
stored as raw Altium `TShape.eRounded` with unequal dimensions and no modern
alternate-shape record. These pads render as native obround/capsule geometry.

PcbLib footprint SVG previews now also treat rule-based solder-mask and
paste-mask expansion as zero when no board-rule context exists, while preserving
manual expansion values.

## Pad Testpoint Flags

PcbLib and PcbDoc pad testpoint flags now decode and author the observed Altium
fields. Fabrication top pads use the Altium `flags1 0x80` bit, and assembly
top/bottom pads use the SubRecord 5 tail bytes saved by Altium instead of
unrelated flag bits.

## Validation

This release was prepared with focused SchLib FileHeader coverage, the updated
public `hello_schlib` sample, focused SchDoc SVG checks, PcbLib/PcbDoc rounded
pad SVG and IPC/Draftsman oracle coverage, pad testpoint flag fixtures, and the
release validation pipeline.

---

# altium-monkey 2026.06.16 Release Notes

Package version: `2026.6.16`

`2026.06.16` is represented in Python package metadata as the PEP 440
canonical form `2026.6.16`.

This release fixes SchLib-to-SchDoc component insertion order so schematic
symbol draw order is preserved through placement, save/reopen, and symbol
extraction.

## SchLib Component Insertion Order

`AltiumSchDoc.add_component_from_library(...)` now preserves the source
`AltiumSymbol.objects` child order when cloning pins, body graphics,
designators, labels, images, text frames, and parameters into a placed
component.

This matters for symbols where body graphics intentionally sit in front of or
behind pins. Earlier insertion grouped cloned records by type, which could move
rounded rectangles ahead of pins and change the visible Altium z-order.

SchLib designator records are also preserved during insertion when the source
symbol provides them; their text is still replaced with the requested placed
designator.

## Validation

This release was checked with focused SchLib insertion tests that create a
SchDoc, place symbols from SchLib, save and reopen the SchDoc, extract the
placed symbol back to SchLib, and assert that source and extracted child order
match. The corpus-backed checks cover both the original `SCTA1A0103.SchLib`
order and the intentional inverse order in `SCTA1A0103_pin_on_top.SchLib` so
the release proves preservation rather than forcing one preferred order.

The focused public example checks and `altium_cruncher` mate tests also passed
against the patched local `altium-monkey` source.

---

# altium-monkey 2026.06.14 Release Notes

Package version: `2026.6.14`

`2026.06.14` is represented in Python package metadata as the PEP 440
canonical form `2026.6.14`.

This release fixes alternate display-mode schematic netlisting and adds
first-class PcbDoc/PcbLib mechanical layer kind authoring.

## SchDoc Display-Mode Netlisting

SchDoc netlist extraction now follows the active symbol `DisplayMode` for
placed components instead of assuming the primary symbol mode. Component pin
views and WireList output now match the active display body, including the
native single-sheet extractor path.

## PcbDoc And PcbLib Mechanical Layer Kinds

PcbDoc and PcbLib now expose typed mechanical layer kind assignments through
`MechanicalLayerKind`, `mechanical_layer_kinds`,
`get_mechanical_layer_kind(...)`, and `set_mechanical_layer_kind(...)`.
`PcbDocBuilder.set_mechanical_layer_kind(...)` is also available for direct
PcbDoc builder workflows.

The mapping reads and writes `LayerKindMapping/Data` for PcbDoc and
`Library/LayerKindMapping/Data` for PcbLib, including classic Mechanical 1..16
ids and extended Mechanical 17..32 ids. Authored output also synchronizes
Altium-visible `MECHKIND` layer-table and cache fields so assignments appear in
Altium's layer manager after save/reopen.

PcbDoc mechanical layer display-name, enabled-state, and mirror-pair authoring
now supports Mechanical 17..32 through Board6 V9 cache fields and `MECHPAIR*`
entries without colliding with legacy system-layer ids such as Drill Drawing.
PcbLib has matching mechanical layer registry and mirror-pair authoring through
`AltiumPcbLib.set_mechanical_layer(...)` and
`AltiumPcbLib.set_mechanical_layer_pair(...)`, including Mechanical 17..32
`LAYERV7_*` and `Library/Data` `MECHPAIR*` updates.

## Public Examples

Two new public examples demonstrate metadata-only mechanical layer kind
authoring:

1. `pcbdoc_create_mechanical_layer_kinds`
2. `pcblib_create_mechanical_layer_kinds`

Both examples create mechanical layer names, enabled states, component-layer
pairs, standalone kind assignments at lower mechanical layer indices, paired
component kind assignments at higher indices, and save/reparse JSON readback
manifests for Altium UI verification.

## Validation

This release was prepared with focused SchDoc display-mode coverage, PcbDoc and
PcbLib mechanical layer kind round-trip tests, exported mechanical-layer
example readback checks, clean Ruff lint, and the asset test lane.

---

# altium-monkey 2026.06.13 Release Notes

Package version: `2026.6.13`

`2026.06.13` is represented in Python package metadata as the PEP 440
canonical form `2026.6.13`.

This release tightens schematic netlist behavior and PcbDoc/PcbLib parameter
round-tripping for downstream automation.

## SchDoc Netlist Near-Crossing Behavior

SchDoc WireList generation now matches Altium for off-grid and metric
near-crossing wires: wire endpoints must exactly meet wires or explicit
junctions for connectivity, rather than using editor grid/tolerance settings to
merge nearby geometry.

The release was checked against the full private L5 netlist corpus lane. The
three near-crossing corpus references were regenerated to match the corrected
Altium-style connectivity.

## Design JSON Sheet Numbers

`AltiumDesign.to_json()` now tolerates Altium `SheetNumber` document parameters
that are not canonical decimal numbers, such as part-number strings. Canonical
numeric sheet numbers remain JSON numbers for compatibility; non-canonical
values are preserved exactly as JSON strings.

## DXP Parameter-List Escaping

PcbLib footprint `PrimitiveParameters` and PcbDoc component
`PrimitiveParameters/Data` values now decode Altium's DXP parameter-list
escapes (`{}` for `=` and `[]` for `|`) on read and apply the same encoding
when authoring values.

## Validation

This release was prepared through the validation wrapper, including public
tests, package build, artifact checks, and clean wheel install validation. The
release also passed the full private SchDoc netlist corpus lane.

---

# altium-monkey 2026.06.11 Release Notes

Package version: `2026.6.11`

`2026.06.11` is represented in Python package metadata as the PEP 440
canonical form `2026.6.11`.

This release refreshes the controlled `wn-geometer` runtime dependency used for
STEP-derived component bounds.

## Geometer Dependency Refresh

`altium-monkey` now depends on `wn-geometer==2026.6.10`, moving STEP geometry
workflows onto the OCCT V8-backed Geometer package while preserving the
existing Altium Monkey API surface.

## Validation

This release was prepared through the validation wrapper, including public
tests, package build, artifact checks, and clean wheel install validation.

---

# altium-monkey 2026.06.09 Release Notes

Package version: `2026.6.9`

`2026.06.09` is represented in Python package metadata as the PEP 440
canonical form `2026.6.9`.

This release closes a focused PcbDoc authoring gap for downstream transcode and
visualization workflows. The changes are additive and preserve existing
documented APIs.

## PcbDoc Region And Custom-Pad Authoring

`AltiumPcbDoc.add_region(...)` now accepts `outline_vertices` for
line/arc-preserving shape-based-region authoring. This allows callers to write
native `PcbExtendedVertex` outlines when point-only polygons would lose segment
semantics.

PcbDoc custom-pad authoring now supports arc-capable extended outline vertices
on the primary custom body and on additional per-layer bodies through
`PcbCustomPadLayerShapeSpec(..., outline_vertices=...)`. Custom-pad anchors can
also carry ordinary pad drill fields such as `hole_size_mils`, `plated`,
`hole_shape`, slot fields, and drill tolerances.

Ordinary region authoring and PcbDoc custom-pad body authoring now share the
same outline normalization path for point lists, holes, and optional extended
line/arc outlines. Custom pads remain a composed workflow around that shared
geometry path: the API writes the anchor pad plus native `CustomShapes/*`,
`Regions6`, and `ShapeBasedRegions6` records required for PcbDoc custom-pad
semantics.

## Dimension Preservation

`AltiumPcbDoc.add_dimension_record(...)` and
`PcbDocBuilder.add_dimension_record(...)` can append raw native
`Dimensions6/Data` records from `record_type`, `record_leader`, and payload
bytes. This is a preservation/transcode API for imported dimensions, not a
high-level construction API or full object-oriented dimension model.

## Public Examples

The new `pcbdoc_add_custom_pad_region_outline` public example demonstrates
`add_region(..., outline_vertices=...)`, `add_custom_pad(...,
outline_vertices=...)`, and `PcbCustomPadLayerShapeSpec(...,
outline_vertices=...)`. The example reparses its generated board and writes a
JSON manifest proving arc vertices and `CustomShapes/Data` are present.

## Validation

This release was tested with focused PcbDoc/PcbLib authoring tests, the new
public example, public manifest/docs checks, downstream Data Models writer
tests, and the validation wrapper.

## Public API Compatibility

Existing documented APIs remain compatible. The new region/custom-pad outline
controls and raw dimension replay support are additive.

---

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
through Python `PcbCustomPadLayerShapeSpec` / `layer_shapes=...`.

Placing PcbLib footprints into PcbDoc now preserves footprint-local via
identity through PcbDoc-to-PcbLib extraction, including IPC-4761 feature side
tables, feature materials, propagation delay, hole tolerances, mask and tenting
flags, and fabrication/assembly testpoint flags.

PcbDoc authoring now includes Python helpers for mechanical layer display
names, enabled-state registry fields, and mechanical mirror pairing used by
component side flipping.

PcbDoc user-union creation now supports explicit native union-id replay in
Python, enabling deterministic read/mutate/write recreation of named user
unions while preserving auto-allocation for normal use.

Layer-stack document authoring now includes via-span and backdrill-span
helpers for the same `LAYERPAIR*` Board6/Data contract. Direct via-tail and
counterhole mutation remains intentionally outside this API.

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
serialization, generated public example docs, and the promoted writer
controls.

This release was tested with focused package authoring tests, public example
tests, private PcbDoc/PcbLib fixture lanes, current Altium interop open/save smoke for
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

Focused Python package tests and generated-public-package tests cover the
shared primitive option cleanup, including component-body option parity and
R082-style local stack pad geometry.

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
