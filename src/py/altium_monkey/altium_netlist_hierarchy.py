"""Hierarchy-path helpers extracted for the Altium netlist pipeline."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Iterable

from .altium_netlist_model import HierarchyPath

if TYPE_CHECKING:
    from .altium_netlist_multi_sheet_support import ChannelInstance
    from .altium_schdoc import AltiumSchDoc
    from .altium_schdoc_info import SchSheetSymbolInfo


def resolve_child_indices(
    child_filename_lower: str,
    sheet_symbol_info: SchSheetSymbolInfo,
    filename_to_indices: dict[str, int | list[int]],
    *,
    channel_instances: Iterable[ChannelInstance] = (),
    channel_netlist_map: dict[tuple[int, int], int] | None = None,
) -> list[int]:
    """Resolve a sheet symbol to child sheet indices, including channels."""

    if channel_netlist_map is None:
        channel_netlist_map = {}
    symbol_uid = sheet_symbol_info.unique_id
    indices: list[int] = []
    for channel in channel_instances:
        if getattr(channel, "sheet_sym_unique_id", "") != symbol_uid:
            continue
        new_index = channel_netlist_map.get(
            (getattr(channel, "child_idx"), getattr(channel, "instance_index")),
        )
        if new_index is not None:
            indices.append(new_index)
    if indices:
        return indices
    fallback = filename_to_indices.get(child_filename_lower)
    if fallback is None:
        return []
    if isinstance(fallback, list):
        return list(fallback)
    return [fallback]


def build_sheet_paths(
    schdocs: list[AltiumSchDoc],
    *,
    channel_instances: Iterable[ChannelInstance] = (),
    channel_netlist_map: dict[tuple[int, int], int] | None = None,
) -> dict[int, HierarchyPath]:
    """Build `HierarchyPath` values for every sheet index in the document set."""

    if channel_netlist_map is None:
        channel_netlist_map = {}
    sheet_paths = {index: HierarchyPath() for index in range(len(schdocs))}
    filename_to_indices: dict[str, list[int]] = defaultdict(list)
    for index, schdoc in enumerate(schdocs):
        filepath = getattr(schdoc, "filepath", None)
        if filepath:
            filename_to_indices[filepath.name.lower()].append(index)
    simple_filename_lookup = {
        filename: indices[0]
        for filename, indices in filename_to_indices.items()
        if indices
    }
    channels = list(channel_instances)
    for parent_index, schdoc in enumerate(schdocs):
        parent_path = sheet_paths[parent_index]
        for sheet_symbol_info in schdoc.get_sheet_symbols():
            file_name_obj = getattr(sheet_symbol_info.record, "file_name", None)
            if file_name_obj is None:
                continue
            child_filename = getattr(file_name_obj, "text", "") or ""
            if not child_filename:
                continue
            child_indices = resolve_child_indices(
                child_filename.lower(),
                sheet_symbol_info,
                simple_filename_lookup,
                channel_instances=channels,
                channel_netlist_map=channel_netlist_map,
            )
            if not child_indices:
                continue
            for child_index in child_indices:
                channel = _find_channel_instance(
                    channels,
                    channel_netlist_map,
                    child_index,
                )
                room = getattr(channel, "room", None)
                sheet_paths[child_index] = parent_path.move_down(
                    sheet_symbol_uid=sheet_symbol_info.unique_id,
                    child_filename=child_filename.lower(),
                    designator=sheet_symbol_info.designator or "",
                    channel_name=getattr(room, "room_name", "") if room else "",
                    channel_index=getattr(channel, "instance_index", -1)
                    if channel
                    else -1,
                    repeat_value=getattr(channel, "repeat_value", None)
                    if channel
                    else None,
                )
    return sheet_paths


def _find_channel_instance(
    channel_instances: list[ChannelInstance],
    channel_netlist_map: dict[tuple[int, int], int],
    child_index: int,
) -> ChannelInstance | None:
    for channel in channel_instances:
        new_index = channel_netlist_map.get(
            (getattr(channel, "child_idx"), getattr(channel, "instance_index")),
        )
        if new_index == child_index:
            return channel
    return None
