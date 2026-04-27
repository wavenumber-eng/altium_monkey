"""
Top-level multi-sheet compiler.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import replace
from copy import copy
from typing import TYPE_CHECKING, Optional

from .altium_netlist_common import _altium_net_sort_key
from .altium_netlist_multi_sheet_support import (
    SheetEntryLink,
    _bridge_power_keys_by_net_labels,
    _build_child_harness_entry_map,
    _build_port_location_map,
    _build_wire_endpoint_map,
    _case_insensitive_consolidate,
    _collect_hierarchy_paths,
    _collect_net_label_name,
    _detect_multi_ref_channels,
    _detect_repeat_channel,
    _find_or_create_net_for_wire,
    _merge_single_power_net,
    _merge_groups_by_shared_key,
    _parse_entry_repeat,
    _pick_canonical_power_key,
    _reinsert_bridge_groups,
    _remove_bridged_from_map,
    _resolve_hierarchy_fallback_name,
    _resolve_power_display_name,
    _strip_diff_pair_suffix,
    apply_channel_pattern,
)
from .altium_netlist_single_sheet import AltiumNetlistSingleSheetCompiler

if TYPE_CHECKING:
    from .altium_netlist_model import HierarchyPath, Net, Netlist, Terminal, UnionFind
    from .altium_netlist_options import NetlistOptions
    from .altium_prjpcb import AltiumPrjPcb, NetIdentifierScope
    from .altium_schdoc import AltiumSchDoc
    from .altium_schdoc_info import SchSheetSymbolInfo
    from .altium_netlist_multi_sheet_support import ChannelInstance, RoomDetails

log = logging.getLogger(__name__)


class AltiumNetlistMultiSheetCompiler:
    """
    Independent multi-sheet compiler entrypoint.

        The public compile surface owns its constructor/build flow and compiles
        each sheet through the single-sheet compiler. Merge and hierarchy helpers
        live in support functions and private compiler methods.
    """

    def __init__(
        self,
        schdocs: list["AltiumSchDoc"],
        project: Optional["AltiumPrjPcb"],
        options: "NetlistOptions",
    ) -> None:
        """
        Initialize the multi-sheet compiler.
        """
        self._schdocs = schdocs
        self._source_schdocs = list(schdocs)
        self._project = project
        self._options = options
        self._channel_instances = []
        self._channel_netlist_map = {}
        self._compiled_to_source_sheet_idx = {
            idx: idx for idx in range(len(self._source_schdocs))
        }
        self._effective_scope = None
        self._sheet_paths = {}
        self._hierarchy_links: list[SheetEntryLink] = []
        self._hierarchy_unresolved: list[dict] = []

    def build(self) -> "Netlist":
        """
        Build the multi-sheet netlist.
        """
        from .altium_netlist_model import Netlist
        from .altium_prjpcb import NetIdentifierScope

        log.debug("Starting netlist generation (multi-sheet compiler)")

        self._effective_scope = self._resolve_automatic_scope()
        sheet_netlists = self._generate_per_sheet_netlists()

        channels = self._detect_multi_channel()
        if channels:
            format_str = self._options.channel_designator_format
            if not format_str:
                format_str = "$Component$ChannelAlpha"
            sheet_netlists = self._expand_multi_channel(
                sheet_netlists,
                channels,
                format_str,
            )

        self._build_sheet_paths()
        self._annotate_endpoint_sheet_context(sheet_netlists)

        all_components = self._merge_components(sheet_netlists)
        classified = self._classify_and_expand_nets(sheet_netlists)
        self._bridge_hierarchical_nets(classified, sheet_netlists)

        port_net_map = classified["port_net_map"]
        self._merge_port_groups(port_net_map)
        scope = self._effective_scope
        ports_are_global = scope in (
            NetIdentifierScope.FLAT,
            NetIdentifierScope.GLOBAL,
        )
        self._absorb_matching_other_nets(
            classified["power_net_map"],
            port_net_map,
            classified["other_nets"],
            ports_are_global,
        )

        merged_nets = self._merge_nets_by_scope(classified)

        if self._effective_scope in (
            NetIdentifierScope.HIERARCHICAL,
            NetIdentifierScope.STRICT_HIERARCHICAL,
        ):
            harness_entry_names = classified.get("harness_entry_names", set())

            def _is_hierarchy_artifact(net: "Net") -> bool:
                if net.graphical.ports:
                    return True
                if net.graphical.sheet_entries:
                    return net.name in harness_entry_names
                return False

            merged_nets = [
                net
                for net in merged_nets
                if net.terminals or not _is_hierarchy_artifact(net)
            ]

        if self._effective_scope not in (
            NetIdentifierScope.HIERARCHICAL,
            NetIdentifierScope.STRICT_HIERARCHICAL,
        ):
            for net in merged_nets:
                if (
                    len(net.terminals) == 1
                    and net.auto_named
                    and net.graphical.sheet_entries
                ):
                    net.auto_named = False

        final_nets = self._finalize_nets(merged_nets)

        log.debug(
            "Multi-sheet merge produced %s nets, %s components",
            len(final_nets),
            len(all_components),
        )

        netlist = Netlist(nets=final_nets, components=all_components)
        netlist.schematic_hierarchy = self.build_schematic_hierarchy_metadata()
        return netlist

    def _generate_per_sheet_netlists(self) -> list["Netlist"]:
        """
        Generate per-sheet netlists through the single-sheet compiler.
        """
        from .altium_prjpcb import NetIdentifierScope

        options = copy(self._options)
        options.net_identifier_scope = self._effective_scope
        scope = self._effective_scope
        if scope not in (
            NetIdentifierScope.HIERARCHICAL,
            NetIdentifierScope.STRICT_HIERARCHICAL,
        ):
            options.allow_sheet_entries_to_name_nets = False

        sheet_netlists = []
        for schdoc in self._schdocs:
            gen = AltiumNetlistSingleSheetCompiler(schdoc, options=options)
            sheet_netlists.append(gen.generate())

        log.debug("Generated %s per-sheet netlists", len(sheet_netlists))
        return sheet_netlists

    def _resolve_automatic_scope(self) -> "NetIdentifierScope":
        """
        Resolve AUTOMATIC scope to a concrete scope.
        """
        from .altium_prjpcb import NetIdentifierScope

        scope = self._options.net_identifier_scope
        if scope != NetIdentifierScope.AUTOMATIC:
            return scope

        has_sheet_entries = False
        has_ports = False
        for schdoc in self._schdocs:
            if not has_sheet_entries:
                for sheet_symbol in schdoc.get_sheet_symbols():
                    if sheet_symbol.entries:
                        has_sheet_entries = True
                        break
            if not has_ports and schdoc.get_ports():
                has_ports = True
            if has_sheet_entries and has_ports:
                break

        if has_sheet_entries:
            return NetIdentifierScope.HIERARCHICAL
        if has_ports:
            return NetIdentifierScope.FLAT
        return NetIdentifierScope.GLOBAL

    def _detect_multi_channel(self) -> list["ChannelInstance"]:
        """
        Detect multi-channel children.
        """
        child_refs: dict[str, list[tuple[int, object]]] = defaultdict(list)

        for parent_idx, schdoc in enumerate(self._schdocs):
            for sheet_sym_info in schdoc.get_sheet_symbols():
                file_name_obj = sheet_sym_info.record.file_name
                if file_name_obj is None:
                    continue
                child_filename = getattr(file_name_obj, "text", "") or ""
                if child_filename:
                    child_refs[child_filename.lower()].append(
                        (parent_idx, sheet_sym_info)
                    )

        filename_to_idx: dict[str, int] = {}
        for idx, schdoc in enumerate(self._schdocs):
            if schdoc.filepath:
                filename_to_idx[schdoc.filepath.name.lower()] = idx

        channels = []
        for child_filename_lower, refs in child_refs.items():
            child_idx = filename_to_idx.get(child_filename_lower)
            if child_idx is None:
                continue
            if len(refs) == 1:
                _detect_repeat_channel(refs[0], child_idx, channels)
            else:
                _detect_multi_ref_channels(
                    refs,
                    child_idx,
                    child_filename_lower,
                    channels,
                )

        return channels

    def _expand_multi_channel(
        self,
        sheet_netlists: list["Netlist"],
        channels: list["ChannelInstance"],
        format_str: str,
    ) -> list["Netlist"]:
        """
        Replace single child netlists with cloned channel instances.
        """
        child_channels: dict[int, list["ChannelInstance"]] = defaultdict(list)
        for channel in channels:
            child_channels[channel.child_idx].append(channel)

        expanded = []
        expanded_schdocs = []
        self._channel_netlist_map.clear()
        self._compiled_to_source_sheet_idx.clear()

        for idx, (netlist, schdoc) in enumerate(zip(sheet_netlists, self._schdocs)):
            if idx in child_channels:
                for channel in child_channels[idx]:
                    new_idx = len(expanded)
                    self._channel_netlist_map[(idx, channel.instance_index)] = new_idx
                    self._compiled_to_source_sheet_idx[new_idx] = idx
                    cloned = self._clone_netlist_for_channel(
                        netlist,
                        channel,
                        format_str,
                    )
                    expanded.append(cloned)
                    expanded_schdocs.append(schdoc)
                    log.debug(
                        "Cloned child[%s] as channel %s -> new idx %s",
                        idx,
                        channel.room.channel_alpha,
                        new_idx,
                    )
            else:
                new_idx = len(expanded)
                self._compiled_to_source_sheet_idx[new_idx] = idx
                expanded.append(netlist)
                expanded_schdocs.append(schdoc)

        self._schdocs = expanded_schdocs
        self._channel_instances = channels

        log.debug(
            "Multi-channel expansion: %s sheets -> %s sheets",
            len(sheet_netlists),
            len(expanded),
        )
        return expanded

    def _clone_netlist_for_channel(
        self,
        source: "Netlist",
        channel: "ChannelInstance",
        format_str: str,
    ) -> "Netlist":
        """
        Clone a netlist with channel-annotated designators and net names.
        """
        from .altium_netlist_model import Net, Netlist, NetlistComponent, Terminal

        room = channel.room
        desig_map: dict[str, str] = {}
        for comp in source.components:
            desig_map[comp.designator] = apply_channel_pattern(
                format_str,
                room,
                comp.designator,
            )

        new_components = []
        for comp in source.components:
            new_components.append(
                NetlistComponent(
                    designator=desig_map[comp.designator],
                    value=comp.value,
                    footprint=comp.footprint,
                    library_ref=comp.library_ref,
                    description=comp.description,
                    parameters=comp.parameters.copy(),
                    component_kind=comp.component_kind,
                    exclude_from_bom=comp.exclude_from_bom,
                )
            )

        new_nets = []
        for net in source.nets:
            new_terminals = []
            for term in net.terminals:
                new_terminals.append(
                    Terminal(
                        designator=desig_map.get(term.designator, term.designator),
                        pin=term.pin,
                        pin_name=term.pin_name,
                        pin_type=term.pin_type,
                    )
                )

            new_endpoints = []
            for endpoint in net.endpoints:
                next_endpoint = replace(endpoint)
                if endpoint.designator:
                    next_endpoint.designator = desig_map.get(
                        endpoint.designator,
                        endpoint.designator,
                    )
                if next_endpoint.role == "pin":
                    next_endpoint.endpoint_id = (
                        f"pin:{next_endpoint.designator}:{next_endpoint.pin}"
                    )
                new_endpoints.append(next_endpoint)

            new_nets.append(
                Net(
                    name=self._annotate_net_name(
                        net,
                        room,
                        desig_map,
                        channel,
                        format_str,
                    ),
                    terminals=new_terminals,
                    graphical=net.graphical.copy(),
                    auto_named=net.auto_named,
                    source_sheets=list(net.source_sheets),
                    aliases=list(net.aliases),
                    endpoints=new_endpoints,
                )
            )

        return Netlist(nets=new_nets, components=new_components)

    def _annotate_endpoint_sheet_context(self, sheet_netlists: list["Netlist"]) -> None:
        """
        Stamp net endpoints with source/compiled sheet indexes after channel expansion.
        """
        for compiled_idx, netlist in enumerate(sheet_netlists):
            source_idx = self._source_sheet_index(compiled_idx)
            for net in netlist.nets:
                for endpoint in net.endpoints:
                    if endpoint.compiled_sheet_index is None:
                        endpoint.compiled_sheet_index = compiled_idx
                    if endpoint.sheet_index is None:
                        endpoint.sheet_index = source_idx

    @staticmethod
    def _dedupe_endpoints(endpoints: list) -> list:
        seen = set()
        result = []
        for endpoint in endpoints:
            key = (
                getattr(endpoint, "endpoint_id", ""),
                getattr(endpoint, "source_sheet", ""),
                getattr(endpoint, "compiled_sheet_index", None),
                getattr(endpoint, "sheet_index", None),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(endpoint)
        return result

    @classmethod
    def _collect_sheet_net_endpoints(cls, sheet_nets: list[tuple[int, "Net"]]) -> list:
        endpoints = [
            endpoint
            for _, net in sheet_nets
            for endpoint in getattr(net, "endpoints", [])
        ]
        return cls._dedupe_endpoints(endpoints)

    @classmethod
    def _collect_net_endpoints(cls, nets: list["Net"]) -> list:
        endpoints = [
            endpoint for net in nets for endpoint in getattr(net, "endpoints", [])
        ]
        return cls._dedupe_endpoints(endpoints)

    def _annotate_net_name(
        self,
        net: "Net",
        room: "RoomDetails",
        desig_map: dict[str, str],
        channel: "ChannelInstance | None" = None,
        format_str: str = "",
    ) -> str:
        """
        Annotate a net name for a channel instance.
        """
        if net.graphical.power_ports:
            return net.name

        if net.auto_named:
            match = re.match(r"^Net([A-Za-z]+\d+)_(.+)$", net.name)
            if match:
                old_desig = match.group(1)
                pin = match.group(2)
                return f"Net{desig_map.get(old_desig, old_desig)}_{pin}"
            return net.name

        if (
            channel
            and channel.repeat_value is not None
            and channel.repeat_entry_ports
            and net.graphical.ports
            and net.name.lower() in channel.repeat_entry_ports
        ):
            return f"{net.name}{channel.repeat_value}"

        base_name, diff_suffix = _strip_diff_pair_suffix(net.name)
        if format_str:
            annotated = apply_channel_pattern(format_str, room, base_name)
        else:
            annotated = base_name + room.channel_alpha
        return annotated + diff_suffix

    def _build_sheet_paths(self) -> None:
        """
        Build hierarchy paths for each sheet index.
        """
        from .altium_netlist_hierarchy import build_sheet_paths

        self._sheet_paths = build_sheet_paths(
            self._schdocs,
            channel_instances=self._channel_instances,
            channel_netlist_map=self._channel_netlist_map,
        )
        log.debug("Built hierarchy paths for %s sheets", len(self._sheet_paths))

    def _merge_components(self, sheet_netlists: list["Netlist"]) -> list[object]:
        """
        Merge components across sheets with Altium-compatible deduplication.
        """
        from .altium_prjpcb import NetIdentifierScope

        component_map = {}
        unannotated_components = []

        for netlist in sheet_netlists:
            for comp in netlist.components:
                if "?" in comp.designator:
                    unannotated_components.append(comp)
                else:
                    component_map[comp.designator] = comp

        scope = self._effective_scope
        if scope in (
            NetIdentifierScope.HIERARCHICAL,
            NetIdentifierScope.STRICT_HIERARCHICAL,
        ):
            for comp in component_map.values():
                if comp.value and comp.value.startswith("="):
                    comp.value = comp.value[1:]
            for comp in unannotated_components:
                if comp.value and comp.value.startswith("="):
                    comp.value = comp.value[1:]

        return list(component_map.values()) + unannotated_components

    def _classify_and_expand_nets(self, sheet_netlists: list["Netlist"]) -> dict:
        """
        Classify nets by type and expand buses/harnesses.
        """
        power_net_map, port_net_map, other_nets = self._classify_nets(sheet_netlists)

        log.debug(
            "Classified nets: %s power groups, %s port groups, %s other",
            len(power_net_map),
            len(port_net_map),
            len(other_nets),
        )

        self._merge_bridged_power_groups(power_net_map)

        log.debug("After power bridge merge: %s power groups", len(power_net_map))

        self._expand_bus_ports(port_net_map, other_nets)

        log.debug(
            "After bus port expansion: %s port groups, %s other",
            len(port_net_map),
            len(other_nets),
        )

        harness_keys = self._expand_harness_entries(port_net_map, other_nets)

        log.debug(
            "After harness entry expansion: %s port groups, %s other",
            len(port_net_map),
            len(other_nets),
        )

        harness_entry_names = set()
        for schdoc in self._schdocs:
            for sym in schdoc.get_sheet_symbols():
                for entry in sym.entries:
                    harness_type = getattr(entry, "harness_type", "")
                    if harness_type and entry.name:
                        harness_entry_names.add(entry.name)

        return {
            "power_net_map": power_net_map,
            "port_net_map": port_net_map,
            "other_nets": other_nets,
            "harness_keys": harness_keys,
            "harness_entry_names": harness_entry_names,
        }

    def _classify_nets(self, sheet_netlists: list["Netlist"]) -> tuple:
        """
        Classify nets by power port, port, or other.
        """
        from .altium_prjpcb import NetIdentifierScope

        power_net_map = defaultdict(list)
        port_net_map = defaultdict(list)
        other_nets = []

        scope = self._effective_scope
        for sheet_idx, netlist in enumerate(sheet_netlists):
            for net in netlist.nets:
                classified = False
                has_power_port = bool(net.graphical.power_ports)
                has_port = bool(net.graphical.ports)

                if has_power_port:
                    pp_is_local = has_port and scope in (
                        NetIdentifierScope.HIERARCHICAL,
                        NetIdentifierScope.STRICT_HIERARCHICAL,
                    )
                    if pp_is_local:
                        port_names = self._find_all_port_names_for_net(sheet_idx, net)
                        if port_names:
                            for port_name in port_names:
                                port_net_map[port_name].append((sheet_idx, net))
                            classified = True
                    else:
                        power_name = self._get_power_port_name_for_net(sheet_idx, net)
                        if power_name:
                            power_net_map[power_name].append((sheet_idx, net))
                            classified = True

                if has_port and not classified:
                    port_names = self._find_all_port_names_for_net(sheet_idx, net)
                    if port_names:
                        for port_name in port_names:
                            port_net_map[port_name].append((sheet_idx, net))
                        classified = True

                if not classified:
                    other_nets.append((sheet_idx, net))

        return power_net_map, port_net_map, other_nets

    def _merge_bridged_power_groups(self, power_net_map: dict) -> None:
        """
        Merge power groups bridged by shared power port names.
        """
        from .altium_netlist_model import UnionFind

        if len(power_net_map) < 2:
            return

        pp_name_to_keys = defaultdict(set)
        for key, entries in power_net_map.items():
            for sheet_idx, net in entries:
                for power_name in self._get_all_power_port_names_for_net(
                    sheet_idx, net
                ):
                    pp_name_to_keys[power_name].add(key)

        uf = UnionFind()
        for key in power_net_map:
            uf.find(key)

        for keys in pp_name_to_keys.values():
            keys_list = list(keys)
            for idx in range(1, len(keys_list)):
                uf.union(keys_list[0], keys_list[idx])

        _bridge_power_keys_by_net_labels(
            uf,
            power_net_map,
            self._options.higher_level_names_take_priority,
        )

        groups = defaultdict(list)
        for key in list(power_net_map.keys()):
            groups[uf.find(key)].append(key)

        for members in groups.values():
            if len(members) <= 1:
                continue
            canonical = _pick_canonical_power_key(members, power_net_map)
            for member in members:
                if member != canonical:
                    power_net_map[canonical].extend(power_net_map.pop(member))
            log.debug(
                "Merged power groups %s -> %s",
                [member for member in members if member != canonical],
                canonical,
            )

    def _expand_bus_ports(self, port_net_map: dict, other_nets: list) -> None:
        """
        Expand bus-width ports into the port map.
        """
        for sheet_idx, schdoc in enumerate(self._schdocs):
            for port in schdoc.get_ports():
                if not port.name:
                    continue
                members = self._parse_bus_range(port.name)
                if not members:
                    continue

                port_uid = port.unique_id
                already_handled = False
                if port_uid:
                    for sheet_nets in port_net_map.values():
                        for existing_sheet_idx, net in sheet_nets:
                            if (
                                existing_sheet_idx == sheet_idx
                                and port_uid in net.graphical.ports
                            ):
                                already_handled = True
                                break
                        if already_handled:
                            break
                if already_handled:
                    continue

                for member_name in members:
                    for idx in range(len(other_nets) - 1, -1, -1):
                        existing_sheet_idx, net = other_nets[idx]
                        if existing_sheet_idx == sheet_idx and net.name == member_name:
                            port_net_map[member_name].append((sheet_idx, net))
                            other_nets.pop(idx)
                            break

    def _expand_harness_entries(self, port_net_map: dict, other_nets: list) -> set[str]:
        """
        Expand harness entries into the port map.
        """
        harness_keys = set()
        for sheet_idx, schdoc in enumerate(self._schdocs):
            if not schdoc.harness_connectors:
                continue

            source_sheet = schdoc.filepath.name if schdoc.filepath else ""
            source_sheet_index = self._source_sheet_index(sheet_idx)
            wire_endpoint_map = _build_wire_endpoint_map(schdoc)
            port_location_map = _build_port_location_map(schdoc)

            for connector in schdoc.harness_connectors:
                harness_port_name = self._find_harness_port_name(
                    connector,
                    schdoc.signal_harnesses,
                    port_location_map,
                )

                for entry in connector.entries:
                    entry_y = connector.location.y - entry.distance_from_top * 10
                    entry_x_left = connector.location.x
                    entry_x_right = connector.location.x + connector.xsize

                    wire_uid = wire_endpoint_map.get((entry_x_left, entry_y))
                    if not wire_uid:
                        wire_uid = wire_endpoint_map.get((entry_x_right, entry_y))
                    if not wire_uid:
                        continue

                    merge_key = (
                        f"{harness_port_name}.{entry.name}"
                        if harness_port_name
                        else entry.name
                    )

                    _find_or_create_net_for_wire(
                        wire_uid,
                        sheet_idx,
                        merge_key,
                        port_net_map,
                        other_nets,
                        harness_keys,
                        str(getattr(entry, "unique_id", "") or ""),
                        str(getattr(entry, "name", "") or ""),
                        source_sheet,
                        source_sheet_index,
                    )

        return harness_keys

    def _merge_port_groups(self, port_net_map: dict) -> None:
        """
        Merge port groups by shared net identity and netlabel name.
        """
        bridge_groups = {}
        for key in list(port_net_map.keys()):
            if key.startswith("__bridge_"):
                bridge_groups[key] = port_net_map.pop(key)

        if len(port_net_map) > 1:
            merge_count = _merge_groups_by_shared_key(
                port_net_map,
                lambda sheet_idx, net: id(net),
            )
            if merge_count:
                log.debug(
                    "Fan-out convergence merged port groups: %s groups remaining",
                    len(port_net_map),
                )

        if len(port_net_map) > 1:

            def get_netlabel_name(sheet_idx: int, net: "Net") -> str | None:
                if not net.auto_named and net.graphical.labels:
                    return net.name
                return None

            merge_count = _merge_groups_by_shared_key(port_net_map, get_netlabel_name)
            if merge_count:
                log.debug(
                    "Netlabel-based port group merge: %s groups remaining",
                    len(port_net_map),
                )

        port_net_map.update(bridge_groups)

    def _absorb_matching_other_nets(
        self,
        power_net_map: dict,
        port_net_map: dict,
        other_nets: list,
        ports_are_global: bool,
    ) -> None:
        """
        Move other nets with matching power/port names into their groups.
        """
        remaining = []
        for sheet_idx, net in other_nets:
            if net.name in power_net_map:
                absorb = True
                if not net.graphical.power_ports:
                    pp_key = net.name
                    for _, power_net in power_net_map[pp_key]:
                        if (
                            power_net.graphical.labels
                            and not power_net.auto_named
                            and power_net.name != pp_key
                        ):
                            absorb = False
                            break
                if absorb:
                    power_net_map[net.name].append((sheet_idx, net))
                else:
                    remaining.append((sheet_idx, net))
            elif ports_are_global and net.name in port_net_map:
                has_real_name_source = bool(net.graphical.ports or net.graphical.labels)
                if has_real_name_source:
                    port_net_map[net.name].append((sheet_idx, net))
                else:
                    remaining.append((sheet_idx, net))
            else:
                remaining.append((sheet_idx, net))

        other_nets.clear()
        other_nets.extend(remaining)

    def _resolve_child_indices(
        self,
        child_filename_lower: str,
        sheet_sym_info: "SchSheetSymbolInfo",
        filename_to_idx: dict[str, int],
    ) -> list[int]:
        """
        Resolve a sheet symbol to child netlist index(es).
        """
        from .altium_netlist_hierarchy import resolve_child_indices

        return resolve_child_indices(
            child_filename_lower,
            sheet_sym_info,
            filename_to_idx,
            channel_instances=self._channel_instances,
            channel_netlist_map=self._channel_netlist_map,
        )

    def _build_hierarchy_links(self) -> list[SheetEntryLink]:
        """
        Build parent-entry to child-port links.
        """
        from .altium_netlist_model import HierarchyPath

        filename_to_idx = {}
        for idx, schdoc in enumerate(self._schdocs):
            if schdoc.filepath:
                filename_to_idx[schdoc.filepath.name.lower()] = idx

        child_idx_to_channel = {}
        for channel in self._channel_instances:
            new_idx = self._channel_netlist_map.get(
                (channel.child_idx, channel.instance_index)
            )
            if new_idx is not None:
                child_idx_to_channel[new_idx] = channel

        links = []
        for parent_idx, schdoc in enumerate(self._schdocs):
            for sheet_sym_info in schdoc.get_sheet_symbols():
                file_name_obj = sheet_sym_info.record.file_name
                if file_name_obj is None:
                    continue
                child_filename = getattr(file_name_obj, "text", "") or ""
                if not child_filename:
                    continue

                child_indices = self._resolve_child_indices(
                    child_filename.lower(),
                    sheet_sym_info,
                    filename_to_idx,
                )
                if not child_indices:
                    self._hierarchy_unresolved.append(
                        {
                            "kind": "missing_child_sheet",
                            "parent_sheet_index": self._source_sheet_index(parent_idx),
                            "parent_compiled_sheet_index": parent_idx,
                            "sheet_symbol_id": sheet_sym_info.unique_id,
                            "child_filename": child_filename,
                        }
                    )
                    log.debug(
                        "Sheet symbol references '%s' but no matching SchDoc found in project",
                        child_filename,
                    )
                    continue

                for child_idx in child_indices:
                    child_schdoc = self._schdocs[child_idx]
                    channel = child_idx_to_channel.get(child_idx)
                    path = HierarchyPath().move_down(
                        sheet_symbol_uid=sheet_sym_info.unique_id,
                        child_filename=child_filename.lower(),
                        designator=sheet_sym_info.designator or "",
                        channel_name=(
                            channel.room.room_name if channel and channel.room else ""
                        ),
                        channel_index=channel.instance_index if channel else -1,
                        repeat_value=channel.repeat_value if channel else None,
                    )

                    self._create_entry_links(
                        links,
                        sheet_sym_info,
                        parent_idx,
                        child_idx,
                        child_schdoc,
                        channel,
                        path,
                    )

        return links

    def _create_entry_links(
        self,
        links: list[SheetEntryLink],
        sheet_sym_info: "SchSheetSymbolInfo",
        parent_idx: int,
        child_idx: int,
        child_schdoc: "AltiumSchDoc",
        channel: Optional["ChannelInstance"],
        hierarchy_path: "HierarchyPath | None" = None,
    ) -> None:
        """
        Match sheet symbol entries to child ports and append links.
        """
        from .altium_netlist_model import HierarchyPath

        if hierarchy_path is None:
            hierarchy_path = HierarchyPath()

        child_port_names = {
            port.name.lower() for port in child_schdoc.get_ports() if port.name
        }
        child_harness_entries = _build_child_harness_entry_map(self, child_schdoc)

        for entry in sheet_sym_info.entries:
            entry_name = entry.display_name or ""
            if not entry_name:
                continue

            harness_type = getattr(entry, "harness_type", "")
            if harness_type:
                harness_entries = child_harness_entries.get(entry_name.lower(), [])
                if harness_entries:
                    for harness_entry in harness_entries:
                        harness_entry_name = str(harness_entry.get("name", "")).strip()
                        if not harness_entry_name:
                            continue
                        child_object_id = str(
                            harness_entry.get("object_id", "")
                        ).strip()
                        links.append(
                            SheetEntryLink(
                                entry_name=harness_entry_name,
                                parent_sheet_idx=parent_idx,
                                child_sheet_idx=child_idx,
                                sheet_sym_uid=sheet_sym_info.unique_id,
                                match_by_name=True,
                                parent_entry_name=entry_name,
                                child_object_ids=(
                                    (child_object_id,) if child_object_id else ()
                                ),
                                hierarchy_path=hierarchy_path,
                            )
                        )
                    log.debug(
                        "Harness bridge: '%s' parent[%s] -> child[%s] (%s signals)",
                        entry_name,
                        parent_idx,
                        child_idx,
                        len(harness_entries),
                    )
                continue

            inner_port = _parse_entry_repeat(entry_name)
            match_name = inner_port if inner_port else entry_name
            if match_name.lower() not in child_port_names:
                self._hierarchy_unresolved.append(
                    {
                        "kind": "unmatched_child_port",
                        "parent_sheet_index": self._source_sheet_index(parent_idx),
                        "parent_compiled_sheet_index": parent_idx,
                        "child_sheet_index": self._source_sheet_index(child_idx),
                        "child_compiled_sheet_index": child_idx,
                        "sheet_symbol_id": sheet_sym_info.unique_id,
                        "entry_name": entry_name,
                        "port_name": match_name,
                    }
                )
                continue

            if inner_port and channel and channel.repeat_value is not None:
                links.append(
                    SheetEntryLink(
                        entry_name=f"{inner_port}{channel.repeat_value}",
                        parent_sheet_idx=parent_idx,
                        child_sheet_idx=child_idx,
                        sheet_sym_uid="",
                        port_name=inner_port,
                        hierarchy_path=hierarchy_path,
                    )
                )
                log.debug(
                    "Repeat bus link: '%s%s' parent[%s] -> child[%s]",
                    inner_port,
                    channel.repeat_value,
                    parent_idx,
                    child_idx,
                )
            else:
                links.append(
                    SheetEntryLink(
                        entry_name=entry_name,
                        parent_sheet_idx=parent_idx,
                        child_sheet_idx=child_idx,
                        sheet_sym_uid=sheet_sym_info.unique_id,
                        hierarchy_path=hierarchy_path,
                    )
                )
                log.debug(
                    "Hierarchy link: '%s' parent[%s] -> child[%s]",
                    entry_name,
                    parent_idx,
                    child_idx,
                )

    def _bridge_hierarchical_nets(
        self,
        classified: dict,
        sheet_netlists: list["Netlist"],
    ) -> None:
        """
        Bridge parent entry nets with child port nets via hierarchy links.
        """
        from .altium_netlist_model import UnionFind
        from .altium_prjpcb import NetIdentifierScope

        scope = self._effective_scope
        if scope not in (
            NetIdentifierScope.HIERARCHICAL,
            NetIdentifierScope.STRICT_HIERARCHICAL,
        ):
            self._hierarchy_links = []
            return

        links = self._build_hierarchy_links()
        self._hierarchy_links = links
        if not links:
            return

        port_net_map = classified["port_net_map"]
        other_nets = classified["other_nets"]

        uf = UnionFind()
        net_registry = {}
        bridged_count = self._process_hierarchy_links(
            links,
            uf,
            net_registry,
            port_net_map,
            other_nets,
            sheet_netlists,
        )
        bridged_count += self._bridge_orphaned_harness_children(
            uf,
            net_registry,
            port_net_map,
            sheet_netlists,
        )

        if not bridged_count:
            return

        log.debug("Bridged %s hierarchical net pairs", bridged_count)
        self._update_maps_after_bridging(uf, net_registry, classified)

    def build_schematic_hierarchy_metadata(self) -> dict:
        """
        Export compiler-resolved schematic hierarchy metadata.
        """
        from .altium_netlist_model import _hierarchy_path_to_json

        path_id_by_value = {}
        hierarchy_paths = []
        for compiled_idx, path in sorted(self._sheet_paths.items()):
            if path.is_empty:
                continue
            if path not in path_id_by_value:
                path_id = f"path-{len(path_id_by_value) + 1:04d}"
                path_id_by_value[path] = path_id
                hierarchy_paths.append(
                    {
                        "id": path_id,
                        "source_sheet_index": self._source_sheet_index(compiled_idx),
                        "compiled_sheet_index": compiled_idx,
                        "levels": _hierarchy_path_to_json(path),
                    }
                )

        channels = []
        for channel in self._channel_instances:
            compiled_idx = self._channel_netlist_map.get(
                (channel.child_idx, channel.instance_index)
            )
            path = self._sheet_paths.get(compiled_idx)
            room = channel.room
            channels.append(
                {
                    "id": f"channel-{len(channels) + 1:04d}",
                    "sheet_symbol_id": channel.sheet_sym_unique_id,
                    "parent_sheet_index": channel.parent_idx,
                    "child_sheet_index": channel.child_idx,
                    "compiled_child_sheet_index": compiled_idx,
                    "instance_index": channel.instance_index,
                    "channel_name": room.room_name if room else "",
                    "channel_prefix": room.channel_prefix if room else "",
                    "channel_index": room.channel_index if room else "",
                    "channel_alpha": room.channel_alpha if room else "",
                    "repeat_value": channel.repeat_value,
                    "repeat_entry_ports": sorted(channel.repeat_entry_ports),
                    "hierarchy_path_id": path_id_by_value.get(path, "") if path else "",
                }
            )

        return {
            "schema": "altium_monkey.schematic_hierarchy.a1",
            "requested_scope": self._options.net_identifier_scope.name,
            "effective_scope": (
                self._effective_scope.name if self._effective_scope is not None else ""
            ),
            "documents": self._build_hierarchy_documents(),
            "sheet_symbols": self._build_hierarchy_sheet_symbols(),
            "hierarchy_paths": hierarchy_paths,
            "channels": channels,
            "links": self._build_hierarchy_link_json(path_id_by_value),
            "harness_bundle_links": self._build_harness_bundle_link_json(),
            "unresolved": list(self._hierarchy_unresolved),
        }

    def _source_sheet_index(self, compiled_sheet_idx: int | None) -> int | None:
        if compiled_sheet_idx is None:
            return None
        return self._compiled_to_source_sheet_idx.get(compiled_sheet_idx, compiled_sheet_idx)

    def _build_hierarchy_documents(self) -> list[dict]:
        documents = []
        for idx, schdoc in enumerate(self._source_schdocs):
            filepath = schdoc.filepath
            documents.append(
                {
                    "sheet_index": idx,
                    "filename": filepath.name if filepath else f"sheet{idx}",
                    "path": str(filepath) if filepath else "",
                    "is_top_level": not any(
                        symbol.child_filename.lower()
                        == (filepath.name.lower() if filepath else "")
                        for parent in self._source_schdocs
                        for symbol in parent.get_sheet_symbols()
                    ),
                    "metadata": {},
                }
            )
        return documents

    def _build_hierarchy_sheet_symbols(self) -> list[dict]:
        symbols = []
        for parent_idx, schdoc in enumerate(self._source_schdocs):
            for sheet_sym_info in schdoc.get_sheet_symbols():
                child_filename = sheet_sym_info.child_filename
                child_indices = [
                    idx
                    for idx, child in enumerate(self._source_schdocs)
                    if child.filepath and child.filepath.name.lower() == child_filename.lower()
                ]
                symbols.append(
                    {
                        "id": sheet_sym_info.unique_id,
                        "parent_sheet_index": parent_idx,
                        "designator": sheet_sym_info.designator,
                        "child_filename": child_filename,
                        "child_sheet_indices": child_indices,
                        "repeat": self._parse_sheet_symbol_repeat(
                            sheet_sym_info.designator
                        ),
                        "entries": [
                            {
                                "id": getattr(entry, "unique_id", "") or "",
                                "name": entry.display_name or "",
                                "graphical_id": self._sheet_entry_graphical_id(
                                    sheet_sym_info.unique_id,
                                    entry.display_name or "",
                                ),
                                "harness_type": getattr(entry, "harness_type", "") or "",
                                "metadata": {},
                            }
                            for entry in sheet_sym_info.entries
                        ],
                        "metadata": {},
                    }
                )
        return symbols

    def _build_hierarchy_link_json(self, path_id_by_value: dict) -> list[dict]:
        links = []
        for link in self._hierarchy_links:
            child_port_name = link.port_name or link.entry_name
            parent_entry_name = link.parent_entry_name or link.entry_name
            child_schdoc = self._schdocs[link.child_sheet_idx]
            child_port_ids = [
                port.unique_id
                for port in child_schdoc.get_ports()
                if port.name.lower() == child_port_name.lower()
            ]
            child_object_ids = []
            child_object_seen = set()
            for object_id in [*child_port_ids, *link.child_object_ids]:
                clean_id = str(object_id or "").strip()
                if clean_id and clean_id not in child_object_seen:
                    child_object_seen.add(clean_id)
                    child_object_ids.append(clean_id)
            hierarchy_path_id = path_id_by_value.get(link.hierarchy_path, "")
            channel = self._channel_for_compiled_child(link.child_sheet_idx)
            links.append(
                {
                    "id": f"hier-link-{len(links) + 1:04d}",
                    "kind": "sheet_entry_to_port",
                    "parent": {
                        "sheet_index": self._source_sheet_index(link.parent_sheet_idx),
                        "compiled_sheet_index": link.parent_sheet_idx,
                        "object_kind": "sheet_entry",
                        "object_id": self._sheet_entry_graphical_id(
                            link.sheet_sym_uid,
                            parent_entry_name,
                        ),
                        "graphical_id": self._sheet_entry_graphical_id(
                            link.sheet_sym_uid,
                            parent_entry_name,
                        ),
                        "sheet_symbol_id": link.sheet_sym_uid,
                        "name": link.entry_name,
                        "sheet_entry_name": parent_entry_name,
                    },
                    "child": {
                        "sheet_index": self._source_sheet_index(link.child_sheet_idx),
                        "compiled_sheet_index": link.child_sheet_idx,
                        "object_kind": (
                            "harness_entry"
                            if link.match_by_name and link.child_object_ids
                            else "port"
                        ),
                        "object_ids": child_object_ids,
                        "name": child_port_name,
                    },
                    "match_kind": "name" if not link.match_by_name else "harness_name",
                    "hierarchy_path_id": hierarchy_path_id,
                    "channel_index": channel.instance_index if channel else None,
                    "channel_name": channel.room.room_name
                    if channel and channel.room
                    else "",
                    "repeat_value": channel.repeat_value if channel else None,
                    "metadata": {},
                }
            )
        return links

    def _build_harness_bundle_endpoint_map(self) -> dict[str, list[dict]]:
        endpoint_map: dict[str, list[dict]] = defaultdict(list)
        for compiled_idx, schdoc in enumerate(self._schdocs):
            if not getattr(schdoc, "harness_connectors", None):
                continue
            port_location_map = _build_port_location_map(schdoc)
            for connector in schdoc.harness_connectors:
                bundle_info = self._find_harness_bundle_info(
                    connector,
                    getattr(schdoc, "signal_harnesses", None),
                    port_location_map,
                )
                port_name = bundle_info.get("port_name") or ""
                if not port_name:
                    continue
                port_ids = [
                    port.unique_id
                    for port in schdoc.get_ports()
                    if port.name and port.name.lower() == port_name.lower()
                ]
                connector_id = str(getattr(connector, "unique_id", "") or "").strip()
                signal_harness_ids = bundle_info.get("signal_harness_ids") or []
                object_ids = self._unique_nonempty(
                    [*port_ids, *signal_harness_ids, connector_id]
                )
                endpoint_map[port_name.lower()].append(
                    {
                        "name": port_name,
                        "sheet_index": self._source_sheet_index(compiled_idx),
                        "compiled_sheet_index": compiled_idx,
                        "port_ids": self._unique_nonempty(port_ids),
                        "signal_harness_ids": self._unique_nonempty(
                            signal_harness_ids
                        ),
                        "harness_connector_ids": self._unique_nonempty(
                            [connector_id]
                        ),
                        "object_ids": object_ids,
                        "member_names": [
                            entry.name
                            for entry in getattr(connector, "entries", [])
                            if getattr(entry, "name", "")
                        ],
                    }
                )
        return endpoint_map

    def _build_harness_bundle_link_json(self) -> list[dict]:
        endpoints_by_name = self._build_harness_bundle_endpoint_map()
        links: list[dict] = []

        def append_link(
            *,
            name: str,
            topology: str,
            parent_endpoint: dict,
            child_endpoint: dict,
            parent: dict | None = None,
        ) -> None:
            parent_ids = self._unique_nonempty(
                parent.get("object_ids", []) if parent else parent_endpoint["port_ids"]
            )
            child_ids = self._unique_nonempty(child_endpoint["port_ids"])
            bundle_object_ids = self._unique_nonempty(
                [
                    *parent_ids,
                    *child_ids,
                    *(
                        parent_endpoint.get("object_ids", [])
                        if parent_endpoint
                        else []
                    ),
                    *child_endpoint.get("object_ids", []),
                ]
            )
            links.append(
                {
                    "id": f"harness-bundle-link-{len(links) + 1:04d}",
                    "kind": "harness_bundle",
                    "topology": topology,
                    "name": name,
                    "parent": parent
                    or {
                        "sheet_index": parent_endpoint["sheet_index"],
                        "compiled_sheet_index": parent_endpoint[
                            "compiled_sheet_index"
                        ],
                        "object_kind": "harness_port",
                        "object_ids": parent_ids,
                        "name": parent_endpoint["name"],
                    },
                    "child": {
                        "sheet_index": child_endpoint["sheet_index"],
                        "compiled_sheet_index": child_endpoint[
                            "compiled_sheet_index"
                        ],
                        "object_kind": "harness_port",
                        "object_ids": child_ids,
                        "name": child_endpoint["name"],
                    },
                    "bundle": {
                        "object_ids": bundle_object_ids,
                        "parent_object_ids": parent_endpoint.get("object_ids", []),
                        "child_object_ids": child_endpoint.get("object_ids", []),
                        "member_names": self._unique_nonempty(
                            [
                                *parent_endpoint.get("member_names", []),
                                *child_endpoint.get("member_names", []),
                            ]
                        ),
                        "signal_harness_ids": self._unique_nonempty(
                            [
                                *parent_endpoint.get("signal_harness_ids", []),
                                *child_endpoint.get("signal_harness_ids", []),
                            ]
                        ),
                        "harness_connector_ids": self._unique_nonempty(
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

        hierarchical_keys = set()
        for link in self._hierarchy_links:
            parent_entry_name = (link.parent_entry_name or "").strip()
            if not parent_entry_name:
                continue
            child_endpoint = next(
                (
                    endpoint
                    for endpoint in endpoints_by_name.get(parent_entry_name.lower(), [])
                    if endpoint["compiled_sheet_index"] == link.child_sheet_idx
                ),
                None,
            )
            if not child_endpoint:
                continue
            parent_graphical_id = self._sheet_entry_graphical_id(
                link.sheet_sym_uid,
                parent_entry_name,
            )
            parent_endpoint = {
                "name": parent_entry_name,
                "sheet_index": self._source_sheet_index(link.parent_sheet_idx),
                "compiled_sheet_index": link.parent_sheet_idx,
                "port_ids": [parent_graphical_id],
                "object_ids": [parent_graphical_id],
                "signal_harness_ids": [],
                "harness_connector_ids": [],
            }
            key = (
                link.parent_sheet_idx,
                link.child_sheet_idx,
                link.sheet_sym_uid,
                parent_entry_name.lower(),
            )
            if key in hierarchical_keys:
                continue
            hierarchical_keys.add(key)
            append_link(
                name=parent_entry_name,
                topology="hierarchical",
                parent_endpoint=parent_endpoint,
                child_endpoint=child_endpoint,
                parent={
                    "sheet_index": self._source_sheet_index(link.parent_sheet_idx),
                    "compiled_sheet_index": link.parent_sheet_idx,
                    "object_kind": "sheet_entry",
                    "object_id": parent_graphical_id,
                    "graphical_id": parent_graphical_id,
                    "object_ids": [parent_graphical_id],
                    "sheet_symbol_id": link.sheet_sym_uid,
                    "name": parent_entry_name,
                },
            )

        from .altium_prjpcb import NetIdentifierScope

        if self._effective_scope in (
            NetIdentifierScope.FLAT,
            NetIdentifierScope.GLOBAL,
        ):
            for endpoint_name in sorted(endpoints_by_name):
                endpoints = sorted(
                    endpoints_by_name[endpoint_name],
                    key=lambda endpoint: (
                        endpoint["compiled_sheet_index"],
                        endpoint["name"],
                    ),
                )
                for idx in range(len(endpoints) - 1):
                    append_link(
                        name=endpoints[idx]["name"],
                        topology="flat",
                        parent_endpoint=endpoints[idx],
                        child_endpoint=endpoints[idx + 1],
                    )

        return links

    def _channel_for_compiled_child(self, compiled_child_idx: int) -> "ChannelInstance | None":
        for channel in self._channel_instances:
            if (
                self._channel_netlist_map.get((channel.child_idx, channel.instance_index))
                == compiled_child_idx
            ):
                return channel
        return None

    @staticmethod
    def _sheet_entry_graphical_id(sheet_symbol_uid: str, entry_name: str) -> str:
        if not sheet_symbol_uid:
            return entry_name
        return f"{sheet_symbol_uid}_{entry_name}"

    @staticmethod
    def _unique_nonempty(values: list[str] | tuple[str, ...]) -> list[str]:
        ids = []
        seen = set()
        for value in values:
            clean = str(value or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                ids.append(clean)
        return ids

    @staticmethod
    def _parse_sheet_symbol_repeat(designator: str) -> dict:
        repeat = {
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

    def _process_hierarchy_links(
        self,
        links: list[SheetEntryLink],
        uf: "UnionFind[int]",
        net_registry: dict[int, tuple[int, "Net"]],
        port_net_map: dict[str, list[tuple[int, "Net"]]],
        other_nets: list[tuple[int, "Net"]],
        sheet_netlists: list["Netlist"],
    ) -> int:
        """
        Iterate hierarchy links and union parent/child nets.
        """
        bridged_count = 0
        self._orphan_children = defaultdict(list)

        for link in links:
            if link.match_by_name:
                parent_net = self._find_harness_parent_net(
                    link.parent_sheet_idx,
                    link.entry_name,
                    port_net_map,
                    other_nets,
                    sheet_netlists[link.parent_sheet_idx],
                )
                child_net = self._find_net_by_name(
                    link.child_sheet_idx,
                    link.entry_name,
                    sheet_netlists[link.child_sheet_idx],
                )
            else:
                parent_net = self._find_net_with_sheet_entry(
                    link.parent_sheet_idx,
                    link.entry_name,
                    sheet_netlists[link.parent_sheet_idx],
                    sheet_sym_uid=link.sheet_sym_uid,
                )
                child_port_name = link.port_name or link.entry_name
                child_net = self._find_net_with_port(
                    link.child_sheet_idx,
                    child_port_name,
                    sheet_netlists[link.child_sheet_idx],
                )

            if child_net is None:
                debug_name = link.port_name or link.entry_name
                log.debug(
                    "No child net found for port '%s' on sheet[%s]",
                    debug_name,
                    link.child_sheet_idx,
                )
                continue

            if parent_net is None:
                if link.match_by_name:
                    self._orphan_children[link.entry_name].append(
                        (link.child_sheet_idx, child_net)
                    )
                else:
                    log.debug(
                        "No parent net found for entry '%s' on sheet[%s]",
                        link.entry_name,
                        link.parent_sheet_idx,
                    )
                continue

            parent_id = id(parent_net)
            child_id = id(child_net)
            net_registry[parent_id] = (link.parent_sheet_idx, parent_net)
            net_registry[child_id] = (link.child_sheet_idx, child_net)
            uf.find(parent_id)
            uf.find(child_id)
            uf.union(parent_id, child_id)
            bridged_count += 1

        return bridged_count

    def _bridge_orphaned_harness_children(
        self,
        uf: "UnionFind[int]",
        net_registry: dict[int, tuple[int, "Net"]],
        port_net_map: dict[str, list[tuple[int, "Net"]]],
        sheet_netlists: list["Netlist"],
    ) -> int:
        """
        Bridge orphaned harness children directly.
        """
        bridged_count = 0

        for entry_name, children in self._orphan_children.items():
            children_with_terminals = [
                (sheet_idx, net)
                for sheet_idx, net in children
                if len(net.terminals) > 0
            ]

            if len(children_with_terminals) >= 2:
                effective_children = children
            else:
                effective_children = children_with_terminals

            if len(effective_children) < 2:
                if effective_children:
                    sheet_idx, net = effective_children[0]
                    child_id = id(net)
                    if child_id not in net_registry:
                        net_registry[child_id] = (sheet_idx, net)
                        uf.find(child_id)
                        if entry_name in port_net_map:
                            for parent_sheet_idx, parent_net in port_net_map[
                                entry_name
                            ]:
                                parent_id = id(parent_net)
                                net_registry[parent_id] = (parent_sheet_idx, parent_net)
                                uf.find(parent_id)
                                uf.union(parent_id, child_id)
                                bridged_count += 1
                                break
                continue

            first_id = id(effective_children[0][1])
            net_registry[first_id] = effective_children[0]
            uf.find(first_id)
            for sheet_idx, net in effective_children[1:]:
                child_id = id(net)
                net_registry[child_id] = (sheet_idx, net)
                uf.find(child_id)
                uf.union(first_id, child_id)
                bridged_count += 1

            if entry_name in port_net_map:
                for parent_sheet_idx, parent_net in port_net_map[entry_name]:
                    parent_id = id(parent_net)
                    net_registry[parent_id] = (parent_sheet_idx, parent_net)
                    uf.find(parent_id)
                    uf.union(first_id, parent_id)
                    bridged_count += 1

        del self._orphan_children
        return bridged_count

    def _update_maps_after_bridging(
        self,
        uf: "UnionFind[int]",
        net_registry: dict[int, tuple[int, "Net"]],
        classified: dict,
    ) -> None:
        """
        Build connected components from union-find and update classified maps.
        """
        port_net_map = classified["port_net_map"]
        other_nets = classified["other_nets"]
        power_net_map = classified["power_net_map"]

        components = defaultdict(list)
        for net_id, (sheet_idx, net) in net_registry.items():
            components[uf.find(net_id)].append((sheet_idx, net))

        bridged_net_ids = set(net_registry.keys())
        other_nets[:] = [
            (sheet_idx, net)
            for sheet_idx, net in other_nets
            if id(net) not in bridged_net_ids
        ]

        original_key_for_net = _remove_bridged_from_map(port_net_map, bridged_net_ids)
        power_names_for_net = _remove_bridged_from_map(power_net_map, bridged_net_ids)

        _reinsert_bridge_groups(
            components,
            power_net_map,
            port_net_map,
            power_names_for_net,
            original_key_for_net,
        )

    def _find_net_with_sheet_entry(
        self,
        sheet_idx: int,
        entry_name: str,
        sheet_netlist: "Netlist",
        sheet_sym_uid: str = "",
    ) -> "Net | None":
        """
        Find the net on a sheet that contains a matching sheet entry.
        """
        if sheet_sym_uid:
            target = f"{sheet_sym_uid}_{entry_name}"
            for net in sheet_netlist.nets:
                if target in net.graphical.sheet_entries:
                    return net

        target_suffix = f"_{entry_name}"
        for net in sheet_netlist.nets:
            for sheet_entry_id in net.graphical.sheet_entries:
                if sheet_entry_id.endswith(target_suffix):
                    return net

        for net in sheet_netlist.nets:
            if net.name.lower() == entry_name.lower():
                return net

        return None

    def _find_net_with_port(
        self,
        sheet_idx: int,
        port_name: str,
        sheet_netlist: "Netlist",
    ) -> "Net | None":
        """
        Find the net on a sheet that contains a matching port.
        """
        schdoc = self._schdocs[sheet_idx]
        target_port_uids = set()
        for port in schdoc.get_ports():
            if port.name and port.name.lower() == port_name.lower() and port.unique_id:
                target_port_uids.add(port.unique_id)

        if not target_port_uids:
            return None

        for net in sheet_netlist.nets:
            for port_uid in net.graphical.ports:
                if port_uid in target_port_uids:
                    return net
        return None

    @staticmethod
    def _find_net_by_name(
        sheet_idx: int,
        net_name: str,
        sheet_netlist: "Netlist",
    ) -> "Net | None":
        """
        Find a net on a sheet by name.
        """
        for net in sheet_netlist.nets:
            if net.name.lower() == net_name.lower():
                return net
        return None

    @staticmethod
    def _find_harness_parent_net(
        sheet_idx: int,
        entry_name: str,
        port_net_map: dict[str, list[tuple[int, "Net"]]],
        other_nets: list[tuple[int, "Net"]],
        sheet_netlist: "Netlist",
    ) -> "Net | None":
        """
        Find the parent net for a harness signal bridge link.
        """
        if entry_name in port_net_map:
            for existing_sheet_idx, net in port_net_map[entry_name]:
                if existing_sheet_idx == sheet_idx:
                    return net

        for key, entries in port_net_map.items():
            if key.endswith(f".{entry_name}"):
                for existing_sheet_idx, net in entries:
                    if existing_sheet_idx == sheet_idx:
                        return net

        for existing_sheet_idx, net in other_nets:
            if (
                existing_sheet_idx == sheet_idx
                and net.name.lower() == entry_name.lower()
            ):
                return net

        for net in sheet_netlist.nets:
            if net.name.lower() == entry_name.lower():
                return net
        return None

    def _merge_nets_by_scope(self, classified: dict) -> list["Net"]:
        """
        Merge classified nets according to the effective scope.
        """
        from .altium_prjpcb import NetIdentifierScope

        power_net_map = classified["power_net_map"]
        port_net_map = classified["port_net_map"]
        other_nets = classified["other_nets"]

        scope = self._effective_scope
        ports_are_global = scope in (
            NetIdentifierScope.FLAT,
            NetIdentifierScope.GLOBAL,
        )

        merged_nets = []
        self._merge_power_nets(power_net_map, merged_nets, scope)
        harness_keys = classified.get("harness_keys", set())
        self._merge_port_nets(
            port_net_map,
            merged_nets,
            ports_are_global,
            scope,
            harness_keys,
        )
        self._merge_other_nets(other_nets, merged_nets, scope)

        if scope == NetIdentifierScope.GLOBAL and len(merged_nets) > 1:
            merged_nets = self._final_same_name_merge(merged_nets)
        return merged_nets

    def _merge_power_nets(
        self,
        power_net_map: dict[str, list[tuple[int, "Net"]]],
        merged_nets: list["Net"],
        scope: "NetIdentifierScope | None" = None,
    ) -> None:
        """
        Merge power-port nets by name.
        """
        from .altium_netlist_model import Net, NetGraphical
        from .altium_prjpcb import NetIdentifierScope

        _case_insensitive_consolidate(power_net_map)

        if scope == NetIdentifierScope.STRICT_HIERARCHICAL:
            for _, sheet_nets in power_net_map.items():
                for _, net in reversed(sheet_nets):
                    merged_nets.append(net)
            return

        for power_name, sheet_nets in power_net_map.items():
            if len(sheet_nets) > 1:
                merged_terminals = []
                merged_graphical = NetGraphical()
                for _, net in sheet_nets:
                    merged_terminals.extend(net.terminals)
                    merged_graphical.merge(net.graphical)

                all_power_names = set()
                for sheet_idx, power_net in sheet_nets:
                    for power_port_name in self._get_all_power_port_names_for_net(
                        sheet_idx,
                        power_net,
                    ):
                        all_power_names.add(power_port_name)

                display_name = _resolve_power_display_name(
                    power_name,
                    sheet_nets,
                    all_power_names,
                    self._options.power_port_names_take_priority,
                )

                all_names = {
                    net.name for _, net in sheet_nets if not net.auto_named and net.name
                }
                aliases = sorted(name for name in all_names if name != display_name)

                merged_nets.append(
                    Net(
                        name=display_name,
                        terminals=merged_terminals,
                        graphical=merged_graphical,
                        auto_named=False,
                        source_sheets=sorted(
                            {
                                sheet
                                for _, net in sheet_nets
                                for sheet in net.source_sheets
                            }
                        ),
                        aliases=aliases,
                        hierarchy_paths=_collect_hierarchy_paths(
                            sheet_nets,
                            self._sheet_paths,
                        ),
                        endpoints=self._collect_sheet_net_endpoints(sheet_nets),
                    )
                )
            else:
                merged_nets.append(
                    _merge_single_power_net(
                        power_name,
                        sheet_nets[0][1],
                        self._options.power_port_names_take_priority,
                        Net,
                    )
                )

    def _merge_port_nets(
        self,
        port_net_map: dict[str, list[tuple[int, "Net"]]],
        merged_nets: list["Net"],
        ports_are_global: bool,
        scope: "NetIdentifierScope",
        harness_keys: set[str] | None = None,
    ) -> None:
        """
        Merge port nets by name according to scope rules.
        """
        from .altium_netlist_model import Net, NetGraphical

        for port_name, sheet_nets in port_net_map.items():
            should_merge, has_hierarchy_bridge = self._classify_port_net_group(
                port_name,
                sheet_nets,
                ports_are_global,
                harness_keys,
            )

            if should_merge:
                merged_terminals = []
                merged_graphical = NetGraphical()
                for _, net in sheet_nets:
                    merged_terminals.extend(net.terminals)
                    merged_graphical.merge(net.graphical)

                final_name, is_auto = self._determine_merged_net_name(
                    port_name,
                    sheet_nets,
                    merged_terminals,
                    has_hierarchy_bridge,
                    scope,
                )

                all_names = {
                    net.name for _, net in sheet_nets if not net.auto_named and net.name
                }
                if harness_keys is not None and port_name in harness_keys:
                    all_names.add(port_name)
                    if "." in port_name:
                        member_name = port_name.rsplit(".", 1)[-1].strip()
                        if member_name:
                            all_names.add(member_name)
                aliases = sorted(name for name in all_names if name != final_name)

                merged_nets.append(
                    Net(
                        name=final_name,
                        terminals=merged_terminals,
                        graphical=merged_graphical,
                        auto_named=is_auto,
                        source_sheets=sorted(
                            {
                                sheet
                                for _, net in sheet_nets
                                for sheet in net.source_sheets
                            }
                        ),
                        aliases=aliases,
                        hierarchy_paths=_collect_hierarchy_paths(
                            sheet_nets,
                            self._sheet_paths,
                        ),
                        endpoints=self._collect_sheet_net_endpoints(sheet_nets),
                    )
                )
            elif ports_are_global and len(sheet_nets) == 1:
                _, net = sheet_nets[0]
                if net.graphical.labels:
                    merged_nets.append(net)
                elif self._options.allow_ports_to_name_nets:
                    merged_nets.append(
                        Net(
                            name=port_name,
                            terminals=net.terminals,
                            graphical=net.graphical,
                            auto_named=False,
                            source_sheets=list(net.source_sheets),
                            endpoints=list(net.endpoints),
                        )
                    )
                else:
                    merged_nets.append(net)
            else:
                for _, net in sheet_nets:
                    merged_nets.append(net)

    @staticmethod
    def _classify_port_net_group(
        port_name: str,
        sheet_nets: list,
        ports_are_global: bool,
        harness_keys: set[str] | None,
    ) -> tuple[bool, bool]:
        """
        Determine whether a port-net group should merge.
        """
        has_hierarchy_bridge = port_name.startswith("__bridge_") or any(
            net.graphical.sheet_entries for _, net in sheet_nets
        )
        is_harness_group = "." in port_name
        is_harness_expanded = harness_keys is not None and port_name in harness_keys
        should_merge = (
            ports_are_global
            or has_hierarchy_bridge
            or is_harness_group
            or is_harness_expanded
        ) and len(sheet_nets) > 1
        return should_merge, has_hierarchy_bridge

    def _determine_merged_net_name(
        self,
        port_name: str,
        sheet_nets: list[tuple[int, "Net"]],
        merged_terminals: list["Terminal"],
        has_hierarchy_bridge: bool,
        scope: "NetIdentifierScope",
    ) -> tuple[str, bool]:
        """
        Determine the final name for a merged port-net group.
        """
        net_label_name = _collect_net_label_name(
            sheet_nets,
            self._options.higher_level_names_take_priority,
            has_hierarchy_bridge,
            scope,
        )

        if net_label_name:
            final_name = net_label_name
            is_auto = False
        elif has_hierarchy_bridge:
            final_name, is_auto = _resolve_hierarchy_fallback_name(
                sheet_nets,
                self._options.allow_sheet_entries_to_name_nets,
                self._auto_name_from_terminals,
                merged_terminals,
            )
        elif self._options.allow_ports_to_name_nets:
            final_name = port_name
            is_auto = False
        else:
            final_name = self._auto_name_from_terminals(merged_terminals)
            is_auto = True

        has_any_labels = any(
            not net.auto_named and net.graphical.labels for _, net in sheet_nets
        )
        if has_hierarchy_bridge and len(merged_terminals) == 1 and not has_any_labels:
            has_power_port = any(net.graphical.power_ports for _, net in sheet_nets)
            if not has_power_port:
                is_auto = True

        return final_name, is_auto

    def _merge_other_nets(
        self,
        other_nets: list[tuple[int, "Net"]],
        merged_nets: list["Net"],
        scope: "NetIdentifierScope",
    ) -> None:
        """
        Add remaining nets, merging same-named nets in GLOBAL mode.
        """
        from .altium_netlist_model import Net, NetGraphical
        from .altium_prjpcb import NetIdentifierScope

        if scope == NetIdentifierScope.GLOBAL and len(other_nets) > 1:
            other_by_name = defaultdict(list)
            for sheet_idx, net in other_nets:
                other_by_name[net.name].append((sheet_idx, net))

            for net_name, group in other_by_name.items():
                if len(group) > 1:
                    merged_terminals = []
                    merged_graphical = NetGraphical()
                    all_names = set()
                    is_auto = False
                    for _, net in group:
                        merged_terminals.extend(net.terminals)
                        merged_graphical.merge(net.graphical)
                        if not net.auto_named and net.name:
                            all_names.add(net.name)
                        if net.auto_named:
                            is_auto = True
                    aliases = sorted(name for name in all_names if name != net_name)
                    merged_nets.append(
                        Net(
                            name=net_name,
                            terminals=merged_terminals,
                            graphical=merged_graphical,
                            auto_named=is_auto,
                            source_sheets=sorted(
                                {
                                    sheet
                                    for _, net in group
                                    for sheet in net.source_sheets
                                }
                            ),
                            aliases=aliases,
                            hierarchy_paths=_collect_hierarchy_paths(
                                group,
                                self._sheet_paths,
                            ),
                            endpoints=self._collect_sheet_net_endpoints(group),
                        )
                    )
                else:
                    merged_nets.append(group[0][1])
        else:
            for _, net in other_nets:
                merged_nets.append(net)

    def _final_same_name_merge(self, merged_nets: list) -> list["Net"]:
        """
        Final merge of same-named nets across categories.
        """
        from .altium_netlist_model import Net, NetGraphical

        nets_by_name = defaultdict(list)
        for net in merged_nets:
            nets_by_name[net.name].append(net)

        final_merged = []
        for net_name, group in nets_by_name.items():
            if len(group) > 1:
                power_backed = [net for net in group if net.graphical.power_ports]
                if len(power_backed) > 1:
                    final_merged.extend(group)
                    continue

                merged_terminals = []
                merged_graphical = NetGraphical()
                all_names = set()
                is_auto = False
                for net in group:
                    merged_terminals.extend(net.terminals)
                    merged_graphical.merge(net.graphical)
                    if not net.auto_named and net.name:
                        all_names.add(net.name)
                    if net.auto_named:
                        is_auto = True
                    all_names.update(net.aliases)

                aliases = sorted(name for name in all_names if name != net_name)
                final_merged.append(
                    Net(
                        name=net_name,
                        terminals=merged_terminals,
                        graphical=merged_graphical,
                        auto_named=is_auto,
                        source_sheets=sorted(
                            {sheet for net in group for sheet in net.source_sheets}
                        ),
                        aliases=aliases,
                        endpoints=self._collect_net_endpoints(group),
                    )
                )
            else:
                final_merged.append(group[0])

        return final_merged

    def _finalize_nets(self, merged_nets: list) -> list["Net"]:
        """
        Finalize nets: dedupe terminals, apply sheet numbers, sort.
        """
        from .altium_netlist_model import Net

        final_nets = []
        for net in merged_nets:
            seen = set()
            unique_terminals = []
            for term in net.terminals:
                key = (term.designator, term.pin)
                if key not in seen:
                    seen.add(key)
                    unique_terminals.append(term)

            paths = net.hierarchy_paths
            if not paths and net.source_sheets:
                paths = self._paths_from_source_sheets(net.source_sheets)

            final_nets.append(
                Net(
                    name=net.name,
                    terminals=unique_terminals,
                    graphical=net.graphical,
                    auto_named=net.auto_named,
                    source_sheets=list(net.source_sheets),
                    aliases=net.aliases,
                    hierarchy_paths=paths,
                    endpoints=self._dedupe_endpoints(list(net.endpoints)),
                )
            )

        if self._options.append_sheet_numbers_to_local_nets:
            sheet_numbers = self._resolve_sheet_numbers()
            for net in final_nets:
                if self._is_local_net(net):
                    sheet_num = (
                        sheet_numbers.get(net.source_sheets[0])
                        if len(net.source_sheets) == 1
                        else None
                    )
                    if sheet_num:
                        net.name = f"{net.name}_{sheet_num}"

        if self._channel_instances:
            final_nets = [net for net in final_nets if net.terminals]

        final_nets.sort(key=lambda net: _altium_net_sort_key(net.name), reverse=True)
        return final_nets

    def _paths_from_source_sheets(
        self, source_sheets: list[str]
    ) -> list["HierarchyPath"]:
        """
        Build hierarchy paths from source sheet filenames.
        """
        from .altium_netlist_model import HierarchyPath

        if not hasattr(self, "_filename_to_sheet_idx"):
            self._filename_to_sheet_idx = {}
            for idx, schdoc in enumerate(self._schdocs):
                if schdoc.filepath:
                    self._filename_to_sheet_idx[schdoc.filepath.name] = idx

        paths = []
        seen = set()
        for sheet_name in source_sheets:
            idx = self._filename_to_sheet_idx.get(sheet_name)
            if idx is not None:
                path = self._sheet_paths.get(idx, HierarchyPath())
                key = path.unique_id_path
                if key not in seen:
                    seen.add(key)
                    paths.append(path)
        return paths

    def _get_power_port_name_for_net(self, sheet_idx: int, net: "Net") -> str | None:
        """
        Resolve the first power-like object name for a net.
        """
        schdoc = self._schdocs[sheet_idx]
        for uid in net.graphical.power_ports:
            for power_port in [
                *schdoc.get_power_ports(),
                *schdoc.get_cross_sheet_connectors(),
            ]:
                if power_port.unique_id == uid:
                    return power_port.text
        return None

    def _get_all_power_port_names_for_net(
        self,
        sheet_idx: int,
        net: "Net",
    ) -> list[str]:
        """
        Resolve all power-like object names for a net.
        """
        schdoc = self._schdocs[sheet_idx]
        names = []
        for uid in net.graphical.power_ports:
            for power_port in [
                *schdoc.get_power_ports(),
                *schdoc.get_cross_sheet_connectors(),
            ]:
                if power_port.unique_id == uid:
                    names.append(power_port.text)
                    break
        return names

    def _find_port_name_for_net(self, sheet_idx: int, net: "Net") -> str | None:
        """
        Resolve the first port name associated with a net.
        """
        schdoc = self._schdocs[sheet_idx]
        for port_uid in net.graphical.ports:
            for port in schdoc.get_ports():
                if port.name and port.unique_id == port_uid:
                    return port.name
        return None

    def _find_all_port_names_for_net(self, sheet_idx: int, net: "Net") -> list[str]:
        """
        Resolve all port names associated with a net.
        """
        schdoc = self._schdocs[sheet_idx]
        names = []
        for port_uid in net.graphical.ports:
            for port in schdoc.get_ports():
                if port.name and port.unique_id == port_uid:
                    names.append(port.name)
                    break
        return names

    @staticmethod
    def _find_harness_port_name(
        connector: object,
        signal_harnesses: list[object] | None,
        port_location_map: dict[tuple[int, int], str],
    ) -> str | None:
        """
        Find the port connected to a harness connector.
        """
        info = AltiumNetlistMultiSheetCompiler._find_harness_bundle_info(
            connector,
            signal_harnesses,
            port_location_map,
        )
        return info.get("port_name") or None

    @staticmethod
    def _find_harness_bundle_info(
        connector: object,
        signal_harnesses: list[object] | None,
        port_location_map: dict[tuple[int, int], str],
    ) -> dict[str, object]:
        """
        Find the harness port and signal-harness object IDs for a connector.
        """
        result: dict[str, object] = {
            "port_name": "",
            "signal_harness_ids": [],
        }
        if not signal_harnesses:
            return result

        harness_y = connector.location.y - connector.primary_connection_position
        harness_x_left = connector.location.x
        harness_x_right = connector.location.x + connector.xsize
        signal_harness_ids = []
        seen_signal_harness_ids = set()

        for signal_harness in signal_harnesses:
            if not signal_harness.points or len(signal_harness.points) < 2:
                continue

            touches_connector = False
            for point in signal_harness.points:
                if point.y == harness_y and (
                    point.x == harness_x_left or point.x == harness_x_right
                ):
                    touches_connector = True
                    break
            if not touches_connector:
                continue

            signal_harness_id = str(
                getattr(signal_harness, "unique_id", "") or ""
            ).strip()
            if signal_harness_id and signal_harness_id not in seen_signal_harness_ids:
                seen_signal_harness_ids.add(signal_harness_id)
                signal_harness_ids.append(signal_harness_id)

            for point in signal_harness.points:
                port_name = port_location_map.get((point.x, point.y))
                if port_name:
                    result["port_name"] = port_name
                    result["signal_harness_ids"] = signal_harness_ids
                    return result

        result["signal_harness_ids"] = signal_harness_ids
        return result

    @staticmethod
    def _auto_name_from_terminals(terminals: list["Terminal"]) -> str:
        """
        Generate an auto-name from the first naturally sorted terminal.
        """
        if not terminals:
            return "Net"

        def natural_key(term: "Terminal") -> list[int | str]:
            parts = re.split(r"(\d+)", term.designator)
            return [int(part) if part.isdigit() else part.lower() for part in parts]

        first = sorted(terminals, key=natural_key)[0]
        return f"Net{first.designator}_{first.pin}"

    def _resolve_sheet_numbers(self) -> dict[str, str]:
        """
        Resolve sheet numbers for each document.
        """
        sheet_numbers = {}

        if self._options.auto_sheet_numbering:
            for idx, schdoc in enumerate(self._schdocs):
                filename = schdoc.filepath.name if schdoc.filepath else f"sheet{idx}"
                sheet_numbers[filename] = str(idx + 1)
        else:
            for idx, schdoc in enumerate(self._schdocs):
                filename = schdoc.filepath.name if schdoc.filepath else f"sheet{idx}"
                sheet_number = self._get_document_parameter(schdoc, "SheetNumber")
                sheet_numbers[filename] = sheet_number or str(idx + 1)

        return sheet_numbers

    @staticmethod
    def _parse_bus_range(name: str) -> list[str] | None:
        """
        Parse bus notation like `A[0..3]` into member names.
        """
        match = re.match(r"^(.+)\[(\d+)\.\.(\d+)\]$", name)
        if not match:
            return None

        prefix = match.group(1)
        start = int(match.group(2))
        end = int(match.group(3))
        if start <= end:
            return [f"{prefix}{idx}" for idx in range(start, end + 1)]
        return [f"{prefix}{idx}" for idx in range(start, end - 1, -1)]

    @staticmethod
    def _is_local_net(net: "Net") -> bool:
        """
        Check whether a net is local to a single sheet.
        """
        if len(net.source_sheets) != 1:
            return False
        if net.graphical.power_ports or net.graphical.ports:
            return False
        return True

    @staticmethod
    def _get_document_parameter(schdoc: "AltiumSchDoc", name: str) -> str | None:
        """
        Get a document parameter value by name from a SchDoc.
        """
        for parameter in schdoc.parameters:
            if getattr(parameter, "name", "") == name:
                value = getattr(parameter, "text", "")
                if value and value != "*":
                    return value
        return None


__all__ = ["AltiumNetlistMultiSheetCompiler"]
