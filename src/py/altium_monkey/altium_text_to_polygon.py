"""
Text-to-Polygon Conversion for Altium PCB Text Primitives.

Converts Altium PCB text records into polygon contours (TrueType) or
line segments (stroke font) for IPC-2581 output and SVG rendering.

Two Text Types:
    TrueType: Glyph outlines via FreeType + HarfBuzz -> filled contours
    Stroke:   Altium built-in vector font -> line segments with width

Altium Text Height Calibration:
    PCB text Height field = cell height (ascent + descent), NOT em size.
    em_size = Height * factor, where factor = UPM / (usWinAscent + usWinDescent)

    For Arial:  factor = 2048 / 2288 = 0.8951
    Height 60mil -> em_size = 53.71mil -> cap height 38.4mil (matches reference)

Coordinate System:
    Altium PCB: Y-up, internal units = 10000/mil
    IPC-2581: Y-up, millimeters
    Conversion: mm = internal_units / 10000 * 0.0254

"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Altium internal unit conversion
_UNITS_PER_MIL = 10000.0
_MIL_TO_MM = 0.0254

# Mutable alias map so older tests and helper code can register custom font
# family names directly against on-disk font files.
_FONT_MAP: dict[str, str] = {}


# ================================================================== #
# Data Structures
# ================================================================== #


@dataclass
class GlyphPolygon:
    """
    A single glyph region: outer boundary + optional holes.

        In IPC-2581, this maps to one <Contour> element with a <Polygon>
        for the outline and <Cutout> elements for holes.
    """

    outline: list[tuple[float, float]]  # (x_mm, y_mm) closed polygon
    holes: list[list[tuple[float, float]]] = field(default_factory=list)


@dataclass
class TextPolygonResult:
    """
    Result of TrueType text-to-polygon conversion.

        characters: One entry per visible character. Each character may have
        multiple polygons (e.g., 'i' has dot + body as separate regions).
    """

    characters: list[list[GlyphPolygon]] = field(default_factory=list)

    @property
    def total_contours(self) -> int:
        return sum(len(polys) for polys in self.characters)


@dataclass
class StrokeTextResult:
    """
    Result of stroke text conversion.

        lines: List of (x1, y1, x2, y2) in mm, representing stroke centerlines.
        stroke_width_mm: Line width for rendering.
    """

    lines: list[tuple[float, float, float, float]] = field(default_factory=list)
    stroke_width_mm: float = 0.254


# ================================================================== #
# Bezier Curve Flattening
# ================================================================== #


def _bezier_subdivide_quadratic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    tolerance: float,
    depth: int = 0,
) -> list[tuple[float, float]]:
    """
    Flatten quadratic Bezier curve by recursive subdivision.
    """
    max_depth = 8

    mid_x = (p0[0] + p2[0]) / 2.0
    mid_y = (p0[1] + p2[1]) / 2.0
    curve_x = (p0[0] + 2.0 * p1[0] + p2[0]) / 4.0
    curve_y = (p0[1] + 2.0 * p1[1] + p2[1]) / 4.0
    dist = math.sqrt((curve_x - mid_x) ** 2 + (curve_y - mid_y) ** 2)

    if dist <= tolerance or depth >= max_depth:
        return [p2]

    q0 = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    q1 = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    r = ((q0[0] + q1[0]) / 2.0, (q0[1] + q1[1]) / 2.0)

    left = _bezier_subdivide_quadratic(p0, q0, r, tolerance, depth + 1)
    right = _bezier_subdivide_quadratic(r, q1, p2, tolerance, depth + 1)
    return left + right


def _bezier_subdivide_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    tolerance: float,
    depth: int = 0,
) -> list[tuple[float, float]]:
    """
    Flatten cubic Bezier curve by recursive subdivision.
    """
    max_depth = 8

    dx = p3[0] - p0[0]
    dy = p3[1] - p0[1]
    len_sq = dx * dx + dy * dy

    def point_line_dist(px: float, py: float) -> float:
        if len_sq < 1e-10:
            return math.sqrt((px - p0[0]) ** 2 + (py - p0[1]) ** 2)
        t = max(0.0, min(1.0, ((px - p0[0]) * dx + (py - p0[1]) * dy) / len_sq))
        return math.sqrt((px - p0[0] - t * dx) ** 2 + (py - p0[1] - t * dy) ** 2)

    d1 = point_line_dist(p1[0], p1[1])
    d2 = point_line_dist(p2[0], p2[1])

    if max(d1, d2) <= tolerance or depth >= max_depth:
        return [p3]

    q0 = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    q1 = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    q2 = ((p2[0] + p3[0]) / 2.0, (p2[1] + p3[1]) / 2.0)
    r0 = ((q0[0] + q1[0]) / 2.0, (q0[1] + q1[1]) / 2.0)
    r1 = ((q1[0] + q2[0]) / 2.0, (q1[1] + q2[1]) / 2.0)
    s = ((r0[0] + r1[0]) / 2.0, (r0[1] + r1[1]) / 2.0)

    left = _bezier_subdivide_cubic(p0, q0, r0, s, tolerance, depth + 1)
    right = _bezier_subdivide_cubic(s, r1, q2, p3, tolerance, depth + 1)
    return left + right


# ================================================================== #
# Contour Geometry Utilities
# ================================================================== #


def _signed_area(points: list[tuple[float, float]]) -> float:
    """
    Compute signed area of a polygon using the shoelace formula.

        Positive = counter-clockwise (outer contour in Y-up coords)
        Negative = clockwise (hole in Y-up coords)
    """
    area = 0.0
    n = len(points)
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return area / 2.0


def _is_outer_contour(points: list[tuple[float, float]]) -> bool:
    """
    Determine if a contour is an outer boundary (vs a hole).

        In TrueType fonts with FreeType:
        - Outer contours are clockwise (negative signed area in Y-up)
        - Holes are counter-clockwise (positive signed area in Y-up)

        Note: This follows TrueType convention. OpenType/CFF may differ.
    """
    return _signed_area(points) < 0


def _contour_bbox(
    points: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    """
    Get bounding box of a contour: (min_x, min_y, max_x, max_y).
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _contour_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """
    Compute arithmetic centroid of a contour.

        More robust than using the first vertex as a containment probe point,
        since vertex 0 may lie on a shared boundary between contours. The
        centroid is far more likely to be a true interior point.
    """
    n = len(points)
    if n == 0:
        return (0.0, 0.0)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    return (cx, cy)


def _contour_probe_point(points: list[tuple[float, float]]) -> tuple[float, float]:
    """
    Pick a robust interior probe point for containment tests.

        Centroid is preferred (fixes shared-boundary issues from vertex-0 probes).
        For concave/self-intersecting contours where centroid may land outside,
        fall back to additional candidates and finally vertex 0.
    """
    if not points:
        return (0.0, 0.0)

    centroid = _contour_centroid(points)
    if _point_in_polygon(centroid, points):
        return centroid

    min_x, min_y, max_x, max_y = _contour_bbox(points)
    bbox_center = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
    if _point_in_polygon(bbox_center, points):
        return bbox_center

    # For concave contours, a midpoint from centroid toward a vertex
    # often lands inside even when the raw centroid does not.
    for vx, vy in points:
        mid = ((centroid[0] + vx) / 2.0, (centroid[1] + vy) / 2.0)
        if _point_in_polygon(mid, points):
            return mid

    # Last interior-candidate sweep: edge midpoints.
    n = len(points)
    for idx in range(n):
        x1, y1 = points[idx]
        x2, y2 = points[(idx + 1) % n]
        mid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        if _point_in_polygon(mid, points):
            return mid

    return points[0]


def _bbox_contains(
    outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]
) -> bool:
    """
    Check if outer bbox fully contains inner bbox.
    """
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _point_on_segment(
    point: tuple[float, float],
    seg_a: tuple[float, float],
    seg_b: tuple[float, float],
    *,
    eps: float = 1e-9,
) -> bool:
    """
    Return True when point lies on line segment seg_a->seg_b.
    """
    px, py = point
    x1, y1 = seg_a
    x2, y2 = seg_b

    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > eps:
        return False

    dot = (px - x1) * (px - x2) + (py - y1) * (py - y2)
    return dot <= eps


def _point_in_polygon(
    point: tuple[float, float], polygon: list[tuple[float, float]]
) -> bool:
    """
    Ray-casting point-in-polygon with boundary treated as inside.
    """
    x, y = point
    inside = False
    n = len(polygon)
    if n < 3:
        return False

    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]

        if _point_on_segment(point, p1, p2):
            return True

        x1, y1 = p1
        x2, y2 = p2
        crosses = (y1 > y) != (y2 > y)
        if crosses:
            x_intersect = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_intersect:
                inside = not inside

    return inside


def _append_distinct_point(
    points: list[tuple[float, float]],
    point: tuple[float, float],
    *,
    eps: float = 1e-9,
) -> None:
    """
    Append a point unless it duplicates the previous point.
    """
    if not points:
        points.append(point)
        return

    px, py = points[-1]
    if abs(px - point[0]) <= eps and abs(py - point[1]) <= eps:
        return
    points.append(point)


# Redundant-vertex removal on raw FreeType outlines needs a tight threshold:
# small enough to catch floating-point duplicates without shifting glyph ink
# edges.
_VERTEX_DEDUP_THRESHOLD_MM = 0.001


def _remove_redundant_vertices(
    contour: list[tuple[float, float]],
    *,
    eps: float = _VERTEX_DEDUP_THRESHOLD_MM,
) -> list[tuple[float, float]]:
    """
    Remove near-coincident consecutive vertices from a contour.

        Inspired by the native ``gpc_ContourRemoveRedundantVertices`` cleanup.
        Applied as a post-classification cleanup to reduce vertex count without
        affecting contour topology.  Preserves closure: if the input contour is
        closed (first ~= last), the output will also be closed.
    """
    if len(contour) < 3:
        return contour

    # Detect whether input is closed (first ~= last vertex)
    was_closed = (
        len(contour) >= 2
        and abs(contour[0][0] - contour[-1][0]) <= eps
        and abs(contour[0][1] - contour[-1][1]) <= eps
    )

    # Work on an open polygon for both passes
    working = list(contour)
    if was_closed and len(working) >= 2:
        working = working[:-1]

    # Pass 1: remove near-duplicate consecutive vertices
    deduped: list[tuple[float, float]] = [working[0]]
    for pt in working[1:]:
        px, py = deduped[-1]
        if abs(px - pt[0]) > eps or abs(py - pt[1]) > eps:
            deduped.append(pt)

    # Re-close if input was closed
    if was_closed and deduped:
        deduped.append(deduped[0])

    return deduped


def _normalize_contour_points(
    contour: list[tuple[float, float]],
    *,
    eps: float = 1e-9,
) -> list[tuple[float, float]]:
    """
    Normalize contour to an open polygon with duplicate vertices removed.
    """
    if not contour:
        return []

    points = list(contour)
    if len(points) >= 2:
        x0, y0 = points[0]
        x1, y1 = points[-1]
        if abs(x0 - x1) <= eps and abs(y0 - y1) <= eps:
            points = points[:-1]

    normalized: list[tuple[float, float]] = []
    for point in points:
        _append_distinct_point(normalized, point, eps=eps)

    if len(normalized) >= 2:
        x0, y0 = normalized[0]
        x1, y1 = normalized[-1]
        if abs(x0 - x1) <= eps and abs(y0 - y1) <= eps:
            normalized = normalized[:-1]

    return normalized


def _intersect_with_vertical(
    p1: tuple[float, float],
    p2: tuple[float, float],
    x_edge: float,
    *,
    eps: float = 1e-12,
) -> tuple[float, float]:
    """
    Intersect segment p1->p2 with a vertical line x=x_edge.
    """
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    if abs(dx) <= eps:
        return (x_edge, y1)
    t = (x_edge - x1) / dx
    return (x_edge, y1 + (y2 - y1) * t)


