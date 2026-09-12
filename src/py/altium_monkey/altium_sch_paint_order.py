"""Reconstruct schematic geometry paint order from source record order."""

from __future__ import annotations

import re
import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import islice, pairwise

from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from ._sch_source_admission import _SourceAdmission
from ._sch_source_projection import (
    _component_bound_field_slots,
    _hierarchy_bound_field_slots,
)
from .altium_record_sch__component import AltiumSchComponent
from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__ellipse import AltiumSchEllipse
from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
from .altium_record_sch__harness_type import AltiumSchHarnessType
from .altium_record_sch__file_name import AltiumSchFileName
from ._altium_record_sch__harness_layout import (
    AltiumSchHarnessBundle,
    AltiumSchHarnessLayoutConnectionPoint,
    AltiumSchHarnessLayoutCovering,
    AltiumSchHarnessLayoutLabel,
    AltiumSchHarnessSplice,
    _BUS_LINE_WIDTH_INTERNAL,
    _abs_safe_i32,
    _unchecked_i32,
)
from .altium_record_sch__parameter import AltiumSchParameter
from .altium_record_sch__polygon import AltiumSchPolygon
from .altium_record_sch__rectangle import AltiumSchRectangle
from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol
from .altium_record_sch__sheet_name import AltiumSchSheetName
from .altium_record_types import LineWidth, SchGraphicalObject
from .altium_sch_geometry_oracle import SchGeometryBounds, SchGeometryRecord


_GENERATED_RECORD_ID = re.compile(r"^(IEEE|PIE|TPL)(\d{5})$")
_GENERATED_TEMPLATE_CHILD_ID = re.compile(r"^TPL\d{5}C(\d{5})$")
_SOURCE_KIND_OVERRIDES = {
    "filename": "sheetfilename",
    "harnesstype": "harnessconnectortype",
    "ieeesymbol": "ieee_symbol",
    "piechart": "pie",
    "powerport": "power",
    "roundedrectangle": "roundrectangle",
}
_GENERATED_SOURCE_PREFIXES = {
    "ieee_symbol": "IEEE",
    "pie": "PIE",
    "template": "TPL",
}

_GeneratedSourceKey = tuple[str, int]

_MAX_HARNESS_BUNDLES = 50_000
_MAX_HARNESS_BUNDLE_EDGES = 200_000
_MAX_HARNESS_LABEL_EDGE_TESTS = 10_000_000


@dataclass(frozen=True, slots=True)
class _HarnessBundlePaintShape:
    """First-level harness bundle state used by label dependency extraction."""

    state_uid: str
    line_width: LineWidth
    vertices: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _HarnessBundleEdge:
    bundle_index: int
    point1: tuple[int, int]
    point2: tuple[int, int]
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class _HarnessBundleEdgeNode:
    bounds: tuple[int, int, int, int]
    edges: tuple[_HarnessBundleEdge, ...] = ()
    left: _HarnessBundleEdgeNode | None = None
    right: _HarnessBundleEdgeNode | None = None


class _HarnessBundleSpatialIndex:
    """Static BVH for exact label-to-bundle edge queries."""

    _LEAF_SIZE = 8

    def __init__(
        self,
        document_bundles: Iterable[AltiumSchHarnessBundle | _HarnessBundlePaintShape],
        *,
        max_bundles: int = _MAX_HARNESS_BUNDLES,
        max_edges: int = _MAX_HARNESS_BUNDLE_EDGES,
        max_edge_tests: int = _MAX_HARNESS_LABEL_EDGE_TESTS,
    ) -> None:
        for name, value in (
            ("bundle", max_bundles),
            ("bundle-edge", max_edges),
            ("label edge-test", max_edge_tests),
        ):
            if value < 0:
                raise ValueError(f"harness {name} limit cannot be negative")
        self.max_bundles = max_bundles
        self.max_edges = max_edges
        self.bundle_state_uids, edges = self._collect_geometry(document_bundles)
        self.edge_count = len(edges)
        self.last_examined_edge_count = 0
        self.examined_edge_count = 0
        self.last_exact_edge_test_count = 0
        self.exact_edge_test_count = 0
        self.max_edge_tests = max_edge_tests
        self._remaining_edge_tests = max_edge_tests
        self.root = self._build_node(edges)

    def _collect_geometry(
        self,
        bundles: Iterable[AltiumSchHarnessBundle | _HarnessBundlePaintShape],
    ) -> tuple[tuple[str, ...], tuple[_HarnessBundleEdge, ...]]:
        state_uids: list[str] = []
        edges: list[_HarnessBundleEdge] = []
        scanned_bundles = 0
        for bundle in bundles:
            if scanned_bundles >= self.max_bundles:
                raise ValueError(
                    "harness label dependency extraction exceeds the document "
                    f"limit of {self.max_bundles} bundles"
                )
            scanned_bundles += 1
            state_uid, line_width, vertices = _bundle_index_state(bundle)
            if not state_uid:
                continue
            bundle_index = len(state_uids)
            state_uids.append(state_uid)
            half_width = _BUS_LINE_WIDTH_INTERNAL[line_width] // 2
            for point1, point2 in pairwise(vertices):
                for edge1, edge2 in _offset_segment_edges(
                    point1,
                    point2,
                    half_width,
                ):
                    if len(edges) >= self.max_edges:
                        raise ValueError(
                            "harness label dependency extraction exceeds the "
                            f"document limit of {self.max_edges} offset edges"
                        )
                    edges.append(
                        _HarnessBundleEdge(
                            bundle_index=bundle_index,
                            point1=edge1,
                            point2=edge2,
                            bounds=_segment_bounds(edge1, edge2),
                        )
                    )
        return tuple(state_uids), tuple(edges)

    def predecessors(self, own_bounds: SchGeometryBounds) -> tuple[str, ...]:
        background_bounds = _label_background_bounds(own_bounds)
        query_bounds = _normalized_bounds(background_bounds)
        candidates: set[int] = set()
        examined = 0
        exact_tests = 0
        for edge in self._candidate_edges(query_bounds):
            examined += 1
            if examined > self._remaining_edge_tests:
                self.last_examined_edge_count = examined
                self.last_exact_edge_test_count = exact_tests
                raise ValueError(
                    "harness label dependency extraction exceeds the document "
                    f"limit of {self.max_edge_tests} candidate-edge visits"
                )
            if edge.bundle_index in candidates:
                continue
            exact_tests += 1
            if _segment_intersects_bounds(
                edge.point1,
                edge.point2,
                background_bounds,
            ):
                candidates.add(edge.bundle_index)
        self.last_examined_edge_count = examined
        self.examined_edge_count += examined
        self.last_exact_edge_test_count = exact_tests
        self.exact_edge_test_count += exact_tests
        self._remaining_edge_tests -= examined
        return tuple(
            self.bundle_state_uids[index]
            for index in sorted(candidates)
            if self.bundle_state_uids[index]
        )

    def _candidate_edges(
        self,
        query_bounds: tuple[int, int, int, int],
    ) -> Iterable[_HarnessBundleEdge]:
        pending = [self.root] if self.root is not None else []
        while pending:
            node = pending.pop()
            if not _bounds_overlap(node.bounds, query_bounds):
                continue
            if node.edges:
                for edge in node.edges:
                    if _bounds_overlap(edge.bounds, query_bounds):
                        yield edge
                continue
            if node.right is not None:
                pending.append(node.right)
            if node.left is not None:
                pending.append(node.left)

    @classmethod
    def _build_node(
        cls,
        edges: tuple[_HarnessBundleEdge, ...],
    ) -> _HarnessBundleEdgeNode | None:
        if not edges:
            return None
        bounds = _combined_bounds(edge.bounds for edge in edges)
        if len(edges) <= cls._LEAF_SIZE:
            return _HarnessBundleEdgeNode(bounds=bounds, edges=edges)
        x_span = bounds[2] - bounds[0]
        y_span = bounds[3] - bounds[1]
        axis = 0 if x_span >= y_span else 1
        ordered = sorted(
            edges,
            key=lambda edge: edge.bounds[axis] + edge.bounds[axis + 2],
        )
        middle = len(ordered) // 2
        return _HarnessBundleEdgeNode(
            bounds=bounds,
            left=cls._build_node(tuple(ordered[:middle])),
            right=cls._build_node(tuple(ordered[middle:])),
        )


def _bundle_index_state(
    source: AltiumSchHarnessBundle | _HarnessBundlePaintShape,
) -> tuple[str, LineWidth, Iterable[tuple[int, int]]]:
    if isinstance(source, _HarnessBundlePaintShape):
        return source.state_uid, source.line_width, source.vertices
    return (
        str(source.unique_id or ""),
        source.line_width,
        (
            (
                _unchecked_i32(point.x * 100_000 + point.x_frac),
                _unchecked_i32(point.y * 100_000 + point.y_frac),
            )
            for point in source.points
        ),
    )


def _harness_bundle_paint_shape(
    source: AltiumSchHarnessBundle,
) -> _HarnessBundlePaintShape:
    """Snapshot the managed state fields used by label dependency extraction."""
    return _HarnessBundlePaintShape(
        state_uid=str(source.unique_id or ""),
        line_width=source.line_width,
        vertices=tuple(
            (
                _unchecked_i32(point.x * 100_000 + point.x_frac),
                _unchecked_i32(point.y * 100_000 + point.y_frac),
            )
            for point in source.points
        ),
    )


