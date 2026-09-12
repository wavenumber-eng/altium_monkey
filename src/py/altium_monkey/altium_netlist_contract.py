"""Handwritten rich-model adapters for generated netlist transport DTOs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

import msgspec

from .altium_netlist_model import (
    HierarchyPath,
    Net,
    NetEndpoint,
    NetGraphical,
    Netlist,
    NetlistComponent,
    NetlistSourcePage,
)
from .altium_schematic_contract import (
    SchematicContractError,
    SchematicContractLimits,
    enforce_collection_limit,
    enforce_output_limit,
    pointer_child,
    preflight_mapping,
    resolve_limits,
)
from .sch_compiled_design.generated.models import (
    NetlistB0,
    NetlistB0Component,
    NetlistB0ConnectionPoint,
    NetlistB0Endpoint,
    NetlistB0Graphical,
    NetlistB0GraphicalPin,
    NetlistB0HierarchyLevel,
    NetlistB0Net,
    NetlistB0Terminal,
    SchematicSourcePage,
)


_NETLIST_B0_SCHEMA = "altium_monkey.netlist.b0"


@dataclass(frozen=True, slots=True)
class _ComponentIdentities:
    by_id: Mapping[str, tuple[str, str]]
    by_designator: Mapping[str, tuple[str, str]]


def netlist_to_mapping(
    netlist: Netlist, *, limits: SchematicContractLimits | None = None
) -> dict[str, object]:
    """Project a rich model to the canonical current generated DTO."""
    dto = _netlist_to_dto(netlist, resolve_limits(limits))
    value = msgspec.to_builtins(dto)
    if not isinstance(value, dict):
        raise RuntimeError("generated netlist root did not convert to an object")
    mapping = cast(dict[str, object], value)
    preflight_mapping(mapping, limits)
    return mapping


def netlist_to_bytes(
    netlist: Netlist, *, limits: SchematicContractLimits | None = None
) -> bytes:
    """Encode one canonical current DTO with the shared output ceiling."""
    dto = _netlist_to_dto(netlist, resolve_limits(limits))
    raw = msgspec.json.encode(dto)
    return enforce_output_limit(raw, limits)


def _netlist_to_dto(netlist: Netlist, limits: SchematicContractLimits) -> NetlistB0:
    enforce_collection_limit(
        len(netlist.components), limits.max_components, "/components", "component limit"
    )
    enforce_collection_limit(len(netlist.nets), limits.max_nets, "/nets", "net limit")
    _enforce_netlist_aggregate_limits(netlist, limits)
    _validate_source_page_correlations(netlist)
    component_rows, identities = _component_dtos(netlist.components, limits)
    net_rows = [
        _net_dto(net, identities, limits, index)
        for index, net in enumerate(netlist.nets)
    ]
    _unique_identity_rows(
        ((row.uid, index) for index, row in enumerate(net_rows)), "/nets", "uid"
    )
    return NetlistB0(
        schema=_NETLIST_B0_SCHEMA,
        generator="altium_monkey",
        components=sorted(
            component_rows, key=lambda row: (row.component_id, row.designator)
        ),
        nets=sorted(net_rows, key=lambda row: (row.uid, row.name)),
    )


def _enforce_netlist_aggregate_limits(
    netlist: Netlist, limits: SchematicContractLimits
) -> None:
    enforce_collection_limit(
        sum(len(component.parameters) for component in netlist.components),
        limits.max_parameter_pairs,
        "/components",
        "total parameter-pair limit",
    )
    totals = (
        (_terminal_count(netlist), limits.max_terminals, "terminals"),
        (_endpoint_count(netlist), limits.max_endpoints, "endpoints"),
        (_graphical_count(netlist), limits.max_graphical_links, "graphical links"),
        (_alias_count(netlist), limits.max_aliases, "aliases"),
        (_hierarchy_path_count(netlist), limits.max_hierarchy_paths, "hierarchy paths"),
        (
            _hierarchy_level_count(netlist),
            limits.max_hierarchy_levels,
            "hierarchy levels",
        ),
        (_source_page_count(netlist), limits.max_source_pages, "source pages"),
    )
    for count, maximum, label in totals:
        enforce_collection_limit(count, maximum, "/nets", f"total {label} limit")


def _terminal_count(netlist: Netlist) -> int:
    return sum(len(net.terminals) for net in netlist.nets)


def _endpoint_count(netlist: Netlist) -> int:
    return sum(len(net.endpoints) for net in netlist.nets)


def _graphical_count(netlist: Netlist) -> int:
    return sum(_graphical_link_count(net.graphical) for net in netlist.nets)


def _alias_count(netlist: Netlist) -> int:
    return sum(len(net.aliases) for net in netlist.nets)


def _hierarchy_path_count(netlist: Netlist) -> int:
    return sum(len(net.hierarchy_paths) for net in netlist.nets)


def _hierarchy_level_count(netlist: Netlist) -> int:
    return sum(len(path.levels) for net in netlist.nets for path in net.hierarchy_paths)


def _source_page_count(netlist: Netlist) -> int:
    return sum(page is not None for page in _netlist_source_pages(netlist))


def _graphical_link_count(graphical: NetGraphical) -> int:
    return sum(
        len(values)
        for values in (
            graphical.wires,
            graphical.junctions,
            graphical.labels,
            graphical.power_ports,
            graphical.ports,
            graphical.sheet_entries,
            graphical.pins,
        )
    )


def _netlist_source_pages(netlist: Netlist) -> Iterable[NetlistSourcePage | None]:
    yield from (component.source_page for component in netlist.components)
    for net in netlist.nets:
        yield from net.source_pages
        yield from (endpoint.source_page for endpoint in net.endpoints)


def _validate_source_page_correlations(netlist: Netlist) -> None:
    observed: dict[str, str] = {}
    for page in _netlist_source_pages(netlist):
        if (
            page is None
            or page.physical_document_id is None
            or page.source_sheet_file is None
        ):
            continue
        previous = observed.setdefault(
            page.physical_document_id, page.source_sheet_file
        )
        if previous != page.source_sheet_file:
            raise SchematicContractError(
                "invariant", "/source_page", "physical document maps to two filenames"
            )


def _component_dtos(
    components: list[NetlistComponent], limits: SchematicContractLimits
) -> tuple[list[NetlistB0Component], _ComponentIdentities]:
    designator_candidates: dict[str, set[tuple[str, str]]] = {}
    ids: dict[str, str] = {}
    rows: list[NetlistB0Component] = []
    for index, component in enumerate(components):
        path = pointer_child("/components", index)
        _require_identity(component.designator, f"{path}/designator")
        component_id = component.component_id or f"rich-component:{index}"
        _require_identity(component_id, f"{path}/component_id")
        if component_id in ids:
            _duplicate(f"{path}/component_id", component_id)
        ids[component_id] = component.designator
        designator_candidates.setdefault(component.designator, set()).add(
            (component_id, component.designator)
        )
        parameters = _string_map(component.parameters, f"{path}/parameters")
        enforce_collection_limit(
            len(parameters),
            limits.max_parameter_pairs,
            f"{path}/parameters",
            "parameter-pair limit",
        )
        rows.append(
            NetlistB0Component(
                component_id=component_id,
                designator=component.designator,
                logical_designator=component.logical_designator,
                physical_designator=component.physical_designator,
                source_page=_source_page_dto(
                    component.source_page, f"{path}/source_page"
                ),
                value=_string(component.value, f"{path}/value"),
                footprint=_string(component.footprint, f"{path}/footprint"),
                library_ref=_string(component.library_ref, f"{path}/library_ref"),
                description=_string(component.description, f"{path}/description"),
                parameters=parameters,
            )
        )
    return rows, _ComponentIdentities(
        by_id={key: (key, value) for key, value in ids.items()},
        by_designator={
            designator: next(iter(candidates))
            for designator, candidates in designator_candidates.items()
            if len(candidates) == 1
        },
    )


def _resolve_component_reference(
    component_id: str,
    designator: str,
    identities: _ComponentIdentities,
    path: str,
) -> tuple[str, str]:
    if component_id:
        return _resolve_reference_by_id(component_id, designator, identities, path)
    return _resolve_reference_by_designator(designator, identities, path)


def _resolve_reference_by_id(
    component_id: str,
    designator: str,
    identities: _ComponentIdentities,
    path: str,
) -> tuple[str, str]:
    resolved = identities.by_id.get(component_id)
    if resolved is None:
        raise SchematicContractError(
            "dangling_reference", f"{path}/component_id", "unknown component ID"
        )
    if designator and resolved[1] != designator:
        raise SchematicContractError(
            "contradictory_reference", path, "component ID and designator disagree"
        )
    return resolved


def _resolve_reference_by_designator(
    designator: str,
    identities: _ComponentIdentities,
    path: str,
) -> tuple[str, str]:
    if not designator:
        raise SchematicContractError(
            "dangling_reference", f"{path}/component_id", "component reference is empty"
        )
    resolved = identities.by_designator.get(designator)
    if resolved is None:
        raise SchematicContractError(
            "dangling_reference",
            f"{path}/designator",
            "unknown or ambiguous designator",
        )
    return resolved


def _net_dto(
    net: Net,
    identities: _ComponentIdentities,
    limits: SchematicContractLimits,
    index: int,
) -> NetlistB0Net:
    path = pointer_child("/nets", index)
    _require_identity(net.uid, f"{path}/uid")
    enforce_collection_limit(
        len(net.terminals), limits.max_terminals, f"{path}/terminals", "terminal limit"
    )
    enforce_collection_limit(
        len(net.endpoints), limits.max_endpoints, f"{path}/endpoints", "endpoint limit"
    )
    terminals = []
    for index, terminal in enumerate(net.terminals):
        row_path = pointer_child(f"{path}/terminals", index)
        component_id, designator = _resolve_component_reference(
            terminal.component_id, terminal.designator, identities, row_path
        )
        _require_identity(terminal.pin, f"{row_path}/pin")
        terminals.append(
            NetlistB0Terminal(
                component_id=component_id,
                designator=designator,
                pin=terminal.pin,
                pin_name=_string(terminal.pin_name, f"{row_path}/pin_name"),
                pin_type=terminal.pin_type.name,
            )
        )
    graphical = _graphical_dto(net.graphical, identities, limits, f"{path}/graphical")
    endpoints = [
        _endpoint_dto(endpoint, identities, pointer_child(f"{path}/endpoints", index))
        for index, endpoint in enumerate(net.endpoints)
    ]
    _unique_identity_rows(
        ((row.endpoint_id, index) for index, row in enumerate(endpoints)),
        f"{path}/endpoints",
        "endpoint_id",
    )
    source_pages = _canonical_source_pages(
        net.source_pages, limits, f"{path}/source_pages"
    )
    aliases = sorted(set(_string(value, f"{path}/aliases") for value in net.aliases))
    enforce_collection_limit(
        len(aliases), limits.max_aliases, f"{path}/aliases", "alias limit"
    )
    hierarchy_paths = _hierarchy_path_dtos(net.hierarchy_paths, limits, path)
    return NetlistB0Net(
        uid=net.uid,
        name=_string(net.name, f"{path}/name"),
        auto_named=_boolean(net.auto_named, f"{path}/auto_named"),
        source_pages=source_pages,
        terminals=sorted(
            terminals,
            key=lambda row: (
                row.component_id,
                row.pin,
                row.designator,
                row.pin_name,
                row.pin_type,
            ),
        ),
        graphical=graphical,
        aliases=aliases,
        endpoints=sorted(endpoints, key=lambda row: (row.endpoint_id, row.role)),
        hierarchy_paths=hierarchy_paths,
    )


def _graphical_dto(
    graphical: NetGraphical,
    identities: _ComponentIdentities,
    limits: SchematicContractLimits,
    path: str,
) -> NetlistB0Graphical:
    pins: list[NetlistB0GraphicalPin] = []
    for index, pin in enumerate(graphical.pins):
        row_path = pointer_child(f"{path}/pins", index)
        component_id, designator = _resolve_component_reference(
            pin.component_id, pin.designator, identities, row_path
        )
        _require_identity(pin.pin, f"{row_path}/pin")
        _require_identity(pin.svg_id, f"{row_path}/svg_id")
        pins.append(
            NetlistB0GraphicalPin(
                component_id=component_id,
                designator=designator,
                pin=pin.pin,
                svg_id=pin.svg_id,
            )
        )
    strings = (
        graphical.wires,
        graphical.junctions,
        graphical.labels,
        graphical.power_ports,
        graphical.ports,
        graphical.sheet_entries,
    )
    link_count = sum(len(values) for values in strings) + len(pins)
    enforce_collection_limit(
        link_count, limits.max_graphical_links, path, "graphical-link limit"
    )
    canonical_strings = [
        sorted(set(_string(value, path) for value in values)) for values in strings
    ]
    unique_pins = {
        (row.component_id, row.pin, row.svg_id, row.designator): row for row in pins
    }
    return NetlistB0Graphical(
        wires=canonical_strings[0],
        junctions=canonical_strings[1],
        labels=canonical_strings[2],
        power_ports=canonical_strings[3],
        ports=canonical_strings[4],
        sheet_entries=canonical_strings[5],
        pins=[unique_pins[key] for key in sorted(unique_pins)],
    )


def _endpoint_dto(
    endpoint: NetEndpoint,
    identities: _ComponentIdentities,
    path: str,
) -> NetlistB0Endpoint:
    _require_identity(endpoint.endpoint_id, f"{path}/endpoint_id")
    _require_identity(endpoint.role, f"{path}/role")
    component_id: str | None = None
    designator: str | None = None
    pin: str | None = None
    pin_name: str | None = None
    pin_type: str | None = None
    if endpoint.role == "pin":
        component_id, designator = _resolve_component_reference(
            endpoint.component_id, endpoint.designator, identities, path
        )
        _require_identity(endpoint.pin, f"{path}/pin")
        pin = endpoint.pin
        pin_name = _string(endpoint.pin_name, f"{path}/pin_name")
        pin_type = endpoint.pin_type.name
    elif (
        endpoint.component_id
        or endpoint.designator
        or endpoint.pin
        or endpoint.pin_name
    ):
        raise SchematicContractError(
            "invariant", path, "non-pin endpoint carries component or pin identity"
        )
    point = (
        NetlistB0ConnectionPoint(
            x=endpoint.connection_point[0],
            y=endpoint.connection_point[1],
            units="altium_coord",
        )
        if endpoint.connection_point is not None
        else None
    )
    return NetlistB0Endpoint(
        endpoint_id=endpoint.endpoint_id,
        role=endpoint.role,
        element_id=_string(endpoint.element_id, f"{path}/element_id"),
        object_id=_string(endpoint.object_id, f"{path}/object_id"),
        name=_string(endpoint.name, f"{path}/name"),
        source_page=_source_page_dto(endpoint.source_page, f"{path}/source_page"),
        component_id=component_id,
        designator=designator,
        pin=pin,
        pin_name=pin_name,
        pin_type=pin_type,
        connection_point=point,
    )


def _hierarchy_path_dtos(
    paths: list[HierarchyPath], limits: SchematicContractLimits, net_path: str
) -> list[list[NetlistB0HierarchyLevel]]:
    enforce_collection_limit(
        len(paths),
        limits.max_hierarchy_paths,
        f"{net_path}/hierarchy_paths",
        "hierarchy-path limit",
    )
    total_levels = sum(len(path.levels) for path in paths)
    enforce_collection_limit(
        total_levels,
        limits.max_hierarchy_levels,
        f"{net_path}/hierarchy_paths",
        "hierarchy-level limit",
    )
    rows = [
        [
            NetlistB0HierarchyLevel(
                sheet_symbol_uid=_identity(level.sheet_symbol_uid, "sheet_symbol_uid"),
                child_filename=_identity(level.child_filename, "child_filename"),
                designator=_string(level.designator, "designator"),
                channel_name=_string(level.channel_name, "channel_name"),
                channel_index=_channel_index(level.channel_index),
                repeat_value=level.repeat_value,
            )
            for level in path.levels
        ]
        for path in paths
    ]
    by_key = {_hierarchy_key(path): path for path in rows}
    return [by_key[key] for key in sorted(by_key)]


def _hierarchy_key(
    path: list[NetlistB0HierarchyLevel],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            level.sheet_symbol_uid,
            level.child_filename,
            level.designator,
            level.channel_name,
            -1 if level.channel_index is None else level.channel_index,
            -1 if level.repeat_value is None else level.repeat_value,
        )
        for level in path
    )


def _channel_index(value: int) -> int | None:
    if value == -1:
        return None
    if value < 0:
        raise SchematicContractError(
            "invariant", "/hierarchy_paths", "channel index must be -1 or non-negative"
        )
    return value


def _canonical_source_pages(
    pages: list[NetlistSourcePage],
    limits: SchematicContractLimits,
    path: str,
) -> list[SchematicSourcePage]:
    enforce_collection_limit(
        len(pages), limits.max_source_pages, path, "source-page limit"
    )
    rows = [
        _source_page_dto(page, pointer_child(path, index))
        for index, page in enumerate(pages)
    ]
    present = [row for row in rows if row is not None]
    by_key = {(row.physical_document_id, row.source_sheet_file): row for row in present}
    return [by_key[key] for key in sorted(by_key, key=_nullable_pair_key)]


def _nullable_pair_key(
    value: tuple[str | None, str | None],
) -> tuple[tuple[int, str], tuple[int, str]]:
    first, second = value
    return (
        (0, "") if first is None else (1, first),
        (0, "") if second is None else (1, second),
    )


def _source_page_dto(
    page: NetlistSourcePage | None, path: str
) -> SchematicSourcePage | None:
    if page is None:
        return None
    physical = page.physical_document_id
    filename = page.source_sheet_file
    if physical == "" or filename == "" or (physical is None and filename is None):
        raise SchematicContractError(
            "invariant", path, "source page requires one non-empty identity"
        )
    return SchematicSourcePage(
        physical_document_id=physical, source_sheet_file=filename
    )


def _string_map(value: Mapping[str, str], path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise SchematicContractError(
                "type_mismatch", path, "parameter keys and values must be strings"
            )
        result[key] = item
    return dict(sorted(result.items()))


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise SchematicContractError("type_mismatch", path, "expected string")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise SchematicContractError("type_mismatch", path, "expected boolean")
    return value


def _identity(value: object, field: str) -> str:
    text = _string(value, f"/{field}")
    _require_identity(text, f"/{field}")
    return text


def _require_identity(value: str, path: str) -> None:
    if not value:
        raise SchematicContractError("invariant", path, "identity must be non-empty")


def _duplicate(path: str, value: str) -> None:
    raise SchematicContractError(
        "duplicate_identity", path, f"duplicate identity {value!r}"
    )


def _unique_identity_rows(
    rows: Iterable[tuple[str, int]], collection_path: str, field_name: str
) -> None:
    seen: set[str] = set()
    for value, index in rows:
        if value in seen:
            _duplicate(f"{collection_path}/{index}/{field_name}", value)
        seen.add(value)


__all__ = (
    "netlist_to_bytes",
    "netlist_to_mapping",
)
