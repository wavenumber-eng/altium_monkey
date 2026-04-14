"""Shared wire connectivity helpers for the Altium netlist pipeline."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Protocol, TypeVar

from .altium_netlist_common import _points_connected
from .altium_netlist_model import UnionFind

if TYPE_CHECKING:
    from .altium_schdoc import AltiumSchDoc


Point = tuple[int, int]


class _PointLike(Protocol):
    x: int
    y: int


class _WireLike(Protocol):
    points: list[_PointLike]


class _HasLocationRecord(Protocol):
    location: _PointLike


class _NetLabelLike(Protocol):
    record: _HasLocationRecord


class _PinLike(Protocol):
    connection_point: Point


PinLike = TypeVar("PinLike", bound=_PinLike)


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
    tolerance: int = 0,
) -> bool:
    """Return True when `point` lies on the segment, honoring fractions."""

    px, py = point.x, point.y
    p_x_frac = getattr(point, "x_frac", 0)
    p_y_frac = getattr(point, "y_frac", 0)
    s_x_frac = getattr(seg_start, "x_frac", 0)
    s_y_frac = getattr(seg_start, "y_frac", 0)
    e_x_frac = getattr(seg_end, "x_frac", 0)
    e_y_frac = getattr(seg_end, "y_frac", 0)
    min_x = min(seg_start.x, seg_end.x) - tolerance
    max_x = max(seg_start.x, seg_end.x) + tolerance
    min_y = min(seg_start.y, seg_end.y) - tolerance
    max_y = max(seg_start.y, seg_end.y) + tolerance
    if not (min_x <= px <= max_x and min_y <= py <= max_y):
        return False
    if seg_start.y == seg_end.y:
        if abs(py - seg_start.y) > tolerance:
            return False
        return not (py == seg_start.y and s_y_frac == e_y_frac and p_y_frac != s_y_frac)
    if seg_start.x == seg_end.x:
        if abs(px - seg_start.x) > tolerance:
            return False
        return not (px == seg_start.x and s_x_frac == e_x_frac and p_x_frac != s_x_frac)
    return point_on_segment(
        (px, py),
        (seg_start.x, seg_start.y),
        (seg_end.x, seg_end.y),
        tolerance,
    )


class WireGeometryIndex:
    """Spatial index for fast wire endpoint and segment lookups."""

    def __init__(
        self,
        wires: Iterable[_WireLike],
        tolerance: int = 0,
        cell_size: int = 100,
    ) -> None:
        self._wire_points: dict[int, list[Point]] = {}
        self._all_endpoints: list[Point] = []
        self._segment_grid: dict[
            Point,
            list[tuple[Point, Point]],
        ] = defaultdict(list)
        self._endpoint_grid: dict[Point, list[Point]] = defaultdict(list)
        self._segment_coords: dict[Point, list[tuple[_PointLike, _PointLike]]] = (
            defaultdict(list)
        )
        self._cell_size = cell_size
        for wire in wires:
            raw_points = getattr(wire, "points", [])
            int_points = [(point.x, point.y) for point in raw_points]
            self._wire_points[id(wire)] = int_points
            if len(int_points) >= 2:
                first_point = int_points[0]
                last_point = int_points[-1]
                self._all_endpoints.append(first_point)
                self._all_endpoints.append(last_point)
                self._endpoint_grid[self._cell_key(first_point)].append(first_point)
                self._endpoint_grid[self._cell_key(last_point)].append(last_point)
            for index in range(len(int_points) - 1):
                segment = (int_points[index], int_points[index + 1])
                for cell in self._segment_cells(segment[0], segment[1]):
                    self._segment_grid[cell].append(segment)
            for index in range(len(raw_points) - 1):
                segment = (int_points[index], int_points[index + 1])
                for cell in self._segment_cells(segment[0], segment[1]):
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

    def get_points(self, wire: _WireLike) -> list[Point]:
        return self._wire_points.get(id(wire), [])

    def get_all_endpoints(self) -> list[Point]:
        return self._all_endpoints

    def find_nearby_endpoints(self, location: Point, tolerance: int) -> Iterator[Point]:
        for cell in self._nearby_cells(location, tolerance):
            yield from self._endpoint_grid.get(cell, ())

    def find_nearby_segments(
        self,
        location: Point,
        tolerance: int,
    ) -> Iterator[tuple[Point, Point]]:
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
    ) -> Point | None:
        for endpoint in self.find_nearby_endpoints(location, tolerance):
            if points_connected(location, endpoint, tolerance):
                return endpoint
        seen_segments: set[tuple[tuple[int, int], tuple[int, int]]] = set()
        for seg_start, seg_end in self.find_nearby_segments(location, tolerance):
            segment_key = (seg_start, seg_end)
            if segment_key in seen_segments:
                continue
            seen_segments.add(segment_key)
            if point_on_segment(location, seg_start, seg_end, tolerance):
                return seg_start
        return None

    def find_wire_connection_for_netlabel(
        self,
        net_label: _NetLabelLike,
        tolerance: int,
    ) -> Point | None:
        location_obj = net_label.record.location
        location = (location_obj.x, location_obj.y)
        for endpoint in self.find_nearby_endpoints(location, tolerance):
            if points_connected(location, endpoint, tolerance):
                return endpoint
        seen_segments: set[tuple[int, int, int, int]] = set()
        for seg_start, seg_end in self.find_nearby_segment_coords(location, tolerance):
            segment_key = (seg_start.x, seg_start.y, seg_end.x, seg_end.y)
            if segment_key in seen_segments:
                continue
            seen_segments.add(segment_key)
            if point_on_segment_with_frac(location_obj, seg_start, seg_end, tolerance):
                return (seg_start.x, seg_start.y)
        return None


def connect_point_to_wires(
    point: Point,
    wire_index: WireGeometryIndex,
    union_find: UnionFind[Point],
    tolerance: int,
) -> None:
    """Connect a junction point to all nearby wire endpoints and segments."""

    connected_points: list[tuple[int, int]] = []
    for endpoint in wire_index.find_nearby_endpoints(point, tolerance):
        if points_connected(point, endpoint, tolerance):
            connected_points.append(endpoint)
    seen_segments: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for seg_start, seg_end in wire_index.find_nearby_segments(point, tolerance):
        segment_key = (seg_start, seg_end)
        if segment_key in seen_segments:
            continue
        seen_segments.add(segment_key)
        if point_on_segment(point, seg_start, seg_end, tolerance):
            connected_points.append(seg_start)
    unique_points: list[tuple[int, int]] = []
    seen_points: set[tuple[int, int]] = set()
    for connected_point in connected_points:
        if connected_point in seen_points:
            continue
        seen_points.add(connected_point)
        unique_points.append(connected_point)
    for index in range(len(unique_points) - 1):
        union_find.union(unique_points[index], unique_points[index + 1])


def connect_endpoint_t_junctions(
    endpoint: Point,
    wire_index: WireGeometryIndex,
    union_find: UnionFind[Point],
    tolerance: int,
) -> None:
    """Connect an endpoint to segments it lies on."""

    seen_segments: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for seg_start, seg_end in wire_index.find_nearby_segments(endpoint, tolerance):
        segment_key = (seg_start, seg_end)
        if segment_key in seen_segments:
            continue
        seen_segments.add(segment_key)
        if points_connected(endpoint, seg_start, tolerance):
            continue
        if points_connected(endpoint, seg_end, tolerance):
            continue
        if point_on_segment(endpoint, seg_start, seg_end, tolerance):
            union_find.union(endpoint, seg_start)


def build_wire_graph(
    schdoc: "AltiumSchDoc",
    tolerance: int = 0,
    *,
    cell_size: int = 100,
) -> tuple[UnionFind, dict[tuple[int, int], list[str]], WireGeometryIndex]:
    """Build a Union-Find wire graph for one schematic document."""

    wires = list(schdoc.get_wires())
    junction_points = [
        _location_to_tuple(junction.location) for junction in schdoc.get_junctions()
    ]
    union_find = UnionFind()
    wire_index = WireGeometryIndex(wires, tolerance=tolerance, cell_size=cell_size)
    for wire in wires:
        points = wire_index.get_points(wire)
        for index in range(len(points) - 1):
            union_find.union(points[index], points[index + 1])
    for junction_point in junction_points:
        connect_point_to_wires(junction_point, wire_index, union_find, tolerance)
    for endpoint in wire_index.get_all_endpoints():
        for other_endpoint in wire_index.find_nearby_endpoints(endpoint, tolerance):
            if endpoint != other_endpoint and points_connected(
                endpoint,
                other_endpoint,
                tolerance,
            ):
                union_find.union(endpoint, other_endpoint)
        connect_endpoint_t_junctions(endpoint, wire_index, union_find, tolerance)
    wire_ids_by_root: dict[Point, list[str]] = defaultdict(list)
    for wire in wires:
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
    union_find: UnionFind[Point],
    wire_index: WireGeometryIndex,
    tolerance: int = 0,
    *,
    pins: Iterable[PinLike] | None = None,
) -> tuple[dict[Point, list[PinLike]], set[Point]]:
    """Group schematic pins by the wire network they connect to."""

    if pins is None:
        pins = list(schdoc.get_all_pins())
    else:
        pins = list(pins)
    pins_by_location: dict[Point, list[PinLike]] = defaultdict(list)
    for pin in pins:
        pins_by_location[pin.connection_point].append(pin)
    for location, pins_at_location in pins_by_location.items():
        if len(pins_at_location) <= 1:
            continue
        connected_wire_point = wire_index.find_wire_connection(location, tolerance)
        if connected_wire_point is not None:
            union_find.union(location, connected_wire_point)
        else:
            union_find.add_root(location)
        for pin in pins_at_location:
            union_find.union(pin.connection_point, location)
    pin_groups: dict[Point, list[PinLike]] = defaultdict(list)
    floating_pin_roots: set[Point] = set()
    for pin in pins:
        connection_point = pin.connection_point
        if union_find.contains(connection_point):
            root = union_find.find(connection_point)
            pin_groups[root].append(pin)
            continue
        wire_point = wire_index.find_wire_connection(connection_point, tolerance)
        if wire_point is not None:
            root = union_find.find(wire_point)
            pin_groups[root].append(pin)
            continue
        union_find.add_root(connection_point)
        pin_groups[connection_point].append(pin)
        floating_pin_roots.add(connection_point)
    return pin_groups, floating_pin_roots


def _location_to_tuple(location: Point | _PointLike) -> Point:
    if isinstance(location, tuple):
        return location
    return (location.x, location.y)