def _intersect_with_horizontal(
    p1: tuple[float, float],
    p2: tuple[float, float],
    y_edge: float,
    *,
    eps: float = 1e-12,
) -> tuple[float, float]:
    """
    Intersect segment p1->p2 with a horizontal line y=y_edge.
    """
    x1, y1 = p1
    x2, y2 = p2
    dy = y2 - y1
    if abs(dy) <= eps:
        return (x1, y_edge)
    t = (y_edge - y1) / dy
    return (x1 + (x2 - x1) * t, y_edge)


def _clip_polygon_against_edge(
    points: list[tuple[float, float]],
    *,
    inside_fn: Callable[[tuple[float, float]], bool],
    intersect_fn: Callable[
        [tuple[float, float], tuple[float, float]], tuple[float, float]
    ],
) -> list[tuple[float, float]]:
    """
    Clip a polygon against a single half-plane edge.
    """
    if len(points) < 3:
        return []

    clipped: list[tuple[float, float]] = []
    prev = points[-1]
    prev_inside = inside_fn(prev)

    for curr in points:
        curr_inside = inside_fn(curr)
        if curr_inside:
            if not prev_inside:
                _append_distinct_point(clipped, intersect_fn(prev, curr))
            _append_distinct_point(clipped, curr)
        elif prev_inside:
            _append_distinct_point(clipped, intersect_fn(prev, curr))
        prev = curr
        prev_inside = curr_inside

    return clipped


def _clip_contour_to_rect(
    contour: list[tuple[float, float]],
    *,
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    eps: float = 1e-9,
) -> list[list[tuple[float, float]]]:
    """
    Clip one contour to an axis-aligned rectangle.

        Returns a list for API compatibility with future multi-part clipping.
        Current Sutherland-Hodgman path emits at most one clipped contour.
    """
    points = _normalize_contour_points(contour, eps=eps)
    if len(points) < 3:
        return []

    clipped = points
    clipped = _clip_polygon_against_edge(
        clipped,
        inside_fn=lambda p: p[0] >= x_min - eps,
        intersect_fn=lambda a, b: _intersect_with_vertical(a, b, x_min),
    )
    clipped = _clip_polygon_against_edge(
        clipped,
        inside_fn=lambda p: p[0] <= x_max + eps,
        intersect_fn=lambda a, b: _intersect_with_vertical(a, b, x_max),
    )
    clipped = _clip_polygon_against_edge(
        clipped,
        inside_fn=lambda p: p[1] >= y_min - eps,
        intersect_fn=lambda a, b: _intersect_with_horizontal(a, b, y_min),
    )
    clipped = _clip_polygon_against_edge(
        clipped,
        inside_fn=lambda p: p[1] <= y_max + eps,
        intersect_fn=lambda a, b: _intersect_with_horizontal(a, b, y_max),
    )

    clipped = _normalize_contour_points(clipped, eps=eps)
    if len(clipped) < 3:
        return []

    area = abs(_signed_area(clipped))
    if area <= 1e-12:
        return []

    if clipped[0] != clipped[-1]:
        clipped = [*clipped, clipped[0]]
    return [clipped]


# ================================================================== #
# FreeType Outline Extraction
# ================================================================== #


def _outline_to_contours(
    outline: Any, tolerance: float = 0.5
) -> list[list[tuple[float, float]]]:
    """
    Convert a FreeType outline to polygon contours.

        Handles on-curve points, quadratic (conic) control points, and
        cubic control points. Bezier curves are flattened via recursive
        subdivision.

        Args:
            outline: FreeType glyph outline object
            tolerance: Bezier flattening tolerance (in FreeType coordinate units)

        Returns:
            List of closed contours, each a list of (x, y) float tuples.
    """
    contours = []
    start = 0

    for contour_end in outline.contours:
        points = []
        i = start
        last_point = None

        while i <= contour_end:
            tag = outline.tags[i]

            if tag & 1:  # On curve
                pt = (
                    float(outline.points[i][0]) / 64.0,
                    float(outline.points[i][1]) / 64.0,
                )
                if not points or points[-1] != pt:
                    points.append(pt)
                last_point = pt
                i += 1

            elif tag & 2:  # Cubic bezier
                # FreeType contours may end with two cubic control points where
                # the final on-curve point wraps to contour start. Handle that
                # wrap explicitly; otherwise we drop the last curve and close
                # with a straight edge (visible angular artifacts on glyph '0').
                if i + 1 > contour_end:
                    i += 1
                    continue

                contour_len = contour_end - start + 1

                def _pt(
                    idx: int,
                    *,
                    contour_start: int = start,
                    contour_length: int = contour_len,
                ) -> tuple[float, float]:
                    wrapped_idx = contour_start + (
                        (idx - contour_start) % contour_length
                    )
                    return (
                        float(outline.points[wrapped_idx][0]) / 64.0,
                        float(outline.points[wrapped_idx][1]) / 64.0,
                    )

                p0 = last_point or _pt(contour_end)
                p1 = _pt(i)
                p2 = _pt(i + 1)
                p3 = _pt(i + 2)

                bezier_pts = _bezier_subdivide_cubic(p0, p1, p2, p3, tolerance)
                for bp in bezier_pts:
                    if not points or points[-1] != bp:
                        points.append(bp)
                last_point = p3
                i = contour_end + 1 if i + 2 > contour_end else i + 3

            else:  # Quadratic bezier (conic)
                p0 = last_point or (
                    float(outline.points[contour_end][0]) / 64.0,
                    float(outline.points[contour_end][1]) / 64.0,
                )
                p1 = (
                    float(outline.points[i][0]) / 64.0,
                    float(outline.points[i][1]) / 64.0,
                )

                next_i = i + 1 if i + 1 <= contour_end else start
                next_tag = outline.tags[next_i]
                next_pt = (
                    float(outline.points[next_i][0]) / 64.0,
                    float(outline.points[next_i][1]) / 64.0,
                )

                if next_tag & 1:  # Next is on-curve
                    p2 = next_pt
                    bezier_pts = _bezier_subdivide_quadratic(p0, p1, p2, tolerance)
                    for bp in bezier_pts:
                        if not points or points[-1] != bp:
                            points.append(bp)
                    last_point = p2
                    i += 1
                else:  # Implicit on-curve between two off-curve
                    p2 = ((p1[0] + next_pt[0]) / 2.0, (p1[1] + next_pt[1]) / 2.0)
                    bezier_pts = _bezier_subdivide_quadratic(p0, p1, p2, tolerance)
                    for bp in bezier_pts:
                        if not points or points[-1] != bp:
                            points.append(bp)
                    last_point = p2
                    i += 1

        if points:
            # Close the contour
            if points[0] != points[-1]:
                points.append(points[0])
            contours.append(points)

        start = contour_end + 1

    return contours


# ================================================================== #
# Contour Classification (outer vs holes)
# ================================================================== #


def _check_self_intersections(
    contours: list[list[tuple[float, float]]],
    *,
    eps: float = _VERTEX_DEDUP_THRESHOLD_MM,
) -> None:
    """
    Log warnings for contours with duplicate vertices (self-intersections).

        FreeType can produce self-intersecting outlines for certain complex/logo
        glyphs.  Native Altium splits these via a custom post-GPC pass
        (FUN_010b4450).  We detect and warn only - the containment classifier
        handles most cases correctly despite self-intersections.
    """
    for ci, contour in enumerate(contours):
        if len(contour) < 4:
            continue
        seen: dict[tuple[int, int], int] = {}
        for vi, pt in enumerate(contour):
            # Quantize to dedup threshold grid
            key = (round(pt[0] / eps), round(pt[1] / eps))
            if key in seen:
                log.debug(
                    "Self-intersecting contour %d: vertex %d ~= vertex %d (%.6f, %.6f)",
                    ci,
                    vi,
                    seen[key],
                    pt[0],
                    pt[1],
                )
                break  # one warning per contour is enough
            seen[key] = vi


def _classify_contours(contours: list[list[tuple[float, float]]]) -> list[GlyphPolygon]:
    """
    Classify contours into filled polygons using containment parity.

        Some fonts (especially custom/logo fonts) do not consistently follow a
        single winding convention for outer/hole contours. To stay robust, this
        classification derives hierarchy from geometric containment and applies
        even-odd parity on contour depth:
        - even depth contours are filled polygons
        - immediate odd-depth children become holes of that polygon

        Returns:
            List of GlyphPolygon, each with outline + immediate holes.
    """
    normalized = [contour for contour in contours if len(contour) >= 3]
    if not normalized:
        return []

    _check_self_intersections(normalized)

    bboxes = [_contour_bbox(contour) for contour in normalized]
    areas = [abs(_signed_area(contour)) for contour in normalized]

    # Build nearest-parent containment tree for all contours regardless of winding.
    parents: list[int | None] = [None] * len(normalized)
    for idx, contour in enumerate(normalized):
        probe = _contour_probe_point(contour)
        candidates: list[int] = []
        for container_idx, container in enumerate(normalized):
            if container_idx == idx:
                continue
            if areas[container_idx] <= areas[idx]:
                continue
            if not _bbox_contains(bboxes[container_idx], bboxes[idx]):
                continue
            if _point_in_polygon(probe, container):
                candidates.append(container_idx)
        if candidates:
            parents[idx] = min(
                candidates, key=lambda container_idx: areas[container_idx]
            )

    children: list[list[int]] = [[] for _ in normalized]
    for idx, parent_idx in enumerate(parents):
        if parent_idx is not None:
            children[parent_idx].append(idx)

    depths: list[int] = [0] * len(normalized)
    for idx in range(len(normalized)):
        depth = 0
        parent_idx = parents[idx]
        visited: set[int] = set()
        while parent_idx is not None and parent_idx not in visited:
            visited.add(parent_idx)
            depth += 1
            parent_idx = parents[parent_idx]
        depths[idx] = depth

    # Filled regions are even-depth contours in even-odd parity.
    filled_indices = [idx for idx, depth in enumerate(depths) if depth % 2 == 0]
    filled_indices.sort(key=lambda idx: (depths[idx], -areas[idx], idx))

    result: list[GlyphPolygon] = []
    for idx in filled_indices:
        hole_indices = [
            child_idx
            for child_idx in children[idx]
            if depths[child_idx] == depths[idx] + 1
        ]
        hole_indices.sort(key=lambda hole_idx: (-areas[hole_idx], hole_idx))
        result.append(
            GlyphPolygon(
                outline=normalized[idx],
                holes=[normalized[hole_idx] for hole_idx in hole_indices],
            )
        )

    return result


# ================================================================== #
# TrueType Text Renderer
# ================================================================== #

# FreeType reference size for high-quality outline extraction.
# Larger = more detail in the outline, but the final output is scaled
# to the target size so the choice doesn't affect accuracy much.
_REFERENCE_SIZE = 256  # points
_REFERENCE_DPI = 72  # standard typographic DPI (1pt = 1px)


def _truetype_outline_load_flags(freetype_module: Any) -> int:
    """
    Use unhinted vector outlines to match native GDI+ path geometry.
    """
    return (
        freetype_module.FT_LOAD_NO_BITMAP
        | freetype_module.FT_LOAD_NO_HINTING
        | freetype_module.FT_LOAD_NO_AUTOHINT
    )


# Callback for resolving a font to a filesystem TTF path.
FontPathResolver = Callable[[str, bool, bool], str | None]


