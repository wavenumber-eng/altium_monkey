"""Shared wire connectivity helpers for the Altium netlist pipeline."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from enum import Enum
from math import hypot
from typing import TYPE_CHECKING, Protocol

from .altium_netlist_common import _points_connected
from .altium_netlist_model import UnionFind
from .altium_sch_enums import PinElectrical

if TYPE_CHECKING:
    from .altium_schdoc import AltiumSchDoc


Point = tuple[int, int]
RootPoint = tuple[int, int, int, int]
ALTIUM_COORD_SCALE = 100000
ALTIUM_METRIC_INTERNAL_TOLERANCE = 20000
_METRIC_DISPLAY_UNITS = frozenset({1, 3, 5, 7})


class AnalyserNetItemKind(str, Enum):
    """AD spatial-analyser destination for a schematic net item."""

    WIRE = "wire"
    BUS = "bus"
    HARNESS = "harness"


class _PointLike(Protocol):
    @property
    def x(self) -> int:
        raise NotImplementedError("point x")

    @property
    def y(self) -> int:
        raise NotImplementedError("point y")


class _WireLike(Protocol):
    @property
    def points(self) -> Sequence[_PointLike]:
        raise NotImplementedError("wire points")


class _HasLocationRecord(Protocol):
    @property
    def location(self) -> _PointLike:
        raise NotImplementedError("record location")


class _NetLabelLike(Protocol):
    @property
    def record(self) -> _HasLocationRecord:
        raise NotImplementedError("label record")


class _StoredPoint:
    __slots__ = ("x", "x_frac", "y", "y_frac")

    def __init__(self, point: tuple[int, int, int, int]) -> None:
        self.x, self.y, self.x_frac, self.y_frac = point


def _signal_harness_intersects_location(
    location: RootPoint,
    signal_harness: object,
    internal_tolerance: int,
) -> bool:
    """Mirror ``SpatialAnalyser.IntersectLineAtLocation`` for harness lines."""
    location_point = _StoredPoint(location)
    points = tuple(
        _StoredPoint(_precise_point(point))
        for point in (getattr(signal_harness, "points", ()) or ())
    )
    for start, end in zip(points, points[1:], strict=False):
        point_x, point_y = _composed_point(location_point)
        start_x, start_y = _composed_point(start)
        end_x, end_y = _composed_point(end)
        if start_y == end_y:
            if point_y == start_y and min(start_x, end_x) <= point_x <= max(
                start_x, end_x
            ):
                return True
            continue
        if start_x == end_x:
            if point_x == start_x and min(start_y, end_y) <= point_y <= max(
                start_y, end_y
            ):
                return True
            continue
        if precise_points_connected(
            location, _precise_point(start), internal_tolerance
        ):
            continue
        if precise_points_connected(location, _precise_point(end), internal_tolerance):
            continue
        if point_on_segment_with_frac(
            location_point,
            start,
            end,
            internal_tolerance,
        ):
            return True
    return False


def analyser_net_item_kind(
    identifier: str,
    *,
    harness_type: str = "",
    locations: Sequence[RootPoint],
    signal_harnesses: Sequence[object] = (),
    harness_connector_primary_points: Sequence[RootPoint] = (),
    internal_tolerance: int = 0,
) -> AnalyserNetItemKind:
    """Classify a net item using AD26 ``ReaderSCH.GetNetItemKind`` order."""
    if ".." in identifier:
        return AnalyserNetItemKind.BUS
    if harness_type:
        return AnalyserNetItemKind.HARNESS
    if any(
        _signal_harness_intersects_location(
            location,
            signal_harness,
            internal_tolerance,
        )
        for location in locations[:2]
        for signal_harness in signal_harnesses
    ):
        return AnalyserNetItemKind.HARNESS
    if any(
        precise_points_connected(location, primary, internal_tolerance)
        for location in locations[:2]
        for primary in harness_connector_primary_points
    ):
        return AnalyserNetItemKind.HARNESS
    return AnalyserNetItemKind.WIRE


def _power_bus_net_label_text(
    identifier: str,
    *,
    gnd_ordinal: int,
    vcc_ordinal: int,
) -> str | None:
    """Return AD26's synthesized label for a bracketed power-bus object."""
    if "[" not in identifier or "]" not in identifier:
        return None
    folded = identifier.casefold()
    if "gndbus" in folded:
        return f"GND{gnd_ordinal}_BUS[..] <= GND"
    if "vccbus" in folded:
        return f"VCC{vcc_ordinal}_BUS[..] <= VCC"
    return None


