"""Bounded request-local covering cache evaluation over prepared engine state."""

from __future__ import annotations

from collections.abc import Callable, Generator
from copy import copy
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from ._altium_record_sch__harness_layout import (
    AltiumSchHarnessLayoutCovering,
    _HarnessCoveringLine,
    _HarnessCoveringNode,
    _HarnessCoveringPathData,
    _HarnessCoveringProjection,
    _HarnessCoveringTopologyIndex,
    _autoposition_covering_fields,
    _checked_covering_sum,
    _covering_active_offsets,
    _covering_path_was_reversed,
    _covering_polygon_geometry,
    _covering_projection_result,
    _covering_should_draw,
    _recalculate_covering_offsets,
    _unchecked_i32,
)
from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__parameter import AltiumSchParameter

type _Point = tuple[int, int]
type _Borders = tuple[tuple[_Point, ...], ...]
type _Patterns = tuple[_HarnessCoveringPathData, ...]
type _Path = tuple[_HarnessCoveringNode, ...] | None
type _BorderCache = list[tuple[_Point, ...]]
type _PatternCache = list[_HarnessCoveringPathData]
type _Cache = _BorderCache | _PatternCache
type _Request = Literal["border", "pattern", "calculate"]
type _Steps = Generator[_Request, _Cache | None, _Cache | None]

_MAX_COVERING_RENDER_CACHE_WORK = 10_000_000


@dataclass(frozen=True, slots=True)
class _CoveringEndpoints:
    start: _Point = (0, 0)
    end: _Point = (0, 0)
    visual_start: _Point = (0, 0)
    visual_end: _Point = (0, 0)


@dataclass(frozen=True, slots=True)
class _CoveringCacheGeometry:
    borders: _Borders
    patterns: _Patterns
    lines: tuple[_HarnessCoveringLine, ...]
    endpoints: _CoveringEndpoints


@dataclass(frozen=True, slots=True)
class _PreparedCoveringRender:
    projection: _HarnessCoveringProjection
    designator: AltiumSchDesignator | None
    comment: AltiumSchParameter | None


class _CoveringCacheDriver(Protocol):
    """Prepared-state operations; providers own source isolation and geometry limits."""

    def covered_length(self) -> int: ...

    def calculate_length(self, covered_length: int) -> None: ...

    def recalculate_offsets(self, covered_length: int) -> None: ...

    def path(self) -> _Path: ...

    def flip_offsets(self) -> None: ...

    def polygon(self, path: _Path) -> _CoveringCacheGeometry: ...

    def move_fields(self) -> None: ...


@dataclass(slots=True)
class _CoveringCacheBudget:
    max_work: int = 1_000_000
    max_frames: int = 4096
    used: int = 0
    failed: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.max_work, self.max_frames, self.used)
        ):
            raise ValueError("covering cache work budget is invalid")
        if type(self.failed) is not bool or self.used > self.max_work:
            raise ValueError("covering cache work budget is invalid")

    def charge(self, work: int = 1) -> None:
        if self.failed:
            raise RuntimeError("covering cache request is unusable after failure")
        if type(work) is not int or work < 0:
            self.failed = True
            raise ValueError("covering cache work charge must be a nonnegative integer")
        if work > self.max_work - self.used:
            self.failed = True
            raise ValueError("covering cache request work limit exceeded")
        self.used += work


