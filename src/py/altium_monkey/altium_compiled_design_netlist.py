"""Adapters from compiled schematic designs to the legacy netlist model."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, cast

from .altium_compiled_design_model import (
    AltiumCompiledDesign,
    AltiumCompiledNet,
    AltiumCompiledNetEndpoint,
    AltiumCompiledNetTerminal,
    AltiumCompiledPhysicalDocument,
)
from .altium_compiled_design_support import (
    _build_port_location_map,
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
    PinType,
    Terminal,
)
from .altium_netlist_wire_connectivity import (
    altium_internal_tolerance_for_display_unit,
)

if TYPE_CHECKING:
    from .altium_schdoc import AltiumSchDoc

SCHEMATIC_HIERARCHY_SCHEMA = "altium_monkey.schematic_hierarchy.a1"


def _legacy_terminal_designator_map(
    compiled: AltiumCompiledDesign,
) -> dict[str, str]:
    """Map compiled full-part designators back to legacy component rows."""
    result: dict[str, str] = {}
    display_designators = {
        component.display_designator
        for component in compiled.components
        if component.display_designator
    }
    for display_designator in display_designators:
        result[display_designator] = display_designator

    def add_alias(alias: str, display_designator: str) -> None:
        if not alias:
            return
        if alias in display_designators and alias != display_designator:
            return
        result.setdefault(alias, display_designator)

    for component in compiled.components:
        display_designator = component.display_designator
        if not display_designator:
            continue
        base_designators = {
            value
            for value in (
                component.logical_designator,
                component.physical_designator,
                component.display_designator,
            )
            if value
        }
        for base_designator in base_designators:
            add_alias(base_designator, display_designator)
            for part_id in range(1, component.part_count + 1):
                suffix = _component_part_alpha_suffix(
                    part_count=component.part_count,
                    current_part_id=part_id,
                )
                if suffix:
                    add_alias(f"{base_designator}{suffix}", display_designator)
    return result


def _pin_type_from_name(name: str) -> PinType:
    try:
        return PinType[name]
    except KeyError:
        return PinType.PASSIVE


def _compiled_terminal_to_terminal(
    terminal: AltiumCompiledNetTerminal,
    designator_map: dict[str, str],
) -> Terminal:
    return Terminal(
        designator=designator_map.get(terminal.designator, terminal.designator),
        pin=terminal.pin,
        pin_name=terminal.pin_name,
        pin_type=_pin_type_from_name(terminal.pin_type),
    )


def _compiled_endpoint_to_endpoint(
    endpoint: AltiumCompiledNetEndpoint,
    terminal_types: dict[tuple[str, str], PinType],
    designator_map: dict[str, str],
) -> NetEndpoint:
    designator = designator_map.get(endpoint.designator, endpoint.designator)
    return NetEndpoint(
        endpoint_id=endpoint.id,
        role=endpoint.role,
        element_id=endpoint.element_id,
        object_id=endpoint.object_id,
        name=endpoint.name,
        designator=designator,
        pin=endpoint.pin,
        pin_name=endpoint.pin_name,
        pin_type=terminal_types.get(
            (designator, endpoint.pin),
            PinType.PASSIVE,
        ),
        connection_point=endpoint.connection_point,
    )


def _compiled_graphical(
    net: AltiumCompiledNet,
    designator_map: dict[str, str],
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
            graphical.pins.append(
                GraphicalPinRef(
                    designator=designator_map.get(
                        endpoint.designator,
                        endpoint.designator,
                    ),
                    pin=endpoint.pin,
                    svg_id=element_id,
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
    designator_map: dict[str, str],
) -> Net:
    terminals: list[Terminal] = []
    seen_terminals: set[tuple[str, str]] = set()
    for compiled_terminal in compiled_net.terminals:
        terminal = _compiled_terminal_to_terminal(compiled_terminal, designator_map)
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
        graphical=_compiled_graphical(compiled_net, designator_map),
        auto_named=_compiled_netlist_auto_named(compiled_net, terminals),
        uid=compiled_net.id,
        source_sheets=list(compiled_net.physical_document_ids),
        aliases=_compiled_net_aliases(compiled_net),
        endpoints=[
            _compiled_endpoint_to_endpoint(endpoint, terminal_types, designator_map)
            for endpoint in compiled_net.endpoints
        ],
    )


def _compiled_net_is_legacy_visible(
    compiled: AltiumCompiledDesign,
    compiled_net: AltiumCompiledNet,
) -> bool:
    """Return whether a compiled net should appear on legacy netlist surfaces.

    Compile rows can retain inferred and object-only signals. Named zero-pin
    rows remain visible, matching the AD26 flattened-design surfaces; isolated
    one-pin nets remain project-option controlled.
    """
    pin_count = len(compiled_net.terminals)
    if pin_count == 0:
        return bool(compiled_net.name)
    if pin_count > 1:
        return True
    endpoint_roles = {endpoint.role for endpoint in compiled_net.endpoints}
    connector_only_hierarchy_net = bool(compiled_net.link_ids) and not (
        endpoint_roles & {"net_label", "power_port"}
    )
    if connector_only_hierarchy_net and not compiled.options.netlist_single_pin_nets:
        return False
    if _compiled_net_item_count_for_legacy_visibility(compiled_net) > 1:
        return True
    return compiled.options.netlist_single_pin_nets


def _compiled_net_item_count_for_legacy_visibility(
    compiled_net: AltiumCompiledNet,
) -> int:
    """Count ordinary and removed net items retained by the compiled model."""
    removed_items = getattr(compiled_net, "removed_items", ())
    return len(compiled_net.items) + len(removed_items)


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


def _parse_sheet_symbol_repeat(designator: str) -> dict[str, object]:
    repeat: dict[str, object] = {
        "is_repeated": False,
        "syntax": "",
        "base_designator": "",
        "values": [],
    }
    match = re.match(
        r"^REPEAT\s*\(\s*([^,]+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$",
        (designator or "").strip(),
        re.IGNORECASE,
    )
    if not match:
        return repeat
    start_idx = int(match.group(2))
    end_idx = int(match.group(3))
    return {
        "is_repeated": True,
        "syntax": designator,
        "base_designator": match.group(1).strip(),
        "values": list(range(start_idx, end_idx + 1)),
    }


def _parse_entry_repeat_name(entry_name: str) -> str:
    match = re.match(
        r"^REPEAT\s*\(\s*([^,]+)\s*,\s*\d+\s*,\s*\d+\s*\)$",
        (entry_name or "").strip(),
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


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
            file_name_map.setdefault(file_name.lower(), schdoc)
    for logical in compiled.logical_documents:
        result.setdefault(logical.id, file_name_map.get(logical.file_name.lower()))
    return {key: value for key, value in result.items() if value is not None}


def _source_sheet_symbol_for(
    symbol_id: str,
    source_object_id: str,
    schdoc: object | None,
) -> object | None:
    if schdoc is None:
        return None
    get_sheet_symbols = getattr(schdoc, "get_sheet_symbols", None)
    if not callable(get_sheet_symbols):
        return None
    for symbol in cast(Iterable[object], get_sheet_symbols()):
        unique_id = str(getattr(symbol, "unique_id", "") or "")
        if unique_id in {source_object_id, symbol_id}:
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
                "repeat": _parse_sheet_symbol_repeat(symbol.designator),
                "entries": entries,
                "metadata": {},
            }
        )
    return result


def _is_channel_physical_document(
    compiled: AltiumCompiledDesign,
    physical_document_id: str,
) -> bool:
    physical_document = {
        document.id: document for document in compiled.physical_documents
    }.get(physical_document_id)
    if (
        physical_document is not None
        and physical_document.parent_sheet_symbol_id is not None
        and (
            physical_document.channel_index > 0
            or bool(physical_document.channel_prefix)
            or bool(physical_document.channel_alpha)
        )
    ):
        return True
    physical_symbol = {
        symbol.child_physical_document_id: symbol
        for symbol in compiled.physical_sheet_symbols
    }.get(physical_document_id)
    if physical_symbol is None:
        return False
    logical_symbol = {symbol.id: symbol for symbol in compiled.sheet_symbols}.get(
        physical_symbol.logical_sheet_symbol_id
    )
    return bool(
        logical_symbol
        and (
            logical_symbol.is_repeat
            or len(logical_symbol.child_physical_document_ids) > 1
        )
    )


def _compiled_hierarchy_level(
    compiled: AltiumCompiledDesign,
    physical_document_id: str,
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
    is_channel = _is_channel_physical_document(compiled, physical_document_id)
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
    designator = (
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
    if is_channel and document.channel_index >= 0:
        level["channel_index"] = document.channel_index
    if (
        logical_symbol is not None
        and logical_symbol.is_repeat
        and logical_symbol.repeat_start is not None
        and document.channel_index >= 0
    ):
        level["repeat_value"] = document.channel_index
    return level


def _compiled_hierarchy_path_levels(
    compiled: AltiumCompiledDesign,
    physical_document_id: str,
    memo: dict[str, list[dict[str, object]]],
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
    )
    levels = [*parent_levels, _compiled_hierarchy_level(compiled, physical_document_id)]
    memo[physical_document_id] = levels
    return levels


def _hierarchy_path_display(levels: list[dict[str, object]]) -> str:
    if not levels:
        return "---"
    parts = []
    for level in levels:
        name = str(level.get("designator") or level.get("child_filename") or "")
        channel_name = str(level.get("channel_name") or "")
        if channel_name:
            name = f"{name}[{channel_name}]"
        parts.append(name)
    return " > ".join(parts)


def _build_compiled_hierarchy_paths(
    compiled: AltiumCompiledDesign,
) -> tuple[list[dict], dict[str, str]]:
    memo: dict[str, list[dict[str, object]]] = {}
    path_id_by_physical_id: dict[str, str] = {}
    rows: list[dict] = []
    for document in sorted(compiled.physical_documents, key=lambda item: item.ordinal):
        if not document.parent_id:
            continue
        levels = _compiled_hierarchy_path_levels(compiled, document.id, memo)
        path_id = f"path-{len(rows) + 1:04d}"
        path_id_by_physical_id[document.id] = path_id
        rows.append(
            {
                "id": path_id,
                "levels": levels,
                "path": _hierarchy_path_display(levels),
                "unique_id_path": "\\".join(
                    str(level.get("sheet_symbol_uid") or "") for level in levels
                ),
            }
        )
    return rows, path_id_by_physical_id


def _build_compiled_channels(
    compiled: AltiumCompiledDesign,
    path_id_by_physical_id: dict[str, str],
) -> list[dict]:
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    physical_by_id = {document.id: document for document in compiled.physical_documents}
    symbol_by_id = {symbol.id: symbol for symbol in compiled.sheet_symbols}
    rows: list[dict] = []
    for document in sorted(compiled.physical_documents, key=lambda item: item.ordinal):
        if not document.parent_id or not _is_channel_physical_document(
            compiled, document.id
        ):
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
            and document.channel_index >= 0
        ):
            repeat_value = document.channel_index
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
                "channel_index": str(document.channel_index)
                if document.channel_index >= 0
                else "",
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
    if len(candidates) >= 2:
        return candidates[0], candidates[1]
    return None, None


def _strip_harness_parent_name(value: str) -> str:
    clean = str(value or "").strip()
    if clean.startswith("{") and clean.endswith("}"):
        return clean[1:-1]
    return clean


def _build_compiled_hierarchy_links(
    compiled: AltiumCompiledDesign,
    path_id_by_physical_id: dict[str, str],
) -> list[dict]:
    if compiled.options.effective_hierarchy_mode not in {
        "HIERARCHICAL",
        "STRICT_HIERARCHICAL",
    }:
        return []
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    symbol_by_id = {symbol.id: symbol for symbol in compiled.sheet_symbols}
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
        parent_items = [
            item
            for item in net.items
            if item.physical_document_id == parent_document.id
            and item.kind in {"sheet_entry", "harness_entry"}
        ]
        child_items = [
            item
            for item in net.items
            if item.physical_document_id == child_document.id
            and item.kind in {"port", "harness_entry"}
        ]
        parent_item = parent_items[0] if parent_items else None
        child_item = child_items[0] if child_items else None
        if parent_item is None or child_item is None:
            continue
        match_by_harness = (
            parent_item.kind == "harness_entry" or child_item.kind == "harness_entry"
        )
        if match_by_harness:
            parent_entry_name = _strip_harness_parent_name(parent_item.parent_id)
            link_name = net.name
            child_name = net.name
        else:
            parent_entry_name = parent_item.name
            link_name = parent_item.name
            child_name = child_item.name or parent_item.name
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
        is_channel = _is_channel_physical_document(compiled, child_document.id)
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
                    "object_ids": _unique_nonempty(
                        [
                            child_item.object_id,
                            child_item.element_id,
                        ]
                    ),
                    "name": child_name,
                },
                "match_kind": "harness_name" if match_by_harness else "name",
                "hierarchy_path_id": path_id_by_physical_id.get(child_document.id, ""),
                "channel_index": child_document.channel_index if is_channel else None,
                "channel_name": child_document.room_name if is_channel else "",
                "repeat_value": None,
                "metadata": {},
            }
        )
    return rows


def _build_harness_bundle_endpoint_map(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None,
) -> dict[str, list[dict]]:
    schdoc_by_logical_id = _source_schdoc_by_logical_id(compiled, schdocs)
    first_physical_by_logical_id = {}
    for document in sorted(compiled.physical_documents, key=lambda item: item.ordinal):
        first_physical_by_logical_id.setdefault(document.logical_document_id, document)
    logical_by_id = {document.id: document for document in compiled.logical_documents}
    endpoint_map: dict[str, list[dict]] = defaultdict(list)
    for logical_id, schdoc in schdoc_by_logical_id.items():
        if not getattr(schdoc, "harness_connectors", None):
            continue
        logical = logical_by_id.get(logical_id)
        physical = first_physical_by_logical_id.get(logical_id)
        sheet_index = logical.ordinal if logical is not None else None
        compiled_sheet_index = getattr(physical, "ordinal", sheet_index)
        port_location_map = _build_port_location_map(schdoc)
        display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
        internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
        for connector in schdoc.harness_connectors:
            bundle_info = find_harness_bundle_info(
                connector,
                getattr(schdoc, "signal_harnesses", None),
                port_location_map,
                internal_tolerance,
            )
            port_name_value = bundle_info.get("port_name")
            port_name = port_name_value if isinstance(port_name_value, str) else ""
            if not port_name:
                continue
            port_ids = [
                str(getattr(port, "unique_id", "") or "")
                for port in schdoc.get_ports()
                if getattr(port, "name", "")
                and str(getattr(port, "name", "")).lower() == port_name.lower()
            ]
            signal_harness_ids_value = bundle_info.get("signal_harness_ids")
            signal_harness_ids = (
                [str(value) for value in signal_harness_ids_value]
                if isinstance(signal_harness_ids_value, list)
                else []
            )
            connector_id = str(getattr(connector, "unique_id", "") or "").strip()
            object_ids = _unique_nonempty(
                [*port_ids, *signal_harness_ids, connector_id]
            )
            endpoint_map[port_name.lower()].append(
                {
                    "name": port_name,
                    "sheet_index": sheet_index,
                    "compiled_sheet_index": compiled_sheet_index,
                    "port_ids": _unique_nonempty(port_ids),
                    "signal_harness_ids": _unique_nonempty(signal_harness_ids),
                    "harness_connector_ids": _unique_nonempty([connector_id]),
                    "object_ids": object_ids,
                    "member_names": [
                        str(getattr(entry, "name", "") or "")
                        for entry in getattr(connector, "entries", [])
                        if getattr(entry, "name", "")
                    ],
                }
            )
    return endpoint_map


def _build_compiled_harness_bundle_links(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None,
    hierarchy_links: list[dict],
) -> list[dict]:
    endpoints_by_name = _build_harness_bundle_endpoint_map(compiled, schdocs)
    rows: list[dict] = []

    def append_link(
        *,
        name: str,
        topology: str,
        parent_endpoint: dict,
        child_endpoint: dict,
        parent: dict | None = None,
    ) -> None:
        parent_ids = _unique_nonempty(
            parent.get("object_ids", []) if parent else parent_endpoint["port_ids"]
        )
        child_ids = _unique_nonempty(child_endpoint["port_ids"])
        rows.append(
            {
                "id": f"harness-bundle-link-{len(rows) + 1:04d}",
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
        )

    hierarchical_keys: set[tuple[object, object, str, str]] = set()
    for link in hierarchy_links:
        if link.get("match_kind") != "harness_name":
            continue
        parent = link.get("parent", {})
        child = link.get("child", {})
        parent_entry_name = str(parent.get("sheet_entry_name") or "").strip()
        if not parent_entry_name:
            continue
        child_endpoint = next(
            (
                endpoint
                for endpoint in endpoints_by_name.get(parent_entry_name.lower(), [])
                if endpoint["sheet_index"] == child.get("sheet_index")
            ),
            None,
        )
        if not child_endpoint:
            continue
        key = (
            parent.get("sheet_index"),
            child.get("sheet_index"),
            str(parent.get("sheet_symbol_id") or ""),
            parent_entry_name.lower(),
        )
        if key in hierarchical_keys:
            continue
        hierarchical_keys.add(key)
        parent_graphical_id = str(parent.get("object_id") or "")
        parent_endpoint = {
            "name": parent_entry_name,
            "sheet_index": parent.get("sheet_index"),
            "compiled_sheet_index": parent.get("compiled_sheet_index"),
            "port_ids": [parent_graphical_id],
            "object_ids": [parent_graphical_id],
            "signal_harness_ids": [],
            "harness_connector_ids": [],
        }
        append_link(
            name=parent_entry_name,
            topology="hierarchical",
            parent_endpoint=parent_endpoint,
            child_endpoint=child_endpoint,
            parent={
                "sheet_index": parent.get("sheet_index"),
                "compiled_sheet_index": parent.get("compiled_sheet_index"),
                "object_kind": "sheet_entry",
                "object_id": parent_graphical_id,
                "graphical_id": parent_graphical_id,
                "object_ids": [parent_graphical_id],
                "sheet_symbol_id": parent.get("sheet_symbol_id"),
                "name": parent_entry_name,
            },
        )

    if compiled.options.effective_hierarchy_mode in {"FLAT", "GLOBAL"}:
        for endpoint_name in sorted(endpoints_by_name):
            endpoints = sorted(
                endpoints_by_name[endpoint_name],
                key=lambda endpoint: (
                    endpoint["compiled_sheet_index"],
                    endpoint["name"],
                ),
            )
            for index in range(len(endpoints) - 1):
                append_link(
                    name=endpoints[index]["name"],
                    topology="flat",
                    parent_endpoint=endpoints[index],
                    child_endpoint=endpoints[index + 1],
                )
    return rows


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
            str(link.get("parent", {}).get("sheet_entry_name") or "").lower(),
        )
        for link in hierarchy_links
    }
    rows: list[dict] = []
    for symbol in compiled.sheet_symbols:
        parent_logical = logical_by_id.get(symbol.logical_document_id)
        child_logical = (
            logical_by_id.get(symbol.child_logical_document_id)
            if symbol.child_logical_document_id
            else None
        )
        sheet_symbol_id = symbol.source_object_id or symbol.id
        if child_logical is None:
            rows.append(
                {
                    "kind": "missing_child_sheet",
                    "parent_sheet_index": (
                        parent_logical.ordinal if parent_logical is not None else None
                    ),
                    "parent_compiled_sheet_index": (
                        parent_logical.ordinal if parent_logical is not None else None
                    ),
                    "sheet_symbol_id": sheet_symbol_id,
                    "child_filename": symbol.child_filename,
                }
            )
            continue
        source_symbol = _source_sheet_symbol_for(
            symbol.id,
            symbol.source_object_id,
            schdoc_by_logical_id.get(symbol.logical_document_id),
        )
        child_schdoc = schdoc_by_logical_id.get(child_logical.id)
        child_port_names = (
            {
                str(getattr(port, "name", "") or "").lower()
                for port in child_schdoc.get_ports()
            }
            if child_schdoc is not None
            else set()
        )
        for entry in getattr(source_symbol, "entries", ()) or ():
            entry_name = _entry_display_name(entry)
            if not entry_name:
                continue
            if (sheet_symbol_id, entry_name.lower()) in linked_keys:
                continue
            if str(getattr(entry, "harness_type", "") or ""):
                continue
            match_name = _parse_entry_repeat_name(entry_name) or entry_name
            if match_name.lower() in child_port_names:
                continue
            rows.append(
                {
                    "kind": "unmatched_child_port",
                    "parent_sheet_index": (
                        parent_logical.ordinal if parent_logical is not None else None
                    ),
                    "parent_compiled_sheet_index": (
                        parent_logical.ordinal if parent_logical is not None else None
                    ),
                    "child_sheet_index": child_logical.ordinal,
                    "child_compiled_sheet_index": child_logical.ordinal,
                    "sheet_symbol_id": sheet_symbol_id,
                    "entry_name": entry_name,
                    "port_name": match_name,
                }
            )
    return rows


def compiled_design_schematic_hierarchy(
    compiled: AltiumCompiledDesign,
    schdocs: Sequence["AltiumSchDoc"] | None = None,
) -> dict:
    """Build public design-JSON schematic hierarchy metadata from compiled rows."""
    hierarchy_paths, path_id_by_physical_id = _build_compiled_hierarchy_paths(compiled)
    hierarchy_links = _build_compiled_hierarchy_links(
        compiled,
        path_id_by_physical_id,
    )
    return {
        "schema": SCHEMATIC_HIERARCHY_SCHEMA,
        "requested_scope": compiled.options.hierarchy_mode,
        "effective_scope": compiled.options.effective_hierarchy_mode,
        "documents": _build_compiled_hierarchy_documents(compiled),
        "sheet_symbols": _build_compiled_hierarchy_sheet_symbols(compiled, schdocs),
        "hierarchy_paths": hierarchy_paths,
        "channels": _build_compiled_channels(compiled, path_id_by_physical_id),
        "links": hierarchy_links,
        "harness_bundle_links": _build_compiled_harness_bundle_links(
            compiled,
            schdocs,
            hierarchy_links,
        ),
        "unresolved": _build_compiled_hierarchy_unresolved(
            compiled,
            schdocs,
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
    components_by_designator: dict[str, NetlistComponent] = {}
    for component in compiled.components:
        if not component.include_in_netlist or not component.display_designator:
            continue
        components_by_designator[component.display_designator] = NetlistComponent(
            designator=component.display_designator,
            value=component.value,
            footprint=component.footprint,
            library_ref=component.lib_reference,
            description=component.description,
            parameters=dict(component.parameters),
            component_kind=component.component_kind_value,
            exclude_from_bom=component.exclude_from_bom,
        )
    legacy_terminal_designators = _legacy_terminal_designator_map(compiled)
    nets = [_compiled_net_to_net(net, legacy_terminal_designators) for net in flat_nets]
    return Netlist(nets=nets, components=list(components_by_designator.values()))


__all__ = ["compiled_design_schematic_hierarchy", "compiled_design_to_netlist"]
