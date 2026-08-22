"""Shared helpers for compiled schematic design construction and projection."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .altium_sch_record_helpers import (
    _effective_basic_entry_distance_frac1,
    _coord_scalar_to_native_parts,
    _coord_scalar_with_basic_entry_distance_to_native_parts,
)
from .altium_netlist_wire_connectivity import (
    altium_internal_tolerance_for_display_unit,
    point_on_segment_with_frac,
    precise_points_connected,
)

if TYPE_CHECKING:
    from .altium_schdoc import AltiumSchDoc


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


def _build_wire_endpoint_map(schdoc: AltiumSchDoc) -> dict[_PrecisePoint, str]:
    """Build a full-coordinate wire-endpoint lookup."""
    wire_endpoint_map: dict[_PrecisePoint, str] = {}
    for wire in schdoc.get_wires():
        if wire.points and wire.unique_id:
            for point in wire.points:
                wire_endpoint_map[_point_key(point)] = wire.unique_id
    return wire_endpoint_map


_PrecisePoint = tuple[int, int, int, int]


@dataclass(frozen=True)
class _StoredHarnessPoint:
    x: int
    y: int
    x_frac: int = 0
    y_frac: int = 0


def _normalize_precise_point(x_total: int, y_total: int) -> _PrecisePoint:
    x, x_frac = divmod(x_total, 100000)
    y, y_frac = divmod(y_total, 100000)
    return (x, y, x_frac, y_frac)


def _point_key(point: object) -> _PrecisePoint:
    if isinstance(point, tuple):
        if len(point) == 2:
            return (int(point[0]), int(point[1]), 0, 0)
        return (int(point[0]), int(point[1]), int(point[2]), int(point[3]))
    return (
        int(getattr(point, "x")),
        int(getattr(point, "y")),
        int(getattr(point, "x_frac", 0) or 0),
        int(getattr(point, "y_frac", 0) or 0),
    )


def _harness_connector_master_entry_point(connector: object) -> _PrecisePoint:
    """Return AD's full-precision harness-connector master entry location."""
    location = getattr(connector, "location")
    scale = 100000
    x_total = int(location.x) * scale + int(getattr(location, "x_frac", 0) or 0)
    y_total = int(location.y) * scale + int(getattr(location, "y_frac", 0) or 0)
    x_size_total = int(getattr(connector, "xsize", 0) or 0) * scale + int(
        getattr(connector, "xsize_frac", 0) or 0
    )
    y_size_total = int(getattr(connector, "ysize", 0) or 0) * scale + int(
        getattr(connector, "ysize_frac", 0) or 0
    )
    primary_total = int(
        getattr(connector, "primary_connection_position", 0) or 0
    ) * scale + int(getattr(connector, "primary_connection_position_frac", 0) or 0)
    side_value = getattr(connector, "side", 1)
    side = int(getattr(side_value, "value", side_value))
    if side == 0:
        y_total -= primary_total
    elif side == 1:
        x_total += x_size_total
        y_total -= primary_total
    elif side == 2:
        x_total += primary_total
    else:
        x_total += primary_total
        y_total -= y_size_total
    return _normalize_precise_point(x_total, y_total)


def _harness_entry_connection_point(
    connector: object,
    entry: object,
) -> _PrecisePoint:
    """Return AD's derived full-precision harness-entry location."""
    location = getattr(connector, "location")
    scale = 100000
    x_total = int(location.x) * scale + int(getattr(location, "x_frac", 0) or 0)
    y_total = int(location.y) * scale + int(getattr(location, "y_frac", 0) or 0)
    x_size_total = int(getattr(connector, "xsize", 0) or 0) * scale + int(
        getattr(connector, "xsize_frac", 0) or 0
    )
    y_size_total = int(getattr(connector, "ysize", 0) or 0) * scale + int(
        getattr(connector, "ysize_frac", 0) or 0
    )
    distance_total = int(getattr(entry, "distance_from_top", 0) or 0) * (
        10 * scale
    ) + _effective_basic_entry_distance_frac1(entry)
    connector_side_value = getattr(connector, "side", 1)
    connector_side = int(getattr(connector_side_value, "value", connector_side_value))
    entry_side = (1, 0, 3, 2)[connector_side]
    if entry_side == 0:
        y_total -= distance_total
    elif entry_side == 1:
        x_total += x_size_total
        y_total -= distance_total
    elif entry_side == 2:
        x_total += distance_total
    else:
        x_total += distance_total
        y_total -= y_size_total
    return _normalize_precise_point(x_total, y_total)


