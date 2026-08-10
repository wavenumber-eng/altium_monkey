# AltiumDesign Contract

`AltiumDesign` is the public project-level loader and integration model.

## Stable Surface

- Load an Altium project from `.PrjPcb`.
- Discover schematic, PCB, library, harness, and output-job documents where
  supported.
- Build compiled schematic netlists.
- Emit JSON design and netlist payloads with declared schema ids.
- Render project-level schematic SVG outputs.
- Preserve active project variant identity and component DNP/fitted state in
  project-level JSON when the `.PrjPcb` declares a current variant.
- Surface compact compiled-design metadata and diagnostics in project-level
  JSON without embedding the full compiled model.

## Schema Contracts

JSON payloads include explicit schema ids such as `altium_monkey.design.b0` and
`altium_monkey.netlist.a0`. Every payload-shape change requires a new schema ID:
breaking changes advance the leading major letter, while additive changes that
preserve existing field meaning advance the trailing minor number. Consumers
should match supported schema IDs exactly unless they implement an explicit
migration or compatibility range.

Design b0 requires `compiled_schematic_graph` with schema
`altium_monkey.compiled_schematic_graph.a0`. It intentionally removes the
Design a2 `physical_pages` projection rather than reusing its schema id.

## Netlist Connectivity

Project-level `AltiumDesign.to_netlist()` and `AltiumDesign.to_json()` are
compiled physical outputs. Single-document `AltiumSchDoc` SVG/IR/netlist APIs
remain logical source-sheet outputs unless the caller routes through
`AltiumDesign.to_physical_ir(...)` or `AltiumDesign.to_physical_svg(...)`.

Compiled netlist output uses the same sheet-entry and harness-entry hotspot
contract as `AltiumSchDoc`: `DistanceFromTop` and `DistanceFromTop_Frac1` are
composed before endpoint matching, then rounded half-away-from-zero to native
integer schematic units. This prevents fractional entry placement from
collapsing onto an adjacent same-named wire or port during project hierarchy
resolution.

## SVG Linkage

Design JSON may carry ids that link back to schematic SVG output.
`components[].svg_id` points to the rendered component group id, and optional
`indexes.svg_to_component` maps SVG ids back to component designators.
Netlist records carry `nets[].graphical` and `nets[].endpoints` for schematic
highlighting and semantic trace workflows.

For designs with repeated sheets or instantiated channels, `svg_id` alone is a
logical drawing identifier and is not enough to identify one realized object.
Design b0 carries the authoritative `compiled_schematic_graph` instead of a
repeated `physical_pages` projection. Its ten collections describe unit/page
definitions and occurrences, hierarchy, component bodies, page-local scalar
nets, terminals, hierarchy terminal bindings, and graphical artifact links.

The drawing lookup key is:

```text
page_occurrence_ref + artifact_key + element_id
```

Current schematic SVG/IR uses `artifact_key == "sch.dwg_scene"`. A
`graphical_artifact_links[]` row resolves that scoped selector to a semantic
graph object through `target_type` and `target_ref`. Consumers should not infer
connectivity from graphical nesting or from a bare element id.

`physical_page_metadata[]` is keyed by the same canonical
`page_occurrence_ref`. It carries only Altium presentation facts:
`physical_instance_path`, channel index/prefix/alpha, room names, and document
number. It does not repeat components or nets.

When indexes are requested, `svg_to_component`, `svg_to_components`,
`component_to_nets`, and `net_to_components` remain compatibility conveniences.
The retired physical-page indexes are not part of Design b0.

## Net Name Provenance

Compiled flat-net rows expose one winning `name`. When the compiler discovers
additional candidate names on the same electrical net, those names are exposed
as `aliases` and may be explained through `name_sources`.

Consumers that need stable connectivity keys should use the winning `name`.
Consumers that need search, review, or graphical explanation can include
`aliases` and `name_sources` to show labels, ports, sheet entries, power ports,
and other source objects that contributed alternate names.

`aliases` are emitted in deterministic Altium-compatible total sort order with
the winning `name` excluded. Case-only ties therefore remain stable across
processes, which is important for review bundles and visualizers that diff or
cache alternate-name lists.

## Variants and DNP

`project.current_variant` reports the active project variant from the
`.PrjPcb`, when one is set. `variants[]` lists available project variants and
marks the active row with `is_current`.

Top-level `components[]` rows include:

- `dnp`: true when the component's resolved physical designator is marked
  not-fitted in the active project variant;
- `fitted`: the inverse of `dnp`.

The design JSON contract does not filter DNP components. Consumers that need
variant-aware visibility can filter or style using these fields while retaining
the full resolved design context.

## Compile Metadata and Diagnostics

`compile` is a compact summary of the compiled-design state that produced the
public design projection. It includes:

- `schema`: compiled-design model schema id;
- `summary`: counts and warning/error health;
- `options`: resolved compile options such as channel designator format and
  channel room naming style;
- `annotation`: annotation-file load state and counts;
- `stats`: compiler statistics and selected top-level physical/logical ids.

`diagnostics[]` flattens compile warnings/errors from the compiled model into a
consumer-friendly list. Each row keeps the compiled diagnostic fields and adds
`owner_kind` plus `owner_id` when the diagnostic belongs to a specific
document, component, symbol, or net.

## Physical IR and SVG Rendering

Project-level physical rendering is explicit:

- `AltiumDesign.to_physical_ir(page_occurrence_ref)` returns schematic geometry IR
  for one compiled physical page.
- `AltiumDesign.to_physical_svg(page_occurrence_ref)` renders that physical IR to
  SVG.

Both APIs use the logical SchDoc geometry for the selected page, but component
designator text is resolved from the compiled physical page before text
measurement and IR/SVG emission. This is required for repeated sheets and
instantiated channels where the raw sheet contains `R1` but the physical page
contains `R1.1`, `R1A`, or another project-configured resolved designator.

The default `AltiumSchDoc.to_ir()` and `AltiumSchDoc.to_svg()` APIs remain
logical-sheet renderers. Consumers that review compiled projects should select
the intended `compiled_schematic_graph.page_occurrences[].id` and use the
physical rendering APIs.

Variant-specific graphical suppression, such as hiding or dimming DNP
components, is a consumer policy layered over the physical rendering path. The
resolved design JSON carries DNP/fitted state; the source schematic geometry is
not mutated.

See [SVG](svg.md) for the SVG-side contract.

## Boundary

`AltiumDesign` exposes Altium-native project data. Cross-CAD normalization is a
separate consumer concern.

## Test Gates

The AltiumDesign contract is covered by design loading, graph validation,
identity stability, Python/native parity, netlist, JSON schema, SVG, public
examples, and release signoff.