def _find_font_path(
    font_name: str,
    bold: bool = False,
    italic: bool = False,
    font_resolver: FontPathResolver | None = None,
) -> str | None:
    """
    Find system font file path.
    """
    from .altium_ttf_metrics import get_font_path, get_font_path_with_style

    normalized_name = " ".join(font_name.strip().strip("\"'").split()).lower()
    mapped_path = _FONT_MAP.get(normalized_name)
    if mapped_path:
        try:
            with open(mapped_path, "rb"):
                return mapped_path
        except OSError:
            pass

    if font_resolver is not None:
        try:
            custom = font_resolver(font_name, bold, italic)
        except Exception:
            custom = None
        if custom:
            try:
                with open(custom, "rb"):
                    return custom
            except OSError:
                pass

    shared_path = get_font_path_with_style(font_name, bold=bold, italic=italic)
    if shared_path is not None:
        return shared_path

    return get_font_path("Arial")


def _translated_glyph_polygons(
    polygons: list[GlyphPolygon],
    *,
    dx_mm: float,
    dy_mm: float,
) -> list[GlyphPolygon]:
    """
    Return translated copies so cached glyph geometry is never mutated by callers.
    """
    if not polygons:
        return []

    def translate_point(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] + dx_mm, point[1] + dy_mm)

    return [
        GlyphPolygon(
            outline=[translate_point(point) for point in poly.outline],
            holes=[[translate_point(point) for point in hole] for hole in poly.holes],
        )
        for poly in polygons
    ]


class TrueTypeTextRenderer:
    """
    Renders TrueType text to filled polygon contours.

        Uses FreeType for glyph outline extraction and HarfBuzz for text
        shaping (kerning, ligatures). Output polygons can be emitted as
        IPC-2581 Contour/Polygon/Cutout or SVG path elements.

        Altium Sizing Model:
            PCB text Height = cell height (ascent + descent scaled)
            em_size = Height * factor
            factor = units_per_em / (usWinAscent + usWinDescent)
    """

    def __init__(self) -> None:
        self._ft_face_cache: dict[str, Any] = {}
        self._font_data_cache: dict[str, bytes] = {}
        self._hb_font_cache: dict[tuple[str, int], Any] = {}
        self._glyph_polygon_cache: dict[
            tuple[str, int, float, float, float], list[GlyphPolygon]
        ] = {}

    def _glyph_polygons_at_origin(
        self,
        font_path: str,
        glyph_id: int,
        outline: Any,
        *,
        scale: float,
        x_scale: float,
        flatten_tolerance: float,
    ) -> list[GlyphPolygon]:
        """
        Cache scaled glyph contours before per-occurrence translation.

        FreeType face/font objects are already cached, but the expensive work for
        schematic polytext is outline flattening plus contour classification.
        Real schematics repeat the same glyphs thousands of times, so cache that
        geometry at the origin and translate copies for each character.
        """
        cache_key = (
            font_path,
            glyph_id,
            round(float(scale), 12),
            round(float(x_scale), 12),
            round(float(flatten_tolerance), 12),
        )
        cached = self._glyph_polygon_cache.get(cache_key)
        if cached is not None:
            return cached

        contours = _outline_to_contours(outline, flatten_tolerance)
        transformed_contours = []
        for contour in contours:
            transformed = []
            for pt in contour:
                transformed.append((pt[0] * scale * x_scale, pt[1] * scale))
            if len(transformed) >= 3:
                transformed_contours.append(transformed)

        glyph_polygons = _classify_contours(transformed_contours)

        # Remove near-coincident and collinear vertices (matches native
        # gpc_ContourRemoveRedundantVertices post-boolean cleanup). Applied
        # after classification to preserve topology needed for containment.
        for poly in glyph_polygons:
            cleaned = _remove_redundant_vertices(poly.outline)
            if len(cleaned) >= 3:
                poly.outline = cleaned
            poly.holes = [
                ch for h in poly.holes if len(ch := _remove_redundant_vertices(h)) >= 3
            ]

        self._glyph_polygon_cache[cache_key] = glyph_polygons
        return glyph_polygons

    def _get_face(self, font_path: str) -> Any:
        """
        Get or load a FreeType face.
        """
        import freetype

        if font_path in self._ft_face_cache:
            return self._ft_face_cache[font_path]

        face = freetype.Face(font_path)
        self._ft_face_cache[font_path] = face

        # Cache font data for HarfBuzz
        if font_path not in self._font_data_cache:
            with open(font_path, "rb") as f:
                self._font_data_cache[font_path] = f.read()

        return face

    def _get_hb_font(self, font_path: str, upem: int) -> Any:
        """
        Get or create a HarfBuzz font.
        """
        import uharfbuzz as hb

        hb_mod: Any = hb

        cache_key = (font_path, upem)
        if cache_key in self._hb_font_cache:
            return self._hb_font_cache[cache_key]

        if font_path not in self._font_data_cache:
            with open(font_path, "rb") as f:
                self._font_data_cache[font_path] = f.read()

        blob = hb_mod.Blob(self._font_data_cache[font_path])
        hb_face = hb_mod.Face(blob)
        hb_font = hb_mod.Font(hb_face)
        hb_font.scale = (upem, upem)

        self._hb_font_cache[cache_key] = hb_font
        return hb_font

    def _get_font_factor(self, face: Any, font_path: str | None = None) -> float:
        """
        Get Altium's GDI+ font factor.

                factor = units_per_em / (usWinAscent + usWinDescent)

                This is the same factor used in altium_ttf_metrics.py TrueTypeFont.get_factor()
        """
        upem = face.units_per_EM

        # Prefer OS/2 win metrics (matches Altium/GDI+ for fonts where hhea
        # and OS/2 differ, e.g. Myriad Pro).
        if font_path:
            try:
                from .altium_ttf_metrics import get_font

                ttf_font = get_font(font_path)
                ascender = float(getattr(ttf_font, "ascender", 0.0))
                descender = float(getattr(ttf_font, "descender", 0.0))
                cell_height = ascender + descender
                if cell_height > 0:
                    return upem / cell_height
            except Exception:
                pass

        # Fallback to FreeType metrics if OS/2 parsing is unavailable.
        cell_height = face.ascender + abs(face.descender)
        if cell_height == 0:
            return 1.0
        return upem / cell_height

    def _render_single_line(
        self,
        line_text: str,
        font_path: str,
        face: Any,
        upem: int,
        scale: float,
        x_scale: float = 1.0,
        flatten_tolerance: float = 0.05,
        y_offset_mm: float = 0.0,
        *,
        anchor_to_ink_edge: bool = True,
        use_hb_positioning: bool = False,
    ) -> tuple[list[list[GlyphPolygon]], float]:
        """
        Render a single line of text to polygon contours at the origin.

                Positioning modes:
                    use_hb_positioning=False:
                        Uses raw FreeType hmtx advances (no kerning), matching legacy
                        behavior tuned for Altium/PCB text paths.
                    use_hb_positioning=True:
                        Uses HarfBuzz glyph positions (advance + offsets), producing
                        browser-like spacing for schematic SVG polytext.

                Returns:
                    Tuple of (per-character polygon lists, advance_width_mm).
                    advance_width_mm is the total advance span in mm (used for
                    frame justification positioning to match GDI+ origin-based
                    alignment).
        """
        import freetype
        import uharfbuzz as hb

        hb_mod: Any = hb

        characters: list[list[GlyphPolygon]] = []
        if not line_text:
            return characters, 0.0

        # Use HarfBuzz only for Unicode-to-glyph ID mapping (handles cmap, GSUB).
        # We do NOT use HarfBuzz's x_advance/x_offset/y_offset because GDI+
        # AddString does not apply GPOS positioning or kern table kerning.
        hb_font = self._get_hb_font(font_path, upem)
        buf = hb_mod.Buffer()
        buf.add_str(line_text)
        buf.guess_segment_properties()
        hb_mod.shape(hb_font, buf)
        glyph_infos = buf.glyph_infos
        glyph_positions = buf.glyph_positions

        if not glyph_infos:
            return characters, 0.0

        unit_to_ref = _REFERENCE_SIZE / float(upem)

        cursor_x = 0.0
        if anchor_to_ink_edge:
            # For non-frame text, anchor at first glyph ink edge.
            first_glyph_id = glyph_infos[0].codepoint
            face.load_glyph(first_glyph_id, _truetype_outline_load_flags(freetype))
            first_lsb = face.glyph.metrics.horiBearingX / 64.0  # reference px
            cursor_x = -first_lsb

        for i, info in enumerate(glyph_infos):
            glyph_id = info.codepoint
            pos = glyph_positions[i]

            # Load glyph outline
            face.load_glyph(glyph_id, _truetype_outline_load_flags(freetype))
            outline = face.glyph.outline

            if use_hb_positioning:
                # HarfBuzz positions are returned in font units (hb_font.scale=upem).
                # Convert to reference px so contour extraction remains stable.
                x_offset_ref = pos.x_offset * unit_to_ref
                y_offset_ref = pos.y_offset * unit_to_ref
                glyph_origin_x = cursor_x + x_offset_ref
                glyph_origin_y = y_offset_ref
                raw_advance = pos.x_advance * unit_to_ref
            else:
                # Raw advance from hmtx (no kerning) - linearHoriAdvance is
                # in 16.16 fixed-point at the face's current size.
                glyph_origin_x = cursor_x
                glyph_origin_y = 0.0
                raw_advance = face.glyph.linearHoriAdvance / 65536.0

            char_polygons = []

            if outline.n_points > 0:
                cached_polygons = self._glyph_polygons_at_origin(
                    font_path,
                    glyph_id,
                    outline,
                    scale=scale,
                    x_scale=x_scale,
                    flatten_tolerance=flatten_tolerance,
                )
                char_polygons = _translated_glyph_polygons(
                    cached_polygons,
                    dx_mm=glyph_origin_x * scale * x_scale,
                    dy_mm=glyph_origin_y * scale + y_offset_mm,
                )

            characters.append(char_polygons)
            cursor_x += raw_advance

        advance_width_mm = cursor_x * scale * x_scale
        return characters, advance_width_mm

    def _measure_text_advance_mm(
        self,
        text: str,
        font_path: str,
        face: Any,
        upem: int,
        scale: float,
        *,
        use_hb_positioning: bool = False,
    ) -> float:
        """
        Measure total advance width of text in mm without generating polygons.

                Uses the same positioning mode as _render_single_line() so width
                measurements match rendered output exactly.
        """
        import freetype
        import uharfbuzz as hb

        hb_mod: Any = hb

        if not text:
            return 0.0

        hb_font = self._get_hb_font(font_path, upem)
        buf = hb_mod.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb_mod.shape(hb_font, buf)

        cursor_x = 0.0
        if use_hb_positioning:
            unit_to_ref = _REFERENCE_SIZE / float(upem)
            for pos in buf.glyph_positions:
                cursor_x += pos.x_advance * unit_to_ref
        else:
            for info in buf.glyph_infos:
                glyph_id = info.codepoint
                # Match _render_single_line() exactly: linearHoriAdvance is 16.16.
                face.load_glyph(glyph_id, _truetype_outline_load_flags(freetype))
                cursor_x += face.glyph.linearHoriAdvance / 65536.0

        return cursor_x * scale

    def _word_wrap_lines(
        self,
        lines: list[str],
        font_path: str,
        face: Any,
        upem: int,
        scale: float,
        frame_width_mm: float,
        *,
        use_hb_positioning: bool = False,
    ) -> list[str]:
        """
        Wrap lines at word boundaries when text exceeds frame width.

                Matches Altium's multiline text word wrap behavior:
                - Space and tab are word separators
                - Accumulate words until combined width exceeds frame width
                - When overflow: flush current line, start new line with the word
        """
        wrapped: list[str] = []
        space_advance = self._measure_text_advance_mm(
            " ",
            font_path,
            face,
            upem,
            scale,
            use_hb_positioning=use_hb_positioning,
        )

        for line in lines:
            if not line.strip():
                wrapped.append(line)
                continue

            # Measure full line first - skip wrapping if it fits
            full_advance = self._measure_text_advance_mm(
                line,
                font_path,
                face,
                upem,
                scale,
                use_hb_positioning=use_hb_positioning,
            )
            if full_advance <= frame_width_mm:
                wrapped.append(line)
                continue

            # Split into words on spaces (Altium uses space and tab)
            words = line.split(" ")
            current_words: list[str] = []
            current_advance = 0.0

            for word in words:
                if not word:
                    # Consecutive spaces produce empty strings from split
                    if current_words:
                        current_advance += space_advance
                    continue

                word_advance = self._measure_text_advance_mm(
                    word,
                    font_path,
                    face,
                    upem,
                    scale,
                    use_hb_positioning=use_hb_positioning,
                )

                if not current_words:
                    # First word on the line - always accept
                    current_words.append(word)
                    current_advance = word_advance
                else:
                    # Test if adding this word (with space) fits
                    test_advance = current_advance + space_advance + word_advance
                    if test_advance <= frame_width_mm:
                        current_words.append(word)
                        current_advance = test_advance
                    else:
                        # Flush current line, start new with this word
                        wrapped.append(" ".join(current_words))
                        current_words = [word]
                        current_advance = word_advance

            if current_words:
                wrapped.append(" ".join(current_words))

        return wrapped

    def render(
        self,
        text: str,
        font_name: str = "Arial",
        height_mm: float = 1.524,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        rotation: float = 0.0,
        is_mirrored: bool = False,
        is_bold: bool = False,
        is_italic: bool = False,
        x_scale: float = 1.0,
        flatten_tolerance: float = 0.05,
        justification: int = 3,
        frame_width_mm: float = 0.0,
        frame_height_mm: float = 0.0,
        is_inverted: bool = False,
        inverted_margin_mm: float = 0.0,
        anchor_to_ink_edge: bool | None = None,
        font_resolver: FontPathResolver | None = None,
        use_hb_positioning: bool = False,
    ) -> TextPolygonResult:
        """
        Render TrueType text to polygon contours.
        """
        result = TextPolygonResult()
        if not text:
            return result

        font_path, face, upem, scale = self._resolve_render_font(
            font_name,
            height_mm=height_mm,
            is_bold=is_bold,
            is_italic=is_italic,
            font_resolver=font_resolver,
        )
        if font_path is None or face is None:
            return result

        lines, line_spacing_mm = self._resolved_render_lines(
            text,
            font_path,
            face,
            upem,
            scale,
            height_mm=height_mm,
            frame_width_mm=frame_width_mm,
            frame_height_mm=frame_height_mm,
            use_hb_positioning=use_hb_positioning,
        )
        line_char_ranges, line_advance_widths = self._render_true_type_lines(
            result,
            lines,
            font_path,
            face,
            upem,
            scale,
            height_mm=height_mm,
            x_scale=x_scale,
            flatten_tolerance=flatten_tolerance,
            line_spacing_mm=line_spacing_mm,
            anchor_to_ink_edge=self._effective_anchor_to_ink_edge(
                anchor_to_ink_edge,
                frame_width_mm=frame_width_mm,
                frame_height_mm=frame_height_mm,
            ),
            use_hb_positioning=use_hb_positioning,
        )
        self._apply_true_type_frame_justification(
            result,
            justification=justification,
            frame_width_mm=frame_width_mm,
            frame_height_mm=frame_height_mm,
            inverted_margin_mm=inverted_margin_mm,
            line_char_ranges=line_char_ranges,
            line_advance_widths=line_advance_widths,
        )

        if is_inverted:
            result = _apply_knockout_transform(
                result,
                inverted_margin_mm,
                frame_width_mm,
                frame_height_mm,
            )

        if rotation != 0.0 or x_mm != 0.0 or y_mm != 0.0 or is_mirrored:
            _apply_global_transform(result, x_mm, y_mm, rotation, is_mirrored)

        return result

    def _resolve_render_font(
        self,
        font_name: str,
        *,
        height_mm: float,
        is_bold: bool,
        is_italic: bool,
        font_resolver: FontPathResolver | None,
    ) -> tuple[str | None, object | None, int, float]:
        font_path = _find_font_path(
            font_name,
            is_bold,
            is_italic,
            font_resolver=font_resolver,
        )
        if font_path is None:
            log.warning(
                "Font not found: %s (bold=%s, italic=%s)",
                font_name,
                is_bold,
                is_italic,
            )
            return None, None, 0, 0.0

        face = self._get_face(font_path)
        upem = face.units_per_EM or 2048
        factor = self._get_font_factor(face, font_path)
        em_size_mm = height_mm * factor
        scaler = _REFERENCE_SIZE * 64
        face.set_char_size(0, scaler, _REFERENCE_DPI, 0)
        scale = em_size_mm / _REFERENCE_SIZE
        return font_path, face, upem, scale

    def _effective_anchor_to_ink_edge(
        self,
        anchor_to_ink_edge: bool | None,
        *,
        frame_width_mm: float,
        frame_height_mm: float,
    ) -> bool:
        if anchor_to_ink_edge is not None:
            return bool(anchor_to_ink_edge)
        return not (frame_width_mm > 0 and frame_height_mm > 0)

    def _resolved_render_lines(
        self,
        text: str,
        font_path: str,
        face: object,
        upem: int,
        scale: float,
        *,
        height_mm: float,
        frame_width_mm: float,
        frame_height_mm: float,
        use_hb_positioning: bool,
    ) -> tuple[list[str], float | None]:
        lines = text.replace("\r\n", "\n").split("\n")
        if frame_width_mm <= 0 or frame_height_mm <= 0:
            return lines, None

        line_spacing_mm = self._ttf_line_spacing_mm(font_path, height_mm)
        if frame_height_mm > line_spacing_mm + 1e-9:
            lines = self._word_wrap_lines(
                lines,
                font_path,
                face,
                upem,
                scale,
                frame_width_mm,
                use_hb_positioning=use_hb_positioning,
            )
        return lines, line_spacing_mm

    def _ttf_line_spacing_mm(self, font_path: str, height_mm: float) -> float:
        from .altium_ttf_metrics import get_font

        try:
            return get_font(font_path).get_pcb_line_spacing(height_mm)
        except Exception:
            return height_mm

    def _render_true_type_lines(
        self,
        result: TextPolygonResult,
        lines: list[str],
        font_path: str,
        face: object,
        upem: int,
        scale: float,
        *,
        height_mm: float,
        x_scale: float,
        flatten_tolerance: float,
        line_spacing_mm: float | None,
        anchor_to_ink_edge: bool,
        use_hb_positioning: bool,
    ) -> tuple[list[tuple[int, int]], list[float]]:
        if len(lines) <= 1:
            chars, advance_mm = self._render_single_line(
                lines[0] if lines else "",
                font_path,
                face,
                upem,
                scale,
                x_scale,
                flatten_tolerance,
                anchor_to_ink_edge=anchor_to_ink_edge,
                use_hb_positioning=use_hb_positioning,
            )
            result.characters = chars
            return [(0, len(chars))], [advance_mm]

        effective_line_spacing_mm = (
            line_spacing_mm
            if line_spacing_mm is not None
            else self._ttf_line_spacing_mm(font_path, height_mm)
        )
        line_char_ranges: list[tuple[int, int]] = []
        line_advance_widths: list[float] = []
        char_idx = 0
        n_lines = len(lines)
        for line_idx, line_text in enumerate(lines):
            y_offset = effective_line_spacing_mm * (n_lines - 1 - line_idx)
            chars, advance_mm = self._render_single_line(
                line_text,
                font_path,
                face,
                upem,
                scale,
                x_scale,
                flatten_tolerance,
                y_offset,
                anchor_to_ink_edge=anchor_to_ink_edge,
                use_hb_positioning=use_hb_positioning,
            )
            n_chars = len(chars)
            result.characters.extend(chars)
            line_char_ranges.append((char_idx, char_idx + n_chars))
            line_advance_widths.append(advance_mm)
            char_idx += n_chars
        return line_char_ranges, line_advance_widths

    def _apply_true_type_frame_justification(
        self,
        result: TextPolygonResult,
        *,
        justification: int,
        frame_width_mm: float,
        frame_height_mm: float,
        inverted_margin_mm: float,
        line_char_ranges: list[tuple[int, int]],
        line_advance_widths: list[float],
    ) -> None:
        if frame_width_mm <= 0 or frame_height_mm <= 0:
            return

        eff_fw = frame_width_mm
        eff_fh = frame_height_mm
        frame_inset_mm = 0.0
        if (
            inverted_margin_mm > 0
            and frame_width_mm > (2.0 * inverted_margin_mm)
            and frame_height_mm > (2.0 * inverted_margin_mm)
        ):
            frame_inset_mm = inverted_margin_mm
            eff_fw = frame_width_mm - 2.0 * frame_inset_mm
            eff_fh = frame_height_mm - 2.0 * frame_inset_mm

        _apply_perline_frame_justification(
            result,
            justification,
            eff_fw,
            eff_fh,
            line_char_ranges,
            line_advance_widths,
        )
        if frame_inset_mm > 0.0:
            _translate_text_polygons(
                result.characters,
                frame_inset_mm,
                frame_inset_mm,
            )