class _InferredWire:
    __slots__ = ("points",)

    def __init__(self, segment: tuple[RootPoint, RootPoint]) -> None:
        self.points = tuple(_StoredPoint(point) for point in segment)


class _PinLike(Protocol):
    @property
    def connection_point(self) -> Point:
        raise NotImplementedError("wire pin connection point")

    @property
    def _precise_connection_point(self) -> tuple[int, int, int, int]:
        raise NotImplementedError("wire pin full-coordinate connection point")

    @property
    def component_designator(self) -> str:
        raise NotImplementedError("wire pin component designator")

    @property
    def designator(self) -> str:
        raise NotImplementedError("wire pin designator")

    @property
    def name(self) -> str:
        raise NotImplementedError("wire pin name")

    @property
    def electrical(self) -> PinElectrical:
        raise NotImplementedError("wire pin electrical")

    @property
    def unique_id(self) -> str:
        raise NotImplementedError("wire pin unique id")


def points_connected(
    p1: tuple[int, int],
    p2: tuple[int, int],
    tolerance: int = 0,
) -> bool:
    """Return True when two points are electrically connected."""
    return _points_connected(p1, p2, tolerance)


def point_on_segment(
    point: tuple[int, int],
    seg_start: tuple[int, int],
    seg_end: tuple[int, int],
    tolerance: int = 0,
) -> bool:
    """Return True when `point` lies on the line segment."""

    min_x = min(seg_start[0], seg_end[0]) - tolerance
    max_x = max(seg_start[0], seg_end[0]) + tolerance
    min_y = min(seg_start[1], seg_end[1]) - tolerance
    max_y = max(seg_start[1], seg_end[1]) + tolerance
    if not (min_x <= point[0] <= max_x and min_y <= point[1] <= max_y):
        return False
    if seg_start[1] == seg_end[1]:
        return abs(point[1] - seg_start[1]) <= tolerance
    if seg_start[0] == seg_end[0]:
        return abs(point[0] - seg_start[0]) <= tolerance
    cross = (point[0] - seg_start[0]) * (seg_end[1] - seg_start[1]) - (
        point[1] - seg_start[1]
    ) * (seg_end[0] - seg_start[0])
    seg_len_sq = (seg_end[0] - seg_start[0]) ** 2 + (seg_end[1] - seg_start[1]) ** 2
    if seg_len_sq == 0:
        return points_connected(point, seg_start, tolerance)
    max_cross = tolerance * (seg_len_sq**0.5)
    return abs(cross) <= max_cross


def point_on_segment_with_frac(
    point: _PointLike,
    seg_start: _PointLike,
    seg_end: _PointLike,
    internal_tolerance: int = 0,
) -> bool:
    """Mirror AD26 ``SpatialUtils.CheckPointOnLine`` on full coordinates."""

    px, py = _composed_point(point)
    x1, y1 = _composed_point(seg_start)
    x2, y2 = _composed_point(seg_end)
    width = max(0, internal_tolerance)
    if x1 == x2:
        return _point_on_vertical_line(px, py, x1, y1, y2, width)
    if y1 == y2:
        return _point_on_horizontal_line(px, py, x1, x2, y1, width)
    if _point_outside_diagonal_bounds(px, py, x1, y1, x2, y2, width):
        return False
    return _point_to_segment_distance(px, py, x1, y1, x2, y2) <= width


def _point_on_vertical_line(
    px: int, py: int, line_x: int, y1: int, y2: int, width: int
) -> bool:
    return (
        abs(line_x - px) <= width and min(y1, y2) - width <= py <= max(y1, y2) + width
    )


def _point_on_horizontal_line(
    px: int, py: int, x1: int, x2: int, line_y: int, width: int
) -> bool:
    return (
        abs(line_y - py) <= width and min(x1, x2) - width <= px <= max(x1, x2) + width
    )


