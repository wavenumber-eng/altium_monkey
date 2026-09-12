"""Bounded covering own bounds from prepared engine border-polygon state."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from ._altium_record_sch__harness_layout import (
    AltiumSchHarnessLayoutCovering,
    HarnessCoveredItem,
)
from ._altium_sch_bounds_source_tree import _BoundsSourceTree
from .altium_sch_geometry_oracle import SchGeometryBounds


_CoveringBorders = Iterable[Iterable[tuple[int, int]]]


@dataclass(frozen=True, slots=True)
class _BoundsCoveringIndex:
    tree: _BoundsSourceTree
    own_bounds: Mapping[int, SchGeometryBounds]
    polygon_count: int
    point_count: int


@dataclass(frozen=True, slots=True)
class _ColdCoveringCapture:
    source: AltiumSchHarnessLayoutCovering
    covered_items: tuple[HarnessCoveredItem, ...]


@dataclass(frozen=True, slots=True)
class _BoundsColdCoveringIndex:
    tree: _BoundsSourceTree
    document_kind: Literal["schematic", "library"]
    captures: Mapping[int, _ColdCoveringCapture]


def _capture_cold_non_layout_coverings(
    tree: _BoundsSourceTree,
    sources: Iterable[int],
    *,
    document_kind: Literal["schematic", "library"],
    max_coverings: int = 50_000,
) -> _BoundsColdCoveringIndex:
    """Capture explicit cold-border provenance in one attached document phase.

    The caller supplies actual current-document and engine-cache facts. Neither
    rendering context nor a Python projection/current-path implies these facts.
    Detached/original-owner and layout-document state require separate evidence.
    """
    if document_kind not in ("schematic", "library"):
        raise ValueError("cold covering capture requires an attached SchDoc or SchLib")
    if type(max_coverings) is not int or max_coverings < 0:
        raise ValueError("cold covering source limit must be a nonnegative integer")
    captured: dict[int, _ColdCoveringCapture] = {}
    for index in sources:
        if len(captured) == max_coverings:
            raise ValueError("cold covering source limit exceeded")
        _validate_covering_bounds_source(tree, index)
        if index in captured:
            raise ValueError("duplicate cold covering source")
        source = tree.records[index]
        assert type(source) is AltiumSchHarnessLayoutCovering
        captured[index] = _ColdCoveringCapture(source, source.covered_items)
    return _BoundsColdCoveringIndex(tree, document_kind, MappingProxyType(captured))


@dataclass(slots=True)
class _CoveringBoundsBudget:
    max_polygons: int
    max_points: int
    polygons: int = 0
    points: int = 0

    def polygon(self) -> None:
        if self.polygons == self.max_polygons:
            raise ValueError("covering bounds polygon limit exceeded")
        self.polygons += 1

    def point(self) -> None:
        if self.points == self.max_points:
            raise ValueError("covering bounds point limit exceeded")
        self.points += 1


def _covering_bound_coordinate(value: int) -> int:
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise ValueError("covering bounds coordinates must be signed Int32 values")
    return value


def _covering_bound_point(point: tuple[int, int]) -> tuple[int, int]:
    if type(point) is not tuple or len(point) != 2:
        raise ValueError("covering bounds points must be coordinate pairs")
    return _covering_bound_coordinate(point[0]), _covering_bound_coordinate(point[1])


def _reduce_covering_border_bounds(
    polygons: _CoveringBorders, budget: _CoveringBoundsBudget
) -> SchGeometryBounds:
    left = bottom = 2_147_483_647
    right = top = -2_147_483_647
    for polygon in polygons:
        budget.polygon()
        for point in polygon:
            budget.point()
            x, y = _covering_bound_point(point)
            left, bottom = min(left, x), min(bottom, y)
            right, top = max(right, x), max(top, y)
    # Empty polygons, including an empty first polygon, do not end the scan.
    # Keep the engine sentinel and its INT_MIN-point extrema behavior intact.
    return SchGeometryBounds(left=left, bottom=bottom, right=right, top=top)


def _validate_covering_bounds_source(tree: _BoundsSourceTree, index: int) -> None:
    if type(index) is not int or not 0 <= index < len(tree.records):
        raise ValueError("covering bounds source is outside the prepared tree")
    if type(tree.records[index]) is not AltiumSchHarnessLayoutCovering:
        raise ValueError("covering bounds source requires an exact covering record")


def _build_bounds_covering_index(
    tree: _BoundsSourceTree,
    sources: Iterable[tuple[int, _CoveringBorders]],
    *,
    max_coverings: int = 50_000,
    max_polygons: int = 200_000,
    max_points: int = 800_000,
) -> _BoundsCoveringIndex:
    """Reduce immutable query results from engine-equivalent border snapshots.

    Producers must bound polygon generation before supplying these snapshots.
    Empty state is explicit; an absent source does not mean empty. Draw-time
    projection output cannot substitute for the engine's lazy/cache state.
    """
    if min(max_coverings, max_polygons, max_points) < 0:
        raise ValueError("covering bounds limits cannot be negative")
    budget = _CoveringBoundsBudget(max_polygons, max_points)
    reduced: dict[int, SchGeometryBounds] = {}
    for index, polygons in sources:
        if len(reduced) == max_coverings:
            raise ValueError("covering bounds source limit exceeded")
        _validate_covering_bounds_source(tree, index)
        if index in reduced:
            raise ValueError("duplicate prepared covering bounds source")
        reduced[index] = _reduce_covering_border_bounds(polygons, budget)
    return _BoundsCoveringIndex(
        tree, MappingProxyType(reduced), budget.polygons, budget.points
    )