def _build_port_location_map(schdoc: AltiumSchDoc) -> dict[_PrecisePoint, str]:
    """Build a full-coordinate port/entry lookup for harness matching."""
    port_location_map: dict[_PrecisePoint, str] = {}

    for port in schdoc.get_ports():
        if port.name and port.location:
            for point in port._precise_connection_points:
                port_location_map[_point_key(point)] = port.name

    for sheet_symbol in schdoc.get_sheet_symbols():
        record = sheet_symbol.record
        for entry in sheet_symbol.entries:
            if not getattr(entry, "harness_type", ""):
                continue
            entry_name = entry.display_name or ""
            if not entry_name:
                continue
            entry_side = getattr(entry, "side", None)
            distance_from_top = getattr(entry, "distance_from_top", None)
            if entry_side is None or distance_from_top is None:
                continue
            distance_from_top_frac1 = _effective_basic_entry_distance_frac1(entry)
            if entry_side == 1:
                entry_x, entry_x_frac = _coord_scalar_to_native_parts(
                    record.location.x + record.x_size,
                    getattr(record.location, "x_frac", 0)
                    + getattr(record, "x_size_frac", 0),
                )
            else:
                entry_x, entry_x_frac = _coord_scalar_to_native_parts(
                    record.location.x,
                    getattr(record.location, "x_frac", 0),
                )
            entry_y, entry_y_frac = (
                _coord_scalar_with_basic_entry_distance_to_native_parts(
                    record.location.y,
                    getattr(record.location, "y_frac", 0),
                    distance_from_top,
                    distance_from_top_frac1,
                    direction=-1,
                )
            )
            port_location_map[(entry_x, entry_y, entry_x_frac, entry_y_frac)] = (
                entry_name
            )

    return port_location_map


def _point_on_signal_harness_segment(
    point: _PrecisePoint,
    start: _PrecisePoint,
    end: _PrecisePoint,
    internal_tolerance: int,
) -> bool:
    point_obj = _StoredHarnessPoint(*point)
    start_obj = _StoredHarnessPoint(*start)
    end_obj = _StoredHarnessPoint(*end)
    start_x = start[0] * 100000 + start[2]
    start_y = start[1] * 100000 + start[3]
    end_x = end[0] * 100000 + end[2]
    end_y = end[1] * 100000 + end[3]
    if precise_points_connected(
        point, start, internal_tolerance
    ) or precise_points_connected(point, end, internal_tolerance):
        return True
    tolerance = 0 if start_x == end_x or start_y == end_y else internal_tolerance
    return point_on_segment_with_frac(point_obj, start_obj, end_obj, tolerance)


def _signal_harness_points(signal_harness: object) -> tuple[_PrecisePoint, ...]:
    return tuple(
        _point_key(point) for point in (getattr(signal_harness, "points", ()) or ())
    )


def _point_on_signal_harness(
    point: _PrecisePoint,
    signal_harness: object,
    internal_tolerance: int = 0,
) -> bool:
    points = _signal_harness_points(signal_harness)
    return any(
        _point_on_signal_harness_segment(point, start, end, internal_tolerance)
        for start, end in zip(points, points[1:], strict=False)
    )


def _signal_harnesses_touch(
    left: object, right: object, internal_tolerance: int = 0
) -> bool:
    left_points = _signal_harness_points(left)
    right_points = _signal_harness_points(right)
    return any(
        _point_on_signal_harness(point, right, internal_tolerance)
        for point in (left_points[0], left_points[-1])
    ) or any(
        _point_on_signal_harness(point, left, internal_tolerance)
        for point in (right_points[0], right_points[-1])
    )


def _connected_signal_harness_indexes(
    harnesses: list[object],
    connector_points: tuple[_PrecisePoint, ...],
    internal_tolerance: int = 0,
) -> list[int]:
    pending = [
        index
        for index, signal_harness in enumerate(harnesses)
        if any(
            _point_on_signal_harness(point, signal_harness, internal_tolerance)
            for point in connector_points
        )
    ]
    connected = set(pending)
    while pending:
        current = pending.pop()
        for index, candidate in enumerate(harnesses):
            if index in connected or not _signal_harnesses_touch(
                harnesses[current], candidate, internal_tolerance
            ):
                continue
            connected.add(index)
            pending.append(index)
    return sorted(connected)


def _signal_harness_ids(
    harnesses: list[object],
    connected_indexes: list[int],
) -> list[str]:
    return list(
        dict.fromkeys(
            signal_harness_id
            for index in connected_indexes
            if (
                signal_harness_id := str(
                    getattr(harnesses[index], "unique_id", "") or ""
                ).strip()
            )
        )
    )


def _signal_harness_network_port_name(
    harnesses: list[object],
    connected_indexes: list[int],
    port_location_map: dict[tuple[int, ...], str],
    internal_tolerance: int = 0,
) -> str:
    precise_names = {
        _point_key(point): name for point, name in port_location_map.items()
    }
    for index in connected_indexes:
        for point in _signal_harness_points(harnesses[index]):
            if port_name := precise_names.get(point):
                return port_name
    for index in connected_indexes:
        signal_harness = harnesses[index]
        for point, port_name in precise_names.items():
            if _point_on_signal_harness(point, signal_harness, internal_tolerance):
                return port_name
    return ""


