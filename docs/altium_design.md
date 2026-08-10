# AltiumDesign

`AltiumDesign` is the project-level analysis surface. Use it when you need a
compiled view across a `.PrjPcb`, its schematic documents, variants, component
metadata, and schematic connectivity.

Use it when you need to:

1. load a project from `.PrjPcb`
2. emit the public design JSON contract
3. emit the public netlist JSON contract
4. inspect project netlist JSON
5. generate PCB-backed pick-and-place data when a PcbDoc is referenced
6. inspect project parameters, variants, components, sheets, and nets

## Public Contracts

`AltiumDesign.to_json(...)` emits `altium_monkey.design.b0`.

`AltiumDesign.to_netlist().to_json(...)` emits `altium_monkey.netlist.a0`.

`AltiumDesign.compile(force=False)` returns the beta compiled schematic model.
Project netlist, design JSON, and physical schematic rendering now derive from
this compiled model instead of a separate legacy hierarchy rewriter.

`AltiumDesign.to_physical_ir(page_occurrence_ref)` and
`AltiumDesign.to_physical_svg(page_occurrence_ref)` render one compiled physical
schematic page. Use these APIs for repeated sheets and multi-channel projects
where one logical `.SchDoc` appears multiple times with different resolved
designators such as `R1.1`, `R1.2`, `R1A`, or `R1B`.

`AltiumDesign.to_pnp(...)` returns pick-and-place entries from the project
PcbDoc. When a project has a PcbDoc, `AltiumDesign.to_json(...)` also includes
the same data under the optional root `pnp` field.

The default PnP coordinate mode is `altium-pick-place`. It matches Altium's
Pick Place export by taking the center of the bounding box of component-owned
pad anchor points and falling back to the component origin when a component has
no owned pads. Use `position_mode="component-origin"` when the footprint
placement origin is the desired coordinate. In design JSON, `pnp.position_mode`
records the selected mode and `center_x`/`center_y` are the selected PnP
position, not a generic geometric centroid.

The `schema` field is the contract version. These payloads do not use a root
`version` field.

The root `generator` field is `altium_monkey`.

See [schema contracts](schemas/index.md) for field-level contract notes.
See [compiled design migration](api_patterns/compiled_design.md) for guidance
when moving strict validators or SVG/component consumers from Design a2 to
Design b0. The retained `altium_monkey.design.a2` schema describes archived
physical-page payloads, and `altium_monkey.design.a1` describes the earlier
project contract. Neither predecessor is emitted by the current API.

## Compiled vs Logical Views

Use `AltiumDesign` when a consumer needs the resolved physical project view.
For project inputs, these public surfaces derive from the compiled schematic
model:

1. `AltiumDesign.to_json(...)`
2. `AltiumDesign.to_netlist()`
3. `AltiumDesign.to_physical_ir(page_occurrence_ref)`
4. `AltiumDesign.to_physical_svg(page_occurrence_ref)`

This means repeated sheets, channel instances, annotation-driven designator
changes, and project net naming are resolved before data is emitted.

Use `AltiumSchDoc.to_ir()` or `AltiumSchDoc.to_svg()` when you intentionally
want the raw logical source sheet without project compile context. Those
single-sheet renderers do not know which physical page instance they represent,
so they do not substitute channel-resolved designators.

For projects without repeated physical sheet instances, the compiled project
view decays to the familiar one-source-sheet/one-page-occurrence shape. The
same graph contract still applies, so consumers do not need a separate simple
project code path.

## Compiled Schematic Graph

Design b0 requires `compiled_schematic_graph`, a variant-neutral transport with
the same source-neutral ten collections used by the governed generic graph:

1. `unit_definitions`
2. `page_definitions`
3. `unit_occurrences`
4. `page_occurrences`
5. `hierarchy_occurrences`
6. `component_occurrences`
7. `local_net_occurrences`
8. `terminal_occurrences`
9. `hierarchy_terminal_bindings`
10. `graphical_artifact_links`

Definitions describe reusable logical source material. Occurrences describe
the realized compiled design, so repeated sheets and channels have distinct
canonical identities. Local nets belong to page occurrences, and explicit
hierarchy terminal bindings connect parent sheet entries to child ports.

