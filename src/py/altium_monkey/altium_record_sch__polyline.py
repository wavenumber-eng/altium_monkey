"""Schematic record model for SchRecordType.POLYLINE."""

import math
from typing import TYPE_CHECKING, Any, cast

from .altium_record_types import (
    CoordPoint,
    LineShape,
    LineStyle,
    LineWidth,
    SchGraphicalObject,
    SchPointMils,
    SchRecordType,
    color_to_hex,
)
from ._sch_managed_defaults import POLYLINE_COLOR
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    _LineStyleDirtyMixin,
    _validate_schematic_vertex_counts,
    _validate_schematic_vertex_total,
    detect_case_mode_method_from_dotted_uppercase_fields,
    geometry_coord_tuple,
    indexed_coord_has_invalid_wire_value,
    read_indexed_coord,
    validate_indexed_coord,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SchSvgRenderContext,
    compute_dash_segments,
)
from ._sch_managed_numeric import (
    managed_f32 as _managed_f32,
    managed_gdi_rotate_translate,
    managed_internal_coord,
    unchecked_i32 as _managed_i32,
)

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryOp


def _render_line_shape(
    endpoint: tuple[float, float],
    prev_point: tuple[float, float],
    shape: LineShape,
    shape_size: LineWidth,
    line_width: LineWidth,
    stroke: str,
    stroke_width: float,
    is_hairline: bool,
    is_start: bool,
) -> list[str]:
    """
    Render line shape decorations (arrows, circles, squares, etc.) at polyline endpoints.

    Rendering rules:
    - Arrow: 2 lines forming V shape
    - SolidArrow: 2 polygons (filled + stroked triangle)
    - Tail: 4 lines forming X-tail pattern
    - SolidTail: 2 polygons (bowtie shape)
    - Circle: 2 rects with rx/ry
    - Square: 2 polygons (rectangle)

    Args:
        endpoint: The endpoint coordinates (x, y) where decoration appears
        prev_point: The previous point coordinates for direction calculation
        shape: The LineShape enum value
        shape_size: Endpoint marker size enum. This uses the same native
            schematic size states as ``LineWidth``.
        line_width: LineWidth enum - determines Circle/Square visual size
        stroke: Stroke color in hex format
        stroke_width: Stroke width in pixels
        is_hairline: Whether this is a hairline stroke (0.5px with vector-effect)
        is_start: True for start endpoint, False for end endpoint

    Returns:
        List of SVG element strings for the decoration
    """
    if shape == LineShape.NONE:
        return []

    size_scale = shape_size.value + 1

    x, y = endpoint
    px, py = prev_point

    # Calculate direction vector (normalized)
    dx = x - px
    dy = y - py
    length = math.sqrt(dx * dx + dy * dy)
    if length < 0.001:
        return []  # Points too close, can't determine direction

    # Unit vector pointing FROM previous point TO endpoint
    # For end shapes: prev_point is second-to-last, endpoint is last - points outward
    # For start shapes: prev_point is second vertex, endpoint is first - points outward from line
    ux = dx / length
    uy = dy / length

    # Perpendicular vector (90 degrees counterclockwise)
    nx = -uy
    ny = ux

    # Note: For start shapes, the direction vector is already correct
    # (pointing FROM second vertex TO first vertex = outward from line)
    # No inversion needed.

    # Stroke width attribute
    if is_hairline:
        stroke_attr = (
            f'stroke="{stroke}" stroke-width="0.5px" vector-effect="non-scaling-stroke"'
        )
    else:
        stroke_attr = f'stroke="{stroke}" stroke-width="{stroke_width:.0f}px"'

    elements = []

    if shape == LineShape.ARROW:
        # Arrow: Two lines forming V shape
        # Native open-arrow geometry scales with the rendered stroke width.
        # Hairline 1px lines use a 4px backoff / 1.333px half-width arrow,
        # and wider strokes scale proportionally.
        arrow_length = stroke_width * 4.0 * size_scale
        arrow_half_width = stroke_width * (4.0 / 3.0) * size_scale

        # Calculate arrow vertices
        # Base of arrow (back from endpoint along line direction)
        base_x = x - ux * arrow_length
        base_y = y - uy * arrow_length

        # Wing points (perpendicular offset from base)
        wing1_x = base_x + nx * arrow_half_width
        wing1_y = base_y + ny * arrow_half_width
        wing2_x = base_x - nx * arrow_half_width
        wing2_y = base_y - ny * arrow_half_width

        # Two lines from wings to tip
        elements.append(
            f'<line x1="{wing1_x:.3f}" y1="{wing1_y:.3f}" x2="{x:.3f}" y2="{y:.3f}" {stroke_attr}/>'
        )
        elements.append(
            f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{wing2_x:.3f}" y2="{wing2_y:.3f}" {stroke_attr}/>'
        )

    elif shape == LineShape.SOLID_ARROW:
        # SolidArrow: Filled triangle (two polygons - fill + stroke)
        # Base dimensions vary by LineWidth, then LineShapeSize scales them:
        #   SMALLEST/SMALL: tip_ext=1.5, base_offset=4, half_height=2
        #   MEDIUM: tip_ext=4.5, base_offset=12, half_height=6
        #   LARGE: tip_ext=7.5, base_offset=20, half_height=10
        if line_width in (LineWidth.SMALLEST, LineWidth.SMALL):
            tip_extension = 1.5
            base_offset = 4
            arrow_half_width = 2
        elif line_width == LineWidth.LARGE:
            tip_extension = 7.5
            base_offset = 20
            arrow_half_width = 10
        else:  # MEDIUM
            tip_extension = 4.5
            base_offset = 12
            arrow_half_width = 6
        tip_extension *= size_scale
        base_offset *= size_scale
        arrow_half_width *= size_scale

        # Tip extends past the endpoint
        tip_x = x + ux * tip_extension
        tip_y = y + uy * tip_extension

        # Base of triangle
        base_x = x - ux * base_offset
        base_y = y - uy * base_offset

        # Base corners - native uses specific winding order: tip, top, bottom, tip
        # "top" is the corner with SMALLER y value (negative perpendicular direction in SVG coords)
        corner_top_x = base_x - nx * arrow_half_width
        corner_top_y = base_y - ny * arrow_half_width
        corner_bot_x = base_x + nx * arrow_half_width
        corner_bot_y = base_y + ny * arrow_half_width

        # Triangle points: tip, top corner, bottom corner, back to tip
        points = f"{tip_x:.1f},{tip_y:.1f} {corner_top_x:.0f},{corner_top_y:.0f} {corner_bot_x:.0f},{corner_bot_y:.0f} {tip_x:.1f},{tip_y:.1f}"

        # Filled polygon
        elements.append(
            f'<polygon points="{points}" fill="{stroke}" fill-opacity="1"/>'
        )
        # Stroked polygon
        elements.append(f'<polygon points="{points}" {stroke_attr}/>')

    elif shape == LineShape.TAIL:
        # Tail: Four lines forming X-tail pattern (opposite of arrow)
        # Native pattern: two Vs - one 6px past endpoint, one 6px back
        outer_offset = 6 * size_scale
        inner_offset = 6 * size_scale
        tail_half_width = 3 * size_scale

        # Outer V (past the endpoint)
        outer_x = x + ux * outer_offset
        outer_y = y + uy * outer_offset
        outer_wing1_x = outer_x + nx * tail_half_width
        outer_wing1_y = outer_y + ny * tail_half_width
        outer_wing2_x = outer_x - nx * tail_half_width
        outer_wing2_y = outer_y - ny * tail_half_width

        # Inner V (back from endpoint)
        inner_x = x - ux * inner_offset
        inner_y = y - uy * inner_offset
        inner_wing1_y = inner_y + ny * tail_half_width
        inner_wing2_y = inner_y - ny * tail_half_width

        # Four lines forming the X-tail
        elements.append(
            f'<line x1="{x:.0f}" y1="{y:.0f}" x2="{outer_wing1_x:.0f}" y2="{outer_wing1_y:.0f}" {stroke_attr}/>'
        )
        elements.append(
            f'<line x1="{x:.0f}" y1="{y:.0f}" x2="{outer_wing2_x:.0f}" y2="{outer_wing2_y:.0f}" {stroke_attr}/>'
        )
        elements.append(
            f'<line x1="{inner_x:.0f}" y1="{inner_y:.0f}" x2="{x:.0f}" y2="{inner_wing1_y:.0f}" {stroke_attr}/>'
        )
        elements.append(
            f'<line x1="{inner_x:.0f}" y1="{inner_y:.0f}" x2="{x:.0f}" y2="{inner_wing2_y:.0f}" {stroke_attr}/>'
        )

    elif shape == LineShape.SOLID_TAIL:
        # SolidTail: Bowtie polygon (7 points).
        # Pattern: center_point, outer_top, endpoint_top, inner_point,
        # endpoint_bot, outer_bot, center.
        outer_offset = 9 * size_scale
        inner_offset = 6 * size_scale
        center_offset = 3 * size_scale
        half_width = 4.5 * size_scale

        # Point 1: Center of bowtie (on line, past endpoint)
        p1_x = x + ux * center_offset
        p1_y = y + uy * center_offset

        # Point 2: Outer wing top (past endpoint, perpendicular top)
        p2_x = x + ux * outer_offset - nx * half_width
        p2_y = y + uy * outer_offset - ny * half_width

        # Point 3: Endpoint top (at endpoint, perpendicular top)
        p3_x = x - nx * half_width
        p3_y = y - ny * half_width

        # Point 4: Inner point (on line, before endpoint)
        p4_x = x - ux * inner_offset
        p4_y = y - uy * inner_offset

        # Point 5: Endpoint bottom (at endpoint, perpendicular bottom)
        p5_x = x + nx * half_width
        p5_y = y + ny * half_width

        # Point 6: Outer wing bottom (past endpoint, perpendicular bottom)
        p6_x = x + ux * outer_offset + nx * half_width
        p6_y = y + uy * outer_offset + ny * half_width

        points = (
            f"{p1_x:.0f},{p1_y:.0f} {p2_x:.0f},{p2_y:.1f} {p3_x:.0f},{p3_y:.1f} "
            f"{p4_x:.0f},{p4_y:.0f} {p5_x:.0f},{p5_y:.1f} {p6_x:.0f},{p6_y:.1f} {p1_x:.0f},{p1_y:.0f}"
        )

        # Filled polygon
        elements.append(
            f'<polygon points="{points}" fill="{stroke}" fill-opacity="1"/>'
        )
        # Stroked polygon
        elements.append(f'<polygon points="{points}" {stroke_attr}/>')

    elif shape == LineShape.CIRCLE:
        # Circle: Two rects with rx/ry (rounded rect as circle)
        # Base size is selected by LineWidth, then LineShapeSize scales it:
        #   SMALLEST/SMALL -> size=2, rx=1
        #   MEDIUM -> size=6, rx=3
        #   LARGE -> size=10, rx=5
        if line_width in (LineWidth.SMALLEST, LineWidth.SMALL):
            size = 2
            radius = 1
        elif line_width == LineWidth.LARGE:
            size = 10
            radius = 5
        else:  # MEDIUM
            size = 6
            radius = 3
        size *= size_scale
        radius *= size_scale

        # Circle centered at endpoint
        rect_x = x - radius
        rect_y = y - radius

        # Filled circle
        elements.append(
            f'<rect x="{rect_x:.0f}" y="{rect_y:.0f}" width="{size}" height="{size}" '
            f'rx="{radius}" ry="{radius}" fill="{stroke}" fill-opacity="1"/>'
        )
        # Stroked circle
        elements.append(
            f'<rect x="{rect_x:.0f}" y="{rect_y:.0f}" width="{size}" height="{size}" '
            f'{stroke_attr} rx="{radius}" ry="{radius}"/>'
        )

    elif shape == LineShape.SQUARE:
        # Square: Two polygons (rectangle)
        # Size is based on LineWidth (like Circle):
        #   SMALLEST/SMALL -> half_size = 1 (2x2 square)
        #   MEDIUM -> half_size = 4.5 (9x9 square)
        #   LARGE -> half_size = 5 (10x10 square)
        if line_width in (LineWidth.SMALLEST, LineWidth.SMALL):
            half_size = 1.0
        elif line_width == LineWidth.LARGE:
            half_size = 5.0
        else:  # MEDIUM
            half_size = 4.5
        half_size *= size_scale

        # Calculate corners relative to endpoint
        # Native uses specific winding order for squares
        c1_x = x + ux * half_size - nx * half_size  # top-right
        c1_y = y + uy * half_size - ny * half_size
        c2_x = x - ux * half_size - nx * half_size  # top-left
        c2_y = y - uy * half_size - ny * half_size
        c3_x = x - ux * half_size + nx * half_size  # bottom-left
        c3_y = y - uy * half_size + ny * half_size
        c4_x = x + ux * half_size + nx * half_size  # bottom-right
        c4_y = y + uy * half_size + ny * half_size

        points = (
            f"{c1_x:.1f},{c1_y:.1f} {c2_x:.1f},{c2_y:.1f} "
            f"{c3_x:.1f},{c3_y:.1f} {c4_x:.1f},{c4_y:.1f} {c1_x:.1f},{c1_y:.1f}"
        )

        # Filled polygon
        elements.append(
            f'<polygon points="{points}" fill="{stroke}" fill-opacity="1"/>'
        )
        # Stroked polygon
        elements.append(f'<polygon points="{points}" {stroke_attr}/>')

    return elements


