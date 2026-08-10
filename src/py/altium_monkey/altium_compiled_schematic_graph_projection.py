"""Project Altium compiled-design evidence into the generic schematic graph."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .altium_compiled_design_model import (
    AltiumCompiledComponent,
    AltiumCompiledDesign,
    AltiumCompiledNet,
    AltiumCompiledNetEndpoint,
    AltiumCompiledNetItem,
    AltiumCompiledNetTerminal,
    AltiumCompiledPhysicalDocument,
    AltiumCompiledPhysicalSheetSymbol,
)
from .altium_compiled_schematic_graph import (
    AltiumCompiledSchematicGraph,
    AltiumPhysicalPageMetadata,
    validate_compiled_schematic_graph,
)
from .altium_compiled_schematic_graph_identity import (
    SOURCE_PATH_KEY,
    SOURCE_UUID_KEY,
    SchCompiledSchematicGraphIdentityAllocator,
    compiled_schematic_graph_design_scope,
    compiled_schematic_pin_source_identity,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .altium_design import AltiumDesign
    from .altium_record_sch__pin import AltiumSchPin
    from .altium_schdoc_info import SchComponentInfo


def _source_identity(**values: object) -> dict[str, object]:
    return {
        key: value
        for key, value in sorted(values.items())
        if value not in (None, "", [], {})
    }


def _source_row(
    allocator: SchCompiledSchematicGraphIdentityAllocator,
    object_type: str,
    identity_source: Mapping[str, object],
    *,
    owner_refs: tuple[str, ...] = (),
    **fields: object,
) -> dict[str, object]:
    return {
        "type": object_type,
        "id": allocator.allocate_source(
            object_type=object_type,
            source_identity=identity_source,
            owner_refs=owner_refs,
        ),
        **fields,
    }


def _derived_row(
    allocator: SchCompiledSchematicGraphIdentityAllocator,
    object_type: str,
    identity: Mapping[str, object],
    **fields: object,
) -> dict[str, object]:
    return {
        "type": object_type,
        "id": allocator.allocate_derived(object_type=object_type, identity=identity),
        **fields,
    }


def _project_filename(design: "AltiumDesign", compiled: AltiumCompiledDesign) -> str:
    project = design.project
    if project is not None and project.filepath is not None:
        return project.filepath.name
    if compiled.source_path:
        return Path(compiled.source_path).name
    if compiled.logical_documents:
        return compiled.logical_documents[0].file_name
    return "untitled.SchDoc"


def _occurrence_source_path(
    physical_instance_unique_id: str,
    *,
    source_path: str,
    physical_instance_path: str,
) -> str:
    exact_path = str(physical_instance_unique_id or "").strip()
    if exact_path:
        return exact_path
    root_selector = str(source_path or physical_instance_path or "root").replace(
        "\\", "/"
    )
    return f"$root/{root_selector}"


@dataclass(frozen=True, slots=True)
class _BodyOccurrenceEvidence:
    id: str
    logical_document_id: str
    physical_document_id: str
    source_object_id: str
    source_designator: str
    physical_designator: str
    display_designator: str
    part_count: int
    current_part_id: int
    body_style: int
    source_component: SchComponentInfo


@dataclass(frozen=True, slots=True)
class _PageOccurrenceEvidence:
    key: str
    physical_document_id: str
    source_path: str
    instance_path: str
    ordinal: int
    parent_key: str | None = None
    physical_sheet_symbol: AltiumCompiledPhysicalSheetSymbol | None = None


def _root_page_occurrence_paths(
    physical: AltiumCompiledPhysicalDocument,
) -> tuple[str, str]:
    logical_path = str(physical.source_path or physical.file_name).replace("\\", "/")
    return (
        _occurrence_source_path(
            physical.physical_instance_unique_id,
            source_path=logical_path,
            physical_instance_path=physical.physical_instance_path,
        ),
        physical.physical_instance_path or physical.file_name,
    )


def _child_page_occurrence_paths(
    physical: AltiumCompiledPhysicalDocument,
    parent: _PageOccurrenceEvidence,
    symbol: AltiumCompiledPhysicalSheetSymbol,
) -> tuple[str, str]:
    source_object_id = str(getattr(symbol, "source_object_id", "") or "")
    if not source_object_id:
        raise ValueError("physical sheet placement requires stable source identity")
    exact_path = str(physical.physical_instance_unique_id or "").strip()
    source_path = exact_path or f"{parent.source_path.rstrip('/')}/{source_object_id}"
    designator = str(
        getattr(symbol, "physical_designator", "")
        or getattr(symbol, "logical_designator", "")
        or source_object_id
    )
    instance_path = (
        physical.physical_instance_path
        if exact_path and physical.physical_instance_path
        else f"{parent.instance_path}\\{designator}"
    )
    return source_path, instance_path


def _visit_page_occurrence(
    *,
    physical_id: str,
    physical_by_id: dict[str, AltiumCompiledPhysicalDocument],
    symbols_by_owner: dict[str, list[AltiumCompiledPhysicalSheetSymbol]],
    occurrences: list[_PageOccurrenceEvidence],
    active_templates: set[str],
    parent: _PageOccurrenceEvidence | None = None,
    symbol: AltiumCompiledPhysicalSheetSymbol | None = None,
) -> None:
    if physical_id in active_templates:
        raise ValueError(f"compiled physical hierarchy contains a cycle: {physical_id}")
    physical = physical_by_id[physical_id]
    if parent is not None and symbol is None:
        raise ValueError("child page occurrence requires a physical sheet placement")
    if parent is None:
        source_path, instance_path = _root_page_occurrence_paths(physical)
    else:
        assert symbol is not None
        source_path, instance_path = _child_page_occurrence_paths(
            physical, parent, symbol
        )
    occurrence = _PageOccurrenceEvidence(
        key=source_path,
        physical_document_id=physical_id,
        source_path=source_path,
        instance_path=instance_path,
        ordinal=len(occurrences),
        parent_key=parent.key if parent is not None else None,
        physical_sheet_symbol=symbol,
    )
    occurrences.append(occurrence)
    active_templates.add(physical_id)
    for child_symbol in symbols_by_owner.get(physical_id, ()):
        _visit_page_occurrence(
            physical_id=child_symbol.child_physical_document_id,
            physical_by_id=physical_by_id,
            symbols_by_owner=symbols_by_owner,
            occurrences=occurrences,
            active_templates=active_templates,
            parent=occurrence,
            symbol=child_symbol,
        )
    active_templates.remove(physical_id)


def _physical_hierarchy_indexes(
    compiled: AltiumCompiledDesign,
) -> tuple[
    dict[str, AltiumCompiledPhysicalDocument],
    dict[str, list[AltiumCompiledPhysicalSheetSymbol]],
    set[str],
]:
    physical_by_id = {row.id: row for row in compiled.physical_documents}
    symbols_by_owner: dict[str, list[AltiumCompiledPhysicalSheetSymbol]] = defaultdict(
        list
    )
    child_template_ids: set[str] = set()
    for symbol in compiled.physical_sheet_symbols:
        symbols_by_owner[symbol.owner_physical_document_id].append(symbol)
        child_template_ids.add(symbol.child_physical_document_id)
    for symbols in symbols_by_owner.values():
        symbols.sort(key=lambda row: (row.source_object_id, row.id))
    return physical_by_id, dict(symbols_by_owner), child_template_ids


def _expand_occurrence_roots(
    compiled: AltiumCompiledDesign,
    physical_by_id: dict[str, AltiumCompiledPhysicalDocument],
    symbols_by_owner: dict[str, list[AltiumCompiledPhysicalSheetSymbol]],
    child_template_ids: set[str],
) -> list[_PageOccurrenceEvidence]:
    occurrences: list[_PageOccurrenceEvidence] = []
    active_templates: set[str] = set()

    def visit(physical_id: str) -> None:
        _visit_page_occurrence(
            physical_id=physical_id,
            physical_by_id=physical_by_id,
            symbols_by_owner=symbols_by_owner,
            occurrences=occurrences,
            active_templates=active_templates,
        )

    ordered = sorted(compiled.physical_documents, key=lambda item: item.ordinal)
    for root in (row for row in ordered if row.id not in child_template_ids):
        visit(root.id)
    reached_templates = {row.physical_document_id for row in occurrences}
    for orphan in (row for row in ordered if row.id not in reached_templates):
        visit(orphan.id)
    return occurrences


def _realized_page_occurrences(
    compiled: AltiumCompiledDesign,
) -> tuple[_PageOccurrenceEvidence, ...]:
    """Expand reusable compiled page templates along every hierarchy placement."""

    physical_by_id, symbols_by_owner, child_template_ids = _physical_hierarchy_indexes(
        compiled
    )
    occurrences = _expand_occurrence_roots(
        compiled, physical_by_id, symbols_by_owner, child_template_ids
    )
    keys = [row.key for row in occurrences]
    if len(keys) != len(set(keys)):
        raise ValueError("realized schematic occurrence paths must be unique")
    return tuple(occurrences)


def _component_part_suffix(part_count: int, part_id: int) -> str:
    if part_count <= 1 or part_id <= 0:
        return ""
    value = part_id
    suffix = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        suffix = chr(ord("A") + remainder) + suffix
    return suffix


def _body_terminal_designators(body: _BodyOccurrenceEvidence) -> set[str]:
    values = {
        str(body.physical_designator or ""),
        str(body.display_designator or ""),
    }
    suffix = _component_part_suffix(body.part_count, body.current_part_id)
    if suffix:
        values.update(
            {
                f"{body.physical_designator}{suffix}",
                f"{body.display_designator}{suffix}",
            }
        )
    return {value for value in values if value}


def _source_components_by_logical_designator(
    design: "AltiumDesign", compiled: AltiumCompiledDesign
) -> dict[tuple[str, str], list[SchComponentInfo]]:
    result: dict[tuple[str, str], list[SchComponentInfo]] = defaultdict(list)
    for logical in compiled.logical_documents:
        if logical.ordinal >= len(design.schdocs):
            continue
        for component in design.schdocs[logical.ordinal].get_components():
            designator = str(component.designator or "").casefold()
            if designator:
                result[(logical.id, designator)].append(component)
    return dict(result)


def _body_occurrence_evidence(
    group: AltiumCompiledComponent, source_component: SchComponentInfo
) -> _BodyOccurrenceEvidence:
    source_uid = str(getattr(source_component, "unique_id", "") or "")
    if not source_uid:
        raise ValueError(f"compiled component body {group.id} has no stable source UID")
    record = getattr(source_component, "record", source_component)
    return _BodyOccurrenceEvidence(
        id=f"{group.id}:body:{source_uid}",
        logical_document_id=group.logical_document_id,
        physical_document_id=group.physical_document_id,
        source_object_id=source_uid,
        source_designator=str(getattr(source_component, "designator", "") or ""),
        physical_designator=group.physical_designator,
        display_designator=group.display_designator,
        part_count=int(getattr(record, "part_count", 1) or 1),
        current_part_id=int(getattr(record, "current_part_id", 1) or 1),
        body_style=int(getattr(source_component, "display_mode", 0) or 0),
        source_component=source_component,
    )


def _expanded_component_body_evidence(
    design: "AltiumDesign",
    compiled: AltiumCompiledDesign,
    component_body_evidence: Sequence[AltiumCompiledComponent],
) -> tuple[_BodyOccurrenceEvidence, ...]:
    source_components_by_logical_and_designator = (
        _source_components_by_logical_designator(design, compiled)
    )
    result: list[_BodyOccurrenceEvidence] = []
    seen: set[tuple[str, str]] = set()
    for group in component_body_evidence:
        source_components = source_components_by_logical_and_designator.get(
            (group.logical_document_id, group.logical_designator.casefold()), []
        )
        if not source_components:
            raise ValueError(
                f"compiled component group {group.id} has no source symbol bodies"
            )
        for source_component in source_components:
            body = _body_occurrence_evidence(group, source_component)
            key = (group.physical_document_id, body.source_object_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(body)
    return tuple(result)


def _pin_matches_terminal(
    pin: AltiumSchPin,
    body: _BodyOccurrenceEvidence,
    *,
    pin_designator: str,
    pin_name: str,
    pin_source_uid: str,
) -> bool:
    return (
        str(getattr(pin, "designator", "") or "") == pin_designator
        and str(getattr(pin, "name", "") or "") == pin_name
        and (
            not pin_source_uid
            or str(getattr(pin, "unique_id", "") or "") == pin_source_uid
        )
        and (
            (owner_part := int(getattr(pin, "owner_part_id", None) or 1)) <= 0
            or owner_part == max(1, body.current_part_id)
        )
    )


def _source_pin_for_terminal(
    source_component: SchComponentInfo,
    body: _BodyOccurrenceEvidence,
    *,
    pin_designator: str,
    pin_name: str,
    pin_source_uid: str = "",
) -> AltiumSchPin:
    candidates = [
        pin
        for pin in getattr(source_component, "pins", ()) or ()
        if _pin_matches_terminal(
            pin,
            body,
            pin_designator=pin_designator,
            pin_name=pin_name,
            pin_source_uid=pin_source_uid,
        )
    ]
    if len(candidates) != 1:
        raise ValueError(
            "compiled schematic terminal must resolve to exactly one source pin: "
            f"{body.id}:{pin_designator}:{pin_name} ({len(candidates)} matches)"
        )
    return candidates[0]


def _endpoint_source_identity(
    endpoint: AltiumCompiledNetEndpoint,
) -> dict[str, object]:
    source_uuid = str(
        getattr(endpoint, "object_id", "") or getattr(endpoint, "element_id", "") or ""
    )
    if not source_uuid:
        raise ValueError("semantic schematic endpoint requires source object identity")
    return _source_identity(**{SOURCE_UUID_KEY: source_uuid})


def _graphical_element_id(
    value: AltiumCompiledNetEndpoint | AltiumCompiledNetItem,
) -> str:
    return str(
        getattr(value, "element_id", "") or getattr(value, "object_id", "") or ""
    )


def _drawing_element_id(
    value: AltiumCompiledNetEndpoint | AltiumCompiledNetItem,
) -> str:
    """Return the retained DwgScene selector for compiled net evidence."""

    role = str(getattr(value, "role", "") or "")
    if role in {"harness_entry", "harness_port"}:
        return ""
    if role in {"sheet_entry", "port", "power_port"}:
        return str(
            getattr(value, "object_id", "") or getattr(value, "element_id", "") or ""
        )
    # Semantic boundary records are already represented by their endpoint.
    # The compiler also emits parallel net items, including synthetic selectors
    # which do not exist in the retained DwgScene.  They are connectivity
    # evidence only and must not become graphical-artifact links.
    kind = str(getattr(value, "kind", "") or "")
    if getattr(value, "removed", False) or kind in {
        "harness_port",
        "sheet_entry",
        "port",
        "power_port",
    }:
        return ""
    return _graphical_element_id(value)


def _component_body_drawing_element_ids(
    body: _BodyOccurrenceEvidence,
) -> tuple[str, ...]:
    """Return retained selectors for the visible body primitives of a component."""

    selectors = tuple(
        value
        for value in dict.fromkeys(
            str(value or "")
            for value in body.source_component.display_body_element_ids()
        )
        if value
    )
    # Some symbols render body geometry directly in the component group because
    # their child graphic records have no UID. The group selector is retained in
    # that case and is the only stable drawing evidence for the body.
    return selectors or (body.source_object_id,)


def _physical_local_net_evidence(net: AltiumCompiledNet) -> frozenset[str]:
    selectors: set[str] = set()
    for terminal in getattr(net, "terminals", ()) or ():
        selectors.add(
            "terminal\u001f"
            f"{getattr(terminal, 'designator', '')}\u001f"
            f"{getattr(terminal, 'pin', '')}\u001f"
            f"{getattr(terminal, 'pin_name', '')}"
        )
    for endpoint in getattr(net, "endpoints", ()) or ():
        element_id = _graphical_element_id(endpoint)
        if element_id:
            selectors.add(
                f"endpoint\u001f{getattr(endpoint, 'role', '')}\u001f{element_id}"
            )
    for item in getattr(net, "items", ()) or ():
        element_id = _graphical_element_id(item)
        if element_id:
            selectors.add(f"artifact\u001f{element_id}")
    return frozenset(selectors)


def _is_redundant_local_net(
    index: int,
    net: AltiumCompiledNet,
    candidates: Sequence[AltiumCompiledNet],
    evidence_by_id: dict[str, frozenset[str]],
) -> bool:
    evidence = evidence_by_id[net.id]
    if not evidence:
        return True
    for other_index, other in enumerate(candidates):
        if (
            other_index == index
            or other.physical_document_ids != net.physical_document_ids
        ):
            continue
        other_evidence = evidence_by_id[other.id]
        if evidence < other_evidence or (
            evidence == other_evidence and other_index < index
        ):
            return True
    return False


def _scalar_physical_local_nets(
    compiled: AltiumCompiledDesign,
) -> list[AltiumCompiledNet]:
    """Drop duplicate/subset compiler roots that do not form distinct islands."""

    candidates = [net for net in compiled.nets if net.scope == "physical_local"]
    evidence_by_id = {net.id: _physical_local_net_evidence(net) for net in candidates}
    return [
        net
        for index, net in enumerate(candidates)
        if not _is_redundant_local_net(index, net, candidates, evidence_by_id)
    ]


@dataclass
class _ProjectionState:
    design: "AltiumDesign"
    compiled: AltiumCompiledDesign
    allocator: SchCompiledSchematicGraphIdentityAllocator
    graph: AltiumCompiledSchematicGraph
    physical_by_id: dict[str, AltiumCompiledPhysicalDocument]
    realized_occurrences: tuple[_PageOccurrenceEvidence, ...]
    page_occurrence_by_key: dict[str, str]
    page_occurrences_by_physical: dict[str, list[str]]
    hierarchy_by_child_page: dict[str, str]


def _add_graphical_link(
    state: _ProjectionState,
    *,
    page_ref: str,
    target_type: str,
    target_ref: object,
    element_id: str,
) -> None:
    state.graph.graphical_artifact_links.append(
        _derived_row(
            state.allocator,
            "sch.graphical_artifact_link",
            {
                "page_occurrence_ref": page_ref,
                "target_type": target_type,
                "target_ref": target_ref,
                "artifact_key": "sch.dwg_scene",
                "element_id": element_id,
            },
            page_occurrence_ref=page_ref,
            target_type=target_type,
            target_ref=target_ref,
            artifact_key="sch.dwg_scene",
            element_id=element_id,
            source_identity={"sch.source_key.artifact_element": element_id},
        )
    )


def _build_definition_and_occurrence_rows(
    design: "AltiumDesign",
    compiled: AltiumCompiledDesign,
    allocator: SchCompiledSchematicGraphIdentityAllocator,
    graph: AltiumCompiledSchematicGraph,
) -> _ProjectionState:
    unit_definition_by_logical: dict[str, str] = {}
    page_definition_by_logical: dict[str, str] = {}
    for logical in compiled.logical_documents:
        source = _source_identity(
            **{
                SOURCE_PATH_KEY: str(logical.source_path or logical.file_name).replace(
                    "\\", "/"
                )
            }
        )
        unit = _source_row(
            allocator,
            "sch.unit_definition",
            source,
            display_name=logical.file_name,
            page_definition_refs=[],
            source_identity=source,
        )
        page = _source_row(
            allocator,
            "sch.page_definition",
            source,
            owner_refs=(str(unit["id"]),),
            unit_definition_ref=unit["id"],
            display_name=logical.file_name,
            source_identity=source,
        )
        unit["page_definition_refs"] = [page["id"]]
        graph.unit_definitions.append(unit)
        graph.page_definitions.append(page)
        unit_definition_by_logical[logical.id] = str(unit["id"])
        page_definition_by_logical[logical.id] = str(page["id"])

    logical_by_id = {row.id: row for row in compiled.logical_documents}
    physical_by_id = {row.id: row for row in compiled.physical_documents}
    realized_occurrences = _realized_page_occurrences(compiled)
    unit_occurrence_by_key: dict[str, str] = {}
    page_occurrence_by_key: dict[str, str] = {}
    page_occurrences_by_physical: dict[str, list[str]] = defaultdict(list)
    for occurrence in realized_occurrences:
        physical = physical_by_id[occurrence.physical_document_id]
        logical = logical_by_id[physical.logical_document_id]
        occurrence_source = _source_identity(
            **{SOURCE_PATH_KEY: occurrence.source_path}
        )
        unit = _source_row(
            allocator,
            "sch.unit_occurrence",
            occurrence_source,
            unit_definition_ref=unit_definition_by_logical[logical.id],
            display_name=occurrence.instance_path,
            page_occurrence_refs=[],
            source_identity=occurrence_source,
        )
        page = _source_row(
            allocator,
            "sch.page_occurrence",
            occurrence_source,
            owner_refs=(str(unit["id"]),),
            page_definition_ref=page_definition_by_logical[logical.id],
            unit_occurrence_ref=unit["id"],
            display_name=physical.file_name,
            sheet_number=physical.sheet_number or "",
            instance_order=occurrence.ordinal,
            source_identity=occurrence_source,
        )
        unit["page_occurrence_refs"] = [page["id"]]
        graph.unit_occurrences.append(unit)
        graph.page_occurrences.append(page)
        unit_occurrence_by_key[occurrence.key] = str(unit["id"])
        page_occurrence_by_key[occurrence.key] = str(page["id"])
        page_occurrences_by_physical[physical.id].append(str(page["id"]))

    state = _ProjectionState(
        design=design,
        compiled=compiled,
        allocator=allocator,
        graph=graph,
        physical_by_id=physical_by_id,
        realized_occurrences=realized_occurrences,
        page_occurrence_by_key=page_occurrence_by_key,
        page_occurrences_by_physical=page_occurrences_by_physical,
        hierarchy_by_child_page={},
    )
    _build_hierarchy_rows(state, unit_occurrence_by_key)
    return state


def _build_hierarchy_rows(
    state: _ProjectionState, unit_occurrence_by_key: dict[str, str]
) -> None:
    drawing_candidates: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(
        list
    )
    occurrence_by_key = {row.key: row for row in state.realized_occurrences}
    for occurrence in state.realized_occurrences:
        symbol = occurrence.physical_sheet_symbol
        if symbol is None or occurrence.parent_key is None:
            continue
        parent = occurrence_by_key[occurrence.parent_key]
        parent_page_ref = state.page_occurrence_by_key[parent.key]
        child_page_ref = state.page_occurrence_by_key[occurrence.key]
        child_unit_ref = unit_occurrence_by_key[occurrence.key]
        source = _source_identity(
            **{
                SOURCE_UUID_KEY: symbol.source_object_id,
                SOURCE_PATH_KEY: parent.source_path,
            }
        )
        hierarchy = _source_row(
            state.allocator,
            "sch.hierarchy_occurrence",
            source,
            owner_refs=(parent_page_ref, child_unit_ref),
            parent_unit_occurrence_ref=unit_occurrence_by_key[parent.key],
            parent_page_occurrence_ref=parent_page_ref,
            child_unit_occurrence_ref=child_unit_ref,
            source_identity=source,
        )
        state.graph.hierarchy_occurrences.append(hierarchy)
        hierarchy_ref = str(hierarchy["id"])
        state.hierarchy_by_child_page[child_page_ref] = hierarchy_ref
        drawing_candidates[(parent_page_ref, str(symbol.source_object_id))].append(
            (parent_page_ref, hierarchy_ref, str(symbol.source_object_id))
        )

    for candidates in drawing_candidates.values():
        if len(candidates) == 1:
            page_ref, hierarchy_ref, element_id = candidates[0]
            _add_graphical_link(
                state,
                page_ref=page_ref,
                target_type="sch.hierarchy_occurrence",
                target_ref=hierarchy_ref,
                element_id=element_id,
            )
    unit_by_id = {str(row["id"]): row for row in state.graph.unit_occurrences}
    page_by_id = {str(row["id"]): row for row in state.graph.page_occurrences}
    for child_page_ref, hierarchy_ref in state.hierarchy_by_child_page.items():
        unit_ref = str(page_by_id[child_page_ref]["unit_occurrence_ref"])
        unit_by_id[unit_ref]["parent_hierarchy_occurrence_ref"] = hierarchy_ref


def _build_component_rows(
    state: _ProjectionState,
    component_body_evidence: Sequence[AltiumCompiledComponent],
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], _BodyOccurrenceEvidence]]:
    component_by_body_and_page: dict[tuple[str, str], str] = {}
    body_candidates: dict[tuple[str, str], list[_BodyOccurrenceEvidence]] = defaultdict(
        list
    )
    for body in _expanded_component_body_evidence(
        state.design, state.compiled, component_body_evidence
    ):
        page_refs = state.page_occurrences_by_physical.get(
            body.physical_document_id, ()
        )
        if not page_refs or not body.source_object_id:
            continue
        for page_ref in page_refs:
            source = _source_identity(**{SOURCE_UUID_KEY: body.source_object_id})
            component = _source_row(
                state.allocator,
                "sch.component_occurrence",
                source,
                owner_refs=(page_ref,),
                page_occurrence_ref=page_ref,
                source_designator=body.source_designator,
                physical_designator=body.physical_designator,
                display_designator=body.display_designator,
                unit=max(1, body.current_part_id),
                body_style=body.body_style,
                source_identity=source,
            )
            state.graph.component_occurrences.append(component)
            component_by_body_and_page[(body.id, page_ref)] = str(component["id"])
            for designator in _body_terminal_designators(body):
                body_candidates[(page_ref, designator.casefold())].append(body)
            for element_id in _component_body_drawing_element_ids(body):
                _add_graphical_link(
                    state,
                    page_ref=page_ref,
                    target_type="sch.component_occurrence",
                    target_ref=component["id"],
                    element_id=element_id,
                )
    unambiguous_bodies = {
        key: candidates[0]
        for key, candidates in body_candidates.items()
        if len({candidate.id for candidate in candidates}) == 1
    }
    return component_by_body_and_page, unambiguous_bodies


def _aggregate_net_elements(
    nets: Sequence[AltiumCompiledNet],
) -> set[tuple[str, str]]:
    net_ids_by_element: dict[tuple[str, str], set[str]] = defaultdict(set)
    for net in nets:
        physical_id = net.physical_document_ids[0]
        for value in (*net.endpoints, *net.items):
            element_id = _graphical_element_id(value)
            if element_id:
                net_ids_by_element[(physical_id, element_id)].add(net.id)
    return {key for key, net_ids in net_ids_by_element.items() if len(net_ids) > 1}


def _pin_endpoint_uids(
    net: AltiumCompiledNet, terminal: AltiumCompiledNetTerminal
) -> set[str]:
    result: set[str] = set()
    for endpoint in net.endpoints:
        element_id = _graphical_element_id(endpoint)
        if (
            endpoint.role == "pin"
            and endpoint.designator.casefold() == terminal.designator.casefold()
            and endpoint.pin == terminal.pin
            and endpoint.pin_name == terminal.pin_name
            and element_id
        ):
            result.add(element_id)
    return result


def _component_pin_terminal_row(
    state: _ProjectionState,
    *,
    net: AltiumCompiledNet,
    terminal: AltiumCompiledNetTerminal,
    page_ref: str,
    body: _BodyOccurrenceEvidence,
    component_by_body_and_page: dict[tuple[str, str], str],
) -> tuple[dict[str, object], str]:
    pin_endpoint_uids = _pin_endpoint_uids(net, terminal)
    pin = _source_pin_for_terminal(
        body.source_component,
        body,
        pin_designator=terminal.pin,
        pin_name=terminal.pin_name,
        pin_source_uid=next(iter(pin_endpoint_uids))
        if len(pin_endpoint_uids) == 1
        else "",
    )
    source = compiled_schematic_pin_source_identity(
        component_source_uid=body.source_object_id,
        owner_part_id=getattr(pin, "owner_part_id", None),
        pin_designator=str(getattr(pin, "designator", "") or ""),
        pin_name=str(getattr(pin, "name", "") or ""),
        pin_source_uid=getattr(pin, "unique_id", None),
    )
    component_ref = component_by_body_and_page[(body.id, page_ref)]
    row = _source_row(
        state.allocator,
        "sch.terminal_occurrence",
        source,
        owner_refs=(page_ref, component_ref),
        page_occurrence_ref=page_ref,
        role="component_pin",
        component_occurrence_ref=component_ref,
        name=terminal.pin_name,
        pin_designator=terminal.pin,
        resolution_diagnostics=["logical_pin_unresolved"],
        source_identity=source,
    )
    return row, str(getattr(pin, "unique_id", "") or "")


def _boundary_terminal_row(
    state: _ProjectionState, endpoint: AltiumCompiledNetEndpoint, page_ref: str
) -> dict[str, object]:
    role = str(endpoint.role or "")
    diagnostics = ["design_net_unresolved"]
    if role == "sheet_entry":
        diagnostics.append("hierarchy_terminal_binding_unresolved")
    source = _endpoint_source_identity(endpoint)
    return _source_row(
        state.allocator,
        "sch.terminal_occurrence",
        source,
        owner_refs=(page_ref,),
        page_occurrence_ref=page_ref,
        role=role,
        name=str(endpoint.name or ""),
        pin_designator="",
        resolution_diagnostics=diagnostics,
        source_identity=source,
    )


def _collect_net_page_terminals(
    state: _ProjectionState,
    *,
    net: AltiumCompiledNet,
    physical_id: str,
    page_ref: str,
    aggregate_elements: set[tuple[str, str]],
    component_by_body_and_page: dict[tuple[str, str], str],
    body_by_page_and_designator: dict[tuple[str, str], _BodyOccurrenceEvidence],
    row_by_page_element: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for terminal in net.terminals:
        body = body_by_page_and_designator.get(
            (page_ref, terminal.designator.casefold())
        )
        if body is None:
            raise ValueError(
                "compiled schematic pin terminal has no component body evidence: "
                f"{physical_id}:{terminal.designator}:{terminal.pin}"
            )
        row, element_id = _component_pin_terminal_row(
            state,
            net=net,
            terminal=terminal,
            page_ref=page_ref,
            body=body,
            component_by_body_and_page=component_by_body_and_page,
        )
        rows.append(row)
        if element_id:
            row_by_page_element[(page_ref, element_id)] = row
            _add_graphical_link(
                state,
                page_ref=page_ref,
                target_type="sch.terminal_occurrence",
                target_ref=row["id"],
                element_id=element_id,
            )
    for endpoint in net.endpoints:
        if str(endpoint.role or "") not in {"sheet_entry", "port", "power_port"}:
            continue
        connectivity_id = _graphical_element_id(endpoint)
        if (physical_id, connectivity_id) in aggregate_elements:
            continue
        row = _boundary_terminal_row(state, endpoint, page_ref)
        rows.append(row)
        drawing_id = _drawing_element_id(endpoint)
        if drawing_id:
            row_by_page_element[(page_ref, drawing_id)] = row
    return rows


def _collect_terminal_candidates(
    state: _ProjectionState,
    *,
    physical_local_nets: Sequence[AltiumCompiledNet],
    aggregate_elements: set[tuple[str, str]],
    component_by_body_and_page: dict[tuple[str, str], str],
    body_by_page_and_designator: dict[tuple[str, str], _BodyOccurrenceEvidence],
) -> tuple[
    dict[tuple[str, str], list[dict[str, object]]],
    dict[tuple[str, str], dict[str, object]],
]:
    rows_by_net_page: dict[tuple[str, str], list[dict[str, object]]] = {}
    row_by_page_element: dict[tuple[str, str], dict[str, object]] = {}
    for net in physical_local_nets:
        physical_id = net.physical_document_ids[0]
        for page_ref in state.page_occurrences_by_physical[physical_id]:
            rows_by_net_page[(net.id, page_ref)] = _collect_net_page_terminals(
                state,
                net=net,
                physical_id=physical_id,
                page_ref=page_ref,
                aggregate_elements=aggregate_elements,
                component_by_body_and_page=component_by_body_and_page,
                body_by_page_and_designator=body_by_page_and_designator,
                row_by_page_element=row_by_page_element,
            )
    return rows_by_net_page, row_by_page_element


def _local_net_topology(
    net: AltiumCompiledNet, terminal_rows: list[dict[str, object]]
) -> dict[str, list[str]]:
    terminal_refs = sorted(str(row["id"]) for row in terminal_rows)
    if terminal_refs:
        return {"terminal_occurrence_refs": terminal_refs}
    graphical_selectors = sorted(
        {
            f"sch.dwg_scene\u001f{element_id}"
            for element_id in (_drawing_element_id(item) for item in net.items)
            if element_id
        }
    )
    return {"graphical_selectors": graphical_selectors} if graphical_selectors else {}


def _materialize_local_nets(
    state: _ProjectionState,
    physical_local_nets: Sequence[AltiumCompiledNet],
    terminal_rows_by_net_page: dict[tuple[str, str], list[dict[str, object]]],
) -> dict[tuple[str, str], str]:
    refs: dict[tuple[str, str], str] = {}
    for net in physical_local_nets:
        physical_id = net.physical_document_ids[0]
        for page_ref in state.page_occurrences_by_physical[physical_id]:
            terminal_rows = terminal_rows_by_net_page[(net.id, page_ref)]
            topology = _local_net_topology(net, terminal_rows)
            if not topology:
                continue
            local_ref = state.allocator.allocate_derived(
                object_type="sch.local_net_occurrence",
                identity={"page_occurrence_ref": page_ref, **topology},
            )
            source = _source_identity(**{"sch.source_key.compiled_net": net.id})
            state.graph.local_net_occurrences.append(
                {
                    "type": "sch.local_net_occurrence",
                    "id": local_ref,
                    "page_occurrence_ref": page_ref,
                    "display_name": net.name,
                    "qualified_name": net.name,
                    "aliases": list(net.aliases),
                    "source_identity": source,
                }
            )
            refs[(net.id, page_ref)] = local_ref
            for terminal_row in terminal_rows:
                terminal_row["local_net_occurrence_ref"] = local_ref
                state.graph.terminal_occurrences.append(terminal_row)
    return refs


def _drawing_target(
    *,
    physical_id: str,
    page_ref: str,
    connectivity_id: str,
    drawing_id: str,
    local_ref: str,
    aggregate_elements: set[tuple[str, str]],
    terminal_by_page_element: dict[tuple[str, str], dict[str, object]],
) -> tuple[str, str]:
    if (physical_id, connectivity_id) in aggregate_elements:
        return "sch.page_occurrence", page_ref
    terminal = terminal_by_page_element.get((page_ref, drawing_id))
    if terminal is not None:
        return "sch.terminal_occurrence", str(terminal["id"])
    return "sch.local_net_occurrence", local_ref


def _build_net_drawing_links(
    state: _ProjectionState,
    *,
    physical_local_nets: Sequence[AltiumCompiledNet],
    aggregate_elements: set[tuple[str, str]],
    local_net_ref_by_net_page: dict[tuple[str, str], str],
    terminal_by_page_element: dict[tuple[str, str], dict[str, object]],
) -> None:
    targets = {
        (str(link["page_occurrence_ref"]), str(link["element_id"])): (
            str(link["target_type"]),
            str(link["target_ref"]),
        )
        for link in state.graph.graphical_artifact_links
    }
    emitted = set(targets)
    for net in physical_local_nets:
        physical_id = net.physical_document_ids[0]
        for page_ref in state.page_occurrences_by_physical[physical_id]:
            local_ref = local_net_ref_by_net_page.get((net.id, page_ref))
            if not local_ref:
                continue
            for item in (*net.endpoints, *net.items):
                drawing_id = _drawing_element_id(item)
                if not drawing_id:
                    continue
                target = _drawing_target(
                    physical_id=physical_id,
                    page_ref=page_ref,
                    connectivity_id=_graphical_element_id(item),
                    drawing_id=drawing_id,
                    local_ref=local_ref,
                    aggregate_elements=aggregate_elements,
                    terminal_by_page_element=terminal_by_page_element,
                )
                selector = (page_ref, drawing_id)
                if (
                    targets.setdefault(selector, target) != target
                    or selector in emitted
                ):
                    continue
                _add_graphical_link(
                    state,
                    page_ref=page_ref,
                    target_type=target[0],
                    target_ref=target[1],
                    element_id=drawing_id,
                )
                emitted.add(selector)


def _build_net_rows(
    state: _ProjectionState,
    component_by_body_and_page: dict[tuple[str, str], str],
    body_by_page_and_designator: dict[tuple[str, str], _BodyOccurrenceEvidence],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    nets = _scalar_physical_local_nets(state.compiled)
    if any(len(net.physical_document_ids) != 1 for net in nets):
        raise ValueError("physical-local nets must belong to exactly one physical page")
    aggregate_elements = _aggregate_net_elements(nets)
    rows_by_net_page, row_by_page_element = _collect_terminal_candidates(
        state,
        physical_local_nets=nets,
        aggregate_elements=aggregate_elements,
        component_by_body_and_page=component_by_body_and_page,
        body_by_page_and_designator=body_by_page_and_designator,
    )
    local_refs = _materialize_local_nets(state, nets, rows_by_net_page)
    _build_net_drawing_links(
        state,
        physical_local_nets=nets,
        aggregate_elements=aggregate_elements,
        local_net_ref_by_net_page=local_refs,
        terminal_by_page_element=row_by_page_element,
    )
    return rows_by_net_page


def _add_hierarchy_binding(
    state: _ProjectionState,
    emitted_pairs: set[tuple[str, str]],
    *,
    hierarchy_ref: str,
    parent: dict[str, object],
    child: dict[str, object],
    source_identity: dict[str, object],
) -> None:
    pair = (str(parent["id"]), str(child["id"]))
    if pair in emitted_pairs:
        return
    state.graph.hierarchy_terminal_bindings.append(
        _derived_row(
            state.allocator,
            "sch.hierarchy_terminal_binding",
            {
                "hierarchy_occurrence_ref": hierarchy_ref,
                "parent_terminal_occurrence_ref": parent["id"],
                "child_terminal_occurrence_ref": child["id"],
            },
            hierarchy_occurrence_ref=hierarchy_ref,
            parent_terminal_occurrence_ref=parent["id"],
            child_terminal_occurrence_ref=child["id"],
            source_identity=source_identity,
        )
    )
    emitted_pairs.add(pair)
    raw_diagnostics = parent.get("resolution_diagnostics", [])
    diagnostics = raw_diagnostics if isinstance(raw_diagnostics, list) else []
    parent["resolution_diagnostics"] = [
        value
        for value in diagnostics
        if value != "hierarchy_terminal_binding_unresolved"
    ]


def _terminal_role_index(
    rows_by_net_page: dict[tuple[str, str], list[dict[str, object]]],
) -> dict[tuple[str, str, str], list[dict[str, object]]]:
    result: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for (net_id, page_ref), rows in rows_by_net_page.items():
        for row in rows:
            result[(net_id, page_ref, str(row["role"]))].append(row)
    return result


def _bind_inter_sheet_child(
    state: _ProjectionState,
    terminal_index: dict[tuple[str, str, str], list[dict[str, object]]],
    emitted_pairs: set[tuple[str, str]],
    hierarchy_by_id: dict[str, dict[str, object]],
    link: AltiumCompiledNet,
    names: set[str],
    child_page_ref: str,
    children: list[dict[str, object]],
) -> None:
    hierarchy_ref = state.hierarchy_by_child_page.get(child_page_ref)
    if not hierarchy_ref:
        return
    parent_page_ref = str(hierarchy_by_id[hierarchy_ref]["parent_page_occurrence_ref"])
    parents = terminal_index.get(
        (link.parent_net_id, parent_page_ref, "sheet_entry"), []
    )
    named_parents = [
        row for row in parents if str(row.get("name", "")).casefold() in names
    ]
    named_children = [
        row for row in children if str(row.get("name", "")).casefold() in names
    ]
    if len(named_parents) == len(named_children) == 1:
        _add_hierarchy_binding(
            state,
            emitted_pairs,
            hierarchy_ref=hierarchy_ref,
            parent=named_parents[0],
            child=named_children[0],
            source_identity={"sch.source_key.compiled_net": link.id},
        )


def _bind_inter_sheet_link(
    state: _ProjectionState,
    terminal_index: dict[tuple[str, str, str], list[dict[str, object]]],
    emitted_pairs: set[tuple[str, str]],
    hierarchy_by_id: dict[str, dict[str, object]],
    link: AltiumCompiledNet,
) -> None:
    names = {str(link.name or "").casefold()}
    names.update(str(alias or "").casefold() for alias in link.aliases)
    for (net_id, page_ref, role), rows in terminal_index.items():
        if net_id == link.child_net_id and role == "port":
            _bind_inter_sheet_child(
                state,
                terminal_index,
                emitted_pairs,
                hierarchy_by_id,
                link,
                names,
                page_ref,
                rows,
            )


def _bind_inter_sheet_links(
    state: _ProjectionState,
    terminal_index: dict[tuple[str, str, str], list[dict[str, object]]],
    emitted_pairs: set[tuple[str, str]],
) -> None:
    hierarchy_by_id = {str(row["id"]): row for row in state.graph.hierarchy_occurrences}
    for link in (net for net in state.compiled.nets if net.scope == "inter_sheet_link"):
        _bind_inter_sheet_link(
            state, terminal_index, emitted_pairs, hierarchy_by_id, link
        )


def _entry_source_ids_by_symbol(design: "AltiumDesign") -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for schdoc in design.schdocs:
        for sheet_symbol in schdoc.get_sheet_symbols():
            symbol_uid = str(sheet_symbol.unique_id or "").casefold()
            result[symbol_uid].update(
                str(getattr(entry, "unique_id", "") or "").casefold()
                for entry in sheet_symbol.entries
                if getattr(entry, "unique_id", "")
            )
    return result


def _terminal_source_uuid(row: dict[str, object]) -> str:
    source_identity = row.get("source_identity")
    if not isinstance(source_identity, dict):
        return ""
    return str(source_identity.get(SOURCE_UUID_KEY, "")).casefold()


def _bind_symbol_occurrence(
    state: _ProjectionState,
    emitted_pairs: set[tuple[str, str]],
    terminal_index: dict[tuple[str, str], list[dict[str, object]]],
    source_ids_by_symbol: dict[str, set[str]],
    occurrence: _PageOccurrenceEvidence,
) -> None:
    symbol = occurrence.physical_sheet_symbol
    if symbol is None or occurrence.parent_key is None:
        return
    child_page_ref = state.page_occurrence_by_key[occurrence.key]
    parent_page_ref = state.page_occurrence_by_key[occurrence.parent_key]
    hierarchy_ref = state.hierarchy_by_child_page[child_page_ref]
    symbol_uid = str(symbol.source_object_id).casefold()
    symbol_prefix = f"{symbol_uid}_"
    entry_source_ids = source_ids_by_symbol.get(symbol_uid, set())
    parents = terminal_index.get((parent_page_ref, "sheet_entry"), [])
    for child in terminal_index.get((child_page_ref, "port"), []):
        name = str(child.get("name", "")).casefold()
        matches = [
            parent
            for parent in parents
            if str(parent.get("name", "")).casefold() == name
            and (
                _terminal_source_uuid(parent) in entry_source_ids
                or _terminal_source_uuid(parent).startswith(symbol_prefix)
            )
        ]
        if len(matches) == 1:
            _add_hierarchy_binding(
                state,
                emitted_pairs,
                hierarchy_ref=hierarchy_ref,
                parent=matches[0],
                child=child,
                source_identity={
                    SOURCE_UUID_KEY: symbol.source_object_id,
                    "sch.source_key.source_subobject": name,
                },
            )


def _bind_symbol_endpoints(
    state: _ProjectionState, emitted_pairs: set[tuple[str, str]]
) -> None:
    terminal_index: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in state.graph.terminal_occurrences:
        terminal_index[(str(row["page_occurrence_ref"]), str(row["role"]))].append(row)
    source_ids_by_symbol = _entry_source_ids_by_symbol(state.design)
    for occurrence in state.realized_occurrences:
        _bind_symbol_occurrence(
            state, emitted_pairs, terminal_index, source_ids_by_symbol, occurrence
        )


def _build_hierarchy_bindings(
    state: _ProjectionState,
    rows_by_net_page: dict[tuple[str, str], list[dict[str, object]]],
) -> None:
    emitted_pairs: set[tuple[str, str]] = set()
    _bind_inter_sheet_links(
        state, _terminal_role_index(rows_by_net_page), emitted_pairs
    )
    _bind_symbol_endpoints(state, emitted_pairs)


def _physical_page_metadata(
    state: _ProjectionState,
) -> tuple[AltiumPhysicalPageMetadata, ...]:
    return tuple(
        AltiumPhysicalPageMetadata(
            page_occurrence_ref=state.page_occurrence_by_key[occurrence.key],
            physical_instance_path=occurrence.instance_path,
            channel_index=physical.channel_index,
            channel_prefix=physical.channel_prefix or "",
            channel_alpha=physical.channel_alpha or "",
            room_name=physical.room_name,
            physical_room_name=physical.physical_room_name,
            document_number=physical.document_number or "",
        )
        for occurrence in state.realized_occurrences
        for physical in (state.physical_by_id[occurrence.physical_document_id],)
    )


def build_compiled_schematic_graph(
    design: "AltiumDesign",
    compiled: AltiumCompiledDesign,
    *,
    component_body_evidence: Sequence[AltiumCompiledComponent],
) -> tuple[AltiumCompiledSchematicGraph, tuple[AltiumPhysicalPageMetadata, ...]]:
    """Build the generic graph and narrow Altium physical-page metadata."""

    scope = compiled_schematic_graph_design_scope(
        source_cad="altium",
        project={"filename": _project_filename(design, compiled)},
    )
    allocator = SchCompiledSchematicGraphIdentityAllocator(design_scope=scope)
    graph = AltiumCompiledSchematicGraph()

    state = _build_definition_and_occurrence_rows(design, compiled, allocator, graph)
    component_occurrence_by_body_and_page, body_by_page_and_designator = (
        _build_component_rows(state, component_body_evidence)
    )

    terminal_rows_by_net_and_page = _build_net_rows(
        state,
        component_occurrence_by_body_and_page,
        body_by_page_and_designator,
    )

    _build_hierarchy_bindings(state, terminal_rows_by_net_and_page)

    metadata = _physical_page_metadata(state)
    validate_compiled_schematic_graph(graph.to_json())
    return graph, metadata


__all__ = ["build_compiled_schematic_graph"]