def _compute_frame_justification_offset(
    bbox: tuple[float, float, float, float],
    justification: int,
    frame_width_mm: float,
    frame_height_mm: float,
) -> tuple[float, float]:
    """
    Compute (dx, dy) offset for text within a frame rectangle.

        Positions the text within the frame defined by (0,0)-(frame_width, frame_height).
        The anchor (X,Y) is always the bottom-left corner of the frame,
        and justification determines where the text sits inside it.

        Args:
            bbox: (min_x, min_y, max_x, max_y) of text geometry in mm
            justification: ALTIUM_TEXT_POSITION enum value (0-9)
            frame_width_mm: Frame width in mm
            frame_height_mm: Frame height in mm

        Returns:
            (dx, dy) translation to apply before global transform.

        ALTIUM_TEXT_POSITION enum:
            0 = MANUAL (treat as LEFT_BOTTOM)
            1 = LEFT_TOP      2 = LEFT_CENTER      3 = LEFT_BOTTOM
            4 = CENTER_TOP     5 = CENTER_CENTER    6 = CENTER_BOTTOM
            7 = RIGHT_TOP      8 = RIGHT_CENTER     9 = RIGHT_BOTTOM
    """
    min_x, min_y, max_x, max_y = bbox
    text_width = max_x - min_x
    text_height = max_y - min_y

    # First normalize text to origin (left edge at x=0, bottom at y=0)
    dx_base = -min_x
    dy_base = -min_y

    # Horizontal offset within frame
    if justification in (1, 2, 3, 0):  # LEFT (and MANUAL)
        dx = dx_base
    elif justification in (4, 5, 6):  # CENTER
        dx = dx_base + (frame_width_mm - text_width) / 2.0
    elif justification in (7, 8, 9):  # RIGHT
        dx = dx_base + (frame_width_mm - text_width)
    else:
        dx = dx_base  # fallback: LEFT

    # Vertical offset within frame
    if justification in (3, 6, 9, 0):  # BOTTOM (and MANUAL)
        dy = dy_base
    elif justification in (2, 5, 8):  # CENTER
        dy = dy_base + (frame_height_mm - text_height) / 2.0
    elif justification in (1, 4, 7):  # TOP
        dy = dy_base + (frame_height_mm - text_height)
    else:
        dy = dy_base  # fallback: BOTTOM

    return (dx, dy)


def _apply_frame_justification(
    result: TextPolygonResult,
    justification: int,
    frame_width_mm: float,
    frame_height_mm: float,
) -> None:
    """
    Apply frame justification offset to TrueType text polygons.
    """
    # Compute bounding box of all geometry
    all_x = []
    all_y = []
    for char_polys in result.characters:
        for poly in char_polys:
            for px, py in poly.outline:
                all_x.append(px)
                all_y.append(py)

    if not all_x:
        return

    bbox = (min(all_x), min(all_y), max(all_x), max(all_y))
    dx, dy = _compute_frame_justification_offset(
        bbox, justification, frame_width_mm, frame_height_mm
    )

    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return

    # Shift all geometry
    for char_polys in result.characters:
        for poly in char_polys:
            poly.outline = [(px + dx, py + dy) for px, py in poly.outline]
            poly.holes = [
                [(px + dx, py + dy) for px, py in hole] for hole in poly.holes
            ]