def find_harness_bundle_info(
    connector: object,
    signal_harnesses: object,
    port_location_map: dict[tuple[int, ...], str],
    internal_tolerance: int = 0,
) -> dict[str, object]:
    """Find the named signal-harness network touching a harness connector."""
    result: dict[str, object] = {"port_name": "", "signal_harness_ids": []}
    if not isinstance(signal_harnesses, Iterable):
        return result
    connector_location = getattr(connector, "location", None)
    if connector_location is None:
        return result
    harnesses = [
        signal_harness
        for signal_harness in signal_harnesses
        if len(getattr(signal_harness, "points", ()) or ()) >= 2
    ]
    connector_points = (_harness_connector_master_entry_point(connector),)
    connected_indexes = _connected_signal_harness_indexes(
        harnesses, connector_points, internal_tolerance
    )
    signal_harness_ids = _signal_harness_ids(harnesses, connected_indexes)
    port_name = _signal_harness_network_port_name(
        harnesses,
        connected_indexes,
        port_location_map,
        internal_tolerance,
    )
    if port_name:
        result["port_name"] = port_name
    result["signal_harness_ids"] = signal_harness_ids
    return result


def find_harness_port_name(
    connector: object,
    signal_harnesses: object,
    port_location_map: dict[tuple[int, ...], str],
    internal_tolerance: int = 0,
) -> str | None:
    """Return the harness port name physically connected to a connector."""
    port_name = find_harness_bundle_info(
        connector,
        signal_harnesses,
        port_location_map,
        internal_tolerance,
    ).get("port_name")
    return port_name if isinstance(port_name, str) and port_name else None


def _build_child_harness_entry_map(
    child_schdoc: AltiumSchDoc,
) -> dict[str, list[dict[str, str]]]:
    """Build the child connector entries indexed by harness port name."""
    child_harness_entries: dict[str, list[dict[str, str]]] = {}
    child_port_location_map = _build_port_location_map(child_schdoc)
    display_unit = int(getattr(getattr(child_schdoc, "sheet", None), "display_unit", 0))
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    for harness_connector in child_schdoc.harness_connectors:
        harness_port = find_harness_port_name(
            harness_connector,
            child_schdoc.signal_harnesses,
            child_port_location_map,
            internal_tolerance,
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


_ENTRY_REPEAT_PATTERN = re.compile(r"^REPEAT\s*\(\s*([^,)]+)\s*\)$", re.IGNORECASE)
_DIFF_PAIR_SUFFIXES = ("_P", "_N")


def _strip_diff_pair_suffix(name: str) -> tuple[str, str]:
    """Strip a differential-pair suffix from a net name."""
    index = name.rfind("_")
    if index > 0 and name[index:] in _DIFF_PAIR_SUFFIXES:
        return name[:index], name[index:]
    return name, ""


def _parse_entry_repeat(entry_name: str) -> str | None:
    """Parse ``REPEAT(portName)`` from a sheet-entry name."""
    match = _ENTRY_REPEAT_PATTERN.match(entry_name.strip())
    return match.group(1).strip() if match else None


def _build_room_details(
    sheet_sym_name: str,
    instance_index: int,
    sheet_designator: str = "",
) -> RoomDetails:
    """Build room details from a sheet-symbol name and instance index."""
    match = re.match(r"^(.*?)(\d+)$", sheet_sym_name)
    if match:
        prefix = match.group(1)
        index = match.group(2)
    else:
        prefix = sheet_sym_name
        index = str(instance_index + 1)

    alpha_index = instance_index + 1
    channel_alpha = (
        chr(ord("A") + alpha_index - 1) if 1 <= alpha_index <= 26 else str(alpha_index)
    )
    return RoomDetails(
        room_name=sheet_sym_name,
        channel_prefix=prefix,
        channel_index=index,
        channel_alpha=channel_alpha,
        sheet_designator=sheet_designator,
        sheet_number=str(instance_index + 1),
        document_number=str(instance_index + 1),
    )


def apply_channel_pattern(
    format_str: str,
    room: RoomDetails,
    designator: str,
) -> str:
    """Apply an Altium channel designator format string."""
    result = format_str
    result = result.replace("$RoomName", room.room_name)
    result = result.replace("$ChannelPrefix", room.channel_prefix)
    result = result.replace("$ChannelIndex", room.channel_index)
    result = result.replace("$ChannelAlpha", room.channel_alpha)
    result = result.replace("$SheetDesignator", room.sheet_designator)
    result = result.replace("$SheetNumber", room.sheet_number)
    result = result.replace("$DocumentNumber", room.document_number)

    match = re.match(r"^(.*?)(\d+)$", designator)
    component_prefix = match.group(1) if match else designator
    component_index = match.group(2) if match else ""
    result = result.replace("$ComponentPrefix", component_prefix)
    result = result.replace("$ComponentIndex", component_index)
    return result.replace("$Component", designator)


__all__ = [
    "RoomDetails",
    "_build_child_harness_entry_map",
    "_build_port_location_map",
    "_build_room_details",
    "_build_wire_endpoint_map",
    "_parse_entry_repeat",
    "_strip_diff_pair_suffix",
    "apply_channel_pattern",
    "find_harness_bundle_info",
    "find_harness_port_name",
]