def _point_outside_diagonal_bounds(
    px: int,
    py: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
) -> bool:
    return (
        (x1 - width >= px and x2 - width >= px)
        or (y1 - width >= py and y2 - width >= py)
        or (x1 + width <= px and x2 + width <= px)
        or (y1 + width <= py and y2 + width <= py)
    )


def altium_internal_tolerance_for_display_unit(display_unit: int) -> int:
    """Return the AD26 schematic spatial tolerance for a display unit."""

    return (
        ALTIUM_METRIC_INTERNAL_TOLERANCE if display_unit in _METRIC_DISPLAY_UNITS else 0
    )


def precise_points_connected(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
    internal_tolerance: int,
) -> bool:
    """Mirror AD26 ``SpatialUtils.LocationsEqual`` on full coordinates."""

    return _full_points_connected(
        _StoredPoint(first),
        _StoredPoint(second),
        internal_tolerance,
    )


def _composed_axis(point: _PointLike, axis: str) -> int:
    return int(getattr(point, axis)) * ALTIUM_COORD_SCALE + int(
        getattr(point, f"{axis}_frac", 0) or 0
    )


def _composed_point(point: _PointLike) -> tuple[int, int]:
    return _composed_axis(point, "x"), _composed_axis(point, "y")


def _precise_point(point: _PointLike) -> RootPoint:
    return (
        int(point.x),
        int(point.y),
        int(getattr(point, "x_frac", 0) or 0),
        int(getattr(point, "y_frac", 0) or 0),
    )


def _nearby_whole_points(point: Point, tolerance: int) -> Iterator[Point]:
    for x in range(point[0] - tolerance, point[0] + tolerance + 1):
        for y in range(point[1] - tolerance, point[1] + tolerance + 1):
            yield (x, y)


def _pin_precise_point(pin: _PinLike) -> tuple[int, int, int, int]:
    precise_point = getattr(pin, "_precise_connection_point", None)
    if precise_point is not None:
        return precise_point
    return (*pin.connection_point, 0, 0)


def _candidate_cluster_indices(
    whole_point: Point,
    search_tolerance: int,
    cluster_indices_by_location: dict[Point, list[int]],
) -> Iterator[int]:
    for nearby_point in _nearby_whole_points(whole_point, search_tolerance):
        yield from cluster_indices_by_location.get(nearby_point, ())


def _cluster_pins_by_precise_location(
    pins: Iterable[_PinLike], internal_tolerance: int
) -> list[tuple[tuple[int, int, int, int], list[_PinLike]]]:
    pin_list = list(pins)
    precise_points = [_pin_precise_point(pin) for pin in pin_list]
    connectivity = UnionFind[int]()
    pin_indices_by_location: dict[Point, list[int]] = defaultdict(list)
    search_tolerance = _whole_search_tolerance(internal_tolerance)
    for pin_index, precise_point in enumerate(precise_points):
        connectivity.add_root(pin_index)
        whole_point = precise_point[:2]
        for candidate_index in _candidate_cluster_indices(
            whole_point,
            search_tolerance,
            pin_indices_by_location,
        ):
            if precise_points_connected(
                precise_point,
                precise_points[candidate_index],
                internal_tolerance,
            ):
                connectivity.union(pin_index, candidate_index)
        pin_indices_by_location[whole_point].append(pin_index)

    clusters_by_root: dict[int, list[int]] = defaultdict(list)
    for pin_index in range(len(pin_list)):
        clusters_by_root[connectivity.find(pin_index)].append(pin_index)
    return [
        (
            precise_points[indices[0]],
            [pin_list[pin_index] for pin_index in indices],
        )
        for indices in clusters_by_root.values()
    ]