def _frame_alignment_components(justification: int) -> tuple[int, int]:
    if justification <= 0:
        return 0, 2
    return (justification - 1) // 3, (justification - 1) % 3


def _text_polygon_bbox(
    characters: list[list[GlyphPolygon]],
) -> tuple[float, float, float, float] | None:
    all_x: list[float] = []
    all_y: list[float] = []
    for char_polys in characters:
        for poly in char_polys:
            for px, py in poly.outline:
                all_x.append(px)
                all_y.append(py)
    if not all_x:
        return None
    return (min(all_x), min(all_y), max(all_x), max(all_y))


def _line_polygon_x_metrics(
    line_chars: list[list[GlyphPolygon]],
) -> tuple[float, float] | None:
    line_x: list[float] = []
    for char_polys in line_chars:
        for poly in char_polys:
            for px, _ in poly.outline:
                line_x.append(px)
    if not line_x:
        return None
    line_min_x = min(line_x)
    return line_min_x, max(line_x) - line_min_x


def _vertical_frame_offset(
    *,
    min_y: float,
    max_y: float,
    v_align: int,
    frame_height_mm: float,
) -> float:
    text_height = max_y - min_y
    dy_base = -min_y
    if v_align == 0:
        return dy_base + (frame_height_mm - text_height)
    if v_align == 1:
        return dy_base + (frame_height_mm - text_height) / 2.0
    return dy_base


def _horizontal_frame_offset(
    *,
    h_align: int,
    frame_width_mm: float,
    line_min_x: float,
    line_width: float,
    advance_width: float | None,
) -> float:
    if advance_width is not None:
        if h_align == 0:
            return 0.0
        if h_align == 1:
            return (frame_width_mm - advance_width) / 2.0
        return frame_width_mm - advance_width

    dx_base = -line_min_x
    if h_align == 0:
        return dx_base
    if h_align == 1:
        return dx_base + (frame_width_mm - line_width) / 2.0
    return dx_base + (frame_width_mm - line_width)


def _translate_text_polygons(
    characters: list[list[GlyphPolygon]],
    dx: float,
    dy: float,
) -> None:
    for char_polys in characters:
        for poly in char_polys:
            poly.outline = [(px + dx, py + dy) for px, py in poly.outline]
            poly.holes = [
                [(px + dx, py + dy) for px, py in hole] for hole in poly.holes
            ]


def _apply_perline_frame_justification(
    result: TextPolygonResult,
    justification: int,
    frame_width_mm: float,
    frame_height_mm: float,
    line_char_ranges: list[tuple[int, int]],
    line_advance_widths: list[float] | None = None,
) -> None:
    """
    Apply per-line horizontal + block vertical frame justification.
    """
    if not result.characters:
        return

    overall_bbox = _text_polygon_bbox(result.characters)
    if overall_bbox is None:
        return

    h_align, v_align = _frame_alignment_components(justification)
    dy = _vertical_frame_offset(
        min_y=overall_bbox[1],
        max_y=overall_bbox[3],
        v_align=v_align,
        frame_height_mm=frame_height_mm,
    )

    for line_idx, (start, end) in enumerate(line_char_ranges):
        line_chars = result.characters[start:end]
        if not line_chars:
            continue
        line_metrics = _line_polygon_x_metrics(line_chars)
        if line_metrics is None:
            continue

        line_min_x, line_width = line_metrics
        advance_width = (
            line_advance_widths[line_idx]
            if line_advance_widths and line_idx < len(line_advance_widths)
            else None
        )
        if advance_width is not None and (
            advance_width <= 1e-9
            or (line_width > 1e-9 and line_width > (advance_width * 1.5))
        ):
            advance_width = None

        dx = _horizontal_frame_offset(
            h_align=h_align,
            frame_width_mm=frame_width_mm,
            line_min_x=line_min_x,
            line_width=line_width,
            advance_width=advance_width,
        )
        _translate_text_polygons(line_chars, dx, dy)


def _apply_stroke_frame_justification(
    result: StrokeTextResult,
    justification: int,
    frame_width_mm: float,
    frame_height_mm: float,
) -> None:
    """
    Apply frame justification offset to stroke text line segments.
    """
    if not result.lines:
        return

    # Compute bounding box including stroke width
    half_w = result.stroke_width_mm / 2.0
    all_x = []
    all_y = []
    for x1, y1, x2, y2 in result.lines:
        all_x.extend([x1 - half_w, x1 + half_w, x2 - half_w, x2 + half_w])
        all_y.extend([y1 - half_w, y1 + half_w, y2 - half_w, y2 + half_w])

    bbox = (min(all_x), min(all_y), max(all_x), max(all_y))
    dx, dy = _compute_frame_justification_offset(
        bbox, justification, frame_width_mm, frame_height_mm
    )

    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return

    result.lines = [
        (x1 + dx, y1 + dy, x2 + dx, y2 + dy) for x1, y1, x2, y2 in result.lines
    ]


def _stroke_lines_bbox(
    lines: list[tuple[float, float, float, float]],
    stroke_width_mm: float,
) -> tuple[float, float, float, float] | None:
    """
    Return a stroke-text bbox including line width.
    """
    if not lines:
        return None

    half_w = stroke_width_mm / 2.0
    all_x: list[float] = []
    all_y: list[float] = []
    for x1, y1, x2, y2 in lines:
        all_x.extend([x1 - half_w, x1 + half_w, x2 - half_w, x2 + half_w])
        all_y.extend([y1 - half_w, y1 + half_w, y2 - half_w, y2 + half_w])
    return (min(all_x), min(all_y), max(all_x), max(all_y))


def _apply_perline_stroke_frame_justification(
    result: StrokeTextResult,
    justification: int,
    frame_width_mm: float,
    frame_height_mm: float,
    line_ranges: list[tuple[int, int]],
    line_advance_widths: list[float] | None = None,
) -> None:
    """
    Apply per-line frame justification to multiline stroke text.
    """
    if not result.lines:
        return

    if justification <= 0:
        h_align, v_align = 0, 2  # MANUAL -> LEFT_BOTTOM
    else:
        h_align = (justification - 1) // 3
        v_align = (justification - 1) % 3

    overall_bbox = _stroke_lines_bbox(result.lines, result.stroke_width_mm)
    if overall_bbox is None:
        return

    overall_min_y = overall_bbox[1]
    overall_max_y = overall_bbox[3]
    text_height = overall_max_y - overall_min_y

    dy_base = -overall_min_y
    if v_align == 0:  # TOP
        dy = dy_base + (frame_height_mm - text_height)
    elif v_align == 1:  # CENTER
        dy = dy_base + (frame_height_mm - text_height) / 2.0
    else:  # BOTTOM
        dy = dy_base

    updated_lines = list(result.lines)
    for line_idx, (start, end) in enumerate(line_ranges):
        line_lines = result.lines[start:end]
        if not line_lines:
            continue

        line_bbox = _stroke_lines_bbox(line_lines, result.stroke_width_mm)
        if line_bbox is None:
            continue

        line_min_x, _, line_max_x, _ = line_bbox
        line_width = line_max_x - line_min_x
        dx_base = -line_min_x

        advance_w = (
            line_advance_widths[line_idx]
            if line_advance_widths and line_idx < len(line_advance_widths)
            else None
        )
        advance_width = (
            advance_w if advance_w is not None and advance_w > 1e-9 else None
        )
        if (
            advance_width is not None
            and line_width > 1e-9
            and line_width > (advance_width * 1.5)
        ):
            advance_width = None

        if advance_width is not None:
            if h_align == 0:  # LEFT
                dx = 0.0
            elif h_align == 1:  # CENTER
                dx = (frame_width_mm - advance_width) / 2.0
            else:  # RIGHT
                dx = frame_width_mm - advance_width
        else:
            if h_align == 0:  # LEFT
                dx = dx_base
            elif h_align == 1:  # CENTER
                dx = dx_base + (frame_width_mm - line_width) / 2.0
            else:  # RIGHT
                dx = dx_base + (frame_width_mm - line_width)

        for seg_idx in range(start, end):
            x1, y1, x2, y2 = updated_lines[seg_idx]
            updated_lines[seg_idx] = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)

    result.lines = updated_lines


def _knockout_rect_outline(
    bbox: tuple[float, float, float, float],
    margin_mm: float,
    frame_width_mm: float,
    frame_height_mm: float,
) -> tuple[list[tuple[float, float]], tuple[float, float, float, float]]:
    if frame_width_mm > 0 and frame_height_mm > 0:
        rect_x0 = 0.0
        rect_y0 = 0.0
        rect_x1 = frame_width_mm
        rect_y1 = frame_height_mm
    else:
        min_x, min_y, max_x, max_y = bbox
        rect_x0 = min_x - margin_mm
        rect_y0 = min_y - margin_mm
        rect_x1 = max_x + margin_mm
        rect_y1 = max_y + margin_mm

    rect_outline = [
        (rect_x0, rect_y0),
        (rect_x1, rect_y0),
        (rect_x1, rect_y1),
        (rect_x0, rect_y1),
        (rect_x0, rect_y0),
    ]
    return rect_outline, (rect_x0, rect_y0, rect_x1, rect_y1)


def _flatten_text_contours(
    result: TextPolygonResult,
) -> list[list[tuple[float, float]]]:
    contours: list[list[tuple[float, float]]] = []
    for char_polys in result.characters:
        for poly in char_polys:
            if len(poly.outline) >= 3:
                contours.append(poly.outline)
            for hole in poly.holes:
                if len(hole) >= 3:
                    contours.append(hole)
    return contours


def _clip_knockout_contours(
    contours: list[list[tuple[float, float]]],
    rect_bounds: tuple[float, float, float, float],
) -> list[list[tuple[float, float]]]:
    clipped_contours: list[list[tuple[float, float]]] = []
    for contour in contours:
        clipped_contours.extend(
            _clip_contour_to_rect(
                contour,
                x_min=rect_bounds[0],
                y_min=rect_bounds[1],
                x_max=rect_bounds[2],
                y_max=rect_bounds[3],
            )
        )
    return clipped_contours


def _build_knockout_hierarchy(
    contours: list[list[tuple[float, float]]],
) -> tuple[list[list[int]], list[int], list[int]]:
    bboxes = [_contour_bbox(contour) for contour in contours]
    areas = [abs(_signed_area(contour)) for contour in contours]
    parents: list[int | None] = [None] * len(contours)
    for idx, contour in enumerate(contours):
        probe = _contour_probe_point(contour)
        candidates: list[int] = []
        for container_idx, container in enumerate(contours):
            if container_idx == idx:
                continue
            if areas[container_idx] <= areas[idx]:
                continue
            if not _bbox_contains(bboxes[container_idx], bboxes[idx]):
                continue
            if _point_in_polygon(probe, container):
                candidates.append(container_idx)
        if candidates:
            parents[idx] = min(
                candidates, key=lambda candidate_idx: areas[candidate_idx]
            )

    children: list[list[int]] = [[] for _ in contours]
    for idx, parent_idx in enumerate(parents):
        if parent_idx is not None:
            children[parent_idx].append(idx)

    depths: list[int] = [0] * len(contours)
    for idx in range(len(contours)):
        depth = 0
        parent_idx = parents[idx]
        visited: set[int] = set()
        while parent_idx is not None and parent_idx not in visited:
            visited.add(parent_idx)
            depth += 1
            parent_idx = parents[parent_idx]
        depths[idx] = depth

    top_level = [idx for idx, parent_idx in enumerate(parents) if parent_idx is None]
    island_indices = [idx for idx, depth in enumerate(depths) if depth % 2 == 1]
    island_indices.sort(key=lambda idx: (depths[idx], -areas[idx]))
    return children, top_level, island_indices