def _normalized_bounds(bounds: SchGeometryBounds) -> tuple[int, int, int, int]:
    return (
        min(bounds.left, bounds.right),
        min(bounds.bottom, bounds.top),
        max(bounds.left, bounds.right),
        max(bounds.bottom, bounds.top),
    )


def _segment_bounds(
    point1: tuple[int, int],
    point2: tuple[int, int],
) -> tuple[int, int, int, int]:
    return (
        min(point1[0], point2[0]),
        min(point1[1], point2[1]),
        max(point1[0], point2[0]),
        max(point1[1], point2[1]),
    )


def _combined_bounds(
    bounds: Iterable[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    rows = tuple(bounds)
    return (
        min(row[0] for row in rows),
        min(row[1] for row in rows),
        max(row[2] for row in rows),
        max(row[3] for row in rows),
    )


def _bounds_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    return not (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def _round_away_from_zero(value: float) -> int:
    fraction, whole = math.modf(value)
    if abs(fraction) >= 0.5:
        whole += math.copysign(1.0, value)
    return int(whole)


def _line_equation(
    point1: tuple[int, int], point2: tuple[int, int]
) -> tuple[float, float, float]:
    if point1[0] == point2[0]:
        return 1.0, 0.0, float(-point1[0])
    if point1[1] == point2[1]:
        return 0.0, 1.0, float(-point1[1])
    slope = (point1[1] - point2[1]) / (point1[0] - point2[0])
    return slope, -1.0, point1[1] - slope * point1[0]


def _perpendicular_line_equation(
    point: tuple[int, int], line: tuple[float, float, float]
) -> tuple[float, float, float]:
    if line[0] == 0.0:
        return 1.0, 0.0, float(-point[0])
    if line[1] == 0.0:
        return 0.0, 1.0, float(-point[1])
    slope = -1.0 / line[0]
    return slope, -1.0, point[1] - slope * point[0]


def _points_moved_by_vector_from_center(
    center: tuple[int, int],
    line: tuple[float, float, float],
    distance: int,
) -> tuple[tuple[int, int], tuple[int, int]]:
    slope, vertical_marker, intercept = line
    if slope == 0.0:
        return (
            (center[0] - distance, center[1]),
            (center[0] + distance, center[1]),
        )
    if vertical_marker == 0.0:
        return (
            (center[0], center[1] - distance),
            (center[0], center[1] + distance),
        )
    quadratic = 1.0 + math.pow(slope, 2.0)
    linear = 2.0 * (slope * (intercept - center[1]) - center[0])
    constant = (
        math.pow(float(center[0]), 2.0)
        + math.pow(intercept - center[1], 2.0)
        - math.pow(float(distance), 2.0)
    )
    discriminant_root = math.sqrt(math.pow(linear, 2.0) - 4.0 * quadratic * constant)
    x1 = _round_away_from_zero(-(linear + discriminant_root) / (2.0 * quadratic))
    x2 = _round_away_from_zero(-(linear - discriminant_root) / (2.0 * quadratic))
    return (
        (x1, _round_away_from_zero(slope * x1 + intercept)),
        (x2, _round_away_from_zero(slope * x2 + intercept)),
    )


def _offset_segment_edges(
    point1: tuple[int, int],
    point2: tuple[int, int],
    distance: int,
) -> tuple[
    tuple[tuple[int, int], tuple[int, int]],
    tuple[tuple[int, int], tuple[int, int]],
]:
    source_line = _line_equation(point1, point2)
    moved1 = _points_moved_by_vector_from_center(
        point1,
        _perpendicular_line_equation(point1, source_line),
        distance,
    )
    moved2 = _points_moved_by_vector_from_center(
        point2,
        _perpendicular_line_equation(point2, source_line),
        distance,
    )
    return ((moved1[0], moved2[0]), (moved1[1], moved2[1]))


def _point_in_bounds(point: tuple[int, int], bounds: SchGeometryBounds) -> bool:
    return (
        bounds.left <= point[0] <= bounds.right
        and bounds.bottom <= point[1] <= bounds.top
    )


def _is_between_axis(first: int, second: int, value: int) -> bool:
    if first >= value or second <= value:
        if second < value:
            return first > value
        return False
    return True


def _segment_intersects_bounds(
    point1: tuple[int, int],
    point2: tuple[int, int],
    bounds: SchGeometryBounds,
) -> bool:
    if _point_in_bounds(point1, bounds) or _point_in_bounds(point2, bounds):
        return True
    return any(
        _segment_intersects_vertical_edge(point1, point2, bounds, edge_x)
        for edge_x in (bounds.left, bounds.right)
    ) or any(
        _segment_intersects_horizontal_edge(point1, point2, bounds, edge_y)
        for edge_y in (bounds.bottom, bounds.top)
    )


def _segment_intersects_vertical_edge(
    point1: tuple[int, int],
    point2: tuple[int, int],
    bounds: SchGeometryBounds,
    edge_x: int,
) -> bool:
    if not _is_between_axis(point1[0], point2[0], edge_x):
        return False
    if point1[1] == point2[1]:
        return bounds.bottom <= point1[1] <= bounds.top
    x_distance = float(abs(point1[0] - point2[0]))
    y_distance = float(abs(point1[1] - point2[1]))
    fraction = float(abs(edge_x - point1[0])) / x_distance
    intersect_y = float(point1[1]) + y_distance * fraction * (
        1.0 if point1[1] < point2[1] else -1.0
    )
    return float(bounds.bottom) <= intersect_y <= float(bounds.top)


def _segment_intersects_horizontal_edge(
    point1: tuple[int, int],
    point2: tuple[int, int],
    bounds: SchGeometryBounds,
    edge_y: int,
) -> bool:
    if not _is_between_axis(point1[1], point2[1], edge_y):
        return False
    if point1[0] == point2[0]:
        return bounds.left <= point1[0] <= bounds.right
    x_distance = float(abs(point1[0] - point2[0]))
    y_distance = float(abs(point1[1] - point2[1]))
    fraction = float(abs(edge_y - point1[1])) / y_distance
    intersect_x = float(point1[0]) + x_distance * fraction * (
        1.0 if point1[0] < point2[0] else -1.0
    )
    return float(bounds.left) <= intersect_x <= float(bounds.right)


def _label_background_bounds(own_bounds: SchGeometryBounds) -> SchGeometryBounds:
    return SchGeometryBounds(
        left=_unchecked_i32(own_bounds.left - 100_000),
        top=_unchecked_i32(own_bounds.top + 200_000),
        right=_unchecked_i32(own_bounds.right + 100_000),
        bottom=_unchecked_i32(own_bounds.bottom - 200_000),
    )


def _label_bundle_predecessors(
    own_bounds: SchGeometryBounds,
    document_bundles: Iterable[AltiumSchHarnessBundle | _HarnessBundlePaintShape]
    | _HarnessBundleSpatialIndex,
) -> tuple[str, ...]:
    if isinstance(document_bundles, _HarnessBundleSpatialIndex):
        return document_bundles.predecessors(own_bounds)
    background_bounds = _label_background_bounds(own_bounds)
    predecessors: list[str] = []
    for source_bundle in document_bundles:
        bundle = (
            _harness_bundle_paint_shape(source_bundle)
            if isinstance(source_bundle, AltiumSchHarnessBundle)
            else source_bundle
        )
        if not bundle.state_uid:
            continue
        half_width = _BUS_LINE_WIDTH_INTERNAL[bundle.line_width] // 2
        if any(
            _segment_intersects_bounds(edge1, edge2, background_bounds)
            for point1, point2 in zip(
                bundle.vertices, bundle.vertices[1:], strict=False
            )
            for edge1, edge2 in _offset_segment_edges(point1, point2, half_width)
        ):
            predecessors.append(bundle.state_uid)
    return tuple(predecessors)


def _harness_predecessor_state_uids(
    source_object: object,
    *,
    label_own_bounds: SchGeometryBounds | None = None,
    document_bundles: Iterable[AltiumSchHarnessBundle | _HarnessBundlePaintShape]
    | _HarnessBundleSpatialIndex = (),
) -> tuple[str, ...]:
    """Extract managed delayed-paint predecessors for harness-layout records."""
    if isinstance(source_object, AltiumSchHarnessSplice):
        return source_object.connected_wires_unique_ids
    if isinstance(source_object, AltiumSchHarnessLayoutConnectionPoint):
        return source_object.connected_bundles_unique_ids
    if isinstance(source_object, AltiumSchHarnessLayoutCovering):
        return tuple(item.unique_id for item in source_object.covered_items)
    if not isinstance(source_object, AltiumSchHarnessLayoutLabel):
        return ()
    if label_own_bounds is None:
        raise ValueError("harness layout label dependency extraction requires bounds")
    return _label_bundle_predecessors(label_own_bounds, document_bundles)


@dataclass(frozen=True)
class _DelayedPaintObject:
    key: str
    initial_state_uid: str
    group_id: str
    predecessor_state_uids: tuple[str, ...] = ()
    predecessor_source_indices: tuple[int, ...] = ()
    drawn_state_uid: str | None = None


@dataclass(frozen=True)
class _DelayedPaintPlan:
    delayed_registrations: tuple[str, ...]
    recursive_releases: tuple[str, ...]
    end_flush: tuple[str, ...]
    draw_calls: tuple[str, ...]
    create_group_after: tuple[tuple[str, str], ...]
    release_after: tuple[tuple[str, str], ...]
    group_order: tuple[str, ...]


@dataclass(frozen=True)
class _ComponentPaintObject:
    token: str
    kind: str
    name: str = ""


@dataclass(frozen=True)
class _ComponentVariantPaintState:
    is_multi_variant_export: bool
    document_show_alternate_symbols: bool
    project_show_alternate_symbols: bool
    has_variant_component: bool
    variant_component_present: bool
    variant_parameter_names: frozenset[str]
    variant_option_present: bool
    variant_graphics: str
    variant_text: bool


@dataclass(frozen=True, slots=True)
class _ManagedPaintRectangle:
    """System.Drawing rectangle values in signed internal schematic units."""

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_bounds(cls, bounds: SchGeometryBounds) -> _ManagedPaintRectangle:
        return cls(
            min(bounds.left, bounds.right),
            min(bounds.top, bounds.bottom),
            _abs_safe_i32(_unchecked_i32(bounds.left - bounds.right)),
            _abs_safe_i32(_unchecked_i32(bounds.bottom - bounds.top)),
        )

    def union(self, other: _ManagedPaintRectangle) -> _ManagedPaintRectangle:
        left, top = min(self.x, other.x), min(self.y, other.y)
        right = max(
            _unchecked_i32(self.x + self.width),
            _unchecked_i32(other.x + other.width),
        )
        bottom = max(
            _unchecked_i32(self.y + self.height),
            _unchecked_i32(other.y + other.height),
        )
        return _ManagedPaintRectangle(
            left, top, _unchecked_i32(right - left), _unchecked_i32(bottom - top)
        )


@dataclass(frozen=True, slots=True)
class _NonAccessibleBoundsSource:
    """An iterator-accepted source object's own bounds, before paint inflation."""

    own_bounds: SchGeometryBounds | None
    is_accessible: bool = False
    is_fsm_state: bool = False
    include_accessible: bool = False


def _reduce_non_accessible_children_bounds(
    location: tuple[int, int],
    sources: Iterable[_NonAccessibleBoundsSource],
    *,
    max_sources: int = 1_000_000,
) -> _ManagedPaintRectangle:
    """Reduce managed iterator-order own bounds without retaining source rows."""
    if max_sources < 0:
        raise ValueError("component bounds source limit cannot be negative")
    empty = _ManagedPaintRectangle()
    result = empty
    for count, source in enumerate(sources, 1):
        if count > max_sources:
            raise ValueError("component bounds source limit exceeded")
        if source.own_bounds is None or (
            source.is_accessible and not source.include_accessible
        ):
            continue
        rectangle = _ManagedPaintRectangle.from_bounds(source.own_bounds)
        # Rectangle.IsEmpty tests all four fields, not just zero area. A later
        # FSM state replaces previous bounds, including a nonempty accumulator.
        result = (
            rectangle
            if result == empty or source.is_fsm_state
            else result.union(rectangle)
        )
    if result != empty:
        return result
    return _ManagedPaintRectangle(
        _unchecked_i32(location[0] - 500_000),
        _unchecked_i32(location[1] + 500_000),
        500_000,
        500_000,
    )


class _GroupSequence:
    """Managed-style unique sibling groups with constant-time move-after."""

    def __init__(self) -> None:
        self._group_ids: list[str] = []
        self._previous_nodes: list[int | None] = []
        self._next_nodes: list[int | None] = []
        self._nodes_by_group_id: dict[str, int] = {}
        self._head: int | None = None
        self._tail: int | None = None

    def append(self, group_id: str) -> int:
        if not group_id:
            raise ValueError("paint group IDs must be resolved before planning")
        existing = self._nodes_by_group_id.get(group_id)
        if existing is not None:
            return existing
        node = len(self._group_ids)
        self._group_ids.append(group_id)
        self._previous_nodes.append(self._tail)
        self._next_nodes.append(None)
        self._nodes_by_group_id[group_id] = node
        if self._tail is None:
            self._head = node
        else:
            self._next_nodes[self._tail] = node
        self._tail = node
        return node

    def insert_after(self, anchor: int, group_id: str) -> int:
        if not group_id:
            raise ValueError("paint group IDs must be resolved before planning")
        node = self._nodes_by_group_id.get(group_id)
        if node == anchor:
            return anchor
        if node is None:
            node = len(self._group_ids)
            self._group_ids.append(group_id)
            self._previous_nodes.append(None)
            self._next_nodes.append(None)
            self._nodes_by_group_id[group_id] = node
        else:
            previous = self._previous_nodes[node]
            following = self._next_nodes[node]
            if previous is None:
                self._head = following
            else:
                self._next_nodes[previous] = following
            if following is None:
                self._tail = previous
            else:
                self._previous_nodes[following] = previous

        following = self._next_nodes[anchor]
        self._previous_nodes[node] = anchor
        self._next_nodes[node] = following
        self._next_nodes[anchor] = node
        if following is None:
            self._tail = node
        else:
            self._previous_nodes[following] = node
        return node

    def value(self, node: int) -> str:
        return self._group_ids[node]

    def node(self, group_id: str) -> int | None:
        return self._nodes_by_group_id.get(group_id)

    def values(self) -> tuple[str, ...]:
        values: list[str] = []
        node = self._head
        while node is not None:
            values.append(self._group_ids[node])
            node = self._next_nodes[node]
        return tuple(values)


class _Transparency(Enum):
    SET = "set"
    NOT_SET = "not_set"
    NOT_POSSIBLE = "not_possible"


class _DelayedPaintPlanner:
    def __init__(self, source_objects: Sequence[_DelayedPaintObject]) -> None:
        self.source_objects = source_objects
        self.groups = _GroupSequence()
        self.current_state_uids = [row.initial_state_uid for row in source_objects]
        self.drawn_state_uids: set[str] = set()
        self.dependencies_by_state_uid: dict[str, Counter[str]] = {}
        self.remaining_by_state_uid: dict[str, int] = {}
        self.active_delayed_by_state_uid: dict[str, dict[int, None]] = defaultdict(dict)
        self.state_uids_by_dependency: dict[str, set[str]] = defaultdict(set)
        self.active_dependency_uids: set[str] = set()
        self.delayed_indices: list[int] = []
        self.released_indices: set[int] = set()
        self.delayed_registrations: list[str] = []
        self.recursive_releases: list[str] = []
        self.end_flush: list[str] = []
        self.draw_calls: list[str] = []
        self.create_group_after: list[tuple[str, str]] = []
        self.release_after: list[tuple[str, str]] = []

    def plan(self) -> _DelayedPaintPlan:
        for source_index, source_object in enumerate(self.source_objects):
            self._accept_source(source_index, source_object)
        for source_index in self.delayed_indices:
            if source_index not in self.released_indices:
                self._flush(source_index)
        return _DelayedPaintPlan(
            delayed_registrations=tuple(self.delayed_registrations),
            recursive_releases=tuple(self.recursive_releases),
            end_flush=tuple(self.end_flush),
            draw_calls=tuple(self.draw_calls),
            create_group_after=tuple(self.create_group_after),
            release_after=tuple(self.release_after),
            group_order=self.groups.values(),
        )

    def _accept_source(
        self, source_index: int, source_object: _DelayedPaintObject
    ) -> None:
        remaining = self._remaining_predecessors(source_object)
        if not remaining:
            self._draw(source_index)
            return
        self.delayed_indices.append(source_index)
        self.delayed_registrations.append(source_object.key)
        state_uid = source_object.initial_state_uid
        if not state_uid:
            return
        self.active_delayed_by_state_uid[state_uid][source_index] = None
        if state_uid in self.dependencies_by_state_uid:
            return
        counts = Counter(remaining)
        self.dependencies_by_state_uid[state_uid] = counts
        self.remaining_by_state_uid[state_uid] = len(remaining)
        for dependency_uid in counts:
            self.state_uids_by_dependency[dependency_uid].add(state_uid)
            self.active_dependency_uids.add(dependency_uid)

    def _remaining_predecessors(
        self, source_object: _DelayedPaintObject
    ) -> tuple[str, ...]:
        predecessor_state_uids = (
            *source_object.predecessor_state_uids,
            *(
                self.current_state_uids[index]
                for index in source_object.predecessor_source_indices
            ),
        )
        return tuple(
            uid for uid in predecessor_state_uids if uid not in self.drawn_state_uids
        )

    def _draw(self, source_index: int, after_node: int | None = None) -> None:
        pending_draws: list[tuple[int, int | None, str | None, bool]] = [
            (source_index, after_node, None, False)
        ]
        while pending_draws:
            current_index, current_after_node, anchor_key, is_release = (
                pending_draws.pop()
            )
            source_object = self.source_objects[current_index]
            if is_release:
                self.recursive_releases.append(source_object.key)
                if anchor_key is None:
                    raise RuntimeError("released paint object is missing its anchor")
                self.release_after.append((anchor_key, source_object.key))
            group_node = self._place_group(source_object, current_after_node)
            self.draw_calls.append(source_object.key)
            state_uid = source_object.drawn_state_uid
            if state_uid is None:
                state_uid = source_object.initial_state_uid
            self.current_state_uids[current_index] = state_uid
            if not state_uid:
                continue
            self.drawn_state_uids.add(state_uid)
            for delayed_index in self._ready_after(state_uid):
                # LIFO execution reverses ready registration order, while each
                # insertion after the same anchor restores registration order.
                self.released_indices.add(delayed_index)
                pending_draws.append(
                    (delayed_index, group_node, source_object.key, True)
                )

    def _place_group(
        self, source_object: _DelayedPaintObject, after_node: int | None
    ) -> int:
        if after_node is None:
            return self.groups.append(source_object.group_id)
        anchor_group_id = self.groups.value(after_node)
        group_node = self.groups.insert_after(after_node, source_object.group_id)
        self.create_group_after.append((anchor_group_id, source_object.group_id))
        return group_node

    def _ready_after(self, state_uid: str) -> tuple[int, ...]:
        if state_uid not in self.active_dependency_uids:
            return ()
        self.active_dependency_uids.remove(state_uid)
        ready = [
            released_index
            for delayed_state_uid in self.state_uids_by_dependency[state_uid]
            if (
                released_index := self._consume_dependency(delayed_state_uid, state_uid)
            )
            is not None
        ]
        ready.sort()
        return tuple(ready)

    def _consume_dependency(
        self, delayed_state_uid: str, dependency_uid: str
    ) -> int | None:
        counts = self.dependencies_by_state_uid.get(delayed_state_uid)
        if counts is None or dependency_uid not in counts:
            return None
        aliases = self.active_delayed_by_state_uid[delayed_state_uid]
        removed_count = min(counts[dependency_uid], len(aliases))
        counts[dependency_uid] -= removed_count
        self.remaining_by_state_uid[delayed_state_uid] -= removed_count
        if not counts[dependency_uid]:
            del counts[dependency_uid]
        if self.remaining_by_state_uid[delayed_state_uid]:
            return None
        released_index = next(islice(aliases, removed_count - 1, None))
        del aliases[released_index]
        del self.dependencies_by_state_uid[delayed_state_uid]
        del self.remaining_by_state_uid[delayed_state_uid]
        return released_index

    def _flush(self, source_index: int) -> None:
        source_object = self.source_objects[source_index]
        self.end_flush.append(source_object.key)
        self.draw_calls.append(source_object.key)
        self.groups.append(source_object.group_id)


def _plan_delayed_paint_order(
    source_objects: Sequence[_DelayedPaintObject],
) -> _DelayedPaintPlan:
    """Plan the managed delayed-cache calls without recursive Python frames."""
    source_count = len(source_objects)
    if any(
        predecessor_index < 0 or predecessor_index >= source_count
        for source_object in source_objects
        for predecessor_index in source_object.predecessor_source_indices
    ):
        raise ValueError("paint predecessor source index is out of range")
    return _DelayedPaintPlanner(source_objects).plan()


def _show_alternate_component_symbols(state: _ComponentVariantPaintState) -> bool:
    if state.is_multi_variant_export:
        return state.project_show_alternate_symbols
    return state.document_show_alternate_symbols


def _component_variant_objects(
    original: Sequence[_ComponentPaintObject],
    alternate: Sequence[_ComponentPaintObject],
    keep_original_kinds: frozenset[str],
    state: _ComponentVariantPaintState,
) -> list[_ComponentPaintObject]:
    if not (
        state.has_variant_component
        and state.variant_component_present
        and _show_alternate_component_symbols(state)
    ):
        return list(original)
    return [
        *(item for item in alternate if item.kind not in keep_original_kinds),
        *(item for item in original if item.kind in keep_original_kinds),
    ]


def _component_variant_overlays(
    state: _ComponentVariantPaintState,
) -> tuple[str, ...]:
    overlays: list[str] = []
    if state.has_variant_component and not state.variant_component_present:
        overlays.append("overlay:missing")
    if not state.variant_option_present:
        return tuple(overlays)
    if state.variant_graphics != "none":
        overlays.append(f"overlay:{state.variant_graphics}")
    if state.variant_text:
        overlays.append("overlay:text")
    return tuple(overlays)


def _plan_component_variant_paint(
    original: Sequence[_ComponentPaintObject],
    alternate: Sequence[_ComponentPaintObject],
    keep_original_kinds: frozenset[str],
    state: _ComponentVariantPaintState,
) -> tuple[str, ...]:
    """Return the managed component primitive and overlay phase tokens."""
    objects = _component_variant_objects(
        original,
        alternate,
        keep_original_kinds,
        state,
    )
    if state.has_variant_component and state.variant_component_present:
        parameter_names = {
            dotnet_ordinal_ignore_case_key(name)
            for name in state.variant_parameter_names
        }
        objects = [
            item
            for item in objects
            if item.kind != "parameter"
            or dotnet_ordinal_ignore_case_key(item.name) in parameter_names
        ]
    return (
        *(item.token for item in objects),
        *_component_variant_overlays(state),
    )


def _transparency(source_object: object) -> _Transparency:
    if isinstance(
        source_object,
        (AltiumSchEllipse, AltiumSchRectangle, AltiumSchPolygon),
    ):
        return (
            _Transparency.SET
            if bool(getattr(source_object, "transparent", False))
            else _Transparency.NOT_SET
        )
    return _Transparency.NOT_POSSIBLE


@dataclass
class _TransparencySortBudget:
    remaining: int | None

    def __post_init__(self) -> None:
        if self.remaining is not None and (
            type(self.remaining) is not int or self.remaining < 0
        ):
            raise ValueError(
                "transparency sort work limit must be a nonnegative integer"
            )

    def charge(self, work: int) -> None:
        if self.remaining is not None:
            if work > self.remaining:
                raise ValueError("transparency sort work limit exceeded")
            self.remaining -= work


def _sort_transparent_objects(
    source_objects: Sequence[object], *, max_work: int | None = None
) -> list[object]:
    budget = _TransparencySortBudget(max_work)
    budget.charge(len(source_objects))
    ordered = list(source_objects)
    position = len(ordered) - 1
    while position > 0:
        budget.charge(1)
        if _transparency(ordered[position]) is _Transparency.SET:
            target = position
            while (
                target > 0
                and _transparency(ordered[target - 1]) is _Transparency.NOT_POSSIBLE
            ):
                budget.charge(1)
                target -= 1
            scan = position
            while (
                scan < len(ordered)
                and _transparency(ordered[scan]) is _Transparency.SET
            ):
                # Python pop/insert each shifts the suffix. Charge before both
                # mutations; a reference cap alone cannot bound this source algorithm.
                budget.charge(2 * len(ordered) - scan - target - 1)
                ordered.insert(target, ordered.pop(scan))
                scan += 1
            position = target
        position -= 1
    return ordered


def order_component_children_by_source(
    owner: AltiumSchComponent,
    children: Sequence[object],
) -> list[object]:
    """Return filtered component children in the managed painter's draw order."""
    return _sort_transparent_objects(_component_bound_children(owner, children))


def _component_field_role(child: object) -> str | None:
    if isinstance(child, AltiumSchDesignator):
        return "designator"
    if (
        isinstance(child, AltiumSchParameter)
        and dotnet_ordinal_ignore_case_key(child.name) == "COMMENT"
    ):
        return "comment"
    return None


def _component_bound_children(
    owner: AltiumSchComponent, children: Sequence[object]
) -> list[object]:
    """Resolve managed field slots before any visibility/part filtering."""
    fields = _component_field_objects(owner, children)
    return [
        child for child in children if _component_field_role(child) is None
    ] + fields


def _component_field_objects(
    owner: AltiumSchComponent, children: Sequence[object]
) -> list[object]:
    fields = _component_bound_field_slots(children)
    # UpdateOwner replaces these slots in import order; superseded candidates
    # are not ordinary list children and must never be resurrected by filters.
    return [fields[role] for role in ("designator", "comment") if role in fields]


def _owner_field_objects(
    owner: object,
    children: Sequence[object],
    *,
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> list[object]:
    if isinstance(owner, AltiumSchComponent):
        return _component_field_objects(owner, children)
    if isinstance(owner, (AltiumSchSheetSymbol, AltiumSchHarnessConnector)):
        fields = _hierarchy_bound_field_slots(
            owner, children, parent_by_source_id=parent_by_source_id
        )
        return [
            fields[role]
            for role in ("sheet_name", "file_name", "type_label")
            if role in fields
        ]
    if isinstance(owner, AltiumSchHarnessBundle):
        return _harness_bundle_field_objects(children)
    return _harness_named_owner_field_objects(owner, children)


def _bound_or_last_owner_field(
    bound_field: object | None,
    children: Sequence[object],
    field_type: type[object],
) -> object | None:
    if bound_field in children:
        return bound_field
    return next(
        (child for child in reversed(children) if isinstance(child, field_type)),
        None,
    )


def _is_owner_field_candidate(owner: object, child: object) -> bool:
    if isinstance(owner, AltiumSchSheetSymbol):
        return isinstance(child, (AltiumSchSheetName, AltiumSchFileName))
    return isinstance(owner, AltiumSchHarnessConnector) and isinstance(
        child,
        AltiumSchHarnessType,
    )


def _harness_bundle_field_objects(children: Sequence[object]) -> list[object]:
    fields: list[object | None] = [None, None, None, None]
    for child in reversed(children):
        if fields[0] is None and isinstance(child, AltiumSchDesignator):
            fields[0] = child
        elif isinstance(child, AltiumSchParameter):
            slot = _harness_bundle_parameter_field_slot(child)
            if slot is not None and fields[slot] is None:
                fields[slot] = child
    return [field for field in fields if field is not None]


def _harness_bundle_parameter_field_slot(parameter: AltiumSchParameter) -> int | None:
    role = str(
        getattr(parameter, "_harness_bundle_field_candidate_role", "")
        or getattr(parameter, "_harness_bundle_field_role", "")
        or ""
    )
    if role:
        return {"comment": 1, "description": 2, "length": 3}.get(role)
    return {
        "comment": 1,
        "description": 2,
        "lengthparameter": 3,
    }.get(str(getattr(parameter, "name", "") or "").casefold())


def _harness_bundle_field_role(child: object) -> str | None:
    if isinstance(child, AltiumSchDesignator):
        return "designator"
    if not isinstance(child, AltiumSchParameter):
        return None
    slot = _harness_bundle_parameter_field_slot(child)
    if slot is None:
        return None
    return ("designator", "comment", "description", "length")[slot]


def order_harness_bundle_children_by_source(
    children: Sequence[object],
) -> list[object]:
    """Match object-list order followed by the four bundle field slots."""
    fields = _harness_bundle_field_objects(children)
    candidate_ids = {
        id(child) for child in children if _harness_bundle_field_role(child) is not None
    }
    return [child for child in children if id(child) not in candidate_ids] + fields


def _harness_named_owner_field_objects(
    owner: object,
    children: Sequence[object],
) -> list[object]:
    roles = _harness_named_owner_field_roles(owner)
    selected: dict[str, object] = {}
    for child in reversed(children):
        role = _harness_named_owner_field_role(owner, child)
        if role is not None and role not in selected:
            selected[role] = child
    return [selected[role] for role in roles if role in selected]


def _harness_named_owner_field_roles(owner: object) -> tuple[str, ...]:
    if isinstance(
        owner,
        (AltiumSchHarnessLayoutLabel, AltiumSchHarnessLayoutCovering),
    ):
        return ("designator", "comment")
    if isinstance(
        owner,
        (AltiumSchHarnessSplice, AltiumSchHarnessLayoutConnectionPoint),
    ):
        return ("designator",)
    return ()


def _harness_named_owner_field_role(owner: object, child: object) -> str | None:
    roles = _harness_named_owner_field_roles(owner)
    if "designator" in roles and isinstance(child, AltiumSchDesignator):
        return "designator"
    if (
        "comment" in roles
        and isinstance(child, AltiumSchParameter)
        and str(getattr(child, "name", "") or "").casefold() == "comment"
    ):
        return "comment"
    return None


def order_harness_layout_children_by_source(
    owner: object,
    children: Sequence[object],
    *,
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> list[object]:
    """Match managed object-list order followed by exact owner field slots."""
    if isinstance(owner, AltiumSchHarnessBundle):
        return order_harness_bundle_children_by_source(children)
    if isinstance(owner, (AltiumSchHarnessConnector, AltiumSchSheetSymbol)):
        fields = _owner_field_objects(
            owner, children, parent_by_source_id=parent_by_source_id
        )
        return [
            child for child in children if not _is_owner_field_candidate(owner, child)
        ] + fields
    fields = _harness_named_owner_field_objects(owner, children)
    candidate_ids = {
        id(child)
        for child in children
        if _harness_named_owner_field_role(owner, child) is not None
    }
    return [child for child in children if id(child) not in candidate_ids] + fields


def _root_participates_in_order(
    root: object,
    included_source_ids: set[int],
    *,
    sort_root_transparency: bool,
    eligible_source_ids: set[int] | None,
) -> bool:
    root_id = id(root)
    if root_id in included_source_ids:
        return True
    return (
        sort_root_transparency
        and eligible_source_ids is not None
        and root_id in eligible_source_ids
    )


def _semantic_source_objects(
    source_objects: Sequence[object],
    included_source_ids: set[int],
    *,
    sort_root_transparency: bool,
    eligible_source_ids: set[int] | None,
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> list[object]:
    source_ids = {id(source_object) for source_object in source_objects}
    children_by_owner: dict[int, list[object]] = {}
    roots: list[object] = []
    for source_object in source_objects:
        if (
            eligible_source_ids is not None
            and id(source_object) not in eligible_source_ids
        ):
            continue
        parent = (
            parent_by_source_id.get(id(source_object))
            if parent_by_source_id is not None
            else getattr(source_object, "parent", None)
        )
        if parent is None or id(parent) not in source_ids:
            roots.append(source_object)
            continue
        children_by_owner.setdefault(id(parent), []).append(source_object)

    ordered: list[object] = []
    effective_roots = [
        root
        for root in roots
        if _root_participates_in_order(
            root,
            included_source_ids,
            sort_root_transparency=sort_root_transparency,
            eligible_source_ids=eligible_source_ids,
        )
    ]
    if sort_root_transparency:
        effective_roots = _sort_transparent_objects(effective_roots)
    stack = list(reversed(effective_roots))
    while stack:
        source_object = stack.pop()
        if id(source_object) in included_source_ids:
            ordered.append(source_object)
        source_children = children_by_owner.get(id(source_object), [])
        effective_children = _semantic_ordered_children(
            source_object,
            source_children,
            included_source_ids,
            parent_by_source_id=parent_by_source_id,
        )
        stack.extend(reversed(effective_children))
    return ordered


def _semantic_ordered_children(
    source_object: object,
    source_children: Sequence[object],
    included_source_ids: set[int],
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> list[object]:
    if isinstance(source_object, AltiumSchComponent):
        source_children = _component_bound_children(source_object, source_children)
    children = _semantic_visible_children(
        source_object,
        source_children,
        included_source_ids,
    )
    fields = _owner_field_objects(
        source_object, children, parent_by_source_id=parent_by_source_id
    )
    field_ids = {id(field) for field in fields}
    effective_children = [
        child for child in children if id(child) not in field_ids
    ] + fields
    if isinstance(source_object, AltiumSchComponent):
        return _sort_transparent_objects(effective_children)
    return effective_children


def _semantic_visible_children(
    source_object: object,
    source_children: Sequence[object],
    included_source_ids: set[int],
) -> list[object]:
    if isinstance(source_object, AltiumSchComponent):
        eligible_child_ids = {
            id(child) for _, child in source_object._ordered_geometry_children()
        }
        children = [
            child
            for child in source_children
            if id(child) in included_source_ids or id(child) in eligible_child_ids
        ]
    else:
        return [child for child in source_children if id(child) in included_source_ids]
    return children


def _generated_source_key(unique_id: str | None) -> _GeneratedSourceKey | None:
    match = _GENERATED_RECORD_ID.fullmatch(str(unique_id or ""))
    if match is not None:
        return match.group(1), int(match.group(2))
    template_child_match = _GENERATED_TEMPLATE_CHILD_ID.fullmatch(str(unique_id or ""))
    if template_child_match is not None:
        return "TPLC", int(template_child_match.group(1))
    return None


def _source_geometry_kind(source_object: object) -> str:
    class_name = type(source_object).__name__
    short_name = class_name.removeprefix("AltiumSch").replace("_", "").casefold()
    return _SOURCE_KIND_OVERRIDES.get(short_name, short_name)


def _normalized_record_index(source_object: object, fallback: int) -> int:
    raw_index = getattr(source_object, "_record_index", None)
    if raw_index is None:
        return fallback
    try:
        return int(raw_index)
    except (TypeError, ValueError):
        return fallback


def _source_generated_key(source_object: object) -> _GeneratedSourceKey | None:
    parent = getattr(source_object, "parent", None)
    if parent is not None and _source_geometry_kind(parent) == "template":
        return "TPLC", _normalized_record_index(source_object, 0)
    prefix = _GENERATED_SOURCE_PREFIXES.get(_source_geometry_kind(source_object))
    if prefix is None:
        return None
    # The geometry emitters use zero when an authored object has not yet been
    # assigned a persisted record index.
    return prefix, _normalized_record_index(source_object, 0)


def _generated_record_keys(
    records: Sequence[SchGeometryRecord],
) -> set[_GeneratedSourceKey]:
    return {
        key
        for record in records
        if (key := _generated_source_key(record.unique_id)) is not None
    }


def _identified_source_ids(
    source_objects: Sequence[object],
    record_ids: set[str],
    generated_keys: set[_GeneratedSourceKey],
) -> set[int]:
    matched: set[int] = set()
    for source_object in source_objects:
        unique_id = str(getattr(source_object, "unique_id", "") or "")
        if (
            unique_id in record_ids
            or _source_generated_key(source_object) in generated_keys
        ):
            matched.add(id(source_object))
    return matched


def _anonymous_source_ids(
    source_objects: Sequence[object],
    excluded_ids: set[int],
    anonymous_counts: Counter[str],
    eligible_source_ids: set[int] | None,
) -> set[int]:
    matched: set[int] = set()
    for source_object in source_objects:
        if id(source_object) in excluded_ids:
            continue
        if (
            eligible_source_ids is not None
            and id(source_object) not in eligible_source_ids
        ):
            continue
        unique_id = str(getattr(source_object, "unique_id", "") or "")
        kind = _source_geometry_kind(source_object)
        if unique_id or anonymous_counts[kind] <= 0:
            continue
        matched.add(id(source_object))
        anonymous_counts[kind] -= 1
    return matched


def _included_source_ids(
    source_objects: Sequence[object],
    records: Sequence[SchGeometryRecord],
    eligible_source_ids: set[int] | None,
) -> tuple[set[int], set[_GeneratedSourceKey]]:
    record_ids = {record.unique_id for record in records if record.unique_id}
    generated_keys = _generated_record_keys(records)
    anonymous_counts = Counter(
        str(record.kind or "") for record in records if not record.unique_id
    )
    included = _identified_source_ids(source_objects, record_ids, generated_keys)
    included.update(
        _anonymous_source_ids(
            source_objects,
            included,
            anonymous_counts,
            eligible_source_ids,
        )
    )
    return included, generated_keys


def _tagged_source_object(
    source_objects: Sequence[object], record: SchGeometryRecord
) -> object | None:
    source_index = record.source_object_index
    if source_index is None or not 0 <= source_index < len(source_objects):
        return None
    return source_objects[source_index]


def _index_semantic_objects(
    semantic_objects: Sequence[object],
    generated_source_keys: set[_GeneratedSourceKey],
) -> tuple[
    dict[str, int],
    dict[_GeneratedSourceKey, int],
    dict[str, deque[int]],
]:
    positions: dict[str, int] = {}
    generated_positions: dict[_GeneratedSourceKey, int] = {}
    anonymous_by_kind: dict[str, deque[int]] = defaultdict(deque)
    for position, source_object in enumerate(semantic_objects):
        unique_id = str(getattr(source_object, "unique_id", "") or "")
        if unique_id:
            positions.setdefault(unique_id, position)
        generated_key = _source_generated_key(source_object)
        if generated_key is not None:
            generated_positions.setdefault(generated_key, position)
        if not unique_id and generated_key not in generated_source_keys:
            anonymous_by_kind[_source_geometry_kind(source_object)].append(position)
    return positions, generated_positions, anonymous_by_kind


def _match_anonymous_record_positions(
    records: Sequence[SchGeometryRecord],
    anonymous_by_kind: dict[str, deque[int]],
) -> dict[int, int]:
    anonymous_positions: dict[int, int] = {}
    for record_position, record in enumerate(records):
        candidates = anonymous_by_kind[str(record.kind or "")]
        if not record.unique_id and candidates:
            anonymous_positions[record_position] = candidates.popleft()
    return anonymous_positions


def _position_maps(
    source_objects: Sequence[object],
    semantic_objects: Sequence[object],
    generated_source_keys: set[_GeneratedSourceKey],
    records: Sequence[SchGeometryRecord],
) -> tuple[
    dict[str, int],
    dict[_GeneratedSourceKey, int],
    dict[int, int],
]:
    positions, generated_positions, anonymous_by_kind = _index_semantic_objects(
        semantic_objects,
        generated_source_keys,
    )
    semantic_positions = {
        id(source_object): position
        for position, source_object in enumerate(semantic_objects)
    }
    tagged_positions = {
        record_position: semantic_positions[id(source_object)]
        for record_position, record in enumerate(records)
        if (source_object := _tagged_source_object(source_objects, record)) is not None
        and id(source_object) in semantic_positions
    }
    untagged_records = [
        record
        for record in records
        if _tagged_source_object(source_objects, record) is None
    ]
    untagged_positions = [
        record_position
        for record_position, record in enumerate(records)
        if _tagged_source_object(source_objects, record) is None
    ]
    anonymous_positions = _match_anonymous_record_positions(
        untagged_records,
        anonymous_by_kind,
    )
    tagged_positions.update(
        {
            untagged_positions[record_position]: source_position
            for record_position, source_position in anonymous_positions.items()
        }
    )
    return (
        positions,
        generated_positions,
        tagged_positions,
    )


def _source_positions(
    source_objects: Sequence[object],
    records: Sequence[SchGeometryRecord],
    *,
    sort_root_transparency: bool,
    eligible_source_ids: set[int] | None,
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> tuple[
    dict[str, int],
    dict[_GeneratedSourceKey, int],
    dict[int, int],
]:
    tagged_source_ids = {
        id(source_object)
        for record in records
        if (source_object := _tagged_source_object(source_objects, record)) is not None
    }
    untagged_records = [
        record
        for record in records
        if _tagged_source_object(source_objects, record) is None
    ]
    included_ids, generated_source_keys = _included_source_ids(
        source_objects,
        untagged_records,
        eligible_source_ids,
    )
    included_ids.update(tagged_source_ids)
    generated_source_keys.update(_generated_record_keys(records))
    semantic_objects = _semantic_source_objects(
        source_objects,
        included_ids,
        sort_root_transparency=sort_root_transparency,
        eligible_source_ids=eligible_source_ids,
        parent_by_source_id=parent_by_source_id,
    )
    return _position_maps(
        source_objects,
        semantic_objects,
        generated_source_keys,
        records,
    )


_DELAYED_HARNESS_TYPES = (
    AltiumSchHarnessSplice,
    AltiumSchHarnessLayoutLabel,
    AltiumSchHarnessLayoutConnectionPoint,
    AltiumSchHarnessLayoutCovering,
)


def _root_sources_by_id(source_objects: Sequence[object]) -> dict[int, object]:
    """Resolve source roots once, including deep parent chains."""
    source_ids = {id(source_object) for source_object in source_objects}
    roots: dict[int, object] = {}
    for source_object in source_objects:
        if id(source_object) in roots:
            continue
        path: list[object] = []
        current = source_object
        visited: set[int] = set()
        while id(current) not in roots and id(current) not in visited:
            path.append(current)
            visited.add(id(current))
            parent = getattr(current, "parent", None)
            if parent is None or id(parent) not in source_ids:
                break
            current = parent
        root = roots.get(id(current), current)
        for member in path:
            roots[id(member)] = root
    return roots


def _unique_sources_by_uid(source_objects: Sequence[object]) -> dict[str, object]:
    sources: dict[str, object] = {}
    duplicates: set[str] = set()
    for source_object in source_objects:
        unique_id = str(getattr(source_object, "unique_id", "") or "")
        if not unique_id:
            continue
        if unique_id in sources:
            duplicates.add(unique_id)
            continue
        sources[unique_id] = source_object
    for unique_id in duplicates:
        sources.pop(unique_id, None)
    return sources


def _record_source_object(
    source_objects: Sequence[object],
    sources_by_uid: dict[str, object],
    record: SchGeometryRecord,
) -> object | None:
    tagged = _tagged_source_object(source_objects, record)
    if tagged is not None:
        return tagged
    return sources_by_uid.get(str(record.unique_id or ""))


def _record_blocks(
    records: Sequence[SchGeometryRecord],
    source_objects: Sequence[object],
) -> tuple[list[object | None], list[list[SchGeometryRecord]]]:
    roots_by_id = _root_sources_by_id(source_objects)
    sources_by_uid = _unique_sources_by_uid(source_objects)
    block_sources: list[object | None] = []
    block_records: list[list[SchGeometryRecord]] = []
    block_by_source_id: dict[int, int] = {}
    for source_object in source_objects:
        root = roots_by_id[id(source_object)]
        if id(root) in block_by_source_id:
            continue
        block_by_source_id[id(root)] = len(block_records)
        block_sources.append(root)
        block_records.append([])
    for record in records:
        source = _record_source_object(source_objects, sources_by_uid, record)
        root = roots_by_id[id(source)] if source is not None else None
        block_index = block_by_source_id.get(id(root)) if root is not None else None
        if block_index is None:
            block_index = len(block_records)
            block_sources.append(root)
            block_records.append([])
            if root is not None:
                block_by_source_id[id(root)] = block_index
        block_records[block_index].append(record)
    return block_sources, block_records


def _document_bundle_index(
    block_sources: Sequence[object | None],
    source_objects: Sequence[object],
) -> _HarnessBundleSpatialIndex:
    if not any(
        isinstance(source, AltiumSchHarnessLayoutLabel) for source in block_sources
    ):
        return _HarnessBundleSpatialIndex(())
    roots_by_id = _root_sources_by_id(source_objects)
    bundles: list[AltiumSchHarnessBundle] = []
    seen: set[int] = set()
    for source_object in source_objects:
        root = roots_by_id[id(source_object)]
        if not isinstance(root, AltiumSchHarnessBundle) or id(root) in seen:
            continue
        seen.add(id(root))
        bundles.append(root)
    return _HarnessBundleSpatialIndex(bundles)


def _block_state_uid(
    source: object | None,
    records: Sequence[SchGeometryRecord],
) -> str:
    raw_state_uid = (
        getattr(source, "unique_id", "")
        if source is not None
        else records[0].unique_id or ""
    )
    return str(raw_state_uid or "")


def _block_predecessors(
    source: object | None,
    bundle_index: _HarnessBundleSpatialIndex,
    label_own_bounds: dict[int, SchGeometryBounds],
) -> tuple[str, ...]:
    if source is None or not isinstance(source, _DELAYED_HARNESS_TYPES):
        return ()
    label_bounds = label_own_bounds.get(id(source))
    return _harness_predecessor_state_uids(
        source,
        label_own_bounds=label_bounds,
        document_bundles=bundle_index,
    )


def _delayed_paint_objects_for_blocks(
    block_sources: Sequence[object | None],
    block_records: Sequence[Sequence[SchGeometryRecord]],
    bundle_index: _HarnessBundleSpatialIndex,
    label_own_bounds: dict[int, SchGeometryBounds],
) -> list[_DelayedPaintObject]:
    return [
        _DelayedPaintObject(
            key=f"block:{block_index}",
            initial_state_uid=_block_state_uid(source, records),
            group_id=f"block:{block_index}",
            predecessor_state_uids=_block_predecessors(
                source,
                bundle_index,
                label_own_bounds,
            ),
        )
        for block_index, (source, records) in enumerate(
            zip(block_sources, block_records, strict=True)
        )
    ]


def _harness_delayed_record_order(
    records: Sequence[SchGeometryRecord],
    source_objects: Sequence[object],
    *,
    label_own_bounds: dict[int, SchGeometryBounds] | None = None,
) -> list[SchGeometryRecord]:
    """Exercise delayed block placement with caller-supplied source-state bounds."""
    block_sources, block_records = _record_blocks(records, source_objects)
    if not any(isinstance(source, _DELAYED_HARNESS_TYPES) for source in block_sources):
        return list(records)
    bundle_index = _document_bundle_index(block_sources, source_objects)
    paint_objects = _delayed_paint_objects_for_blocks(
        block_sources,
        block_records,
        bundle_index,
        label_own_bounds or {},
    )
    plan = _plan_delayed_paint_order(paint_objects)
    blocks_by_key = {
        f"block:{index}": block for index, block in enumerate(block_records)
    }
    return [record for key in plan.group_order for record in blocks_by_key[key]]


def _runtime_harness_events(
    source_objects: Sequence[object],
    delayed_indexes: set[int],
    predecessors_by_source_index: dict[int, tuple[str, ...]],
    group_ids_by_source_index: Mapping[int, str],
    *,
    show_template_graphics: bool,
    source_admission: _SourceAdmission = _SourceAdmission(),
) -> tuple[list[_DelayedPaintObject], dict[str, int]]:
    dependency_uids = {
        dependency_uid
        for source_index in delayed_indexes
        for dependency_uid in predecessors_by_source_index[source_index]
    }
    events: list[_DelayedPaintObject] = []
    source_index_by_key: dict[str, int] = {}
    for source_index, source_object in enumerate(source_objects):
        if not source_admission.admits(source_object):
            continue
        state_uid = str(getattr(source_object, "unique_id", "") or "")
        if not _runtime_source_is_managed(
            source_object,
            show_template_graphics=show_template_graphics,
        ) or (source_index not in delayed_indexes and state_uid not in dependency_uids):
            continue
        key = f"runtime:{source_index}"
        group_id = group_ids_by_source_index.get(source_index, state_uid)
        if not group_id:
            group_id = f"AMUID{source_index:08X}"
        events.append(
            _DelayedPaintObject(
                key=key,
                initial_state_uid=state_uid,
                group_id=group_id,
                predecessor_state_uids=predecessors_by_source_index.get(
                    source_index,
                    (),
                ),
                drawn_state_uid=state_uid or group_id,
            )
        )
        source_index_by_key[key] = source_index
    return events, source_index_by_key


def _runtime_group_ids_by_source_index(
    records: Sequence[SchGeometryRecord],
    *,
    native_svg_export: bool,
) -> dict[int, str]:
    group_ids: dict[int, str] = {}
    for record in records:
        source_index = record.source_object_index
        if source_index is None or source_index in group_ids:
            continue
        render_group_id = str(record.render_group_id or record.unique_id or "")
        group_id = render_group_id
        if not native_svg_export:
            group_id = str(
                record.render_group_identity
                or (f"{record.object_id}\0{render_group_id}" if render_group_id else "")
            )
        if group_id:
            group_ids[source_index] = group_id
    return group_ids


def _runtime_label_bounds(
    records: Sequence[SchGeometryRecord],
    label_indexes: set[int],
) -> dict[int, SchGeometryBounds]:
    bounds_by_index: dict[int, SchGeometryBounds] = {}
    for record in records:
        source_index = record.source_object_index
        if (
            source_index in label_indexes
            and source_index not in bounds_by_index
            and record.bounds is not None
        ):
            bounds_by_index[source_index] = record.bounds
    return bounds_by_index


def _runtime_bundle_index(
    source_objects: Sequence[object],
    *,
    enabled: bool,
    source_admission: _SourceAdmission = _SourceAdmission(),
) -> _HarnessBundleSpatialIndex:
    if not enabled:
        return _HarnessBundleSpatialIndex(())
    return _HarnessBundleSpatialIndex(
        source_object
        for source_object in source_admission.admitted(source_objects)
        if type(source_object) is AltiumSchHarnessBundle
        and getattr(source_object, "parent", None) is None
    )


def _runtime_source_is_managed(
    source_object: object,
    *,
    show_template_graphics: bool,
) -> bool:
    if getattr(source_object, "parent", None) is not None or not isinstance(
        source_object,
        SchGraphicalObject,
    ):
        return False
    return show_template_graphics or _source_geometry_kind(source_object) != "template"


def _runtime_release_children(
    plan: _DelayedPaintPlan,
) -> dict[str, tuple[str, ...]]:
    creation_order: dict[str, list[str]] = defaultdict(list)
    for anchor, delayed in plan.release_after:
        creation_order[anchor].append(delayed)
    return {
        anchor: tuple(reversed(children)) for anchor, children in creation_order.items()
    }


def _append_runtime_releases(
    output: list[SchGeometryRecord],
    anchor: str,
    release_children: dict[str, tuple[str, ...]],
    records_by_key: dict[str, list[SchGeometryRecord]],
    ready_keys: set[str],
) -> None:
    pending = list(reversed(release_children.get(anchor, ())))
    while pending:
        released = pending.pop()
        ready_keys.add(released)
        output.extend(records_by_key[released])
        records_by_key[released].clear()
        pending.extend(reversed(release_children.get(released, ())))


def _apply_runtime_event(
    output: list[SchGeometryRecord],
    source_index: int,
    event_key: str,
    delayed_indexes: set[int],
    delayed_keys: set[str],
    release_children: dict[str, tuple[str, ...]],
    records_by_key: dict[str, list[SchGeometryRecord]],
    ready_keys: set[str],
) -> None:
    if source_index in delayed_indexes:
        if event_key in delayed_keys:
            return
        ready_keys.add(event_key)
        output.extend(records_by_key[event_key])
        records_by_key[event_key].clear()
    _append_runtime_releases(
        output,
        event_key,
        release_children,
        records_by_key,
        ready_keys,
    )


def _runtime_root_source(
    source_object: object,
    roots_by_id: dict[int, object],
) -> object:
    path: list[object] = []
    current = source_object
    visited: set[int] = set()
    while id(current) not in roots_by_id and id(current) not in visited:
        path.append(current)
        visited.add(id(current))
        parent = getattr(current, "parent", None)
        if parent is None:
            break
        current = parent
    root = roots_by_id.get(id(current), current)
    for member in path:
        roots_by_id[id(member)] = root
    return root


def _runtime_tagged_source(
    record: SchGeometryRecord,
    source_objects: Sequence[object],
) -> tuple[int, object] | None:
    source_index = record.source_object_index
    if source_index is None or not 0 <= source_index < len(source_objects):
        return None
    return source_index, source_objects[source_index]


def _runtime_record_root_index(
    record: SchGeometryRecord,
    source_objects: Sequence[object],
    nested_root_indexes: dict[int, int],
) -> int | None:
    tagged = _runtime_tagged_source(record, source_objects)
    if tagged is None:
        return -1 if record.kind == "sheet" else None
    source_index, source_object = tagged
    if getattr(source_object, "parent", None) is None:
        return source_index
    return nested_root_indexes.get(source_index)


def _runtime_nested_root_indexes(
    records: Sequence[SchGeometryRecord],
    source_objects: Sequence[object],
) -> dict[int, int]:
    roots_by_id: dict[int, object] = {}
    root_ids_by_source_index: dict[int, int] = {}
    for record in records:
        tagged = _runtime_tagged_source(record, source_objects)
        if tagged is None:
            continue
        source_index, source_object = tagged
        if (
            source_index in root_ids_by_source_index
            or getattr(source_object, "parent", None) is None
        ):
            continue
        root_ids_by_source_index[source_index] = id(
            _runtime_root_source(source_object, roots_by_id)
        )
    needed_root_ids = set(root_ids_by_source_index.values())
    root_indexes = {
        id(source_object): source_index
        for source_index, source_object in enumerate(source_objects)
        if id(source_object) in needed_root_ids
    }
    return {
        source_index: root_indexes[root_id]
        for source_index, root_id in root_ids_by_source_index.items()
        if root_id in root_indexes
    }


def _apply_runtime_events_before(
    output: list[SchGeometryRecord],
    event_items: Sequence[tuple[int, str]],
    event_position: int,
    root_index: int,
    delayed_indexes: set[int],
    delayed_keys: set[str],
    release_children: dict[str, tuple[str, ...]],
    records_by_key: dict[str, list[SchGeometryRecord]],
    ready_keys: set[str],
) -> int:
    while (
        event_position < len(event_items)
        and event_items[event_position][0] < root_index
    ):
        source_index, event_key = event_items[event_position]
        _apply_runtime_event(
            output,
            source_index,
            event_key,
            delayed_indexes,
            delayed_keys,
            release_children,
            records_by_key,
            ready_keys,
        )
        event_position += 1
    return event_position


def _append_runtime_root_block(
    output: list[SchGeometryRecord],
    block: Sequence[SchGeometryRecord],
    root_index: int,
    delayed_indexes: set[int],
    key_by_source_index: dict[int, str],
    records_by_key: dict[str, list[SchGeometryRecord]],
) -> None:
    if root_index not in delayed_indexes:
        output.extend(block)
        return
    records_by_key[key_by_source_index[root_index]].extend(block)


def _merge_runtime_splice_records(
    records: Sequence[SchGeometryRecord],
    source_objects: Sequence[object],
    nested_root_indexes: dict[int, int],
    delayed_indexes: set[int],
    key_by_source_index: dict[int, str],
    plan: _DelayedPaintPlan,
) -> list[SchGeometryRecord]:
    delayed_keys = set(plan.delayed_registrations)
    release_children = _runtime_release_children(plan)
    records_by_key = {
        key_by_source_index[source_index]: [] for source_index in delayed_indexes
    }
    ready_keys: set[str] = set()
    output: list[SchGeometryRecord] = []
    event_items = sorted(
        (source_index, key) for source_index, key in key_by_source_index.items()
    )
    event_position = 0
    record_index = 0
    while record_index < len(records):
        root_index = _runtime_record_root_index(
            records[record_index],
            source_objects,
            nested_root_indexes,
        )
        if root_index is None:
            break
        event_position = _apply_runtime_events_before(
            output,
            event_items,
            event_position,
            root_index,
            delayed_indexes,
            delayed_keys,
            release_children,
            records_by_key,
            ready_keys,
        )
        block_end = record_index + 1
        while (
            block_end < len(records)
            and _runtime_record_root_index(
                records[block_end],
                source_objects,
                nested_root_indexes,
            )
            == root_index
        ):
            block_end += 1
        _append_runtime_root_block(
            output,
            records[record_index:block_end],
            root_index,
            delayed_indexes,
            key_by_source_index,
            records_by_key,
        )
        if (
            event_position < len(event_items)
            and event_items[event_position][0] == root_index
        ):
            source_index, event_key = event_items[event_position]
            _apply_runtime_event(
                output,
                source_index,
                event_key,
                delayed_indexes,
                delayed_keys,
                release_children,
                records_by_key,
                ready_keys,
            )
            event_position += 1
        record_index = block_end
    _apply_runtime_events_before(
        output,
        event_items,
        event_position,
        event_items[-1][0] + 1,
        delayed_indexes,
        delayed_keys,
        release_children,
        records_by_key,
        ready_keys,
    )
    for event_key in plan.end_flush:
        output.extend(records_by_key[event_key])
    output.extend(records[record_index:])
    return output


def _runtime_group_block_end(
    records: Sequence[SchGeometryRecord],
    source_objects: Sequence[object],
    nested_root_indexes: dict[int, int],
    record_index: int,
    root_index: int | None,
) -> int:
    block_end = record_index + 1
    while (
        root_index is not None
        and block_end < len(records)
        and _runtime_record_root_index(
            records[block_end],
            source_objects,
            nested_root_indexes,
        )
        == root_index
    ):
        block_end += 1
    return block_end


def _runtime_group_block_id(
    root_index: int | None,
    group_ids_by_source_index: Mapping[int, str],
    anonymous_index: int,
) -> str:
    if root_index is not None and root_index >= 0:
        group_id = group_ids_by_source_index.get(root_index, "")
        if group_id:
            return group_id
    return f"\0anonymous:{anonymous_index}"


def _runtime_apply_group_moves(
    records: Sequence[SchGeometryRecord],
    source_objects: Sequence[object],
    nested_root_indexes: dict[int, int],
    group_ids_by_source_index: Mapping[int, str],
    moves: Sequence[tuple[str, str]],
) -> list[SchGeometryRecord]:
    if not moves:
        return list(records)
    blocks_by_group_id: dict[str, list[list[SchGeometryRecord]]] = defaultdict(list)
    groups = _GroupSequence()
    record_index = 0
    anonymous_index = 0
    while record_index < len(records):
        root_index = _runtime_record_root_index(
            records[record_index],
            source_objects,
            nested_root_indexes,
        )
        block_end = _runtime_group_block_end(
            records,
            source_objects,
            nested_root_indexes,
            record_index,
            root_index,
        )
        group_id = _runtime_group_block_id(
            root_index,
            group_ids_by_source_index,
            anonymous_index,
        )
        if group_id.startswith("\0anonymous:"):
            anonymous_index += 1
        groups.append(group_id)
        blocks_by_group_id[group_id].append(list(records[record_index:block_end]))
        record_index = block_end

    for anchor_group_id, moved_group_id in moves:
        anchor_node = groups.node(anchor_group_id)
        moved_node = groups.node(moved_group_id)
        if anchor_node is not None and moved_node is not None:
            groups.insert_after(anchor_node, moved_group_id)
    return [
        record
        for group_id in groups.values()
        for block in blocks_by_group_id[group_id]
        for record in block
    ]


def _harness_splice_runtime_record_order(
    records: Sequence[SchGeometryRecord],
    source_objects: Sequence[object],
    *,
    show_template_graphics: bool = False,
    native_svg_export: bool = False,
    source_admission: _SourceAdmission = _SourceAdmission(),
) -> list[SchGeometryRecord]:
    """Apply first-level harness delays without per-unrelated-root allocation."""
    delayed_indexes = {
        source_index
        for source_index, source_object in enumerate(source_objects)
        if source_admission.admits(source_object)
        and type(source_object)
        in {
            AltiumSchHarnessLayoutConnectionPoint,
            AltiumSchHarnessLayoutLabel,
            AltiumSchHarnessSplice,
        }
        and getattr(source_object, "parent", None) is None
    }
    if not delayed_indexes:
        return list(records)

    label_indexes = {
        source_index
        for source_index in delayed_indexes
        if type(source_objects[source_index]) is AltiumSchHarnessLayoutLabel
    }
    label_bounds = _runtime_label_bounds(records, label_indexes)
    missing_label_bounds = label_indexes.difference(label_bounds)
    if missing_label_bounds:
        raise ValueError("harness layout label runtime ordering requires own bounds")
    bundle_index = _runtime_bundle_index(
        source_objects,
        enabled=bool(label_indexes),
        source_admission=source_admission,
    )
    predecessors_by_source_index = {
        source_index: _harness_predecessor_state_uids(
            source_objects[source_index],
            label_own_bounds=label_bounds.get(source_index),
            document_bundles=bundle_index,
        )
        for source_index in delayed_indexes
    }
    nested_root_indexes = _runtime_nested_root_indexes(records, source_objects)
    group_ids_by_source_index = _runtime_group_ids_by_source_index(
        records,
        native_svg_export=native_svg_export,
    )
    events, source_index_by_key = _runtime_harness_events(
        source_objects,
        delayed_indexes,
        predecessors_by_source_index,
        group_ids_by_source_index,
        show_template_graphics=show_template_graphics,
        source_admission=source_admission,
    )
    plan = _plan_delayed_paint_order(events)
    key_by_source_index = {
        source_index: key for key, source_index in source_index_by_key.items()
    }
    merged = _merge_runtime_splice_records(
        records,
        source_objects,
        nested_root_indexes,
        delayed_indexes,
        key_by_source_index,
        plan,
    )
    return _runtime_apply_group_moves(
        merged,
        source_objects,
        nested_root_indexes,
        group_ids_by_source_index,
        plan.create_group_after,
    )


def order_geometry_records_by_source(
    records: Sequence[SchGeometryRecord],
    source_objects: Iterable[object],
    *,
    sort_root_transparency: bool = False,
    eligible_source_objects: Iterable[object] | None = None,
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> list[SchGeometryRecord]:
    """Return records in the source container's observable drawing order."""
    source_sequence = list(source_objects)
    eligible_source_ids = (
        None
        if eligible_source_objects is None
        else {id(source_object) for source_object in eligible_source_objects}
    )
    positions, generated_positions, record_positions = _source_positions(
        source_sequence,
        records,
        sort_root_transparency=sort_root_transparency,
        eligible_source_ids=eligible_source_ids,
        parent_by_source_id=parent_by_source_id,
    )
    unknown_base = len(source_sequence) + 1

    def paint_position(
        indexed_record: tuple[int, SchGeometryRecord],
    ) -> tuple[int, int]:
        original_position, record = indexed_record
        source_position = record_positions.get(original_position)
        if source_position is None and record.unique_id is not None:
            source_position = positions.get(record.unique_id)
        if source_position is None:
            generated_key = _generated_source_key(record.unique_id)
            if generated_key is not None:
                source_position = generated_positions.get(generated_key)
        if source_position is None and record.kind == "sheet":
            source_position = -1
        if source_position is None:
            source_position = unknown_base + original_position
        return source_position, original_position

    indexed_records = list(enumerate(records))
    indexed_records.sort(key=paint_position)
    return [record for _, record in indexed_records]