_InternalPoint = tuple[int, int]


def _managed_div(value: int, divisor: int) -> int:
    return value // divisor if value >= 0 else -(abs(value) // divisor)


def _managed_line_angle(center: _InternalPoint, angle_point: _InternalPoint) -> float:
    center_x = _managed_f32(float(center[0]))
    center_y = _managed_f32(float(center[1]))
    angle_x = _managed_f32(float(angle_point[0]))
    angle_y = _managed_f32(float(angle_point[1]))
    if center_x == angle_x and center_y == angle_y:
        return 0.0
    if center_x == angle_x:
        return 90.0 if angle_point[1] > center[1] else 270.0
    if center_y == angle_y:
        return 0.0 if angle_point[0] > center[0] else 180.0

    delta_y = _managed_i32(angle_point[1] - center[1])
    delta_x = _managed_i32(angle_point[0] - center[0])
    angle = _managed_f32(math.atan(float(delta_y) / float(delta_x)))
    angle = _managed_f32(angle * (180.0 / math.pi))
    if angle_point[0] > center[0] and angle_point[1] < center[1]:
        return _managed_f32(360.0 + angle)
    if angle_point[0] < center[0]:
        return _managed_f32(180.0 + angle)
    return angle


def _managed_rotate_translate(
    point: _InternalPoint,
    endpoint: _InternalPoint,
    angle_degrees: float,
) -> _InternalPoint:
    return managed_gdi_rotate_translate(point, endpoint, angle_degrees)