class _CoveringCache:
    def __init__(
        self,
        driver: _CoveringCacheDriver,
        budget: _CoveringCacheBudget,
        *,
        borders: _Borders | None = None,
        patterns: _Patterns | None = None,
        path: _Path = None,
        lines: tuple[_HarnessCoveringLine, ...] | None = None,
        endpoints: _CoveringEndpoints = _CoveringEndpoints(),
        update_count: int = 0,
    ) -> None:
        if type(update_count) is not int or not -(1 << 31) <= update_count < 1 << 31:
            raise ValueError("covering update count must be Int32")
        budget.charge(1 + (len(borders or ()) + len(patterns or ())))
        self._driver = driver
        self._budget = budget
        self._borders: _BorderCache | None = None if borders is None else list(borders)
        self._patterns: _PatternCache | None = (
            None if patterns is None else list(patterns)
        )
        self._path = path
        self._lines = lines
        self._endpoints = endpoints
        self._update_count = update_count
        self._running = False

    def border_polygons(self) -> _Borders:
        result = cast(_BorderCache, self._run("border"))
        self._budget.charge(len(result))
        return tuple(result)

    def polygons(self) -> _Patterns:
        result = cast(_PatternCache, self._run("pattern"))
        self._budget.charge(len(result))
        return tuple(result)

    def calculate(self) -> None:
        self._run("calculate")

    @property
    def current_path(self) -> _Path:
        self._budget.charge(0)
        return self._path

    @property
    def lines(self) -> tuple[_HarnessCoveringLine, ...] | None:
        self._budget.charge(0)
        return self._lines

    @property
    def endpoints(self) -> _CoveringEndpoints:
        self._budget.charge(0)
        return self._endpoints

    def _steps(self, request: _Request) -> _Steps:
        self._budget.charge()
        if request == "calculate":
            return self._calculate_steps()
        return self._read_steps(request)

    def _run(self, request: _Request) -> _Cache | None:
        self._budget.charge(0)
        if self._running:
            self._budget.failed = True
            raise RuntimeError("covering cache driver must not reenter the request")
        self._running = True
        stack: list[_Steps] = []
        result: _Cache | None = None
        try:
            self._push(stack, request)
            while stack:
                try:
                    child = stack[-1].send(result)
                except StopIteration as completed:
                    stack.pop()
                    result = completed.value
                else:
                    self._push(stack, child)
                    result = None
            self._budget.charge(0)
            return result
        except Exception:
            self._budget.failed = True
            raise
        finally:
            for frame in reversed(stack):
                frame.close()
            self._running = False

    def _push(self, stack: list[_Steps], request: _Request) -> None:
        if len(stack) >= self._budget.max_frames:
            raise ValueError("covering cache request frame limit exceeded")
        stack.append(self._steps(request))

    def _read_steps(self, request: Literal["border", "pattern"]) -> _Steps:
        if request == "border":
            if self._borders is None:
                self._borders = []
                yield "calculate"
            return self._borders if self._borders is not None else [()]
        if self._patterns is None:
            self._patterns = []
            yield "calculate"
        return self._patterns if self._patterns is not None else []

    def _calculate_steps(self) -> _Steps:
        if self._update_count != 0:
            return None
        length = self._driver.covered_length()
        if length == 0:
            self._drop_caches()
            self._lines = ()
            return None
        self._driver.calculate_length(length)
        self._driver.recalculate_offsets(length)
        old_path = self._path
        self._path = self._driver.path()
        self._charge_reversal(old_path, self._path)
        if _covering_path_was_reversed(old_path, self._path):
            self._driver.flip_offsets()
        geometry = self._driver.polygon(self._path)
        self._lines = geometry.lines
        # Each property expression must finish before Clear/AddRange mutates
        # its returned list: the getter can run a complete nested calculation.
        border = cast(_BorderCache, (yield "border"))
        self._budget.charge(len(border))
        border.clear()
        border = cast(_BorderCache, (yield "border"))
        self._budget.charge(len(geometry.borders))
        border.extend(geometry.borders)
        patterns = cast(_PatternCache, (yield "pattern"))
        self._budget.charge(len(patterns))
        patterns.clear()
        patterns = cast(_PatternCache, (yield "pattern"))
        self._budget.charge(len(geometry.patterns))
        patterns.extend(geometry.patterns)
        if self._borders and self._borders[0]:
            self._endpoints = geometry.endpoints
        self._driver.move_fields()
        return None

    def _charge_reversal(self, old_path: _Path, new_path: _Path) -> None:
        self._budget.charge(len(old_path or ()) + len(new_path or ()))
        for path in (old_path or (), new_path or ()):
            for node in path:
                self._budget.charge(len(node.unique_id))

    def _drop_caches(self) -> None:
        self._budget.charge(len(self._borders or ()) + len(self._patterns or ()))
        self._borders = None
        self._patterns = None