def _build_knockout_result(
    rect_outline: list[tuple[float, float]],
    contours: list[list[tuple[float, float]]],
    children: list[list[int]],
    top_level: list[int],
    island_indices: list[int],
) -> TextPolygonResult:
    knockout = TextPolygonResult()
    for idx in island_indices:
        island_holes = [contours[ch] for ch in children[idx]]
        knockout.characters.append(
            [GlyphPolygon(outline=contours[idx], holes=island_holes)]
        )

    rect_cutouts = [contours[idx] for idx in top_level]
    knockout.characters.append([GlyphPolygon(outline=rect_outline, holes=rect_cutouts)])
    return knockout


def _apply_knockout_transform(
    result: TextPolygonResult,
    margin_mm: float,
    frame_width_mm: float = 0.0,
    frame_height_mm: float = 0.0,
    *,
    clip_to_frame: bool = False,
) -> TextPolygonResult:
    """
    Transform normal text polygons into knockout/inverted form.
    """
    bbox = _text_polygon_bbox(result.characters)
    if bbox is None:
        return result

    rect_outline, rect_bounds = _knockout_rect_outline(
        bbox,
        margin_mm,
        frame_width_mm,
        frame_height_mm,
    )
    contours = _flatten_text_contours(result)
    if clip_to_frame and frame_width_mm > 0 and frame_height_mm > 0:
        contours = _clip_knockout_contours(contours, rect_bounds)

    if not contours:
        return TextPolygonResult(
            characters=[[GlyphPolygon(outline=rect_outline, holes=[])]]
        )

    children, top_level, island_indices = _build_knockout_hierarchy(contours)
    return _build_knockout_result(
        rect_outline,
        contours,
        children,
        top_level,
        island_indices,
    )


def _apply_global_transform(
    result: TextPolygonResult,
    x_mm: float,
    y_mm: float,
    rotation: float,
    is_mirrored: bool,
) -> None:
    """
    Apply position, rotation, and mirror transforms to all polygons.
    """
    rad = math.radians(rotation)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    do_rotate = abs(rotation % 360.0) > 0.001

    for char_polys in result.characters:
        for poly in char_polys:
            # Transform outline
            poly.outline = _transform_points(
                poly.outline, x_mm, y_mm, cos_a, sin_a, do_rotate, is_mirrored
            )
            # Transform holes
            poly.holes = [
                _transform_points(h, x_mm, y_mm, cos_a, sin_a, do_rotate, is_mirrored)
                for h in poly.holes
            ]


def _transform_points(
    points: list[tuple[float, float]],
    tx: float,
    ty: float,
    cos_a: float,
    sin_a: float,
    do_rotate: bool,
    is_mirrored: bool,
) -> list[tuple[float, float]]:
    """
    Transform a list of points: mirror -> rotate -> translate.
    """
    result = []
    for px, py in points:
        x, y = px, py

        if is_mirrored:
            x = -x

        if do_rotate:
            rx = x * cos_a - y * sin_a
            ry = x * sin_a + y * cos_a
            x, y = rx, ry

        x += tx
        y += ty
        result.append((x, y))

    # Mirroring reverses winding order - reverse to restore
    if is_mirrored:
        result = result[::-1]

    return result


# ================================================================== #
# Altium Stroke Font
# ================================================================== #

from .altium_stroke_font_data import (
    STROKE_ADVANCES_DEFAULT,
    STROKE_ADVANCES_SANS_SERIF,
    STROKE_ADVANCES_SERIF,
    STROKE_FONT_DEFAULT,
    STROKE_FONT_SANS_SERIF,
    STROKE_FONT_SERIF,
    STROKE_WIDTHS_DEFAULT,
    STROKE_WIDTHS_SANS_SERIF,
    STROKE_WIDTHS_SERIF,
)

# Lookup tables indexed by the PCB TEXT record's stroke_font_type field.
# Native PCB files store FontID-style values:
#   1 -> Default
#   2 -> Sans Serif
#   3 -> Serif
#
# Keep 0 mapped to Default as a defensive compatibility fallback for any
# older synthetic/mock callers that still use the earlier 0-based assumption.
_STROKE_FONTS = {
    1: STROKE_FONT_DEFAULT,
    2: STROKE_FONT_SANS_SERIF,
    3: STROKE_FONT_SERIF,
}

_STROKE_WIDTHS = {
    1: STROKE_WIDTHS_DEFAULT,
    2: STROKE_WIDTHS_SANS_SERIF,
    3: STROKE_WIDTHS_SERIF,
}

_STROKE_ADVANCES = {
    1: STROKE_ADVANCES_DEFAULT,
    2: STROKE_ADVANCES_SANS_SERIF,
    3: STROKE_ADVANCES_SERIF,
}

_STROKE_CHAR_SPACING = {
    2: 0.2060,
}

# Default width for characters not in the table
_DEFAULT_STROKE_WIDTH = 0.6665

# Native PCB stroke multiline text uses a much larger baseline-to-baseline
# step than the visible glyph ink height. IPC-2581 calibration on imported
# document text places the step at roughly 1.68x the stored text height.
_STROKE_MULTILINE_SPACING_FACTOR = 1.68


def canonicalize_stroke_font_type(stroke_font_type: int | None) -> int:
    """
    Normalize PCB TEXT stroke font ids to the canonical 1/2/3 range.

        Native PCB TEXT records use:
          1 -> Default
          2 -> Sans Serif
          3 -> Serif

        Older tests and some helper callsites historically treated the field as
        0-based. Keep 0/None/unknown values as a compatibility path to Default,
        but normalize them immediately so all downstream code sees one convention.
    """
    if stroke_font_type in (2, 3):
        return int(stroke_font_type)
    return 1


def stroke_font_type_from_label(font_label: str) -> int:
    """
    Infer the canonical stroke font id from a human-readable font label.
    """
    normalized = (font_label or "").lower()
    if "sans" in normalized:
        return 2
    if "serif" in normalized:
        return 3
    return 1


class StrokeTextRenderer:
    """
    Renders Altium stroke font text to line segments.

        Altium's stroke font uses simple vectorized characters defined
        as line segments. Each character shape is stored in a normalized
        coordinate space and scaled to the target height.
    """

    def render(
        self,
        text: str,
        height_mm: float = 1.524,
        stroke_width_mm: float = 0.254,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        rotation: float = 0.0,
        is_mirrored: bool = False,
        stroke_font_type: int = 0,
        justification: int = 3,
        frame_width_mm: float = 0.0,
        frame_height_mm: float = 0.0,
        stroke_advance_adjustments: dict[int, float] | None = None,
        stroke_cursor_advances: list[float] | None = None,
        target_ink_width_mm: float | None = None,
    ) -> StrokeTextResult:
        """
        Render stroke text to line segments.

                Args:
                    text: Text string to render
                    height_mm: Text height in mm
                    stroke_width_mm: Stroke line width in mm
                    x_mm: X position in mm
                    y_mm: Y position in mm
                    rotation: Rotation angle in degrees
                    is_mirrored: If True, mirror text
                    stroke_font_type: 1=Default, 2=Sans Serif, 3=Serif
                        0 is accepted as a compatibility fallback for Default.
                    justification: ALTIUM_TEXT_POSITION enum (3=LEFT_BOTTOM default)
                    stroke_advance_adjustments: Optional per-render cursor-advance
                        overrides keyed by character code. Merged onto the calibrated
                        defaults for the selected stroke font style.
                    stroke_cursor_advances: Optional per-character cursor advances in
                        normalized text-height units for single-line stroke text.
                    target_ink_width_mm: Optional target visible ink span in mm.
                        When provided, cursor advances are scaled to match the target
                        while preserving each glyph's local stroke geometry.

                Returns:
                    StrokeTextResult with line segments and stroke width
        """
        result = StrokeTextResult(stroke_width_mm=stroke_width_mm)

        if not text:
            return result

        stroke_font_type = canonicalize_stroke_font_type(stroke_font_type)

        # Select font style (fall back to Default for unknown values)
        font_data = _STROKE_FONTS.get(stroke_font_type, _STROKE_FONTS[1])
        width_data = _STROKE_WIDTHS.get(stroke_font_type, _STROKE_WIDTHS[1])
        advance_data = _STROKE_ADVANCES.get(stroke_font_type, _STROKE_ADVANCES[1])
        advance_adjustments: dict[int, float] = {}
        if stroke_advance_adjustments:
            advance_adjustments.update(stroke_advance_adjustments)

        # Scale factor: normalized coords -> mm
        scale = height_mm

        def _render_stroke_line_geometry(
            line_text: str,
            *,
            target_width_mm: float | None = None,
            cursor_advances: list[float] | None = None,
        ) -> tuple[list[tuple[float, float, float, float]], float]:
            """
            Render one logical text line to local stroke geometry.
            """
            cursor_x = 0.0
            char_groups: list[
                tuple[float, list[tuple[float, float, float, float]]]
            ] = []
            use_custom_advances = cursor_advances is not None and len(
                cursor_advances
            ) == len(line_text)

            for char_index, char in enumerate(line_text):
                code = ord(char)
                strokes = font_data.get(code, [])
                char_width = width_data.get(code, _DEFAULT_STROKE_WIDTH)
                if stroke_font_type in _STROKE_CHAR_SPACING:
                    char_advance = char_width + _STROKE_CHAR_SPACING[stroke_font_type]
                else:
                    char_advance = advance_data.get(code, char_width)
                char_advance_adjust = advance_adjustments.get(code, 0.0)
                glyph_lines: list[tuple[float, float, float, float]] = []

                for stroke in strokes:
                    if len(stroke) < 2:
                        continue
                    for j in range(len(stroke) - 1):
                        x1 = stroke[j][0] * scale
                        y1 = stroke[j][1] * scale
                        x2 = stroke[j + 1][0] * scale
                        y2 = stroke[j + 1][1] * scale
                        glyph_lines.append((x1, y1, x2, y2))

                char_groups.append((cursor_x, glyph_lines))
                if use_custom_advances:
                    cursor_x += max(0.0, float(cursor_advances[char_index])) * scale
                else:
                    cursor_x += max(0.0, char_advance + char_advance_adjust) * scale

            def _ink_bbox_for_cursor_scale(
                cursor_scale: float,
            ) -> tuple[float, float, float, float] | None:
                xs: list[float] = []
                ys: list[float] = []
                for base_cursor_x, glyph_lines in char_groups:
                    cursor_offset_x = base_cursor_x * cursor_scale
                    for x1, y1, x2, y2 in glyph_lines:
                        xs.extend((x1 + cursor_offset_x, x2 + cursor_offset_x))
                        ys.extend((y1, y2))
                if not xs or not ys:
                    return None
                return (min(xs), min(ys), max(xs), max(ys))

            cursor_scale = 1.0
            if (
                target_width_mm is not None
                and target_width_mm > 0.0
                and len(char_groups) > 1
            ):
                current_bbox = _ink_bbox_for_cursor_scale(1.0)
                if current_bbox is not None:
                    current_width_mm = current_bbox[2] - current_bbox[0]
                    if (
                        current_width_mm > 1e-9
                        and abs(target_width_mm - current_width_mm) > 1e-6
                    ):
                        if target_width_mm >= current_width_mm:
                            lo = 1.0
                            hi = 2.0
                            while hi < 64.0:
                                hi_bbox = _ink_bbox_for_cursor_scale(hi)
                                if hi_bbox is None:
                                    break
                                if (hi_bbox[2] - hi_bbox[0]) >= target_width_mm:
                                    break
                                lo = hi
                                hi *= 2.0
                        else:
                            lo = 0.0
                            hi = 1.0

                        for _ in range(24):
                            mid = (lo + hi) / 2.0
                            mid_bbox = _ink_bbox_for_cursor_scale(mid)
                            if mid_bbox is None:
                                break
                            mid_width_mm = mid_bbox[2] - mid_bbox[0]
                            if mid_width_mm < target_width_mm:
                                lo = mid
                            else:
                                hi = mid

                        candidate_scales = []
                        for candidate in (lo, hi):
                            candidate_bbox = _ink_bbox_for_cursor_scale(candidate)
                            if candidate_bbox is None:
                                continue
                            candidate_width_mm = candidate_bbox[2] - candidate_bbox[0]
                            candidate_scales.append(
                                (abs(candidate_width_mm - target_width_mm), candidate)
                            )
                        if candidate_scales:
                            cursor_scale = min(
                                candidate_scales, key=lambda item: item[0]
                            )[1]

            line_lines: list[tuple[float, float, float, float]] = []
            for base_cursor_x, glyph_lines in char_groups:
                cursor_offset_x = base_cursor_x * cursor_scale
                for x1, y1, x2, y2 in glyph_lines:
                    line_lines.append(
                        (x1 + cursor_offset_x, y1, x2 + cursor_offset_x, y2)
                    )

            return line_lines, cursor_x * cursor_scale

        logical_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        line_ranges: list[tuple[int, int]] = []
        line_advance_widths: list[float] = []

        if len(logical_lines) <= 1:
            line_lines, line_advance = _render_stroke_line_geometry(
                logical_lines[0],
                target_width_mm=target_ink_width_mm,
                cursor_advances=stroke_cursor_advances,
            )
            result.lines.extend(line_lines)
            line_ranges.append((0, len(result.lines)))
            line_advance_widths.append(line_advance)

            if frame_width_mm > 0 and frame_height_mm > 0:
                _apply_stroke_frame_justification(
                    result, justification, frame_width_mm, frame_height_mm
                )
        else:
            line_spacing_mm = height_mm * _STROKE_MULTILINE_SPACING_FACTOR
            n_lines = len(logical_lines)
            for line_idx, line_text in enumerate(logical_lines):
                line_lines, line_advance = _render_stroke_line_geometry(line_text)
                y_offset = line_spacing_mm * (n_lines - 1 - line_idx)
                start = len(result.lines)
                result.lines.extend(
                    (x1, y1 + y_offset, x2, y2 + y_offset)
                    for x1, y1, x2, y2 in line_lines
                )
                line_ranges.append((start, len(result.lines)))
                line_advance_widths.append(line_advance)

            if frame_width_mm > 0 and frame_height_mm > 0:
                _apply_perline_stroke_frame_justification(
                    result,
                    justification,
                    frame_width_mm,
                    frame_height_mm,
                    line_ranges,
                    line_advance_widths,
                )

        # Apply global transforms
        if rotation != 0.0 or x_mm != 0.0 or y_mm != 0.0 or is_mirrored:
            rad = math.radians(rotation)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            do_rotate = abs(rotation % 360.0) > 0.001

            transformed_lines = []
            for x1, y1, x2, y2 in result.lines:
                if is_mirrored:
                    x1, x2 = -x1, -x2
                if do_rotate:
                    x1, y1 = x1 * cos_a - y1 * sin_a, x1 * sin_a + y1 * cos_a
                    x2, y2 = x2 * cos_a - y2 * sin_a, x2 * sin_a + y2 * cos_a
                transformed_lines.append((x1 + x_mm, y1 + y_mm, x2 + x_mm, y2 + y_mm))
            result.lines = transformed_lines

        return result


