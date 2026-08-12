# Compiled project design and schematic graph

`AltiumDesign.to_json()` emits `altium_monkey.design.b0`. Design b0 requires a
variant-neutral compiled schematic graph and replaces the duplicated Design a2
`physical_pages` projection.

```python
from altium_monkey import AltiumDesign

design = AltiumDesign.from_prjpcb("board.PrjPcb")
payload = design.to_json(include_indexes=True)

assert payload["schema"] == "altium_monkey.design.b0"
graph = payload["compiled_schematic_graph"]
assert graph["schema"] == "altium_monkey.compiled_schematic_graph.a0"
```

## Graph collections

The embedded graph has the same source-neutral ten collections used by other
CAD importers:

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
the realized compiled design, so reused sheets and channels have distinct unit
and page occurrence IDs. Local nets belong to page occurrences; hierarchy
bindings explicitly connect parent sheet-entry terminals to child port
terminals.

Displayed component designators are labels, not occurrence identities. Two
unannotated components may both display `R?` or `M?` while retaining distinct
component occurrences and component-pin terminal ownership. Use
`component_occurrence_ref` for semantic joins; display-keyed indexes are
compatibility conveniences only and omit ambiguous singleton mappings.

Terminal ownership is derived from terminal-specific source component and pin
evidence captured before connectivity reduction. When a terminal has stable
identity but its component owner cannot be resolved, its diagnostics include
`component_occurrence_unresolved`. A terminal without stable source identity is
not invented from display text, geometry, or order. Component bodies are also
projected only from exact source-UID evidence and are never expanded by display
designator. Missing component or terminal source identity omits only that row
and emits `missing_component_source_identity` or
`missing_terminal_source_identity` in the raw compiled-design diagnostics;
conflicting supposedly unique source IDs are rejected as corrupt evidence.

## Page selection and rendering

Select pages from the graph, then use their canonical IDs for project-aware
rendering:

```python
for page in graph["page_occurrences"]:
    page_ref = page["id"]
    svg = design.to_physical_svg(page_ref)
    print(page["display_name"], page_ref, len(svg))
```

`physical_page_metadata` is a narrow Altium extension keyed by
`page_occurrence_ref`. It contains channel/room/document presentation facts but
does not repeat components or nets:

```python
metadata_by_page = {
    row["page_occurrence_ref"]: row for row in payload["physical_page_metadata"]
}
```

## Drawing joins

A graphical selector is scoped by page:

```text
(page_occurrence_ref, artifact_key, element_id)
```

Current schematic SVG and IR use `artifact_key == "sch.dwg_scene"`.
`graphical_artifact_links` maps this selector to a semantic target:

```python
links = {
    (row["page_occurrence_ref"], row["artifact_key"], row["element_id"]): (
        row["target_type"],
        row["target_ref"],
    )
    for row in graph["graphical_artifact_links"]
}
```

Do not use a bare SVG element ID as a realized identity. The same logical
record may appear in several reused page occurrences.

## Variants

The graph represents the complete compiled schematic without applying a
project variant. `project.current_variant`, `variants[]`, and the top-level
component `dnp`/`fitted` fields carry variant state. A viewer applies that state
while retaining the complete graph.

## Compatibility

Design b0 is a breaking transport revision:

- Design a2 used `physical_pages` and page-derived indexes.
- Design b0 requires `compiled_schematic_graph` and `physical_page_metadata`.
- An a2 payload cannot be losslessly upgraded because it lacks authoritative
  local islands and hierarchy terminal bindings.
- Graph consumers should reject graph-absent or unsupported-schema schematic
  payloads with a clear migration error; they should not synthesize a graph
  from `physical_pages`.

The retained `design_a2.schema.json` documents archived payloads.
`design_b0.schema.json` is the current `AltiumDesign.to_json()` contract.

## Raw compiler diagnostics

`AltiumDesign.compile()` returns the beta raw compiled-design model. Use it for
compiler diagnostics and Altium-specific investigation. Use Design b0's
embedded graph for source-neutral application and visualization workflows.