The embedded graph schema is
`altium_monkey.compiled_schematic_graph.a0`; its identity namespace is
`sch.compiled_schematic_graph.a0`.

`compile` and `diagnostics` are optional root fields. Request them with
`AltiumDesign.to_json(include_compile_metadata=True)` when a consumer needs
compile health, resolved options, annotation state, statistics, or warning/error
records. The default payload omits them to keep the normal design JSON compact.

The review-safe drawing selector is:

```text
page_occurrence_ref + artifact_key + element_id
```

Current schematic SVG and IR use `artifact_key == "sch.dwg_scene"`.
`graphical_artifact_links` maps each scoped selector to a component, terminal,
local net, hierarchy occurrence, or page target. A bare SVG element id is not
a realized identity.

`physical_page_metadata` is the only Altium-specific page projection retained
at the Design root. Each row is keyed by canonical `page_occurrence_ref` and
contains only presentation facts:

1. physical instance path;
2. channel index, prefix, and alpha token;
3. logical and physical room names;
4. document number.

It does not repeat components or nets. Optional Design indexes retain only
non-page compatibility lookups such as component-to-net and unambiguous SVG
component maps; the Design a2 page-derived indexes are retired.

Net records may include `aliases` and `name_sources`. `aliases` are alternate
net names discovered while merging compiled connectivity. `name_sources`
records explain where candidate names came from, including the winning compiled
name, explicit labels, ports, sheet entries, power ports, and other compiled
name contributors when available.

The net `name` is the compiled winner. `aliases` are useful when a schematic
wire has multiple labels, when a port/sheet-entry name differs from a local net
label, or when different pages contribute different candidate names to the same
compiled net. Consumers should display or key on `name` unless they explicitly
need provenance or search over alternate names.

## Current Boundaries

Variant processing includes DNP/not-fitted handling, project current-variant
state, variant metadata in design JSON, and per-designator parameter overrides.
`to_bom(variant=...)` applies parameter overrides to component parameters,
values, and descriptions while retaining DNP rows with a `dnp` flag.
`to_pnp(variant=...)` omits DNP placements for the selected variant.
Design JSON component rows expose active-variant `dnp` and `fitted` state when
available. Schematic SVG/IR rendering does not hide, dim, or mutate DNP
component geometry by itself; consumers can apply their own policy from the
metadata.

Alternate fitted component rows are preserved in project variant metadata but
are not applied as semantic component replacements in BOM, netlist, PNP, or SVG
output yet.

The compiled design path resolves hierarchical sheets, repeated channels,
physical page instances, and annotation-file driven designator mapping for the
governed release corpus. `.Annotation` files are parsed for compile-relevant
physical designator and sheet/document metadata. Annotation `NetNameManager`
records are preserved as annotation metadata, but are not applied as compiled
flat-net renames because reference compile evidence does not apply those
records during schematic compilation.

Use schematic SVG rendering directly when you only need page-level drawings.
Use `AltiumDesign` when you need project context such as parameters, variants,
the compiled schematic graph, resolved designators, or netlist data.

WireList output is removed from the public output path. WireList can lose
information that exists in the compiled model, especially for repeated sheets,
long generated names, aliases, name-source provenance, and zero-pin interface
nets. Use `AltiumDesign.to_json(...)`, `AltiumDesign.compile().to_dict()`, or
`AltiumDesign.to_netlist().to_json(...)` for programmatic consumers.

Use `design.load_pcbdoc().components` when a PCB-backed BOM should reflect the
components that are actually placed on the board. The `pcbdoc_bom` example shows
that pattern.

## Examples

Start with:

1. [`hello_altium_design`](../examples/hello_altium_design/README.md)
2. [`pcbdoc_bom`](../examples/pcbdoc_bom/README.md)
3. [`pcbdoc_pick_n_place`](../examples/pcbdoc_pick_n_place/README.md)
4. [`schdoc_svg`](../examples/schdoc_svg/README.md)
5. [`pcbdoc_stats`](../examples/pcbdoc_stats/README.md)
6. [`prjpcb_make_project`](../examples/prjpcb_make_project/README.md)

`hello_altium_design` is the canonical project-design example for this release.
It writes full Design b0 JSON, a compiled-graph summary, compiled net-name
examples, and project-aware physical schematic SVGs.