class _CoveringRenderDriver:
    """Run managed cache phases against request-local covering/field copies."""

    def __init__(
        self,
        covering: AltiumSchHarnessLayoutCovering,
        topology: _HarnessCoveringTopologyIndex,
        designator: AltiumSchDesignator | None,
        comment: AltiumSchParameter | None,
        *,
        comment_y_size: Callable[[], int],
    ) -> None:
        self.covering = copy(covering)
        self.topology = topology
        self.designator = copy(designator) if designator is not None else None
        self.comment = copy(comment) if comment is not None else None
        self.comment_y_size = comment_y_size
        self._path: tuple[_HarnessCoveringNode, ...] | None = None
        self.moved_designator = False
        self.moved_comment = False

    def covered_length(self) -> int:
        path = self.topology.path_for(self.covering)
        if path is None:
            return 0
        return _checked_covering_sum(node.lines_length for node in path)

    def calculate_length(self, covered_length: int) -> None:
        start_distance, end_distance = _covering_active_offsets(self.covering)
        offset_sum = _unchecked_i32(start_distance + end_distance)
        self.covering.length = max(_unchecked_i32(covered_length - offset_sum), 0)

    def recalculate_offsets(self, covered_length: int) -> None:
        _recalculate_covering_offsets(self.covering, self.topology, covered_length)

    def path(self) -> tuple[_HarnessCoveringNode, ...] | None:
        self._path = self.topology.path_for(self.covering)
        return self._path

    def flip_offsets(self) -> None:
        (
            self.covering._visual_start_point_distance,
            self.covering._visual_end_point_distance,
        ) = (
            self.covering._visual_end_point_distance,
            self.covering._visual_start_point_distance,
        )
        (
            self.covering.physical_start_point_distance,
            self.covering.physical_end_point_distance,
        ) = (
            self.covering.physical_end_point_distance,
            self.covering.physical_start_point_distance,
        )

    def polygon(
        self, path: tuple[_HarnessCoveringNode, ...] | None
    ) -> _CoveringCacheGeometry:
        if path is None:
            return _CoveringCacheGeometry(((),), (), (), _CoveringEndpoints())
        self.topology._reserve_geometry_work(path)
        start_distance, end_distance = _covering_active_offsets(self.covering)
        borders, patterns, lines, logical_lines = _covering_polygon_geometry(
            path,
            start_distance=start_distance,
            end_distance=end_distance,
            covering_thickness=self.covering.thickness,
        )
        endpoints = _covering_line_endpoints(logical_lines, lines)
        return _CoveringCacheGeometry(borders, patterns, lines, endpoints)

    def move_fields(self) -> None:
        if self._path is None:
            return
        projection = _HarnessCoveringProjection((), (), (), self._path)
        moved = _autoposition_covering_fields(
            self.covering,
            projection,
            self.designator,
            self.comment,
            comment_y_size=self.comment_y_size,
        )
        self.moved_designator = self.moved_designator or self.designator in moved
        self.moved_comment = self.moved_comment or self.comment in moved


def _covering_line_endpoints(
    logical_lines: tuple[_HarnessCoveringLine, ...],
    visual_lines: tuple[_HarnessCoveringLine, ...],
) -> _CoveringEndpoints:
    if not logical_lines:
        return _CoveringEndpoints()
    start = (int(logical_lines[0].point1[0]), int(logical_lines[0].point1[1]))
    end = (int(logical_lines[-1].point2[0]), int(logical_lines[-1].point2[1]))
    if not visual_lines:
        return _CoveringEndpoints(start, end)
    visual_start = (int(visual_lines[0].point1[0]), int(visual_lines[0].point1[1]))
    visual_end = (int(visual_lines[-1].point2[0]), int(visual_lines[-1].point2[1]))
    return _CoveringEndpoints(start, end, visual_start, visual_end)


def _prepare_covering_render(
    covering: AltiumSchHarnessLayoutCovering,
    topology: _HarnessCoveringTopologyIndex,
    designator: AltiumSchDesignator | None,
    comment: AltiumSchParameter | None,
    *,
    comment_y_size: Callable[[], int],
    budget: _CoveringCacheBudget,
) -> _PreparedCoveringRender | None:
    """Evaluate the renderer's BorderPolygons-first access on isolated state."""
    driver = _CoveringRenderDriver(
        covering,
        topology,
        designator,
        comment,
        comment_y_size=comment_y_size,
    )
    cache = _CoveringCache(
        driver,
        budget,
        path=driver.covering._current_covering_path,
        update_count=driver.covering._covering_update_count,
    )
    borders = cache.border_polygons()
    if not borders or not borders[0]:
        return None
    if not _covering_should_draw(driver.covering, topology):
        return None
    if not _covering_should_draw(driver.covering, topology):
        return None
    borders = cache.border_polygons()
    if not borders or not borders[0]:
        return None
    patterns = cache.polygons()
    path = cache.current_path or ()
    projection = _covering_projection_result(
        borders,
        patterns,
        cache.lines or (),
        path,
    )
    if projection is None:
        return None
    topology._reserve_output_work(projection)
    return _PreparedCoveringRender(
        projection,
        driver.designator if driver.moved_designator else None,
        driver.comment if driver.moved_comment else None,
    )