def _point_to_segment_distance(
    x: int,
    y: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> float:
    if x1 == x2:
        low, high = sorted((y1, y2))
        if low <= y <= high:
            return float(abs(x - x1))
        endpoint_y = low if y < low else high
        return min(hypot(x - x1, y - endpoint_y), 32000.0)
    if y1 == y2:
        low, high = sorted((x1, x2))
        if low <= x <= high:
            return float(abs(y - y1))
        endpoint_x = low if x < low else high
        return min(hypot(x - endpoint_x, y - y1), 32000.0)
    if x1 > x2:
        x1, x2 = x2, x1
        y1, y2 = y2, y1
    slope = (y2 - y1) / (x2 - x1)
    denominator = slope * slope + 1.0
    projected_x = (x + slope * y + slope * slope * x1 - slope * y1) / denominator
    if projected_x < x1:
        return min(hypot(x - x1, y - y1), 32000.0)
    if projected_x > x2:
        return min(hypot(x - x2, y - y2), 32000.0)
    distance = abs(slope * x - y - slope * x1 + y1) / denominator**0.5
    return min(distance, 32000.0)


def _full_points_connected(
    first: _PointLike,
    second: _PointLike,
    internal_tolerance: int,
) -> bool:
    first_x, first_y = _composed_point(first)
    second_x, second_y = _composed_point(second)
    return (
        abs(first_x - second_x) <= internal_tolerance
        and abs(first_y - second_y) <= internal_tolerance
    )


def _whole_search_tolerance(internal_tolerance: int) -> int:
    return (max(0, internal_tolerance) + ALTIUM_COORD_SCALE - 1) // ALTIUM_COORD_SCALE


class WireGeometryIndex:
    """Spatial index for fast wire endpoint and segment lookups."""

    def __init__(
        self,
        wires: Iterable[_WireLike],
        tolerance: int = 0,
        cell_size: int = 100,
    ) -> None:
        self._wire_points: dict[int, list[RootPoint]] = {}
        self._all_endpoints: list[RootPoint] = []
        self._segment_grid: dict[
            Point,
            list[tuple[RootPoint, RootPoint]],
        ] = defaultdict(list)
        self._endpoint_grid: dict[Point, list[RootPoint]] = defaultdict(list)
        self._endpoint_coord_grid: dict[Point, list[_PointLike]] = defaultdict(list)
        self._segment_coords: dict[Point, list[tuple[_PointLike, _PointLike]]] = (
            defaultdict(list)
        )
        self._cell_size = cell_size
        for wire in wires:
            raw_points = getattr(wire, "points", [])
            precise_points = [_precise_point(point) for point in raw_points]
            self._wire_points[id(wire)] = precise_points
            if len(precise_points) >= 2:
                for precise_point, raw_point in zip(
                    precise_points, raw_points, strict=True
                ):
                    int_point = precise_point[:2]
                    self._all_endpoints.append(precise_point)
                    self._endpoint_grid[self._cell_key(int_point)].append(precise_point)
                    self._endpoint_coord_grid[self._cell_key(int_point)].append(
                        raw_point
                    )
            for index in range(len(precise_points) - 1):
                segment = (precise_points[index], precise_points[index + 1])
                for cell in self._segment_cells(segment[0][:2], segment[1][:2]):
                    self._segment_grid[cell].append(segment)
            for index in range(len(raw_points) - 1):
                segment = (precise_points[index], precise_points[index + 1])
                for cell in self._segment_cells(segment[0][:2], segment[1][:2]):
                    self._segment_coords[cell].append(
                        (raw_points[index], raw_points[index + 1]),
                    )

    def _cell_key(self, point: Point) -> Point:
        return (point[0] // self._cell_size, point[1] // self._cell_size)

    def _segment_cells(
        self,
        start: Point,
        end: Point,
    ) -> list[Point]:
        cs = self._cell_size
        min_cx = min(start[0], end[0]) // cs
        max_cx = max(start[0], end[0]) // cs
        min_cy = min(start[1], end[1]) // cs
        max_cy = max(start[1], end[1]) // cs
        cells: list[tuple[int, int]] = []
        for cx in range(min_cx, max_cx + 1):
            for cy in range(min_cy, max_cy + 1):
                cells.append((cx, cy))
        return cells

    def _nearby_cells(
        self,
        point: Point,
        tolerance: int,
    ) -> list[Point]:
        cs = self._cell_size
        min_cx = (point[0] - tolerance) // cs
        max_cx = (point[0] + tolerance) // cs
        min_cy = (point[1] - tolerance) // cs
        max_cy = (point[1] + tolerance) // cs
        cells: list[tuple[int, int]] = []
        for cx in range(min_cx, max_cx + 1):
            for cy in range(min_cy, max_cy + 1):
                cells.append((cx, cy))
        return cells

    def get_points(self, wire: object) -> list[RootPoint]:
        return self._wire_points.get(id(wire), [])

    def get_all_endpoints(self) -> list[RootPoint]:
        return self._all_endpoints

    def find_nearby_endpoints(
        self, location: Point, tolerance: int
    ) -> Iterator[RootPoint]:
        for cell in self._nearby_cells(location, tolerance):
            yield from self._endpoint_grid.get(cell, ())

    def find_nearby_endpoint_coords(
        self,
        location: Point,
        tolerance: int,
    ) -> Iterator[_PointLike]:
        for cell in self._nearby_cells(location, tolerance):
            yield from self._endpoint_coord_grid.get(cell, ())

    def find_nearby_segments(
        self,
        location: Point,
        tolerance: int,
    ) -> Iterator[tuple[RootPoint, RootPoint]]:
        for cell in self._nearby_cells(location, tolerance):
            yield from self._segment_grid.get(cell, ())

    def find_nearby_segment_coords(
        self,
        location: Point,
        tolerance: int,
    ) -> Iterator[tuple[_PointLike, _PointLike]]:
        for cell in self._nearby_cells(location, tolerance):
            yield from self._segment_coords.get(cell, ())

    def find_wire_connection(
        self,
        location: Point,
        tolerance: int,
    ) -> RootPoint | None:
        precise_location: RootPoint = (*location, 0, 0)
        internal_tolerance = max(0, tolerance) * ALTIUM_COORD_SCALE
        for endpoint in self.find_nearby_endpoints(location, tolerance):
            if precise_points_connected(precise_location, endpoint, internal_tolerance):
                return endpoint
        seen_segments: set[tuple[RootPoint, RootPoint]] = set()
        for seg_start, seg_end in self.find_nearby_segments(location, tolerance):
            segment_key = (seg_start, seg_end)
            if segment_key in seen_segments:
                continue
            seen_segments.add(segment_key)
            if point_on_segment_with_frac(
                _StoredPoint(precise_location),
                _StoredPoint(seg_start),
                _StoredPoint(seg_end),
                internal_tolerance,
            ):
                return seg_start
        return None

    def find_wire_connection_for_netlabel(
        self,
        net_label: _NetLabelLike,
        internal_tolerance: int,
    ) -> RootPoint | None:
        matches = self.find_wire_connections_for_netlabel(
            net_label,
            internal_tolerance,
        )
        return matches[0] if matches else None

    def find_wire_connections_for_netlabel(
        self,
        net_label: _NetLabelLike,
        internal_tolerance: int,
    ) -> list[RootPoint]:
        location_obj = net_label.record.location
        location = (location_obj.x, location_obj.y)
        search_tolerance = _whole_search_tolerance(internal_tolerance)
        matches: list[RootPoint] = []
        seen_points: set[RootPoint] = set()
        for endpoint in self.find_nearby_endpoint_coords(location, search_tolerance):
            if _full_points_connected(location_obj, endpoint, internal_tolerance):
                point = _precise_point(endpoint)
                if point not in seen_points:
                    seen_points.add(point)
                    matches.append(point)
        seen_segments: set[tuple[RootPoint, RootPoint]] = set()
        for seg_start, seg_end in self.find_nearby_segment_coords(
            location, search_tolerance
        ):
            segment_key = (_precise_point(seg_start), _precise_point(seg_end))
            if segment_key in seen_segments:
                continue
            seen_segments.add(segment_key)
            if point_on_segment_with_frac(
                location_obj, seg_start, seg_end, internal_tolerance
            ):
                point = _precise_point(seg_start)
                if point not in seen_points:
                    seen_points.add(point)
                    matches.append(point)
        return matches

    def find_wire_endpoint_for_precise_point(
        self,
        location: tuple[int, int, int, int],
        internal_tolerance: int,
    ) -> RootPoint | None:
        """Match an ordinary net item to a full-coordinate wire endpoint."""

        matches = self.find_wire_endpoints_for_precise_point(
            location,
            internal_tolerance,
        )
        return matches[0] if matches else None

    def find_wire_endpoints_for_precise_point(
        self,
        location: tuple[int, int, int, int],
        internal_tolerance: int,
    ) -> list[RootPoint]:
        """Return every wire endpoint matching an ordinary net item."""

        location_obj = _StoredPoint(location)
        int_location = (location_obj.x, location_obj.y)
        search_tolerance = _whole_search_tolerance(internal_tolerance)
        matches: list[RootPoint] = []
        seen_points: set[RootPoint] = set()
        for endpoint in self.find_nearby_endpoint_coords(
            int_location, search_tolerance
        ):
            if _full_points_connected(location_obj, endpoint, internal_tolerance):
                point = _precise_point(endpoint)
                if point not in seen_points:
                    seen_points.add(point)
                    matches.append(point)
        return matches

    def find_wire_connections_for_precise_pin(
        self,
        location: tuple[int, int, int, int],
        internal_tolerance: int,
    ) -> list[RootPoint]:
        """Return AD line-break anchors for one precise pin hot spot."""
        return self.find_wire_connections_for_break_point(
            location,
            internal_tolerance,
        )

    def find_wire_connections_for_break_point(
        self,
        location: tuple[int, int, int, int],
        internal_tolerance: int,
    ) -> list[RootPoint]:
        """Return endpoint matches plus exact segment anchors for an AD break point."""
        matches = self.find_wire_endpoints_for_precise_point(
            location,
            internal_tolerance,
        )
        seen_points = set(matches)
        location_obj = _StoredPoint(location)
        int_location = (location_obj.x, location_obj.y)
        seen_segments: set[tuple[RootPoint, RootPoint]] = set()
        for seg_start, seg_end in self.find_nearby_segment_coords(int_location, 0):
            segment_key = (_precise_point(seg_start), _precise_point(seg_end))
            if segment_key in seen_segments:
                continue
            seen_segments.add(segment_key)
            if point_on_segment_with_frac(location_obj, seg_start, seg_end, 0):
                point = _precise_point(seg_start)
                if point not in seen_points:
                    seen_points.add(point)
                    matches.append(point)
        return matches


def connect_point_to_wires(
    point: RootPoint,
    wire_index: WireGeometryIndex,
    union_find: UnionFind[RootPoint],
    internal_tolerance: int,
) -> None:
    """Connect a junction point to all nearby wire endpoints and segments."""

    connected_points: list[RootPoint] = []
    search_tolerance = _whole_search_tolerance(internal_tolerance)
    for endpoint in wire_index.find_nearby_endpoints(point[:2], search_tolerance):
        if precise_points_connected(point, endpoint, internal_tolerance):
            connected_points.append(endpoint)
    seen_segments: set[tuple[RootPoint, RootPoint]] = set()
    for seg_start, seg_end in wire_index.find_nearby_segments(
        point[:2], search_tolerance
    ):
        segment_key = (seg_start, seg_end)
        if segment_key in seen_segments:
            continue
        seen_segments.add(segment_key)
        if point_on_segment_with_frac(
            _StoredPoint(point),
            _StoredPoint(seg_start),
            _StoredPoint(seg_end),
            internal_tolerance,
        ):
            connected_points.append(seg_start)
    unique_points: list[RootPoint] = []
    seen_points: set[RootPoint] = set()
    for connected_point in connected_points:
        if connected_point in seen_points:
            continue
        seen_points.add(connected_point)
        unique_points.append(connected_point)
    for index in range(len(unique_points) - 1):
        union_find.union(unique_points[index], unique_points[index + 1])


def connect_endpoint_t_junctions(
    endpoint: RootPoint,
    wire_index: WireGeometryIndex,
    union_find: UnionFind[RootPoint],
    internal_tolerance: int,
) -> None:
    """Connect an endpoint to segments it lies on."""

    search_tolerance = _whole_search_tolerance(internal_tolerance)
    seen_segments: set[tuple[RootPoint, RootPoint]] = set()
    for seg_start, seg_end in wire_index.find_nearby_segments(
        endpoint[:2], search_tolerance
    ):
        segment_key = (seg_start, seg_end)
        if segment_key in seen_segments:
            continue
        seen_segments.add(segment_key)
        if precise_points_connected(endpoint, seg_start, internal_tolerance):
            continue
        if precise_points_connected(endpoint, seg_end, internal_tolerance):
            continue
        if point_on_segment_with_frac(
            _StoredPoint(endpoint),
            _StoredPoint(seg_start),
            _StoredPoint(seg_end),
            0,
        ):
            union_find.union(endpoint, seg_start)


def build_wire_graph(
    schdoc: "AltiumSchDoc",
    tolerance: int = 0,
    *,
    cell_size: int = 100,
    inferred_segments: Iterable[tuple[RootPoint, RootPoint]] = (),
) -> tuple[UnionFind, dict[RootPoint, list[str]], WireGeometryIndex]:
    """Build a Union-Find wire graph for one schematic document."""

    source_wires = list(schdoc.get_wires())
    wires: list[_WireLike] = [*source_wires]
    wires.extend(_InferredWire(segment) for segment in inferred_segments)
    junction_points = [
        _location_to_precise_tuple(junction.location)
        for junction in schdoc.get_junctions()
    ]
    union_find = UnionFind()
    wire_index = WireGeometryIndex(wires, tolerance=tolerance, cell_size=cell_size)
    for wire in wires:
        points = wire_index.get_points(wire)
        for index in range(len(points) - 1):
            union_find.union(points[index], points[index + 1])
    for junction_point in junction_points:
        connect_point_to_wires(junction_point, wire_index, union_find, tolerance)
    search_tolerance = _whole_search_tolerance(tolerance)
    for endpoint in wire_index.get_all_endpoints():
        for other_endpoint in wire_index.find_nearby_endpoints(
            endpoint[:2], search_tolerance
        ):
            if endpoint != other_endpoint and precise_points_connected(
                endpoint,
                other_endpoint,
                tolerance,
            ):
                union_find.union(endpoint, other_endpoint)
        connect_endpoint_t_junctions(endpoint, wire_index, union_find, tolerance)
    wire_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)
    for wire in source_wires:
        points = wire_index.get_points(wire)
        if not points:
            continue
        root = union_find.find(points[0])
        wire_id = getattr(wire, "unique_id", None)
        if wire_id:
            wire_ids_by_root[root].append(wire_id)
    return union_find, wire_ids_by_root, wire_index


def group_pins_by_network(
    schdoc: "AltiumSchDoc",
    union_find: UnionFind[RootPoint],
    wire_index: WireGeometryIndex,
    internal_tolerance: int = 0,
    *,
    pins: Iterable[_PinLike] | None = None,
) -> tuple[dict[RootPoint, list[_PinLike]], set[RootPoint]]:
    """Group schematic pins by the wire network they connect to."""

    if pins is None:
        pin_list = list(schdoc.get_all_pins())
    else:
        pin_list = list(pins)
    pin_clusters = _cluster_pins_by_precise_location(pin_list, internal_tolerance)
    pin_groups: dict[RootPoint, list[_PinLike]] = defaultdict(list)
    floating_pin_roots: set[RootPoint] = set()
    for precise_point, pins_at_location in pin_clusters:
        connection_point = precise_point
        wire_points: list[RootPoint] = []
        for pin in pins_at_location:
            wire_points.extend(
                wire_index.find_wire_connections_for_precise_pin(
                    _pin_precise_point(pin),
                    internal_tolerance,
                )
            )
        unique_wire_points = list(dict.fromkeys(wire_points))
        if unique_wire_points:
            for wire_point in unique_wire_points[1:]:
                union_find.union(unique_wire_points[0], wire_point)
            root = union_find.find(unique_wire_points[0])
            pin_groups[root].extend(pins_at_location)
            continue
        floating_root = connection_point
        while union_find.contains(floating_root) or floating_root in floating_pin_roots:
            floating_root = (
                floating_root[0],
                floating_root[1] - 1,
                floating_root[2],
                floating_root[3],
            )
        union_find.add_root(floating_root)
        pin_groups[floating_root].extend(pins_at_location)
        floating_pin_roots.add(floating_root)
    return pin_groups, floating_pin_roots


def _location_to_tuple(location: Point | _PointLike) -> Point:
    if isinstance(location, tuple):
        return location
    return (location.x, location.y)


def _location_to_precise_tuple(location: _PointLike) -> RootPoint:
    return _precise_point(location)