def _line_shape_geometry_ops(
    *,
    ctx: SchSvgRenderContext,
    endpoint: _InternalPoint,
    prev_point: _InternalPoint,
    shape: LineShape,
    shape_size: LineWidth,
    line_width: LineWidth,
    color_raw: int,
    units_per_px: int,
) -> list["SchGeometryOp"]:
    """Build endpoint geometry with the managed integer/GDI staging order."""
    from .altium_sch_geometry_oracle import (
        SchGeometryOp,
        _geometry_item_length,
        make_pen,
        make_solid_brush,
    )

    if shape == LineShape.NONE:
        return []

    base_size = {
        LineWidth.SMALLEST: 0,
        LineWidth.SMALL: 100_000,
        LineWidth.MEDIUM: 300_000,
        LineWidth.LARGE: 500_000,
    }[line_width]
    if base_size == 0:
        base_size = 100_000
    size = _managed_i32(base_size * (shape_size.value + 1))
    diameter = _managed_i32(2 * size)
    angle = _managed_line_angle(prev_point, endpoint)

    def transform(x: int, y: int) -> _InternalPoint:
        return _managed_rotate_translate((x, y), endpoint, angle)

    stroke_width_mils = LINE_WIDTH_MILS.get(line_width, 1.0)
    pen = make_pen(
        color_raw,
        width=0
        if line_width == LineWidth.SMALLEST
        else _geometry_item_length(
            stroke_width_mils * ctx.get_stroke_scale(),
            units_per_px=units_per_px,
        ),
        line_join="pljRound",
    )
    brush = make_solid_brush(color_raw)

    def svg_point(point: _InternalPoint) -> tuple[float, float]:
        return ctx.transform_point(point[0] / 100_000.0, point[1] / 100_000.0)

    def geo_point(point: _InternalPoint) -> tuple[float, float]:
        point_x, point_y = svg_point(point)
        return geometry_coord_tuple(
            point_x,
            point_y,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )

    def geo_line(first: _InternalPoint, second: _InternalPoint) -> Any:
        return SchGeometryOp.lines([geo_point(first), geo_point(second)], pen=pen)

    def geo_polygon(
        points: list[_InternalPoint],
        *,
        fill: bool = False,
        stroke: bool = False,
    ) -> list[SchGeometryOp]:
        geometry_points = [geo_point(point) for point in points]
        ops: list[SchGeometryOp] = []
        if fill:
            ops.append(SchGeometryOp.polygons([geometry_points], brush=brush))
        if stroke:
            ops.append(SchGeometryOp.polygons([geometry_points], pen=pen))
        return ops

    operations: list[SchGeometryOp] = []

    if shape == LineShape.ARROW:
        first = transform(
            _managed_i32(-2 * diameter), _managed_div(_managed_i32(2 * diameter), 3)
        )
        third = transform(
            _managed_i32(-2 * diameter),
            _managed_div(_managed_i32(-2 * diameter), 3),
        )
        operations.append(geo_line(first, endpoint))
        operations.append(geo_line(endpoint, third))

    elif shape == LineShape.SOLID_ARROW:
        tip = transform(_managed_div(_managed_i32(3 * size), 2), 0)
        vertical = _managed_div(_managed_i32(2 * _managed_i32(diameter + size)), 3)
        top = transform(_managed_i32(-2 * diameter), vertical)
        bottom = transform(_managed_i32(-2 * diameter), -vertical)
        operations.extend(geo_polygon([tip, top, bottom, tip], fill=True, stroke=True))

    elif shape == LineShape.TAIL:
        outer_top = transform(diameter, _managed_div(diameter, 2))
        outer_bottom = transform(diameter, _managed_div(-diameter, 2))
        inner = transform(-diameter, 0)
        inner_top = transform(0, _managed_div(diameter, 2))
        inner_bottom = transform(0, _managed_div(-diameter, 2))
        operations.append(geo_line(endpoint, outer_top))
        operations.append(geo_line(endpoint, outer_bottom))
        operations.append(geo_line(inner, inner_top))
        operations.append(geo_line(inner, inner_bottom))

    elif shape == LineShape.SOLID_TAIL:
        extent = _managed_i32(diameter + size)
        half_extent = _managed_div(extent, 2)
        points = [
            transform(size, 0),
            transform(extent, half_extent),
            transform(0, half_extent),
            transform(-diameter, 0),
            transform(0, -half_extent),
            transform(extent, -half_extent),
            transform(size, 0),
        ]
        operations.extend(geo_polygon(points, fill=True, stroke=True))

    elif shape == LineShape.CIRCLE:
        center_svg = svg_point(endpoint)
        radius_svg_point = svg_point((_managed_i32(endpoint[0] + size), endpoint[1]))
        radius = math.hypot(
            radius_svg_point[0] - center_svg[0],
            radius_svg_point[1] - center_svg[1],
        )
        radius_units = _geometry_item_length(
            radius,
            units_per_px=units_per_px,
        )
        center = geo_point(endpoint)
        operations.append(
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=center[0],
                center_y=center[1],
                half_width=radius_units,
                half_height=radius_units,
                corner_x_radius=radius_units,
                corner_y_radius=radius_units,
                brush=brush,
            )
        )
        operations.append(
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=center[0],
                center_y=center[1],
                half_width=radius_units,
                half_height=radius_units,
                corner_x_radius=radius_units,
                corner_y_radius=radius_units,
                pen=pen,
            )
        )

    elif shape == LineShape.SQUARE:
        half = _managed_div(_managed_i32(3 * size), 2)
        points = [
            transform(half, half),
            transform(-half, half),
            transform(-half, -half),
            transform(half, -half),
            transform(half, half),
        ]
        operations.extend(geo_polygon(points, fill=True, stroke=True))

    return operations