# ================================================================== #
# Barcode Renderer
# ================================================================== #


class BarcodeRenderer:
    """
    Renders barcode text records to filled polygon contours.

        Converts Code 39 / Code 128 barcode data into rectangular bar polygons.
        Each bar is a filled rectangle; the result is a TextPolygonResult
        compatible with IPC-2581 output.

        Sizing modes:
            ByMinWidth (0): Each module = barcode_min_width. Total width varies.
            ByFullWidth (1): Total width = barcode_full_width. Module width computed.
    """

    @staticmethod
    def _encode_barcode(text: str, barcode_kind: int) -> Any | None:
        """
        Encode a barcode string into module bits.
        """
        from .altium_barcode_encoders import encode_code39, encode_code128

        try:
            if barcode_kind == 0:
                return encode_code39(text)
            if barcode_kind == 1:
                return encode_code128(text)
            log.warning("Unsupported barcode kind %d, skipping", barcode_kind)
            return None
        except ValueError as e:
            log.warning("Barcode encoding failed: %s", e)
            return None

    @staticmethod
    def _build_char_bboxes(
        text_result: TextPolygonResult,
    ) -> list[tuple[float, float, float, float]]:
        """
        Compute per-character bounding boxes.
        """
        char_bboxes: list[tuple[float, float, float, float]] = []
        for char_polys in text_result.characters:
            cxs: list[float] = []
            cys: list[float] = []
            for poly in char_polys:
                cxs.extend(px for px, _ in poly.outline)
                cys.extend(py for _, py in poly.outline)
            if cxs:
                char_bboxes.append((min(cxs), max(cxs), min(cys), max(cys)))
            else:
                char_bboxes.append((0.0, 0.0, 0.0, 0.0))
        return char_bboxes

    @staticmethod
    def _shift_text_result(text_result: TextPolygonResult, dy: float) -> None:
        """
        Shift all text polygons vertically.
        """
        if dy == 0.0:
            return
        for char_polys in text_result.characters:
            for poly in char_polys:
                poly.outline = [(px, py + dy) for px, py in poly.outline]
                poly.holes = [[(px, py + dy) for px, py in hole] for hole in poly.holes]

    def _prepare_show_text(
        self,
        *,
        text: str,
        barcode_show_text: bool,
        barcode_font_name: str,
        height_mm: float,
        font_resolver: FontPathResolver | None,
        barcode_y_margin_mm: float,
        inv_rect_height_mm: float,
    ) -> tuple[
        TextPolygonResult | None, list[tuple[float, float, float, float]], float, float
    ]:
        """
        Pre-render and position human-readable barcode text.
        """
        if not (barcode_show_text and text):
            return None, [], 0.0, 0.0

        tt_renderer = TrueTypeTextRenderer()
        text_result = tt_renderer.render(
            text=text,
            font_name=barcode_font_name,
            height_mm=height_mm,
            font_resolver=font_resolver,
        )
        if not text_result.characters:
            return text_result, [], 0.0, 0.0

        char_bboxes = self._build_char_bboxes(text_result)
        text_min_x = min(b[0] for b in char_bboxes)
        text_max_x = max(b[1] for b in char_bboxes)
        text_min_y = min(b[2] for b in char_bboxes)
        text_max_y = max(b[3] for b in char_bboxes)
        text_width = text_max_x - text_min_x
        descent_below_baseline = max(0.0, -text_min_y)
        glyph_ink_height = text_max_y - text_min_y
        text_visual_height = glyph_ink_height + descent_below_baseline

        dy = descent_below_baseline
        if inv_rect_height_mm > 0:
            dy += barcode_y_margin_mm
        self._shift_text_result(text_result, dy)
        if dy != 0.0:
            char_bboxes = [(b[0], b[1], b[2] + dy, b[3] + dy) for b in char_bboxes]
        return text_result, char_bboxes, text_width, text_visual_height

    @staticmethod
    def _compute_module_width(
        *,
        total_bits: int,
        barcode_render_mode: int,
        barcode_full_width_mm: float,
        barcode_x_margin_mm: float,
        barcode_min_width_mm: float,
        inv_rect_width_mm: float,
    ) -> tuple[float, float]:
        """
        Compute the target bar-area width and module width.
        """
        if barcode_render_mode != 1:
            return 0.0, barcode_min_width_mm

        bar_area_width = (
            inv_rect_width_mm if inv_rect_width_mm > 0 else barcode_full_width_mm
        )
        if bar_area_width <= 0:
            return bar_area_width, barcode_min_width_mm
        available = bar_area_width - 2 * barcode_x_margin_mm
        module_w = available / total_bits if total_bits > 0 else barcode_min_width_mm
        return bar_area_width, module_w

    @staticmethod
    def _compute_bar_height(
        *,
        barcode_full_height_mm: float,
        barcode_y_margin_mm: float,
        height_mm: float,
        inv_rect_height_mm: float,
        text_visual_height: float,
    ) -> float:
        """
        Compute rendered bar height from the barcode sizing inputs.
        """
        if inv_rect_height_mm > 0:
            bar_height = (
                inv_rect_height_mm - 2 * barcode_y_margin_mm - text_visual_height
            )
        elif barcode_full_height_mm > 0:
            bar_height = barcode_full_height_mm - 2 * barcode_y_margin_mm
        else:
            bar_height = height_mm
        return bar_height if bar_height > 0 else height_mm

    @staticmethod
    def _append_bar_rectangles(
        result: TextPolygonResult,
        *,
        bits: list[bool],
        module_w: float,
        barcode_x_margin_mm: float,
        barcode_y_margin_mm: float,
        bar_height: float,
    ) -> tuple[float, float]:
        """
        Convert module bits into merged rectangular bar polygons.
        """
        cursor_x = barcode_x_margin_mm
        total_bits = len(bits)
        bar_total_width = total_bits * module_w
        index = 0
        while index < total_bits:
            if not bits[index]:
                cursor_x += module_w
                index += 1
                continue

            run_len = 1
            while index + run_len < total_bits and bits[index + run_len]:
                run_len += 1
            x0 = cursor_x
            y0 = barcode_y_margin_mm
            x1 = cursor_x + run_len * module_w
            y1 = barcode_y_margin_mm + bar_height
            rect = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            result.characters.append([GlyphPolygon(outline=rect)])
            cursor_x += run_len * module_w
            index += run_len
        return cursor_x, bar_total_width

    @staticmethod
    def _append_show_text(
        result: TextPolygonResult,
        *,
        text_result: TextPolygonResult | None,
        char_bboxes: list[tuple[float, float, float, float]],
        barcode_x_margin_mm: float,
        bar_total_width: float,
        text_width: float,
        text_visual_height: float,
    ) -> None:
        """
        Position and merge show-text glyphs beneath the bars.
        """
        if text_result is None or not char_bboxes:
            return

        num_text_chars = len(text_result.characters)
        effective_width = max(bar_total_width, text_width)
        cell_width = (
            effective_width / num_text_chars if num_text_chars > 0 else effective_width
        )
        cell_origin_x = barcode_x_margin_mm

        for char_index, char_polys in enumerate(text_result.characters):
            if char_index >= len(char_bboxes):
                break
            char_bbox = char_bboxes[char_index]
            glyph_cx = (char_bbox[0] + char_bbox[1]) / 2.0
            cell_cx = cell_origin_x + (char_index + 0.5) * cell_width
            dx = cell_cx - glyph_cx
            for poly in char_polys:
                poly.outline = [(px + dx, py) for px, py in poly.outline]
                poly.holes = [[(px + dx, py) for px, py in hole] for hole in poly.holes]

        for char_polys in result.characters:
            for poly in char_polys:
                poly.outline = [
                    (px, py + text_visual_height) for px, py in poly.outline
                ]

        result.characters.extend(text_result.characters)

    @staticmethod
    def _finalize_barcode_result(
        result: TextPolygonResult,
        *,
        barcode_inverted: bool,
        barcode_render_mode: int,
        barcode_x_margin_mm: float,
        barcode_y_margin_mm: float,
        bar_height: float,
        cursor_x: float,
        inv_rect_width_mm: float,
        inv_rect_height_mm: float,
        text_visual_height: float,
        rotation: float,
        x_mm: float,
        y_mm: float,
        is_mirrored: bool,
    ) -> TextPolygonResult:
        """
        Apply knockout and final global transforms.
        """
        if barcode_inverted:
            if barcode_render_mode == 1 and inv_rect_width_mm > 0:
                frame_width = inv_rect_width_mm
            else:
                frame_width = cursor_x + barcode_x_margin_mm

            if inv_rect_height_mm > 0:
                frame_height = inv_rect_height_mm
            else:
                frame_height = bar_height + 2 * barcode_y_margin_mm + text_visual_height
            result = _apply_knockout_transform(result, 0.0, frame_width, frame_height)

        if rotation != 0.0 or x_mm != 0.0 or y_mm != 0.0 or is_mirrored:
            _apply_global_transform(result, x_mm, y_mm, rotation, is_mirrored)
        return result

    def render(
        self,
        text: str,
        barcode_kind: int = 0,
        barcode_render_mode: int = 0,
        barcode_full_width_mm: float = 0.0,
        barcode_full_height_mm: float = 0.0,
        barcode_x_margin_mm: float = 0.0,
        barcode_y_margin_mm: float = 0.0,
        barcode_min_width_mm: float = 0.254,
        barcode_inverted: bool = False,
        barcode_show_text: bool = False,
        barcode_font_name: str = "Arial",
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        height_mm: float = 1.524,
        rotation: float = 0.0,
        is_mirrored: bool = False,
        margin_mm: float = 0.0,
        font_resolver: FontPathResolver | None = None,
        inv_rect_width_mm: float = 0.0,
        inv_rect_height_mm: float = 0.0,
    ) -> TextPolygonResult:
        """
        Render a barcode to polygon contours.

                Args:
                    text: Data to encode
                    barcode_kind: 0=Code39, 1=Code128
                    barcode_render_mode: 0=ByMinWidth, 1=ByFullWidth
                    barcode_full_width_mm: Total width for ByFullWidth mode
                    barcode_full_height_mm: Total height for ByFullWidth mode
                    barcode_x_margin_mm: Horizontal margin around bars
                    barcode_y_margin_mm: Vertical margin around bars
                    barcode_min_width_mm: Minimum module width (ByMinWidth mode)
                    barcode_inverted: If True, render as knockout
                    barcode_show_text: If True, render text label below bars
                    barcode_font_name: Font for text label
                    x_mm: X position
                    y_mm: Y position
                    height_mm: Text height (used for bar height)
                    rotation: Rotation in degrees
                    is_mirrored: Mirror flag
                    margin_mm: Inverted text margin
                    inv_rect_width_mm: InvRectWidth - when >0, overrides
                        barcode_full_width for bar sizing and knockout rect
                    inv_rect_height_mm: InvRectHeight - when >0, used for
                        knockout rect height

                Returns:
                    TextPolygonResult with bar rectangle polygons
        """
        result = TextPolygonResult()
        if not text:
            return result

        encoding = self._encode_barcode(text, barcode_kind)
        if encoding is None:
            return result
        total_bits = len(encoding.bits)
        if total_bits == 0:
            return result

        text_result, char_bboxes, text_width, text_visual_height = (
            self._prepare_show_text(
                text=text,
                barcode_show_text=barcode_show_text,
                barcode_font_name=barcode_font_name,
                height_mm=height_mm,
                font_resolver=font_resolver,
                barcode_y_margin_mm=barcode_y_margin_mm,
                inv_rect_height_mm=inv_rect_height_mm,
            )
        )

        bar_area_width, module_w = self._compute_module_width(
            total_bits=total_bits,
            barcode_render_mode=barcode_render_mode,
            barcode_full_width_mm=barcode_full_width_mm,
            barcode_x_margin_mm=barcode_x_margin_mm,
            barcode_min_width_mm=barcode_min_width_mm,
            inv_rect_width_mm=inv_rect_width_mm,
        )

        bar_height = self._compute_bar_height(
            barcode_full_height_mm=barcode_full_height_mm,
            barcode_y_margin_mm=barcode_y_margin_mm,
            height_mm=height_mm,
            inv_rect_height_mm=inv_rect_height_mm,
            text_visual_height=text_visual_height,
        )

        cursor_x, bar_total_width = self._append_bar_rectangles(
            result,
            bits=encoding.bits,
            module_w=module_w,
            barcode_x_margin_mm=barcode_x_margin_mm,
            barcode_y_margin_mm=barcode_y_margin_mm,
            bar_height=bar_height,
        )
        self._append_show_text(
            result,
            text_result=text_result,
            char_bboxes=char_bboxes,
            barcode_x_margin_mm=barcode_x_margin_mm,
            bar_total_width=bar_total_width,
            text_width=text_width,
            text_visual_height=text_visual_height,
        )
        result = self._finalize_barcode_result(
            result,
            barcode_inverted=barcode_inverted,
            barcode_render_mode=barcode_render_mode,
            barcode_x_margin_mm=barcode_x_margin_mm,
            barcode_y_margin_mm=barcode_y_margin_mm,
            bar_height=bar_height,
            cursor_x=cursor_x,
            inv_rect_width_mm=inv_rect_width_mm,
            inv_rect_height_mm=inv_rect_height_mm,
            text_visual_height=text_visual_height,
            rotation=rotation,
            x_mm=x_mm,
            y_mm=y_mm,
            is_mirrored=is_mirrored,
        )

        return result


