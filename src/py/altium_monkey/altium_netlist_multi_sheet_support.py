"""Shared support types/helpers for the top-level multi-sheet compiler."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol, TypeAlias

from .altium_netlist_model import HierarchyPath, NetGraphical, UnionFind
from .altium_prjpcb import NetIdentifierScope

if TYPE_CHECKING:
    from .altium_netlist_model import Net
    from .altium_schdoc import AltiumSchDoc

log = logging.getLogger(__name__)


SheetNetEntries: TypeAlias = list[tuple[int, "Net"]]


class _HarnessPortNameResolver(Protocol):
    def _find_harness_port_name(
        self,
        harness_connector: object,
        signal_harnesses: object,
        port_location_map: dict[tuple[int, int], str],
    ) -> str | None: ...


@dataclass
class SheetEntryLink:
    """Link between a parent sheet entry and a child port."""

    entry_name: str
    parent_sheet_idx: int
    child_sheet_idx: int
    sheet_sym_uid: str = ""
    port_name: str = ""
    match_by_name: bool = False
    parent_entry_name: str = ""
    child_object_ids: tuple[str, ...] = ()
    hierarchy_path: HierarchyPath = field(default_factory=HierarchyPath)


@dataclass
class RoomDetails:
    """Per-channel room details for channel designator expansion."""

    room_name: str = ""
    channel_prefix: str = ""
    channel_index: str = ""
    channel_alpha: str = ""
    sheet_designator: str = ""
    sheet_number: str = ""
    document_number: str = ""


@dataclass
class ChannelInstance:
    """Links a sheet symbol to a specific channel instance."""

    parent_idx: int
    child_idx: int
    instance_index: int
    sheet_sym_unique_id: str
    room: RoomDetails = None
    repeat_value: int | None = None
    repeat_entry_ports: frozenset[str] = frozenset()


def _build_wire_endpoint_map(schdoc: "AltiumSchDoc") -> dict[tuple[int, int], str]:
    """Build `(x, y) -> wire_uid` lookup for wire endpoints."""
    wire_endpoint_map = {}
    for wire in schdoc.get_wires():
        if wire.points and wire.unique_id:
            for point in wire.points:
                wire_endpoint_map[(point.x, point.y)] = wire.unique_id
    return wire_endpoint_map


def _build_port_location_map(schdoc: "AltiumSchDoc") -> dict[tuple[int, int], str]:
    """Build `(x, y) -> port/entry name` lookup for harness matching."""
    port_location_map = {}

    for port in schdoc.get_ports():
        if port.name and port.location:
            location = port.location
            if isinstance(location, tuple):
                x, y = location
            else:
                x, y = location.x, location.y
            port_location_map[(x, y)] = port.name
            port_width = getattr(port, "width", 0) or 0
            if port_width:
                port_location_map[(x + port_width, y)] = port.name

    for sheet_symbol in schdoc.get_sheet_symbols():
        record = sheet_symbol.record
        for entry in sheet_symbol.entries:
            harness_type = getattr(entry, "harness_type", "")
            if not harness_type:
                continue
            entry_name = entry.display_name or ""
            if not entry_name:
                continue
            entry_side = getattr(entry, "side", None)
            distance_from_top = getattr(entry, "distance_from_top", None)
            if entry_side is None or distance_from_top is None:
                continue
            if entry_side == 1:
                entry_x = record.location.x + record.x_size
            else:
                entry_x = record.location.x
            entry_y = record.location.y - distance_from_top * 10
            port_location_map[(entry_x, entry_y)] = entry_name

    return port_location_map


def _find_or_create_net_for_wire(
    wire_uid: str,
    sheet_idx: int,
    merge_key: str,
    port_net_map: dict,
    other_nets: list,
    harness_keys: set[str],
    harness_entry_id: str = "",
    harness_entry_name: str = "",
    source_sheet: str = "",
    source_sheet_index: int | None = None,
) -> bool:
    """Find the net connected to a wire UID and add it to `port_net_map`."""
    from .altium_netlist_model import Net, NetEndpoint

    def append_harness_endpoint(net: "Net") -> None:
        clean_id = str(harness_entry_id or "").strip()
        if not clean_id:
            return
        endpoint_id = f"harness_entry:{clean_id}"
        if any(endpoint.endpoint_id == endpoint_id for endpoint in net.endpoints):
            return
        net.endpoints.append(
            NetEndpoint(
                endpoint_id=endpoint_id,
                role="harness_entry",
                element_id=clean_id,
                object_id=clean_id,
                name=str(harness_entry_name or ""),
                source_sheet=source_sheet,
                sheet_index=source_sheet_index,
                compiled_sheet_index=sheet_idx,
            )
        )

    for idx in range(len(other_nets) - 1, -1, -1):
        existing_sheet_idx, net = other_nets[idx]
        if existing_sheet_idx == sheet_idx and wire_uid in net.graphical.wires:
            append_harness_endpoint(net)
            port_net_map[merge_key].append((sheet_idx, net))
            other_nets.pop(idx)
            harness_keys.add(merge_key)
            return True

    for existing_entries in port_net_map.values():
        for existing_sheet_idx, net in existing_entries:
            if existing_sheet_idx == sheet_idx and wire_uid in net.graphical.wires:
                append_harness_endpoint(net)
                port_net_map[merge_key].append((sheet_idx, net))
                harness_keys.add(merge_key)
                return True

    synthetic = Net(
        name=merge_key,
        terminals=[],
        graphical=NetGraphical(wires=[wire_uid]),
        auto_named=True,
        endpoints=[
            NetEndpoint(
                endpoint_id=f"harness_entry:{harness_entry_id}",
                role="harness_entry",
                element_id=harness_entry_id,
                object_id=harness_entry_id,
                name=harness_entry_name,
                source_sheet=source_sheet,
                sheet_index=source_sheet_index,
                compiled_sheet_index=sheet_idx,
            )
        ]
        if harness_entry_id
        else [],
    )
    port_net_map[merge_key].append((sheet_idx, synthetic))
    harness_keys.add(merge_key)
    return True


def _collect_net_label_name(
    sheet_nets: SheetNetEntries,
    higher_level_priority: bool,
    has_hierarchy_bridge: bool,
    scope: NetIdentifierScope,
) -> str | None:
    """Resolve the best net-label name from a group of sheet nets."""
    if higher_level_priority and has_hierarchy_bridge:
        parent_names = [
            net.name
            for _, net in sheet_nets
            if not net.auto_named
            and net.graphical.labels
            and net.graphical.sheet_entries
        ]
        if parent_names:
            return min(parent_names, key=str.lower)

    label_names = [
        net.name for _, net in sheet_nets if not net.auto_named and net.graphical.labels
    ]
    if not label_names:
        return None

    if scope == NetIdentifierScope.GLOBAL:
        for _, net in sorted(sheet_nets, key=lambda item: item[0]):
            if not net.auto_named and net.graphical.labels:
                return net.name
    return min(label_names, key=str.lower)


def _resolve_power_display_name(
    power_name: str,
    sheet_nets: SheetNetEntries,
    all_pp_names: set[str],
    power_port_names_take_priority: bool,
) -> str:
    """Resolve display name for a merged power-net group."""
    label_names = {
        net.name for _, net in sheet_nets if not net.auto_named and net.graphical.labels
    }

    pp_by_upper = {}
    for power_port_name in all_pp_names:
        upper_name = power_port_name.upper()
        if upper_name not in pp_by_upper:
            pp_by_upper[upper_name] = power_port_name

    if power_port_names_take_priority:
        return power_name
    if label_names:
        return min(label_names, key=lambda name: name.upper())
    if len(pp_by_upper) > 1:
        return min(pp_by_upper.values(), key=lambda name: name.upper())
    return power_name


def _case_insensitive_consolidate(net_map: dict) -> None:
    """Merge keys that differ only in case, keeping the most-populated one."""
    lower_map = defaultdict(list)
    for name in net_map:
        lower_map[name.lower()].append(name)

    for _, variants in lower_map.items():
        if len(variants) <= 1:
            continue
        canonical = max(variants, key=lambda variant: len(net_map[variant]))
        for variant in variants:
            if variant != canonical:
                net_map[canonical].extend(net_map.pop(variant))


def _resolve_hierarchy_fallback_name(
    sheet_nets: SheetNetEntries,
    allow_sheet_entries: bool,
    auto_name_fn: Callable[[list[object]], str],
    merged_terminals: list[object],
) -> tuple[str, bool]:
    """Fallback name for hierarchy-bridged nets without labels."""
    child_names = [
        net.name
        for _, net in sheet_nets
        if not net.auto_named
        and (net.graphical.labels or (net.graphical.ports and net.terminals))
    ]
    if child_names:
        return min(child_names, key=str.lower), False

    if allow_sheet_entries:
        parent_name = next(
            (
                net.name
                for _, net in sheet_nets
                if not net.auto_named and net.graphical.labels
            ),
            None,
        )
        if parent_name:
            return parent_name, False

    return auto_name_fn(merged_terminals), True


def _collect_hierarchy_paths(
    sheet_nets: list[tuple[int, "Net"]],
    sheet_paths: dict[int, HierarchyPath],
) -> list[HierarchyPath]:
    """Collect unique hierarchy paths from a list of `(sheet_idx, Net)` tuples."""
    paths = []
    seen = set()
    for sheet_idx, _ in sheet_nets:
        path = sheet_paths.get(sheet_idx, HierarchyPath())
        key = path.unique_id_path
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _merge_single_power_net(
    power_name: str,
    net: "Net",
    power_port_names_take_priority: bool,
    net_cls: type["Net"],
) -> "Net":
    """Handle single-sheet power-net naming."""
    if (
        not power_port_names_take_priority
        and net.graphical.labels
        and not net.auto_named
        and net.name != power_name
    ):
        display_name = net.name
    else:
        display_name = power_name

    if display_name != net.name:
        return net_cls(
            name=display_name,
            terminals=net.terminals,
            graphical=net.graphical,
            auto_named=False,
            source_sheets=list(net.source_sheets),
            aliases=[net.name] if not net.auto_named else [],
            endpoints=list(net.endpoints),
        )
    return net


def _detect_repeat_channel(
    ref: tuple[int, object],
    child_idx: int,
    channels: list[ChannelInstance],
) -> None:
    """Detect `REPEAT(name, start, end)` multi-channel instances."""
    parent_idx, sheet_sym_info = ref
    if not sheet_sym_info.record.is_multichannel():
        return

    sheet_name_obj = sheet_sym_info.record.sheet_name
    sheet_name_text = getattr(sheet_name_obj, "text", "") or ""
    repeat_match = re.match(
        r"^REPEAT\s*\(\s*([^,]+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$",
        sheet_name_text.strip(),
        re.IGNORECASE,
    )
    if not repeat_match:
        return

    base_name = repeat_match.group(1).strip()
    start_idx = int(repeat_match.group(2))
    end_idx = int(repeat_match.group(3))

    repeat_ports = frozenset(
        inner_port.lower()
        for entry in sheet_sym_info.entries
        if (inner_port := _parse_entry_repeat(entry.display_name or ""))
    )

    for instance_index, channel_num in enumerate(range(start_idx, end_idx + 1)):
        channel_name = f"{base_name}{channel_num}"
        room = _build_room_details(
            channel_name,
            instance_index,
            sheet_designator=sheet_sym_info.designator,
        )
        channels.append(
            ChannelInstance(
                parent_idx=parent_idx,
                child_idx=child_idx,
                instance_index=instance_index,
                sheet_sym_unique_id=sheet_sym_info.unique_id,
                room=room,
                repeat_value=channel_num,
                repeat_entry_ports=repeat_ports,
            )
        )

    log.debug(
        "Repeat multi-channel detected: '%s' -> %s instances",
        sheet_name_text,
        end_idx - start_idx + 1,
    )


def _detect_multi_ref_channels(
    refs: list[tuple[int, object]],
    child_idx: int,
    child_filename_lower: str,
    channels: list[ChannelInstance],
) -> None:
    """Detect multi-channel instances from multiple sheet-symbol references."""
    for instance_index, (parent_idx, sheet_sym_info) in enumerate(refs):
        sheet_name_obj = sheet_sym_info.record.sheet_name
        sheet_name = getattr(sheet_name_obj, "text", "") or ""
        if not sheet_name:
            sheet_name = f"Channel{instance_index + 1}"

        room = _build_room_details(
            sheet_name,
            instance_index,
            sheet_designator=sheet_sym_info.designator,
        )
        channels.append(
            ChannelInstance(
                parent_idx=parent_idx,
                child_idx=child_idx,
                instance_index=instance_index,
                sheet_sym_unique_id=sheet_sym_info.unique_id,
                room=room,
            )
        )

    log.debug(
        "Multi-channel detected: '%s' referenced %s times",
        child_filename_lower,
        len(refs),
    )


def _remove_bridged_from_map(
    net_map: dict[str, list[tuple[int, "Net"]]],
    bridged_net_ids: set[int],
) -> dict[int, str]:
    """Remove bridged nets from a net map and track their original keys."""
    net_to_key = {}
    for key in list(net_map.keys()):
        entries = net_map[key]
        for _, net in entries:
            if id(net) in bridged_net_ids:
                net_to_key[id(net)] = key
        entries[:] = [
            (sheet_idx, net)
            for sheet_idx, net in entries
            if id(net) not in bridged_net_ids
        ]
        if not entries:
            del net_map[key]
    return net_to_key


def _reinsert_bridge_groups(
    components: dict[int, list[tuple[int, "Net"]]],
    power_net_map: dict,
    port_net_map: dict,
    power_names_for_net: dict[int, str],
    original_key_for_net: dict[int, str],
) -> None:
    """Re-insert bridge-group members into the correct classified map."""
    for group_idx, members in enumerate(components.values()):
        power_name = None
        for _, net in members:
            existing_power_name = power_names_for_net.get(id(net))
            if existing_power_name:
                power_name = existing_power_name
                break

        if power_name:
            power_net_map[power_name].extend(members)
            continue

        existing_key = None
        for _, net in members:
            original_key = original_key_for_net.get(id(net))
            if original_key and original_key in port_net_map:
                existing_key = original_key
                break

        if existing_key:
            port_net_map[existing_key].extend(members)
        else:
            port_net_map[f"__bridge_{group_idx}"] = members


def _build_child_harness_entry_map(
    resolver: _HarnessPortNameResolver,
    child_schdoc: "AltiumSchDoc",
) -> dict[str, list[dict[str, str]]]:
    """Build `port_name -> [{name, object_id}]` lookup for child harness connectors."""
    child_harness_entries = {}
    child_port_location_map = _build_port_location_map(child_schdoc)
    for harness_connector in child_schdoc.harness_connectors:
        harness_port = resolver._find_harness_port_name(
            harness_connector,
            child_schdoc.signal_harnesses,
            child_port_location_map,
        )
        if harness_port:
            child_harness_entries[harness_port.lower()] = [
                {
                    "name": entry.name,
                    "object_id": getattr(entry, "unique_id", "") or "",
                }
                for entry in harness_connector.entries
                if entry.name
            ]
    return child_harness_entries


def _merge_groups_by_shared_key(
    groups: dict[str, SheetNetEntries],
    key_fn: Callable[[int, "Net"], str | None],
) -> int:
    """Merge groups that share a common key."""
    key_to_groups = defaultdict(list)
    for group_name, entries in groups.items():
        for sheet_idx, net in entries:
            key = key_fn(sheet_idx, net)
            if key is not None and group_name not in key_to_groups[key]:
                key_to_groups[key].append(group_name)

    merge_sets = {}
    for _, group_names in key_to_groups.items():
        unique_groups = list(set(group_names))
        if len(unique_groups) <= 1:
            continue
        canonical = min(unique_groups)
        if canonical not in merge_sets:
            merge_sets[canonical] = set()
        merge_sets[canonical].update(unique_groups)

    for canonical, names_to_merge in merge_sets.items():
        for name in names_to_merge:
            if name != canonical and name in groups:
                existing_ids = {id(net) for _, net in groups[canonical]}
                for entry in groups[name]:
                    if id(entry[1]) not in existing_ids:
                        groups[canonical].append(entry)
                        existing_ids.add(id(entry[1]))
                del groups[name]

    return len(merge_sets)


_ENTRY_REPEAT_PATTERN = re.compile(r"^REPEAT\s*\(\s*([^,)]+)\s*\)$", re.IGNORECASE)
_DIFF_PAIR_SUFFIXES = ("_P", "_N")


def _strip_diff_pair_suffix(name: str) -> tuple[str, str]:
    """Strip differential-pair suffix from a net name."""
    idx = name.rfind("_")
    if idx > 0:
        suffix = name[idx:]
        if suffix in _DIFF_PAIR_SUFFIXES:
            return name[:idx], suffix
    return name, ""


def _parse_entry_repeat(entry_name: str) -> str | None:
    """Parse `REPEAT(portName)` from a sheet-entry name."""
    match = _ENTRY_REPEAT_PATTERN.match(entry_name.strip())
    return match.group(1).strip() if match else None


def _build_room_details(
    sheet_sym_name: str,
    instance_index: int,
    sheet_designator: str = "",
) -> RoomDetails:
    """Build `RoomDetails` from a sheet-symbol name and instance index."""
    match = re.match(r"^(.*?)(\d+)$", sheet_sym_name)
    if match:
        prefix = match.group(1)
        index = match.group(2)
    else:
        prefix = sheet_sym_name
        index = str(instance_index + 1)

    alpha_index = instance_index + 1
    if 1 <= alpha_index <= 26:
        channel_alpha = chr(ord("A") + alpha_index - 1)
    else:
        channel_alpha = str(alpha_index)

    return RoomDetails(
        room_name=sheet_sym_name,
        channel_prefix=prefix,
        channel_index=index,
        channel_alpha=channel_alpha,
        sheet_designator=sheet_designator,
        sheet_number=str(instance_index + 1),
        document_number=str(instance_index + 1),
    )


def apply_channel_pattern(format_str: str, room: RoomDetails, designator: str) -> str:
    """Apply channel designator format string."""
    result = format_str
    result = result.replace("$RoomName", room.room_name)
    result = result.replace("$ChannelPrefix", room.channel_prefix)
    result = result.replace("$ChannelIndex", room.channel_index)
    result = result.replace("$ChannelAlpha", room.channel_alpha)
    result = result.replace("$SheetDesignator", room.sheet_designator)
    result = result.replace("$SheetNumber", room.sheet_number)
    result = result.replace("$DocumentNumber", room.document_number)

    match = re.match(r"^([A-Za-z]+)(\d+)$", designator)
    if match:
        comp_prefix = match.group(1)
        comp_index = match.group(2)
    else:
        comp_prefix = designator
        comp_index = ""

    result = result.replace("$ComponentPrefix", comp_prefix)
    result = result.replace("$ComponentIndex", comp_index)
    result = result.replace("$Component", designator)
    return result


def _bridge_power_keys_by_net_labels(
    uf: UnionFind,
    power_net_map: dict,
    higher_level_names_take_priority: bool,
) -> None:
    """Bridge power-net-map keys via net-label names and aliases."""
    if higher_level_names_take_priority:
        return
    for key, entries in power_net_map.items():
        for _, net in entries:
            names_to_check = [net.name] + list(net.aliases)
            for label_name in names_to_check:
                if label_name != key and uf.contains(label_name):
                    uf.union(key, label_name)


def _pick_canonical_power_key(members: list[str], power_net_map: dict) -> str:
    """Pick canonical key for a merged power group."""
    label_backed = []
    for member in members:
        for _, net in power_net_map[member]:
            if net.graphical.labels:
                label_backed.append(member)
                break

    if label_backed:
        return min(label_backed, key=lambda key: key.upper())
    return min(members, key=lambda key: key.upper())


__all__ = [
    "ChannelInstance",
    "RoomDetails",
    "SheetEntryLink",
    "_bridge_power_keys_by_net_labels",
    "_build_child_harness_entry_map",
    "_build_port_location_map",
    "_build_wire_endpoint_map",
    "_case_insensitive_consolidate",
    "_collect_hierarchy_paths",
    "_collect_net_label_name",
    "_detect_multi_ref_channels",
    "_detect_repeat_channel",
    "_find_or_create_net_for_wire",
    "_merge_groups_by_shared_key",
    "_merge_single_power_net",
    "_parse_entry_repeat",
    "_pick_canonical_power_key",
    "_reinsert_bridge_groups",
    "_remove_bridged_from_map",
    "_resolve_hierarchy_fallback_name",
    "_resolve_power_display_name",
    "_strip_diff_pair_suffix",
    "apply_channel_pattern",
]