class AltiumSchPolyline(_LineStyleDirtyMixin, SchGraphicalObject):
    """
    Polyline record.

    Multi-segment graphical path with optional endpoint decorations.

    Public code should normally mutate:
    - ``points_mils`` for vertex coordinates
    - ``line_width`` for stroke thickness
    - ``line_style`` for dash pattern
    - ``start_line_shape`` / ``end_line_shape`` for endpoint markers
    - ``line_shape_size`` for the native endpoint marker size state

    ``line_shape_size`` deliberately reuses the public ``LineWidth`` enum.
    That is a naming compromise, not a file-format coincidence: native Altium
    stores polyline endpoint marker size through the same four-step size
    model used for schematic line widths.

    Raw ``vertices`` remains the lower-level internal coordinate storage.
    """

    def __init__(self) -> None:
        super().__init__()
        self.color = POLYLINE_COLOR
        self._apply_nonpersisted_area_color_default()
        self.vertices: list[CoordPoint] = []
        self._source_vertices: tuple[CoordPoint, ...] = ()
        self._source_has_invalid_vertex_wire_value = False
        self.line_width: LineWidth = LineWidth.SMALL
        self._line_style: LineStyle = LineStyle.SOLID
        self._line_style_dirty = False
        self._source_line_style: LineStyle = LineStyle.SOLID
        self.line_style_ext: int = 0  # Extended line style (newer Altium versions)
        # Line shape (endings) - arrows, circles, squares, etc.
        self.start_line_shape: LineShape = LineShape.NONE
        self.end_line_shape: LineShape = LineShape.NONE
        # Native polyline endpoint marker size is another size field, so callers
        # use LineWidth instead of a raw serializer byte.
        self.line_shape_size: LineWidth = LineWidth.SMALLEST
        # Track which fields were present
        self._has_line_width: bool = False
        self._has_line_style: bool = False
        self._has_line_style_ext: bool = False
        self._has_start_line_shape: bool = False
        self._has_end_line_shape: bool = False
        self._has_line_shape_size: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.POLYLINE

    @property
    def points_mils(self) -> list[SchPointMils]:
        """
        Public polyline path points expressed in mils.

        Use this property for normal mutation instead of working with the raw
        internal ``vertices`` coordinate storage.
        """
        return [
            SchPointMils.from_mils(vertex.x_mils, vertex.y_mils)
            for vertex in self.vertices
        ]

    @points_mils.setter
    def points_mils(self, value: list[SchPointMils]) -> None:
        if not isinstance(value, list):
            raise TypeError("points_mils must be a list of SchPointMils values")
        _validate_schematic_vertex_total(len(value))
        converted: list[CoordPoint] = []
        for point in value:
            if not isinstance(point, SchPointMils):
                raise TypeError("points_mils must contain only SchPointMils values")
            converted.append(point.to_coord_point())
        self.vertices = converted

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        """
        Parse from a record.
        """
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()

        # Parse line properties
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()

        # LineStyle vs LineStyleExt: prefer LineStyleExt if LineStyle is 0
        line_style_val, self._has_line_style = s.read_int(
            record, Fields.LINE_STYLE, default=0
        )
        self.line_style_ext, self._has_line_style_ext = s.read_int(
            record, Fields.LINE_STYLE_EXT, default=0
        )
        self.line_style = LineStyle(max(line_style_val, self.line_style_ext))
        self._source_line_style = self.line_style
        self._line_style_dirty = False

        # Parse line shape (endings)
        start_shape_val, self._has_start_line_shape = s.read_int(
            record, Fields.START_LINE_SHAPE, default=0
        )
        end_shape_val, self._has_end_line_shape = s.read_int(
            record, Fields.END_LINE_SHAPE, default=0
        )
        self.start_line_shape = (
            LineShape(start_shape_val) if start_shape_val <= 6 else LineShape.NONE
        )
        self.end_line_shape = (
            LineShape(end_shape_val) if end_shape_val <= 6 else LineShape.NONE
        )
        line_shape_size_val, self._has_line_shape_size = s.read_int(
            record, Fields.LINE_SHAPE_SIZE, default=0
        )
        self.line_shape_size = (
            LineWidth(line_shape_size_val)
            if line_shape_size_val in {member.value for member in LineWidth}
            else LineWidth.SMALLEST
        )

        # Parse vertices (vertex count + coordinates)
        vertex_count, _ = s.read_int(record, Fields.LOCATION_COUNT, default=0)
        extra_vertex_count, _ = s.read_int(record, "EXTRALOCATIONCOUNT", default=0)
        _validate_schematic_vertex_counts(vertex_count, extra_vertex_count)
        self.vertices = []
        self._source_has_invalid_vertex_wire_value = False

        for i in range(vertex_count):
            # Vertices use indexed field names: X1, Y1, X2, Y2, etc.
            x, x_frac = read_indexed_coord(record, f"X{i + 1}")
            y, y_frac = read_indexed_coord(record, f"Y{i + 1}")
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))
            self._source_has_invalid_vertex_wire_value |= (
                indexed_coord_has_invalid_wire_value(record, f"X{i + 1}")
                or indexed_coord_has_invalid_wire_value(record, f"Y{i + 1}")
            )

        for i in range(vertex_count + 1, vertex_count + extra_vertex_count + 1):
            x, x_frac = read_indexed_coord(record, f"EX{i}")
            y, y_frac = read_indexed_coord(record, f"EY{i}")
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))
            self._source_has_invalid_vertex_wire_value |= (
                indexed_coord_has_invalid_wire_value(record, f"EX{i}")
                or indexed_coord_has_invalid_wire_value(record, f"EY{i}")
            )
        self._source_vertices = tuple(self.vertices)

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Polylines use X1/Y1/X2/Y2 for vertices, NOT Location.X/Y
        # Remove Location fields that SchGraphicalObject adds
        for loc_key in [
            "Location.X",
            "Location.Y",
            "LOCATION.X",
            "LOCATION.Y",
            "Location.X_Frac",
            "Location.Y_Frac",
            "LOCATION.X_FRAC",
            "LOCATION.Y_FRAC",
        ]:
            record.pop(loc_key, None)

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record
        vertices_changed = raw is None or tuple(self.vertices) != self._source_vertices
        preserve_invalid_source = (
            not vertices_changed and self._source_has_invalid_vertex_wire_value
        )
        _validate_schematic_vertex_total(len(self.vertices))

        # Write location count
        main_vertex_count = min(len(self.vertices), 50)
        extra_vertex_count = max(len(self.vertices) - main_vertex_count, 0)

        s.write_int(
            record,
            Fields.LOCATION_COUNT,
            main_vertex_count,
            raw,
            force=bool(self.vertices),
        )
        if raw is None and main_vertex_count == 0:
            s.remove_field(record, Fields.LOCATION_COUNT)
        elif raw is not None and main_vertex_count == 0:
            source_count = int(
                raw.get("LocationCount", raw.get("LOCATIONCOUNT", 0)) or 0
            )
            if source_count != 0:
                s.remove_field(record, Fields.LOCATION_COUNT)

        if extra_vertex_count > 0:
            self._update_field(
                record,
                "EXTRALOCATIONCOUNT",
                extra_vertex_count,
                ["ExtraLocationCount", "EXTRALOCATIONCOUNT"],
                force=True,
            )
        else:
            self._remove_field(record, ["ExtraLocationCount", "EXTRALOCATIONCOUNT"])

        # Write line properties
        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )

        if raw is None or self._line_style_dirty:
            self._remove_fields_case_insensitively(
                record,
                [Fields.LINE_STYLE.canonical, Fields.LINE_STYLE_EXT.canonical],
            )
            primary_line_style = (
                0 if self.line_style == LineStyle.DASH_DOT else self.line_style.value
            )
            if primary_line_style != LineStyle.SOLID.value:
                s.write_int(
                    record,
                    Fields.LINE_STYLE,
                    primary_line_style,
                    None,
                    force=True,
                )
            if self.line_style != LineStyle.SOLID:
                s.write_int(
                    record,
                    Fields.LINE_STYLE_EXT,
                    self.line_style.value,
                    None,
                    force=True,
                )

        if raw is None or self._area_color_dirty:
            self._remove_fields_case_insensitively(record, ["AreaColor", "AREACOLOR"])

        # Write line shape (endings)
        self._serialize_managed_family_int(
            record,
            s,
            Fields.START_LINE_SHAPE.canonical,
            self.start_line_shape.value,
        )
        self._serialize_managed_family_int(
            record,
            s,
            Fields.END_LINE_SHAPE.canonical,
            self.end_line_shape.value,
        )
        self._serialize_managed_family_int(
            record, s, Fields.LINE_SHAPE_SIZE.canonical, self.line_shape_size.value
        )

        # Write vertices - Xn/Yn for first 50, EXn/EYn for the remainder.
        for i, vertex in enumerate(self.vertices, 1):
            if i <= 50:
                x_key = f"X{i}"
                y_key = f"Y{i}"
            else:
                x_key = f"EX{i}"
                y_key = f"EY{i}"
            validate_indexed_coord(vertex, x_key, y_key)
            if preserve_invalid_source:
                continue

            # Altium omits zero-value vertex coordinates
            if vertex.x != 0:
                self._update_field(record, x_key, vertex.x, [x_key], force=True)
            else:
                self._remove_field(record, [x_key])
            if vertex.y != 0:
                self._update_field(record, y_key, vertex.y, [y_key], force=True)
            else:
                self._remove_field(record, [y_key])

            frac_x_names = [f"{x_key}_Frac", f"{x_key}_FRAC"]
            frac_y_names = [f"{y_key}_Frac", f"{y_key}_FRAC"]

            if vertex.x_frac:
                self._update_field(
                    record, frac_x_names[0], vertex.x_frac, frac_x_names, force=True
                )
            else:
                self._remove_field(record, frac_x_names)

            if vertex.y_frac:
                self._update_field(
                    record, frac_y_names[0], vertex.y_frac, frac_y_names, force=True
                )
            else:
                self._remove_field(record, frac_y_names)

        stale_total = 0
        if raw is not None:
            stale_total = int(raw.get("LocationCount", raw.get("LOCATIONCOUNT", 0)))
            stale_total += int(
                raw.get("ExtraLocationCount", raw.get("EXTRALOCATIONCOUNT", 0))
            )

        stale_start = (
            stale_total + 1 if preserve_invalid_source else len(self.vertices) + 1
        )
        for i in range(stale_start, stale_total + 1):
            if i <= 50:
                x_key = f"X{i}"
                y_key = f"Y{i}"
            else:
                x_key = f"EX{i}"
                y_key = f"EY{i}"

            self._remove_field(
                record,
                [
                    x_key,
                    y_key,
                    f"{x_key}_Frac",
                    f"{x_key}_FRAC",
                    f"{y_key}_Frac",
                    f"{y_key}_FRAC",
                ],
            )

        self._move_geometry_identity_to_end_if_needed(record)
        return self._order_authored_graphical_fields(
            record,
            (
                "LineWidth",
                "LineStyle",
                "StartLineShape",
                "EndLineShape",
                "LineShapeSize",
                "Color",
                "__Vertices__",
                "LineStyleExt",
                "UniqueID",
            ),
        )

    @property
    def line_style(self) -> LineStyle:
        return super().line_style

    @line_style.setter
    def line_style(self, value: LineStyle) -> None:
        self._set_line_style(value)

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> Any:
        """
        Build an oracle-aligned geometry record for this polyline.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if len(self.vertices) < 2:
            return None

        points = [ctx.transform_coord_precise(vertex) for vertex in self.vertices]
        internal_points = [
            (
                managed_internal_coord(vertex.x, vertex.x_frac),
                managed_internal_coord(vertex.y, vertex.y_frac),
            )
            for vertex in self.vertices
        ]
        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        stroke = (
            color_to_hex(ctx.line_color_override)
            if ctx.line_color_override is not None
            else color_to_hex(self.color)
            if self.color is not None
            else ctx.default_stroke
        )
        stroke = ctx.apply_compile_mask_color(
            stroke, ctx.component_compile_masked is True
        )
        color_text = stroke.strip().lstrip("#")
        if len(color_text) == 6:
            color_raw = (
                int(color_text[0:2], 16)
                | (int(color_text[2:4], 16) << 8)
                | (int(color_text[4:6], 16) << 16)
            )
        else:
            color_raw = int(self.color) if self.color is not None else 0
        pen = make_pen(
            color_raw,
            width=0
            if self.line_width == LineWidth.SMALLEST
            else _geometry_item_length(
                stroke_width_mils * ctx.get_stroke_scale(),
                units_per_px=units_per_px,
            ),
            line_join="pljRound",
        )

        operations: list[SchGeometryOp] = []
        for start, end in zip(points, points[1:], strict=False):
            dash_segments = compute_dash_segments(
                start[0],
                start[1],
                end[0],
                end[1],
                self.line_style,
                stroke_width_mils,
                self.line_width,
                is_polyline_segment=True,
            )
            for sx1, sy1, sx2, sy2 in dash_segments:
                operations.append(
                    SchGeometryOp.lines(
                        [
                            svg_coord_to_geometry(
                                sx1,
                                sy1,
                                sheet_height_px=float(ctx.sheet_height or 0.0),
                                units_per_px=units_per_px,
                            ),
                            svg_coord_to_geometry(
                                sx2,
                                sy2,
                                sheet_height_px=float(ctx.sheet_height or 0.0),
                                units_per_px=units_per_px,
                            ),
                        ],
                        pen=pen,
                    )
                )

        if self.start_line_shape != LineShape.NONE and len(points) >= 2:
            operations.extend(
                _line_shape_geometry_ops(
                    ctx=ctx,
                    endpoint=internal_points[0],
                    prev_point=internal_points[1],
                    shape=self.start_line_shape,
                    shape_size=self.line_shape_size,
                    line_width=self.line_width,
                    color_raw=color_raw,
                    units_per_px=units_per_px,
                )
            )

        if self.end_line_shape != LineShape.NONE and len(points) >= 2:
            previous_index = -2
            endpoint_index = -1
            if len(self.vertices) >= 3:
                final = self.vertices[-1]
                previous = self.vertices[-2]
                if final.x == previous.x and final.y == previous.y:
                    previous_index = -3
                    endpoint_index = -2
            operations.extend(
                _line_shape_geometry_ops(
                    ctx=ctx,
                    endpoint=internal_points[endpoint_index],
                    prev_point=internal_points[previous_index],
                    shape=self.end_line_shape,
                    shape_size=self.line_shape_size,
                    line_width=self.line_width,
                    color_raw=color_raw,
                    units_per_px=units_per_px,
                )
            )

        xs = [float(vertex.x) for vertex in self.vertices]
        ys = [float(vertex.y) for vertex in self.vertices]
        inflate = stroke_width_mils + 2.0

        unique_id = cast(str, self.unique_id)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=unique_id,
            kind="polyline",
            object_id="ePolyline",
            bounds=SchGeometryBounds(
                left=int(round((min(xs) - inflate) * 100000)),
                top=int(round((max(ys) + inflate) * 100000)),
                right=int(round((max(xs) + inflate) * 100000)),
                bottom=int(round((min(ys) - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
