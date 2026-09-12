"""Adapters from compiled schematic designs to the legacy netlist model."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ._compiler_source import _compiler_document_source
from ._logical_source_identity import _logical_source_identity_key
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key, dotnet_trim
from .altium_compiled_design_model import (
    AltiumCompiledComponent,
    AltiumCompiledDesign,
    AltiumCompiledLogicalDocument,
    AltiumCompiledNet,
    AltiumCompiledNetEndpoint,
    AltiumCompiledNetItem,
    AltiumCompiledNetTerminal,
    AltiumCompiledPhysicalDocument,
    AltiumCompiledSheetSymbol,
)
from .altium_compiled_design_support import (
    _compiler_harness_entries,
    _build_port_location_map,
    _harness_connector_master_entry_point,
    _harness_entry_connection_point,
    _parse_entry_repeat,
    _point_key,
    find_harness_bundle_info,
)
from .altium_netlist_common import (
    _component_part_alpha_suffix,
    _sheet_entry_display_name as _entry_display_name,
    _unique_nonempty_strings as _unique_nonempty,
)
from .altium_netlist_model import (
    GraphicalPinRef,
    Net,
    NetEndpoint,
    NetGraphical,
    Netlist,
    NetlistComponent,
    NetlistSourcePage,
    PinType,
    Terminal,
)
from .altium_netlist_wire_connectivity import (
    altium_internal_tolerance_for_display_unit,
)
from .altium_schdoc_info import SchSheetSymbolInfo

if TYPE_CHECKING:
    from .altium_schdoc import AltiumSchDoc

SCHEMATIC_HIERARCHY_SCHEMA = "altium_monkey.schematic_hierarchy.a1"
_CompileMask = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class _LegacyDesignatorResolver:
    by_name: Mapping[str, str]
    by_occurrence_uid: Mapping[tuple[str, str], str]
    by_occurrence_pin: Mapping[tuple[str, int], str]
    physical_document_ids: frozenset[str]
    display_designators: frozenset[str]
    component_ids_by_page_and_designator: Mapping[tuple[str, str], str]
    component_ids_by_page_and_source_uid: Mapping[tuple[str, str], str]
    component_ids_by_page_and_pin_object: Mapping[tuple[str, int], str]
    component_designators_by_id: Mapping[str, str]
    source_files_by_physical_id: Mapping[str, str]

    def physical_document_id(self, item_id: str) -> str:
        physical_document_id, separator, _ = item_id.partition(":physical_net:")
        if separator and physical_document_id in self.physical_document_ids:
            return physical_document_id
        return ""

    def resolve(
        self,
        designator: str,
        *,
        item_id: str,
        source_component_uid: str = "",
        source_pin_object_id: int = 0,
    ) -> str:
        physical_document_id = self.physical_document_id(item_id)
        if physical_document_id and source_component_uid:
            resolved = self.by_occurrence_uid.get(
                (
                    physical_document_id,
                    dotnet_ordinal_ignore_case_key(source_component_uid),
                )
            )
            if resolved is not None:
                return resolved
        if physical_document_id and source_pin_object_id:
            resolved = self.by_occurrence_pin.get(
                (physical_document_id, source_pin_object_id)
            )
            if resolved is not None:
                return resolved
        if designator in self.display_designators:
            return designator
        return self.by_name.get(
            dotnet_ordinal_ignore_case_key(designator),
            designator,
        )

    def component_id(
        self,
        designator: str,
        *,
        item_id: str,
        source_component_uid: str = "",
        source_pin_object_id: int = 0,
    ) -> str:
        """Resolve a component ID inside the exact physical-page namespace."""
        physical_document_id = self.physical_document_id(item_id)
        if not physical_document_id:
            return ""
        if source_component_uid:
            resolved = self.component_ids_by_page_and_source_uid.get(
                (
                    physical_document_id,
                    dotnet_ordinal_ignore_case_key(source_component_uid),
                )
            )
            if resolved is not None:
                return resolved
        if source_pin_object_id:
            resolved = self.component_ids_by_page_and_pin_object.get(
                (physical_document_id, source_pin_object_id)
            )
            if resolved is not None:
                return resolved
        return self.component_ids_by_page_and_designator.get(
            (physical_document_id, designator), ""
        )

    def source_page(self, item_id: str) -> NetlistSourcePage | None:
        """Return correlated page identity without parsing beyond compiled IDs."""
        return _compiled_source_page(
            self.physical_document_id(item_id), self.source_files_by_physical_id
        )

    def canonical_designator(self, component_id: str, fallback: str) -> str:
        """Return the aggregate component name for an exact component identity."""
        return self.component_designators_by_id.get(component_id, fallback)


def _compiled_source_page(
    physical_document_id: str,
    source_files_by_physical_id: Mapping[str, str],
) -> NetlistSourcePage | None:
    if not physical_document_id:
        return None
    return NetlistSourcePage(
        physical_document_id=physical_document_id,
        source_sheet_file=source_files_by_physical_id.get(physical_document_id) or None,
    )


def _add_legacy_designator_alias_candidate(
    aliases: dict[str, set[str]],
    alias: str,
    display_designator: str,
) -> None:
    if not alias:
        return
    aliases[dotnet_ordinal_ignore_case_key(alias)].add(display_designator)


def _legacy_component_designator_aliases_for_bases(
    component: AltiumCompiledComponent,
    bases: Iterable[str],
) -> Iterable[str]:
    seen: set[str] = set()
    for base_designator in bases:
        if not base_designator or base_designator in seen:
            continue
        seen.add(base_designator)
        yield base_designator
        for part_id in range(1, component.part_count + 1):
            suffix = _component_part_alpha_suffix(
                part_count=component.part_count,
                current_part_id=part_id,
            )
            if suffix:
                yield f"{base_designator}{suffix}"


def _legacy_designator_alias_candidates(
    components: Iterable[AltiumCompiledComponent],
    *,
    logical: bool,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for component in components:
        display_designator = component.display_designator
        if not display_designator:
            continue
        bases = (
            (component.logical_designator,)
            if logical
            else (component.physical_designator, component.display_designator)
        )
        for alias in _legacy_component_designator_aliases_for_bases(
            component,
            bases,
        ):
            _add_legacy_designator_alias_candidate(
                result,
                alias,
                display_designator,
            )
    return result


def _unique_legacy_designator_aliases(
    candidates: Mapping[str, set[str]],
    *,
    blocked: Collection[str] = (),
) -> dict[str, str]:
    return {
        alias: next(iter(designators))
        for alias, designators in candidates.items()
        if alias not in blocked and len(designators) == 1
    }


def _legacy_designator_aliases(compiled: AltiumCompiledDesign) -> dict[str, str]:
    # Protect every physical multipart name before admitting logical aliases.
    # A board annotation may intentionally assign one component the logical
    # designator that another component owns in the physical namespace.
    physical_candidates = _legacy_designator_alias_candidates(
        compiled.components,
        logical=False,
    )
    logical_candidates = _legacy_designator_alias_candidates(
        compiled.components,
        logical=True,
    )
    result = _unique_legacy_designator_aliases(physical_candidates)
    result.update(
        _unique_legacy_designator_aliases(
            logical_candidates,
            blocked=physical_candidates,
        )
    )
    return result


def _legacy_occurrence_uid_designators(
    compiled: AltiumCompiledDesign,
) -> dict[tuple[str, str], str]:
    # Board annotations can make one component's logical designator equal a
    # different component's physical designator. Resolve exact source
    # occurrences before consulting the necessarily lossy display-name aliases.
    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    components = compiled._component_body_evidence or compiled.components
    for component in components:
        if (
            not component.physical_document_id
            or not component.source_object_id
            or not component.display_designator
        ):
            continue
        candidates[
            (
                component.physical_document_id,
                dotnet_ordinal_ignore_case_key(component.source_object_id),
            )
        ].add(component.display_designator)
    return {
        identity: next(iter(designators))
        for identity, designators in candidates.items()
        if len(designators) == 1
    }


def _legacy_occurrence_pin_designators(
    compiled: AltiumCompiledDesign,
    resolver: _LegacyDesignatorResolver,
) -> dict[tuple[str, int], str]:
    candidates: dict[tuple[str, int], set[str]] = defaultdict(set)
    for net in compiled.nets:
        for terminal in net.terminals:
            if terminal._source_pin_object_id <= 0:
                continue
            physical_document_id = resolver.physical_document_id(terminal.id)
            if not physical_document_id:
                continue
            candidates[(physical_document_id, terminal._source_pin_object_id)].add(
                resolver.resolve(
                    terminal.designator,
                    item_id=terminal.id,
                    source_component_uid=terminal._source_component_uid,
                )
            )
    return {
        identity: next(iter(designators))
        for identity, designators in candidates.items()
        if len(designators) == 1
    }


def _legacy_component_ids_by_source_uid(
    compiled: AltiumCompiledDesign,
) -> dict[tuple[str, str], str]:
    """Bind every multipart body UID to its aggregate compiled component."""
    aggregate_candidates: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for component in compiled.components:
        aggregate_candidates[
            (
                component.physical_document_id,
                component.logical_designator,
                component.physical_designator,
            )
        ].add(component.id)
        if component.physical_document_id and component.source_object_id:
            candidates[
                (
                    component.physical_document_id,
                    dotnet_ordinal_ignore_case_key(component.source_object_id),
                )
            ].add(component.id)
    for body in compiled._component_body_evidence:
        if body.physical_document_id and body.source_object_id:
            aggregate_ids = aggregate_candidates.get(
                (
                    body.physical_document_id,
                    body.logical_designator,
                    body.physical_designator,
                ),
                set(),
            )
            if len(aggregate_ids) != 1:
                continue
            candidates[
                (
                    body.physical_document_id,
                    dotnet_ordinal_ignore_case_key(body.source_object_id),
                )
            ].update(aggregate_ids)
    return {
        key: next(iter(values))
        for key, values in candidates.items()
        if len(values) == 1
    }


def _legacy_terminal_designator_map(
    compiled: AltiumCompiledDesign,
) -> _LegacyDesignatorResolver:
    """Resolve compiled pin identities back to legacy component rows."""
    by_name = _legacy_designator_aliases(compiled)
    by_occurrence_uid = _legacy_occurrence_uid_designators(compiled)
    physical_document_ids = frozenset(
        document.id for document in compiled.physical_documents
    )
    component_id_candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for component in compiled.components:
        if component.physical_document_id and component.display_designator:
            component_id_candidates[
                (component.physical_document_id, component.display_designator)
            ].add(component.id)
    component_ids = {
        key: next(iter(values))
        for key, values in component_id_candidates.items()
        if len(values) == 1
    }
    component_ids_by_uid = _legacy_component_ids_by_source_uid(compiled)
    source_files = {
        document.id: document.file_name for document in compiled.physical_documents
    }
    component_designators_by_id = {
        component.id: component.display_designator
        for component in compiled.components
        if component.id and component.display_designator
    }
    resolver = _LegacyDesignatorResolver(
        by_name=by_name,
        by_occurrence_uid=by_occurrence_uid,
        by_occurrence_pin={},
        physical_document_ids=physical_document_ids,
        display_designators=frozenset(
            component.display_designator
            for component in compiled.components
            if component.display_designator
        ),
        component_ids_by_page_and_designator=component_ids,
        component_ids_by_page_and_source_uid=component_ids_by_uid,
        component_ids_by_page_and_pin_object={},
        component_designators_by_id=component_designators_by_id,
        source_files_by_physical_id=source_files,
    )
    component_pin_candidates: dict[tuple[str, int], set[str]] = defaultdict(set)
    for net in compiled.nets:
        for terminal in net.terminals:
            if terminal._source_pin_object_id <= 0:
                continue
            physical_document_id = resolver.physical_document_id(terminal.id)
            resolved_designator = resolver.resolve(
                terminal.designator,
                item_id=terminal.id,
                source_component_uid=terminal._source_component_uid,
                source_pin_object_id=terminal._source_pin_object_id,
            )
            component_id = resolver.component_id(
                resolved_designator,
                item_id=terminal.id,
                source_component_uid=terminal._source_component_uid,
            )
            if physical_document_id and component_id:
                component_pin_candidates[
                    (physical_document_id, terminal._source_pin_object_id)
                ].add(component_id)
    component_ids_by_pin = {
        key: next(iter(values))
        for key, values in component_pin_candidates.items()
        if len(values) == 1
    }
    return _LegacyDesignatorResolver(
        by_name=by_name,
        by_occurrence_uid=by_occurrence_uid,
        by_occurrence_pin=_legacy_occurrence_pin_designators(compiled, resolver),
        physical_document_ids=physical_document_ids,
        display_designators=resolver.display_designators,
        component_ids_by_page_and_designator=component_ids,
        component_ids_by_page_and_source_uid=component_ids_by_uid,
        component_ids_by_page_and_pin_object=component_ids_by_pin,
        component_designators_by_id=component_designators_by_id,
        source_files_by_physical_id=source_files,
    )


def _pin_type_from_name(name: str) -> PinType:
    try:
        return PinType[name]
    except KeyError:
        return PinType.PASSIVE


def _compiled_terminal_to_terminal(
    terminal: AltiumCompiledNetTerminal,
    designators: _LegacyDesignatorResolver,
) -> Terminal:
    designator = designators.resolve(
        terminal.designator,
        item_id=terminal.id,
        source_component_uid=terminal._source_component_uid,
        source_pin_object_id=terminal._source_pin_object_id,
    )
    component_id = designators.component_id(
        designator,
        item_id=terminal.id,
        source_component_uid=terminal._source_component_uid,
        source_pin_object_id=terminal._source_pin_object_id,
    )
    return Terminal(
        designator=designators.canonical_designator(component_id, designator),
        pin=terminal.pin,
        pin_name=terminal.pin_name,
        pin_type=_pin_type_from_name(terminal.pin_type),
        _source_component_uid=terminal._source_component_uid,
        _source_pin_uid=terminal._source_pin_uid,
        _source_pin_object_id=terminal._source_pin_object_id,
        _source_owner_part_id=terminal._source_owner_part_id,
        component_id=component_id,
    )


def _compiled_endpoint_to_endpoint(
    endpoint: AltiumCompiledNetEndpoint,
    terminal_types: dict[tuple[str, str], PinType],
    designators: _LegacyDesignatorResolver,
) -> NetEndpoint:
    designator = designators.resolve(
        endpoint.designator,
        item_id=endpoint.id,
        source_pin_object_id=endpoint._source_pin_object_id,
    )
    component_id = (
        designators.component_id(
            designator,
            item_id=endpoint.id,
            source_pin_object_id=endpoint._source_pin_object_id,
        )
        if endpoint.role == "pin"
        else ""
    )
    canonical_designator = designators.canonical_designator(component_id, designator)
    return NetEndpoint(
        endpoint_id=endpoint.id,
        role=endpoint.role,
        element_id=endpoint.element_id,
        object_id=endpoint.object_id,
        name=endpoint.name,
        designator=canonical_designator,
        pin=endpoint.pin,
        pin_name=endpoint.pin_name,
        pin_type=terminal_types.get(
            (canonical_designator, endpoint.pin),
            PinType.PASSIVE,
        ),
        connection_point=endpoint.connection_point,
        component_id=component_id,
        source_page=designators.source_page(endpoint.id),
    )


def _compiled_graphical(
    net: AltiumCompiledNet,
    designators: _LegacyDesignatorResolver,
) -> NetGraphical:
    graphical = NetGraphical()

    def append_unique(target: list[str], value: str) -> None:
        clean = str(value or "").strip()
        if clean and clean not in target:
            target.append(clean)

    for endpoint in net.endpoints:
        element_id = endpoint.element_id or endpoint.object_id
        if not element_id:
            continue
        if endpoint.role == "pin" and endpoint.designator and endpoint.pin:
            resolved_designator = designators.resolve(
                endpoint.designator,
                item_id=endpoint.id,
                source_pin_object_id=endpoint._source_pin_object_id,
            )
            component_id = designators.component_id(
                resolved_designator,
                item_id=endpoint.id,
                source_pin_object_id=endpoint._source_pin_object_id,
            )
            graphical.pins.append(
                GraphicalPinRef(
                    designator=designators.canonical_designator(
                        component_id, resolved_designator
                    ),
                    pin=endpoint.pin,
                    svg_id=element_id,
                    component_id=component_id,
                )
            )
        elif endpoint.role == "net_label":
            graphical.labels.append(element_id)
        elif endpoint.role == "power_port":
            graphical.power_ports.append(element_id)
        elif endpoint.role in {"port", "harness_port"}:
            graphical.ports.append(element_id)
        elif endpoint.role in {"sheet_entry", "harness_entry"}:
            graphical.sheet_entries.append(element_id)
    for item in net.items:
        element_id = item.element_id or item.object_id or item.id
        if not element_id:
            continue
        if item.kind == "wire":
            append_unique(graphical.wires, element_id)
        elif item.kind == "junction":
            append_unique(graphical.junctions, element_id)
        elif item.kind == "net_label":
            append_unique(graphical.labels, element_id)
        elif item.kind == "power_port":
            append_unique(graphical.power_ports, element_id)
        elif item.kind in {"port", "harness_port"}:
            append_unique(graphical.ports, element_id)
        elif item.kind in {"sheet_entry", "harness_entry"}:
            append_unique(graphical.sheet_entries, element_id)
    return graphical


def _compiled_net_aliases(compiled_net: AltiumCompiledNet) -> list[str]:
    aliases = list(compiled_net.aliases)
    for endpoint in compiled_net.endpoints:
        if endpoint.role != "harness_entry":
            continue
        name = endpoint.name
        aliases.append(name)
        if "." in name:
            aliases.append(name.rsplit(".", 1)[-1])
    return _unique_nonempty([alias for alias in aliases if alias != compiled_net.name])


def _compiled_net_to_net(
    compiled_net: AltiumCompiledNet,
    designators: _LegacyDesignatorResolver,
) -> Net:
    terminals: list[Terminal] = []
    seen_terminals: set[tuple[str, str]] = set()
    for compiled_terminal in compiled_net.terminals:
        terminal = _compiled_terminal_to_terminal(compiled_terminal, designators)
        key = (terminal.designator, terminal.pin)
        if key in seen_terminals:
            continue
        seen_terminals.add(key)
        terminals.append(terminal)
    terminal_types = {
        (terminal.designator, terminal.pin): terminal.pin_type for terminal in terminals
    }
    return Net(
        name=compiled_net.name,
        terminals=terminals,
        graphical=_compiled_graphical(compiled_net, designators),
        auto_named=_compiled_netlist_auto_named(compiled_net, terminals),
        uid=compiled_net.id,
        source_sheets=list(compiled_net.physical_document_ids),
        source_pages=[
            page
            for document_id in compiled_net.physical_document_ids
            if (
                page := _compiled_source_page(
                    document_id, designators.source_files_by_physical_id
                )
            )
            is not None
        ],
        aliases=_compiled_net_aliases(compiled_net),
        endpoints=[
            _compiled_endpoint_to_endpoint(endpoint, terminal_types, designators)
            for endpoint in compiled_net.endpoints
        ],
    )


def _compiled_net_is_legacy_visible(
    compiled: AltiumCompiledDesign,
    compiled_net: AltiumCompiledNet,
) -> bool:
    """Return whether a compiled net should appear on legacy netlist surfaces.

    Compile rows can retain inferred and object-only signals. Legacy flattened
    netlist projection rejects zero-pin rows; isolated one-pin nets remain
    project-option controlled.
    """
    pin_count = len(compiled_net.terminals)
    if pin_count == 0:
        return False
    if pin_count > 1:
        return True
    if _compiled_net_item_count_for_legacy_visibility(compiled_net) > 1:
        return True
    return compiled.options.netlist_single_pin_nets


def _compiled_net_item_count_for_legacy_visibility(
    compiled_net: AltiumCompiledNet,
) -> int:
    """Count managed active and removed items without projection duplicates."""
    active = {_compiled_terminal_item_key(row) for row in compiled_net.terminals}
    removed: set[tuple[str, str]] = set()

    for endpoint in compiled_net.endpoints:
        _add_compiled_semantic_item(
            active,
            removed,
            endpoint,
            kind=endpoint.role,
            is_removed=False,
        )

    for item in compiled_net.items:
        _add_compiled_semantic_item(
            active,
            removed,
            item,
            kind=item.kind,
            is_removed=item.removed,
        )

    for item in getattr(compiled_net, "removed_items", ()):
        _add_compiled_semantic_item(
            active,
            removed,
            item,
            kind=item.kind,
            is_removed=True,
        )
    return len(active) + len(removed)


def _compiled_terminal_item_key(
    terminal: AltiumCompiledNetTerminal,
) -> tuple[str, str]:
    identity = terminal.id or f"{terminal.designator}\0{terminal.pin}"
    return ("pin", identity)


def _add_compiled_semantic_item(
    active: set[tuple[str, str]],
    removed: set[tuple[str, str]],
    row: object,
    *,
    kind: str,
    is_removed: bool,
) -> None:
    if kind in {"wire", "junction", "harness_entry"}:
        return
    if not is_removed and kind in {"pin", "terminal"}:
        return
    identity = str(
        getattr(row, "object_id", "")
        or getattr(row, "element_id", "")
        or getattr(row, "id", "")
    )
    target = removed if is_removed else active
    target.add(("object", identity))


def _compiled_netlist_auto_named(
    compiled_net: AltiumCompiledNet,
    terminals: list[Terminal],
) -> bool:
    if compiled_net.auto_named:
        return True
    if len(terminals) != 1 or not compiled_net.link_ids:
        return False
    roles = {endpoint.role for endpoint in compiled_net.endpoints}
    return "net_label" not in roles and "power_port" not in roles


def _sheet_entry_graphical_id(sheet_symbol_uid: str, entry_name: str) -> str:
    if not sheet_symbol_uid:
        return entry_name
    return f"{sheet_symbol_uid}_{entry_name}"


def _compiled_sheet_symbol_repeat(symbol: object) -> dict[str, object]:
    repeat: dict[str, object] = {
        "is_repeated": False,
        "syntax": "",
        "base_designator": "",
        "values": [],
    }
    start = getattr(symbol, "repeat_start", None)
    end = getattr(symbol, "repeat_end", None)
    if (
        not bool(getattr(symbol, "is_repeat", False))
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end < start
    ):
        return repeat
    return {
        "is_repeated": True,
        "syntax": str(getattr(symbol, "designator", "") or ""),
        "base_designator": str(getattr(symbol, "repeat_prefix", "") or ""),
        "values": list(range(start, end + 1)),
    }


def _source_schdoc_by_logical_id(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None,
) -> dict[str, "AltiumSchDoc"]:
    if not schdocs:
        return {}
    result: dict[str, AltiumSchDoc | None] = {}
    for logical in compiled.logical_documents:
        if 0 <= logical.ordinal < len(schdocs):
            result[logical.id] = schdocs[logical.ordinal]
    file_name_map: dict[str, AltiumSchDoc] = {}
    for schdoc in schdocs:
        filepath = getattr(schdoc, "filepath", None)
        file_name = getattr(filepath, "name", "") if filepath else ""
        if file_name:
            file_name_map.setdefault(_logical_source_identity_key(file_name), schdoc)
    for logical in compiled.logical_documents:
        result.setdefault(
            logical.id,
            file_name_map.get(_logical_source_identity_key(logical.file_name)),
        )
    return {key: value for key, value in result.items() if value is not None}


def _source_sheet_symbol_for(
    symbol_id: str,
    source_object_id: str,
    schdoc: object | None,
) -> SchSheetSymbolInfo | None:
    if schdoc is None:
        return None
    get_sheet_symbols = getattr(schdoc, "get_sheet_symbols", None)
    if not callable(get_sheet_symbols):
        return None
    for symbol in cast(Iterable[object], get_sheet_symbols()):
        unique_id = str(getattr(symbol, "unique_id", "") or "")
        if unique_id in {source_object_id, symbol_id} and isinstance(
            symbol, SchSheetSymbolInfo
        ):
            return symbol
    return None


def _build_compiled_hierarchy_documents(compiled: AltiumCompiledDesign) -> list[dict]:
    return [
        {
            "sheet_index": logical.ordinal,
            "filename": logical.file_name,
            "path": logical.source_path,
            "is_top_level": logical.is_top_level_candidate,
            "metadata": {},
        }
        for logical in sorted(compiled.logical_documents, key=lambda item: item.ordinal)
    ]


def _build_compiled_hierarchy_sheet_symbols(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None,
) -> list[dict]:
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    schdoc_by_logical_id = _source_schdoc_by_logical_id(compiled, schdocs)
    result: list[dict] = []
    for symbol in compiled.sheet_symbols:
        logical = logical_by_id.get(symbol.logical_document_id)
        parent_sheet_index = logical.ordinal if logical is not None else None
        child_ids = (
            [symbol.child_logical_document_id]
            if symbol.child_logical_document_id
            else list(symbol.child_candidate_logical_document_ids)
        )
        child_sheet_indices = [
            logical_by_id[child_id].ordinal
            for child_id in child_ids
            if child_id in logical_by_id
        ]
        source_symbol = _source_sheet_symbol_for(
            symbol.id,
            symbol.source_object_id,
            schdoc_by_logical_id.get(symbol.logical_document_id),
        )
        entries = []
        for entry in getattr(source_symbol, "entries", ()) or ():
            entry_name = _entry_display_name(entry)
            entries.append(
                {
                    "id": str(getattr(entry, "unique_id", "") or ""),
                    "name": entry_name,
                    "graphical_id": _sheet_entry_graphical_id(
                        symbol.source_object_id,
                        entry_name,
                    ),
                    "harness_type": str(getattr(entry, "harness_type", "") or ""),
                    "metadata": {},
                }
            )
        result.append(
            {
                "id": symbol.source_object_id or symbol.id,
                "parent_sheet_index": parent_sheet_index,
                "designator": symbol.designator,
                "child_filename": symbol.child_filename,
                "child_sheet_indices": child_sheet_indices,
                "repeat": _compiled_sheet_symbol_repeat(symbol),
                "entries": entries,
                "metadata": {},
            }
        )
    return result


def _multi_path_logical_document_ids(
    compiled: AltiumCompiledDesign,
) -> frozenset[str]:
    occurrence_counts: dict[str, int] = defaultdict(int)
    for document in compiled.physical_documents:
        occurrence_counts[document.logical_document_id] += 1
    return frozenset(
        logical_document_id
        for logical_document_id, count in occurrence_counts.items()
        if count > 1
    )


def _has_explicit_channel_fields(document: AltiumCompiledPhysicalDocument) -> bool:
    return (
        document.channel_index > 0
        or bool(document.channel_prefix)
        or bool(document.channel_alpha)
    )


def _sheet_symbol_expands_channels(symbol: AltiumCompiledSheetSymbol | None) -> bool:
    return bool(
        symbol and (symbol.is_repeat or len(symbol.child_physical_document_ids) > 1)
    )


def _no_channel_index(compiled: AltiumCompiledDesign) -> int:
    return -1 if compiled.options.new_indexing_of_sheet_symbols else 0


def _channel_physical_document_ids(
    compiled: AltiumCompiledDesign,
) -> frozenset[str]:
    multi_path_logical_document_ids = _multi_path_logical_document_ids(compiled)
    physical_symbols_by_child = {
        symbol.child_physical_document_id: symbol
        for symbol in compiled.physical_sheet_symbols
    }
    logical_symbols_by_id = {symbol.id: symbol for symbol in compiled.sheet_symbols}
    result: set[str] = set()
    for document in compiled.physical_documents:
        if document.parent_sheet_symbol_id is not None and (
            _has_explicit_channel_fields(document)
            or document.logical_document_id in multi_path_logical_document_ids
        ):
            result.add(document.id)
            continue
        physical_symbol = physical_symbols_by_child.get(document.id)
        logical_symbol = (
            logical_symbols_by_id.get(physical_symbol.logical_sheet_symbol_id)
            if physical_symbol is not None
            else None
        )
        if _sheet_symbol_expands_channels(logical_symbol):
            result.add(document.id)
    return frozenset(result)


def _compiled_hierarchy_level(
    compiled: AltiumCompiledDesign,
    physical_document_id: str,
    channel_physical_document_ids: frozenset[str],
) -> dict[str, object]:
    physical_by_id = {document.id: document for document in compiled.physical_documents}
    symbol_by_id = {symbol.id: symbol for symbol in compiled.sheet_symbols}
    physical_symbol_by_child_id = {
        symbol.child_physical_document_id: symbol
        for symbol in compiled.physical_sheet_symbols
    }
    document = physical_by_id[physical_document_id]
    physical_symbol = physical_symbol_by_child_id.get(document.id)
    logical_symbol = (
        symbol_by_id.get(physical_symbol.logical_sheet_symbol_id)
        if physical_symbol is not None
        else symbol_by_id.get(document.parent_sheet_symbol_id or "")
    )
    is_channel = physical_document_id in channel_physical_document_ids
    level: dict[str, object] = {
        "sheet_symbol_uid": (
            logical_symbol.source_object_id
            if logical_symbol is not None
            else document.parent_sheet_symbol_id or ""
        ),
        "child_filename": (
            logical_symbol.child_filename
            if logical_symbol is not None
            else document.file_name
        ),
    }
    designator = document.room_name or (
        physical_symbol.physical_designator
        if physical_symbol is not None
        else logical_symbol.designator
        if logical_symbol is not None
        else ""
    )
    if designator:
        level["designator"] = designator
    if is_channel and document.room_name:
        level["channel_name"] = document.room_name
    if is_channel and document.channel_index != _no_channel_index(compiled):
        level["channel_index"] = document.channel_index
    if (
        logical_symbol is not None
        and logical_symbol.is_repeat
        and logical_symbol.repeat_start is not None
        and document._managed_repeat_channel_value is not None
    ):
        level["repeat_value"] = document._managed_repeat_channel_value
    return level


def _compiled_hierarchy_path_levels(
    compiled: AltiumCompiledDesign,
    physical_document_id: str,
    memo: dict[str, list[dict[str, object]]],
    channel_physical_document_ids: frozenset[str],
) -> list[dict[str, object]]:
    if physical_document_id in memo:
        return memo[physical_document_id]
    physical_by_id = {document.id: document for document in compiled.physical_documents}
    document = physical_by_id[physical_document_id]
    if not document.parent_id:
        memo[physical_document_id] = []
        return []
    parent_levels = _compiled_hierarchy_path_levels(
        compiled,
        document.parent_id,
        memo,
        channel_physical_document_ids,
    )
    levels = [
        *parent_levels,
        _compiled_hierarchy_level(
            compiled,
            physical_document_id,
            channel_physical_document_ids,
        ),
    ]
    memo[physical_document_id] = levels
    return levels


def _hierarchy_path_display(levels: list[dict[str, object]]) -> str:
    if not levels:
        return "---"
    parts = []
    for level in levels:
        name = str(level.get("designator") or level.get("child_filename") or "")
        parts.append(name)
    return " > ".join(parts)


def _build_compiled_hierarchy_paths(
    compiled: AltiumCompiledDesign,
    channel_physical_document_ids: frozenset[str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    memo: dict[str, list[dict[str, object]]] = {}
    path_id_by_physical_id: dict[str, str] = {}
    rows: list[dict] = []
    logical_ordinals = {
        document.id: document.ordinal for document in compiled.logical_documents
    }
    if channel_physical_document_ids is None:
        channel_physical_document_ids = _channel_physical_document_ids(compiled)
    ordered_documents = sorted(
        compiled.physical_documents,
        key=lambda item: (
            logical_ordinals.get(item.logical_document_id, item.ordinal),
            item._managed_hierarchy_global_index
            if item._managed_hierarchy_global_index is not None
            else item.ordinal,
            item.ordinal,
        ),
    )
    for document in ordered_documents:
        if not document.parent_id:
            continue
        levels = _compiled_hierarchy_path_levels(
            compiled,
            document.id,
            memo,
            channel_physical_document_ids,
        )
        path_id = f"path-{len(rows) + 1:04d}"
        path_id_by_physical_id[document.id] = path_id
        rows.append(
            {
                "id": path_id,
                "levels": levels,
                "path": _hierarchy_path_display(levels),
                "unique_id_path": document.physical_instance_unique_id,
            }
        )
    return rows, path_id_by_physical_id


def _build_compiled_channels(
    compiled: AltiumCompiledDesign,
    path_id_by_physical_id: dict[str, str],
    channel_physical_document_ids: frozenset[str] | None = None,
) -> list[dict]:
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    physical_by_id = {document.id: document for document in compiled.physical_documents}
    symbol_by_id = {symbol.id: symbol for symbol in compiled.sheet_symbols}
    if channel_physical_document_ids is None:
        channel_physical_document_ids = _channel_physical_document_ids(compiled)
    no_channel_index = _no_channel_index(compiled)
    rows: list[dict] = []
    for document in sorted(
        compiled.physical_documents,
        key=lambda item: (
            logical_by_id[item.logical_document_id].ordinal
            if item.logical_document_id in logical_by_id
            else item.ordinal,
            item._managed_hierarchy_global_index
            if item._managed_hierarchy_global_index is not None
            else item.ordinal,
            item.ordinal,
        ),
    ):
        if not document.parent_id or document.id not in channel_physical_document_ids:
            continue
        parent_document = physical_by_id.get(document.parent_id)
        logical_symbol = symbol_by_id.get(document.parent_sheet_symbol_id or "")
        parent_logical = (
            logical_by_id.get(parent_document.logical_document_id)
            if parent_document is not None
            else None
        )
        child_logical = logical_by_id.get(document.logical_document_id)
        repeat_value = None
        if (
            logical_symbol is not None
            and logical_symbol.is_repeat
            and logical_symbol.repeat_start is not None
            and document._managed_repeat_channel_value is not None
        ):
            repeat_value = document._managed_repeat_channel_value
        rows.append(
            {
                "id": f"channel-{len(rows) + 1:04d}",
                "sheet_symbol_id": (
                    logical_symbol.source_object_id
                    if logical_symbol is not None
                    else document.parent_sheet_symbol_id or ""
                ),
                "parent_sheet_index": (
                    parent_logical.ordinal if parent_logical is not None else None
                ),
                "child_sheet_index": (
                    child_logical.ordinal if child_logical is not None else None
                ),
                "compiled_child_sheet_index": document.ordinal,
                "instance_index": document.channel_index,
                "channel_name": document.room_name,
                "channel_prefix": document.channel_prefix or "",
                "channel_index": (
                    str(document.channel_index)
                    if document.channel_index != no_channel_index
                    else ""
                ),
                "channel_alpha": document.channel_alpha or "",
                "repeat_value": repeat_value,
                "repeat_entry_ports": [],
                "hierarchy_path_id": path_id_by_physical_id.get(document.id, ""),
            }
        )
    return rows


def _document_pair_for_link_net(
    compiled: AltiumCompiledDesign,
    net: AltiumCompiledNet,
) -> tuple[
    AltiumCompiledPhysicalDocument | None, AltiumCompiledPhysicalDocument | None
]:
    physical_by_id = {document.id: document for document in compiled.physical_documents}
    candidates = [
        physical_by_id[doc_id]
        for doc_id in net.physical_document_ids
        if doc_id in physical_by_id
    ]
    for document in candidates:
        if document.parent_id and any(
            parent.id == document.parent_id for parent in candidates
        ):
            return physical_by_id.get(document.parent_id), document
    return None, None


def _strip_harness_parent_name(value: str) -> str:
    clean = dotnet_trim(str(value or ""))
    if clean.startswith("{") and clean.endswith("}"):
        return clean[1:-1]
    return clean


def _retained_hierarchy_link_projection(
    net: AltiumCompiledNet,
) -> tuple[str, str, str, bool, str, list[str]] | None:
    if not net._hierarchy_parent_entry_name:
        return None
    match_kind = net._hierarchy_match_kind or "name"
    harness = match_kind == "harness_name"
    return (
        net._hierarchy_parent_entry_name,
        net._hierarchy_link_name or net.name,
        net._hierarchy_child_name or net.name,
        harness,
        match_kind,
        list(net._hierarchy_child_object_ids),
    )


def _fallback_hierarchy_link_projection(
    net: AltiumCompiledNet,
    parent_document_id: str,
    child_document_id: str,
) -> tuple[str, str, str, bool, str, list[str]] | None:
    parent_item = _first_hierarchy_item(
        net,
        parent_document_id,
        frozenset({"sheet_entry", "harness_entry"}),
    )
    child_item = _first_hierarchy_item(
        net,
        child_document_id,
        frozenset({"port", "harness_entry"}),
    )
    if parent_item is None or child_item is None:
        return None
    harness = parent_item.kind == "harness_entry" or child_item.kind == "harness_entry"
    if harness:
        parent_name = _strip_harness_parent_name(parent_item.parent_id)
        link_name = net.name
        child_name = net.name
    else:
        parent_name = parent_item.name
        link_name = parent_item.name
        child_name = child_item.name or parent_item.name
    return (
        parent_name,
        link_name,
        child_name,
        harness,
        "harness_name" if harness else "name",
        _unique_nonempty([child_item.object_id, child_item.element_id]),
    )


def _first_hierarchy_item(
    net: AltiumCompiledNet,
    document_id: str,
    kinds: frozenset[str],
) -> AltiumCompiledNetItem | None:
    matches = (
        item
        for item in net.items
        if item.physical_document_id == document_id and item.kind in kinds
    )
    first = next(matches, None)
    return first if first is not None and next(matches, None) is None else None


def _build_compiled_hierarchy_links(
    compiled: AltiumCompiledDesign,
    path_id_by_physical_id: dict[str, str],
    channel_physical_document_ids: frozenset[str] | None = None,
) -> list[dict]:
    if compiled.options.effective_hierarchy_mode not in {
        "HIERARCHICAL",
        "STRICT_HIERARCHICAL",
    }:
        return []
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    symbol_by_id = {symbol.id: symbol for symbol in compiled.sheet_symbols}
    if channel_physical_document_ids is None:
        channel_physical_document_ids = _channel_physical_document_ids(compiled)
    rows: list[dict] = []
    for net in compiled.nets:
        if net.scope != "inter_sheet_link":
            continue
        parent_document, child_document = _document_pair_for_link_net(compiled, net)
        if parent_document is None or child_document is None:
            continue
        logical_symbol = symbol_by_id.get(child_document.parent_sheet_symbol_id or "")
        if logical_symbol is None:
            continue
        projection = _retained_hierarchy_link_projection(
            net
        ) or _fallback_hierarchy_link_projection(
            net,
            parent_document.id,
            child_document.id,
        )
        if projection is None:
            continue
        (
            parent_entry_name,
            link_name,
            child_name,
            match_by_harness,
            match_kind,
            child_object_ids,
        ) = projection
        parent_logical = logical_by_id.get(parent_document.logical_document_id)
        child_logical = logical_by_id.get(child_document.logical_document_id)
        parent_sheet_index = (
            parent_logical.ordinal if parent_logical is not None else None
        )
        child_sheet_index = child_logical.ordinal if child_logical is not None else None
        sheet_symbol_id = logical_symbol.source_object_id or logical_symbol.id
        parent_graphical_id = _sheet_entry_graphical_id(
            sheet_symbol_id,
            parent_entry_name,
        )
        is_channel = child_document.id in channel_physical_document_ids
        rows.append(
            {
                "id": f"hier-link-{len(rows) + 1:04d}",
                "kind": "sheet_entry_to_port",
                "parent": {
                    "sheet_index": parent_sheet_index,
                    "compiled_sheet_index": parent_document.ordinal,
                    "object_kind": "sheet_entry",
                    "object_id": parent_graphical_id,
                    "graphical_id": parent_graphical_id,
                    "sheet_symbol_id": sheet_symbol_id,
                    "name": link_name,
                    "sheet_entry_name": parent_entry_name,
                },
                "child": {
                    "sheet_index": child_sheet_index,
                    "compiled_sheet_index": child_document.ordinal,
                    "object_kind": "harness_entry" if match_by_harness else "port",
                    "object_ids": child_object_ids,
                    "name": child_name,
                },
                "match_kind": match_kind,
                "hierarchy_path_id": path_id_by_physical_id.get(child_document.id, ""),
                "channel_index": child_document.channel_index if is_channel else None,
                "channel_name": child_document.room_name if is_channel else "",
                "repeat_value": child_document._managed_repeat_channel_value,
                "metadata": {},
            }
        )
    return rows


def _active_bundle_ports_and_locations(
    schdoc: "AltiumSchDoc",
    compile_masks: Sequence[_CompileMask],
) -> tuple[tuple[object, ...], dict]:
    from .altium_design_compiler import (
        _port_is_compile_masked,
        _sheet_entry_is_compile_masked,
    )

    active_ports = tuple(
        port
        for port in schdoc.get_ports()
        if not _port_is_compile_masked(port, compile_masks)
    )
    active_port_record_ids = {
        id(getattr(port, "record", port)) for port in active_ports
    }
    active_entry_ids = {
        id(entry)
        for sheet_symbol in schdoc.get_sheet_symbols()
        for entry in sheet_symbol.entries
        if not _sheet_entry_is_compile_masked(entry, sheet_symbol, compile_masks)
    }
    return active_ports, _build_port_location_map(
        schdoc,
        port_filter=lambda port: (
            id(getattr(port, "record", port)) in active_port_record_ids
        ),
        entry_filter=lambda entry: id(entry) in active_entry_ids,
    )


def _active_bundle_signal_harnesses(
    schdoc: "AltiumSchDoc",
    compile_masks: Sequence[_CompileMask],
) -> tuple[object, ...]:
    from .altium_design_compiler import _compiled_mask_contains_every_point

    def is_active(signal_harness: object) -> bool:
        points = tuple(
            _point_key(point) for point in getattr(signal_harness, "points", ()) or ()
        )
        return not any(
            _compiled_mask_contains_every_point(mask, points) for mask in compile_masks
        )

    return tuple(
        signal_harness
        for signal_harness in getattr(schdoc, "signal_harnesses", ()) or ()
        if is_active(signal_harness)
    )


def _matching_bundle_port_ids(
    active_ports: Sequence[object], port_key: str
) -> list[str]:
    return [
        str(getattr(port, "unique_id", "") or "")
        for port in active_ports
        if getattr(port, "name", "")
        and dotnet_ordinal_ignore_case_key(str(getattr(port, "name", ""))) == port_key
    ]


def _bundle_signal_harness_ids(bundle_info: dict) -> list[str]:
    values = bundle_info.get("signal_harness_ids")
    return [str(value) for value in values] if isinstance(values, list) else []


def _active_bundle_member_names(
    connector: object,
    compile_masks: Sequence[_CompileMask],
    entries: Iterable[object],
) -> list[str]:
    from .altium_design_compiler import _compiled_point_inside_any_mask

    return [
        str(getattr(entry, "name", "") or "")
        for entry in entries
        if getattr(entry, "name", "")
        and not _compiled_point_inside_any_mask(
            _harness_entry_connection_point(connector, entry), compile_masks
        )
    ]


def _bundle_endpoint_row(
    connector: object,
    *,
    logical_id: str,
    sheet_index: int | None,
    active_ports: Sequence[object],
    active_signal_harnesses: Sequence[object],
    port_location_map: dict,
    internal_tolerance: int,
    compile_masks: Sequence[_CompileMask],
    source_entries: Iterable[object],
) -> tuple[str, dict] | None:
    from .altium_design_compiler import _compiled_point_inside_any_mask

    if _compiled_point_inside_any_mask(
        _harness_connector_master_entry_point(connector), compile_masks
    ):
        return None
    bundle_info = find_harness_bundle_info(
        connector,
        active_signal_harnesses,
        port_location_map,
        internal_tolerance,
    )
    port_name_value = bundle_info.get("port_name")
    port_name = port_name_value if isinstance(port_name_value, str) else ""
    if not port_name:
        return None
    port_key = dotnet_ordinal_ignore_case_key(port_name)
    port_ids = _matching_bundle_port_ids(active_ports, port_key)
    signal_harness_ids = _bundle_signal_harness_ids(bundle_info)
    connector_id = dotnet_trim(str(getattr(connector, "unique_id", "") or ""))
    object_ids = _unique_nonempty([*port_ids, *signal_harness_ids, connector_id])
    return port_key, {
        "name": port_name,
        "logical_document_id": logical_id,
        "sheet_index": sheet_index,
        "port_ids": _unique_nonempty(port_ids),
        "signal_harness_ids": _unique_nonempty(signal_harness_ids),
        "harness_connector_ids": _unique_nonempty([connector_id]),
        "object_ids": object_ids,
        "member_names": _active_bundle_member_names(
            connector, compile_masks, source_entries
        ),
    }


def _build_harness_bundle_endpoint_map(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None,
) -> dict[str, list[dict]]:
    from .altium_design_compiler import _compiled_compile_mask_bounds

    schdoc_by_logical_id = _source_schdoc_by_logical_id(compiled, schdocs)
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    endpoint_map: dict[str, list[dict]] = defaultdict(list)
    for logical_id, schdoc in schdoc_by_logical_id.items():
        if not getattr(schdoc, "harness_connectors", None):
            continue
        logical = logical_by_id.get(logical_id)
        sheet_index = logical.ordinal if logical is not None else None
        compile_masks = _compiled_compile_mask_bounds(schdoc)
        active_ports, port_location_map = _active_bundle_ports_and_locations(
            schdoc, compile_masks
        )
        display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
        internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
        active_signal_harnesses = _active_bundle_signal_harnesses(schdoc, compile_masks)
        for connector in schdoc.harness_connectors:
            endpoint = _bundle_endpoint_row(
                connector,
                logical_id=logical_id,
                sheet_index=sheet_index,
                active_ports=active_ports,
                active_signal_harnesses=active_signal_harnesses,
                port_location_map=port_location_map,
                internal_tolerance=internal_tolerance,
                compile_masks=compile_masks,
                source_entries=_compiler_harness_entries(schdoc, connector),
            )
            if endpoint is None:
                continue
            endpoint_key, endpoint_row = endpoint
            endpoint_map[endpoint_key].append(endpoint_row)
    return endpoint_map


def _compiled_harness_bundle_link_row(
    row_number: int,
    *,
    name: str,
    topology: str,
    parent_endpoint: dict,
    child_endpoint: dict,
    parent: dict | None = None,
) -> dict:
    parent_ids = _unique_nonempty(
        parent.get("object_ids", []) if parent else parent_endpoint["port_ids"]
    )
    child_ids = _unique_nonempty(child_endpoint["port_ids"])
    return {
        "id": f"harness-bundle-link-{row_number:04d}",
        "kind": "harness_bundle",
        "topology": topology,
        "name": name,
        "parent": parent
        or {
            "sheet_index": parent_endpoint["sheet_index"],
            "compiled_sheet_index": parent_endpoint["compiled_sheet_index"],
            "object_kind": "harness_port",
            "object_ids": parent_ids,
            "name": parent_endpoint["name"],
        },
        "child": {
            "sheet_index": child_endpoint["sheet_index"],
            "compiled_sheet_index": child_endpoint["compiled_sheet_index"],
            "object_kind": "harness_port",
            "object_ids": child_ids,
            "name": child_endpoint["name"],
        },
        "bundle": {
            "object_ids": _unique_nonempty(
                [
                    *parent_ids,
                    *child_ids,
                    *parent_endpoint.get("object_ids", []),
                    *child_endpoint.get("object_ids", []),
                ]
            ),
            "parent_object_ids": parent_endpoint.get("object_ids", []),
            "child_object_ids": child_endpoint.get("object_ids", []),
            "member_names": _unique_nonempty(
                [
                    *parent_endpoint.get("member_names", []),
                    *child_endpoint.get("member_names", []),
                ]
            ),
            "signal_harness_ids": _unique_nonempty(
                [
                    *parent_endpoint.get("signal_harness_ids", []),
                    *child_endpoint.get("signal_harness_ids", []),
                ]
            ),
            "harness_connector_ids": _unique_nonempty(
                [
                    *parent_endpoint.get("harness_connector_ids", []),
                    *child_endpoint.get("harness_connector_ids", []),
                ]
            ),
        },
        "match_kind": "harness_bundle",
        "metadata": {},
    }


def _matching_bundle_endpoint_source(
    endpoints_by_name: dict[str, list[dict]], child: dict, name: str
) -> dict | None:
    return next(
        (
            endpoint
            for endpoint in endpoints_by_name.get(
                dotnet_ordinal_ignore_case_key(name), []
            )
            if endpoint["sheet_index"] == child.get("sheet_index")
        ),
        None,
    )


def _bundle_endpoint_for_physical(
    source: dict | None,
    physical: AltiumCompiledPhysicalDocument | None,
) -> dict | None:
    if source is None or physical is None:
        return None
    if physical.logical_document_id != source["logical_document_id"]:
        return None
    return {**source, "compiled_sheet_index": physical.ordinal}


def _hierarchical_bundle_link_parts(
    link: dict,
    endpoints_by_name: dict[str, list[dict]],
    physical_by_ordinal: dict[int, AltiumCompiledPhysicalDocument],
) -> tuple[tuple[object, object, object, str, str], str, dict, dict, dict] | None:
    if link.get("match_kind") != "harness_name":
        return None
    parent = link.get("parent", {})
    child = link.get("child", {})
    parent_entry_name = dotnet_trim(str(parent.get("sheet_entry_name") or ""))
    link_name = str(parent.get("name") or parent_entry_name)
    child_endpoint_name = str(child.get("name") or link_name)
    if not parent_entry_name or not child_endpoint_name:
        return None
    child_source = _matching_bundle_endpoint_source(
        endpoints_by_name, child, child_endpoint_name
    )
    child_physical = physical_by_ordinal.get(child.get("compiled_sheet_index"))
    child_endpoint = _bundle_endpoint_for_physical(child_source, child_physical)
    if child_endpoint is None:
        return None
    parent_graphical_id = str(parent.get("object_id") or "")
    parent_endpoint = {
        "name": link_name,
        "sheet_index": parent.get("sheet_index"),
        "compiled_sheet_index": parent.get("compiled_sheet_index"),
        "port_ids": [parent_graphical_id],
        "object_ids": [parent_graphical_id],
        "signal_harness_ids": [],
        "harness_connector_ids": [],
    }
    parent_row = {
        "sheet_index": parent.get("sheet_index"),
        "compiled_sheet_index": parent.get("compiled_sheet_index"),
        "object_kind": "sheet_entry",
        "object_id": parent_graphical_id,
        "graphical_id": parent_graphical_id,
        "object_ids": [parent_graphical_id],
        "sheet_symbol_id": parent.get("sheet_symbol_id"),
        "name": link_name,
    }
    key = (
        parent.get("sheet_index"),
        child.get("sheet_index"),
        child.get("compiled_sheet_index"),
        str(parent.get("sheet_symbol_id") or ""),
        dotnet_ordinal_ignore_case_key(child_endpoint_name),
    )
    return key, link_name, parent_endpoint, child_endpoint, parent_row


def _flat_bundle_endpoints(
    sources: Sequence[dict],
    physical_documents: Sequence[AltiumCompiledPhysicalDocument],
) -> Iterable[dict]:
    for physical in sorted(physical_documents, key=lambda document: document.ordinal):
        for source in sources:
            if source["logical_document_id"] == physical.logical_document_id:
                yield {**source, "compiled_sheet_index": physical.ordinal}


def _append_flat_bundle_links(
    rows: list[dict],
    endpoints_by_name: dict[str, list[dict]],
    physical_documents: Sequence[AltiumCompiledPhysicalDocument],
) -> None:
    for endpoint_name in sorted(endpoints_by_name):
        sources = sorted(
            endpoints_by_name[endpoint_name], key=lambda endpoint: endpoint["name"]
        )
        previous_endpoint: dict | None = None
        for endpoint in _flat_bundle_endpoints(sources, physical_documents):
            if previous_endpoint is not None:
                rows.append(
                    _compiled_harness_bundle_link_row(
                        len(rows) + 1,
                        name=previous_endpoint["name"],
                        topology="flat",
                        parent_endpoint=previous_endpoint,
                        child_endpoint=endpoint,
                    )
                )
            previous_endpoint = endpoint


def _build_compiled_harness_bundle_links(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None,
    hierarchy_links: list[dict],
) -> list[dict]:
    endpoints_by_name = _build_harness_bundle_endpoint_map(compiled, schdocs)
    physical_by_ordinal = {
        document.ordinal: document for document in compiled.physical_documents
    }
    rows: list[dict] = []
    hierarchical_keys: set[tuple[object, object, object, str, str]] = set()
    for link in hierarchy_links:
        parts = _hierarchical_bundle_link_parts(
            link, endpoints_by_name, physical_by_ordinal
        )
        if parts is None:
            continue
        key, link_name, parent_endpoint, child_endpoint, parent_row = parts
        if key in hierarchical_keys:
            continue
        hierarchical_keys.add(key)
        rows.append(
            _compiled_harness_bundle_link_row(
                len(rows) + 1,
                name=link_name,
                topology="hierarchical",
                parent_endpoint=parent_endpoint,
                child_endpoint=child_endpoint,
                parent=parent_row,
            )
        )
    if compiled.options.effective_hierarchy_mode in {"FLAT", "GLOBAL"}:
        _append_flat_bundle_links(rows, endpoints_by_name, compiled.physical_documents)
    return rows


def _missing_child_sheet_row(
    symbol: AltiumCompiledSheetSymbol,
    parent_logical: AltiumCompiledLogicalDocument | None,
) -> dict:
    parent_index = parent_logical.ordinal if parent_logical is not None else None
    return {
        "kind": "missing_child_sheet",
        "parent_sheet_index": parent_index,
        "parent_compiled_sheet_index": parent_index,
        "sheet_symbol_id": symbol.source_object_id or symbol.id,
        "child_filename": symbol.child_filename,
    }


def _active_port_name_keys(
    schdoc: "AltiumSchDoc | None",
    compile_masks: Sequence[_CompileMask],
) -> set[str]:
    from .altium_design_compiler import _port_is_compile_masked

    if schdoc is None:
        return set()
    return {
        dotnet_ordinal_ignore_case_key(str(getattr(port, "name", "") or ""))
        for port in schdoc.get_ports()
        if not _port_is_compile_masked(port, compile_masks)
    }


def _active_unlinked_entry_name(
    entry: object,
    *,
    source_symbol: SchSheetSymbolInfo,
    sheet_symbol_id: str,
    parent_compile_masks: Sequence[_CompileMask],
    linked_keys: set[tuple[str, str]],
) -> str | None:
    from .altium_design_compiler import _sheet_entry_is_compile_masked

    if _sheet_entry_is_compile_masked(entry, source_symbol, parent_compile_masks):
        return None
    entry_name = _entry_display_name(entry)
    if not entry_name:
        return None
    if (sheet_symbol_id, dotnet_ordinal_ignore_case_key(entry_name)) in linked_keys:
        return None
    return None if str(getattr(entry, "harness_type", "") or "") else entry_name


def _unmatched_child_port_row(
    entry: object,
    *,
    source_symbol: SchSheetSymbolInfo,
    symbol: AltiumCompiledSheetSymbol,
    parent_logical: AltiumCompiledLogicalDocument | None,
    child_logical: AltiumCompiledLogicalDocument,
    parent_compile_masks: Sequence[_CompileMask],
    child_port_names: set[str],
    linked_keys: set[tuple[str, str]],
) -> dict | None:
    sheet_symbol_id = symbol.source_object_id or symbol.id
    entry_name = _active_unlinked_entry_name(
        entry,
        source_symbol=source_symbol,
        sheet_symbol_id=sheet_symbol_id,
        parent_compile_masks=parent_compile_masks,
        linked_keys=linked_keys,
    )
    if entry_name is None:
        return None
    parsed_repeat_name = _parse_entry_repeat(entry_name) if symbol.is_repeat else None
    match_name = parsed_repeat_name if parsed_repeat_name is not None else entry_name
    if dotnet_ordinal_ignore_case_key(match_name) in child_port_names:
        return None
    parent_index = parent_logical.ordinal if parent_logical is not None else None
    return {
        "kind": "unmatched_child_port",
        "parent_sheet_index": parent_index,
        "parent_compiled_sheet_index": parent_index,
        "child_sheet_index": child_logical.ordinal,
        "child_compiled_sheet_index": child_logical.ordinal,
        "sheet_symbol_id": sheet_symbol_id,
        "entry_name": entry_name,
        "port_name": match_name,
    }


def _symbol_unresolved_rows(
    symbol: AltiumCompiledSheetSymbol,
    *,
    parent_logical: AltiumCompiledLogicalDocument | None,
    child_logical: AltiumCompiledLogicalDocument,
    schdoc_by_logical_id: dict[str, "AltiumSchDoc"],
    linked_keys: set[tuple[str, str]],
) -> list[dict]:
    from .altium_design_compiler import _compiled_compile_mask_bounds

    parent_schdoc = schdoc_by_logical_id.get(symbol.logical_document_id)
    child_schdoc = schdoc_by_logical_id.get(child_logical.id)
    parent_masks = (
        _compiled_compile_mask_bounds(parent_schdoc)
        if parent_schdoc is not None
        else ()
    )
    child_masks = (
        _compiled_compile_mask_bounds(child_schdoc) if child_schdoc is not None else ()
    )
    source_symbol = _source_sheet_symbol_for(
        symbol.id, symbol.source_object_id, parent_schdoc
    )
    if source_symbol is None:
        return []
    child_port_names = _active_port_name_keys(child_schdoc, child_masks)
    rows = (
        _unmatched_child_port_row(
            entry,
            source_symbol=source_symbol,
            symbol=symbol,
            parent_logical=parent_logical,
            child_logical=child_logical,
            parent_compile_masks=parent_masks,
            child_port_names=child_port_names,
            linked_keys=linked_keys,
        )
        for entry in getattr(source_symbol, "entries", ()) or ()
    )
    return [row for row in rows if row is not None]


def _build_compiled_hierarchy_unresolved(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None,
    hierarchy_links: list[dict],
) -> list[dict]:
    if compiled.options.effective_hierarchy_mode not in {
        "HIERARCHICAL",
        "STRICT_HIERARCHICAL",
    }:
        return []
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    schdoc_by_logical_id = _source_schdoc_by_logical_id(compiled, schdocs)
    linked_keys = {
        (
            str(link.get("parent", {}).get("sheet_symbol_id") or ""),
            dotnet_ordinal_ignore_case_key(
                str(link.get("parent", {}).get("sheet_entry_name") or "")
            ),
        )
        for link in hierarchy_links
    }
    rows: list[dict] = []
    for symbol in compiled.sheet_symbols:
        parent_logical = logical_by_id.get(symbol.logical_document_id)
        child_logical = logical_by_id.get(symbol.child_logical_document_id or "")
        if child_logical is None:
            rows.append(_missing_child_sheet_row(symbol, parent_logical))
            continue
        rows.extend(
            _symbol_unresolved_rows(
                symbol,
                parent_logical=parent_logical,
                child_logical=child_logical,
                schdoc_by_logical_id=schdoc_by_logical_id,
                linked_keys=linked_keys,
            )
        )
    return rows


def compiled_design_schematic_hierarchy(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None = None,
) -> dict:
    """Build public design-JSON schematic hierarchy metadata from compiled rows."""
    source_schdocs = (
        [_compiler_document_source(schdoc) for schdoc in schdocs]
        if schdocs is not None
        else None
    )
    channel_physical_document_ids = _channel_physical_document_ids(compiled)
    hierarchy_paths, path_id_by_physical_id = _build_compiled_hierarchy_paths(
        compiled,
        channel_physical_document_ids,
    )
    hierarchy_links = _build_compiled_hierarchy_links(
        compiled,
        path_id_by_physical_id,
        channel_physical_document_ids,
    )
    return {
        "schema": SCHEMATIC_HIERARCHY_SCHEMA,
        "requested_scope": compiled.options.hierarchy_mode,
        "effective_scope": compiled.options.effective_hierarchy_mode,
        "documents": _build_compiled_hierarchy_documents(compiled),
        "sheet_symbols": _build_compiled_hierarchy_sheet_symbols(
            compiled, source_schdocs
        ),
        "hierarchy_paths": hierarchy_paths,
        "channels": _build_compiled_channels(
            compiled,
            path_id_by_physical_id,
            channel_physical_document_ids,
        ),
        "links": hierarchy_links,
        "harness_bundle_links": _build_compiled_harness_bundle_links(
            compiled,
            source_schdocs,
            hierarchy_links,
        ),
        "unresolved": _build_compiled_hierarchy_unresolved(
            compiled,
            source_schdocs,
            hierarchy_links,
        ),
    }


def compiled_design_to_netlist(compiled: AltiumCompiledDesign) -> Netlist:
    """Build the legacy `Netlist` model from compiled-flat design rows."""
    flat_nets = [
        net
        for net in compiled.nets
        if net.scope == "compiled_flat"
        and _compiled_net_is_legacy_visible(compiled, net)
    ]
    source_files_by_physical_id = {
        document.id: document.file_name for document in compiled.physical_documents
    }
    components: list[NetlistComponent] = []
    for component in compiled.components:
        if not component.include_in_netlist or not component.display_designator:
            continue
        components.append(
            NetlistComponent(
                designator=component.display_designator,
                value=component.value,
                footprint=component.footprint,
                library_ref=component.lib_reference,
                description=component.description,
                parameters=dict(component.parameters),
                component_kind=component.component_kind_value,
                exclude_from_bom=component.exclude_from_bom,
                component_id=component.id,
                logical_designator=component.logical_designator or None,
                physical_designator=component.physical_designator or None,
                source_page=_compiled_source_page(
                    component.physical_document_id, source_files_by_physical_id
                ),
            )
        )
    legacy_terminal_designators = _legacy_terminal_designator_map(compiled)
    nets = [_compiled_net_to_net(net, legacy_terminal_designators) for net in flat_nets]
    return Netlist(nets=nets, components=components)


__all__ = ["compiled_design_schematic_hierarchy", "compiled_design_to_netlist"]