# ================================================================== #
# Convenience: render from AltiumPcbText record
# ================================================================== #


def render_pcb_text(
    text_record: object,
    truetype_renderer: TrueTypeTextRenderer | None = None,
    stroke_renderer: StrokeTextRenderer | None = None,
    barcode_renderer: BarcodeRenderer | None = None,
    font_resolver: FontPathResolver | None = None,
    text_override: str | None = None,
    stroke_advance_adjustments: dict[int, float] | None = None,
    stroke_cursor_advances: list[float] | None = None,
    stroke_target_ink_width_mm: float | None = None,
    truetype_x_scale: float | None = None,
    truetype_flatten_tolerance: float | None = None,
) -> TextPolygonResult | StrokeTextResult | None:
    """
    Render an AltiumPcbText record to polygons or line segments.

        Args:
            text_record: AltiumPcbText instance from the parser
            truetype_renderer: Shared renderer instance (created if None)
            stroke_renderer: Shared renderer instance (created if None)
            barcode_renderer: Shared renderer instance (created if None)
            text_override: Optional resolved text value used instead of `text_record.text_content`
            stroke_advance_adjustments: Optional per-render stroke cursor-advance
                overrides keyed by character code.
            stroke_cursor_advances: Optional per-character cursor advances in
                normalized text-height units for single-line stroke text.
            stroke_target_ink_width_mm: Optional target visible ink span for
                stroke text, used for native-oracle cursor calibration.

        Returns:
            TextPolygonResult for TrueType/barcode, StrokeTextResult for stroke,
            or None if rendering fails.
    """
    text = text_override if text_override is not None else text_record.text_content
    if not text:
        return None

    # Convert coordinates to mm
    x_mm = text_record.x / _UNITS_PER_MIL * _MIL_TO_MM
    y_mm = text_record.y / _UNITS_PER_MIL * _MIL_TO_MM
    height_mm = text_record.height / _UNITS_PER_MIL * _MIL_TO_MM
    stroke_width_mm = text_record.stroke_width / _UNITS_PER_MIL * _MIL_TO_MM
    rotation = text_record.rotation
    is_mirrored = text_record.is_mirrored
    # Justification only affects frame/multiline text. For non-multiline,
    # Altium always renders at (X,Y) as bottom-left regardless of justification.
    frame_width_mm = 0.0
    frame_height_mm = 0.0
    if text_record.is_frame:
        justification = text_record.effective_justification
        frame_width_mm = text_record.textbox_rect_width / _UNITS_PER_MIL * _MIL_TO_MM
        frame_height_mm = text_record.textbox_rect_height / _UNITS_PER_MIL * _MIL_TO_MM
    else:
        justification = 3  # LEFT_BOTTOM - no offset

    # Inverted/knockout text parameters
    is_inverted = text_record.is_inverted
    margin_mm = text_record.margin_border_width / _UNITS_PER_MIL * _MIL_TO_MM

    if text_record.font_type == 2:
        # Barcode (eText_BarCode)
        if barcode_renderer is None:
            barcode_renderer = BarcodeRenderer()
        # When use_inverted_rectangle is set, textbox_rect_width/height
        # (InvRectWidth/Height) override barcode_full_width/height for
        # both bar sizing and knockout rect dimensions.
        irw_mm = 0.0
        irh_mm = 0.0
        if text_record.use_inverted_rectangle:
            irw_mm = text_record.textbox_rect_width / _UNITS_PER_MIL * _MIL_TO_MM
            irh_mm = text_record.textbox_rect_height / _UNITS_PER_MIL * _MIL_TO_MM
        return barcode_renderer.render(
            text=text,
            barcode_kind=text_record.barcode_kind,
            barcode_render_mode=text_record.barcode_render_mode,
            barcode_full_width_mm=text_record.barcode_full_width
            / _UNITS_PER_MIL
            * _MIL_TO_MM,
            barcode_full_height_mm=text_record.barcode_full_height
            / _UNITS_PER_MIL
            * _MIL_TO_MM,
            barcode_x_margin_mm=text_record.barcode_x_margin
            / _UNITS_PER_MIL
            * _MIL_TO_MM,
            barcode_y_margin_mm=text_record.barcode_y_margin
            / _UNITS_PER_MIL
            * _MIL_TO_MM,
            barcode_min_width_mm=text_record.barcode_min_width
            / _UNITS_PER_MIL
            * _MIL_TO_MM,
            barcode_inverted=text_record.barcode_inverted,
            barcode_show_text=text_record.barcode_show_text,
            barcode_font_name=text_record.barcode_font_name or "Arial",
            x_mm=x_mm,
            y_mm=y_mm,
            height_mm=height_mm,
            rotation=rotation,
            is_mirrored=is_mirrored,
            margin_mm=margin_mm,
            font_resolver=font_resolver,
            inv_rect_width_mm=irw_mm,
            inv_rect_height_mm=irh_mm,
        )
    elif text_record.font_type == 0:
        # Stroke font - inverted stroke not supported in IPC-2581
        # (Altium exports as plain stroke via UserSpecial)
        if stroke_renderer is None:
            stroke_renderer = StrokeTextRenderer()
        return stroke_renderer.render(
            text=text,
            height_mm=height_mm,
            stroke_width_mm=stroke_width_mm,
            x_mm=x_mm,
            y_mm=y_mm,
            rotation=rotation,
            is_mirrored=is_mirrored,
            stroke_font_type=text_record.stroke_font_type,
            justification=justification,
            frame_width_mm=frame_width_mm,
            frame_height_mm=frame_height_mm,
            stroke_advance_adjustments=stroke_advance_adjustments,
            stroke_cursor_advances=stroke_cursor_advances,
            target_ink_width_mm=stroke_target_ink_width_mm,
        )
    else:
        # TrueType font (font_type >= 1, excluding 2=barcode)
        if truetype_renderer is None:
            truetype_renderer = TrueTypeTextRenderer()
        font_name = text_record.font_name or "Arial"
        return truetype_renderer.render(
            text=text,
            font_name=font_name,
            height_mm=height_mm,
            x_mm=x_mm,
            y_mm=y_mm,
            rotation=rotation,
            is_mirrored=is_mirrored,
            is_bold=text_record.is_bold,
            is_italic=text_record.is_italic,
            x_scale=(truetype_x_scale if truetype_x_scale is not None else 1.0),
            justification=justification,
            frame_width_mm=frame_width_mm,
            frame_height_mm=frame_height_mm,
            is_inverted=is_inverted,
            inverted_margin_mm=margin_mm,
            flatten_tolerance=(
                truetype_flatten_tolerance
                if truetype_flatten_tolerance is not None
                else 0.05
            ),
            font_resolver=font_resolver,
        )
