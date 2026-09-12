"""Schematic record model for SchRecordType.BLANKET."""

from collections.abc import Callable, Collection, Iterator
from dataclasses import replace
from math import sqrt
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ._altium_sch_bounds_physical import _BoundsPhysicalModelIndex
    from ._altium_sch_bounds_source_tree import _BoundsSourceTree
    from ._altium_sch_bounds_traversal import _BoundsTraversalIndex
    from .altium_font_manager import FontIDManager
    from .altium_record_sch__component import AltiumSchHarnessComponent
    from .altium_sch_geometry_oracle import (
        SchGeometryBounds,
        SchGeometryOp,
        SchGeometryRecord,
    )

from .altium_record_sch__polygon import AltiumSchPolygon
from ._sch_managed_defaults import BLANKET_BORDER_COLOR, BLANKET_FILL_COLOR
from .altium_record_types import (
    CoordPoint,
    LineStyle,
    LineWidth,
    SchRecordType,
    color_to_hex,
    hex_to_win32_color,
)
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    derive_triangle_indicator_colors,
    detect_case_mode_from_uppercase_fields,
    fill_indicator_color_from_area_color,
    geometry_coord_list,
)
from .altium_sch_svg_renderer import LINE_WIDTH_MILS, SchSvgRenderContext


def _blanket_triangle_color(border_color: int, button_color: int) -> int:
    # ColorSplit is symmetric, not a directed blend toward the second color.
    result = 0
    for shift in (0, 8, 16):
        first = (border_color >> shift) & 0xFF
        second = (button_color >> shift) & 0xFF
        channel = min(first, second) + round(abs(first - second) * 0.2)
        result |= channel << shift
    return result


def _blanket_border_edges(
    points: list[tuple[int, int]],
) -> Iterator[tuple[tuple[int, int], tuple[int, int]]]:
    if not points:
        return
    for index in range(len(points) - 1):
        yield points[index], points[index + 1]
    # BlanketDrawGraphObject closes from the first point to the last.
    yield points[0], points[-1]


def _blanket_pattern_metrics(
    first: tuple[int, int], second: tuple[int, int], *, dotted: bool, width: float
) -> tuple[int, float, float]:
    dx = float(second[0]) - float(first[0])
    dy = float(second[1]) - float(first[1])
    length = sqrt(dx * dx + dy * dy)
    period = width if width > 1.0 else 100_000.0
    count = int(length / period / (2.0 if dotted else 5.0))
    return count, dx, dy


def _blanket_dash_has_end_cap(
    first: tuple[int, int],
    second: tuple[int, int],
    count: int,
    dx: float,
    dy: float,
) -> bool:
    x1, y1 = first
    x2, y2 = second
    if count == 0:
        return x1 <= x2 and y1 <= y2
    step_x = dx / count
    step_y = dy / count
    current_x, current_y = float(x1), float(y1)
    for _ in range(count):
        current_x += step_x
        current_y += step_y
    return current_x <= x2 and current_y <= y2


def _blanket_pattern_operation_count(
    points: list[tuple[int, int]],
    *,
    dotted: bool,
    width: float,
    maximum: int,
) -> int:
    total = 0
    for first, second in _blanket_border_edges(points):
        count, dx, dy = _blanket_pattern_metrics(
            first, second, dotted=dotted, width=width
        )
        if count > maximum - total:
            return maximum + 1
        total += count
        if not dotted and _blanket_dash_has_end_cap(first, second, count, dx, dy):
            total += 1
            if total > maximum:
                return maximum + 1
    return total


def _blanket_pattern_segments(
    first: tuple[int, int],
    second: tuple[int, int],
    *,
    dotted: bool,
    width: float,
) -> Iterator[tuple[tuple[float, float], tuple[float, float]]]:
    x1, y1 = first
    x2, y2 = second
    count, dx, dy = _blanket_pattern_metrics(first, second, dotted=dotted, width=width)
    if count == 0:
        if not dotted and x1 <= x2 and y1 <= y2:
            yield (float(x1), float(y1)), (float(x2), float(y2))
        return
    step_x = dx / count
    step_y = dy / count
    dash_ratio = 100.0 if dotted else 1.6
    dash_x, dash_y = step_x / dash_ratio, step_y / dash_ratio
    current_x, current_y = float(x1), float(y1)
    for _ in range(count):
        yield (
            (current_x, current_y),
            (
                current_x + dash_x,
                current_y + dash_y,
            ),
        )
        current_x += step_x
        current_y += step_y
    if not dotted and current_x <= x2 and current_y <= y2:
        yield (current_x, current_y), (float(x2), float(y2))


class AltiumSchBlanket(AltiumSchPolygon):
    """
    BLANKET record.

    Blanket/region annotation for grouping.
    Inherits from POLYGON but with its own LineStyle support.

    Note: Blanket supports LineStyle/LineStyleExt even though Polygon does not
    (per native file format implementation).
    """

    def __init__(self) -> None:
        super().__init__()
        self.color = BLANKET_BORDER_COLOR
        self.area_color = BLANKET_FILL_COLOR
        self._init_family_dynamic_unique_id()
        self.line_width = LineWidth.SMALLEST
        self.is_solid = False  # Blankets are not filled by default
        self.transparent = True
        # Blanket has its own LineStyle support (unlike parent Polygon)
        self.line_style: LineStyle = LineStyle.DASHED  # Dashed border by default
        self.is_collapsed: bool = False
        self.corner = CoordPoint()
        # Track field presence
        self._has_line_style: bool = False
        self._has_collapsed: bool = False
        self._has_corner_x: bool = False
        self._has_corner_y: bool = False
        self._source_line_style: LineStyle = self.line_style
        self._source_collapsed: bool = self.is_collapsed
        # DrawObjectInfo/editor state is transient and never serialized.
        self._draw_disabled = False
        self._draw_dimmed = False
        self._draw_compilation_masked = False
        self._draw_editable_in_current_view = True
        self._draw_original_is_in_container = False
        self._capture_graphical_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.BLANKET

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse blanket record, including LineStyle fields.
        """
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading
        s = AltiumSerializer()
        self._parse_family_dynamic_unique_id(s, record)

        corner_x, corner_x_frac, self._has_corner_x = s.read_coord(
            record, "Corner", "X"
        )
        corner_y, corner_y_frac, self._has_corner_y = s.read_coord(
            record, "Corner", "Y"
        )
        self.corner = CoordPoint(
            corner_x,
            corner_y,
            corner_x_frac,
            corner_y_frac,
        )

        if not self.vertices:
            self._set_roundtrip_default_vertices(
                [self.location, self.location, self.corner, self.corner]
            )

        # Blanket supports LineStyle + LineStyleExt (same pattern as Line/Polyline)
        line_style_val, self._has_line_style = s.read_int(
            record, Fields.LINE_STYLE, default=0
        )
        line_style_ext, _ = s.read_int(record, Fields.LINE_STYLE_EXT, default=0)

        # LineStyleExt overrides if > LineStyle (same pattern as Line)
        if line_style_ext > line_style_val:
            self.line_style = LineStyle(line_style_ext)
        else:
            self.line_style = LineStyle(line_style_val)

        # Parse collapsed state (field is 'Collapsed', not 'IsCollapsed')
        self.is_collapsed, self._has_collapsed = s.read_bool(
            record, Fields.COLLAPSED, default=False
        )
        self._source_line_style = self.line_style
        self._source_collapsed = self.is_collapsed

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize blanket record, including LineStyle fields.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = detect_case_mode_from_uppercase_fields(
            self._raw_record,
            ignore_record=True,
        )
        s = AltiumSerializer(mode)
        raw = self._raw_record

        self._serialize_managed_family_color(
            record, s, Fields.COLOR.canonical, int(self.color or 0)
        )
        self._serialize_managed_family_color(
            record, s, Fields.AREA_COLOR.canonical, int(self.area_color or 0)
        )

        self._serialize_managed_family_coord(
            record,
            s,
            "Location",
            "X",
            self.location.x,
            self.location.x_frac,
        )
        self._serialize_managed_family_coord(
            record,
            s,
            "Location",
            "Y",
            self.location.y,
            self.location.y_frac,
        )
        self._serialize_managed_family_coord(
            record, s, "Corner", "X", self.corner.x, self.corner.x_frac
        )
        self._serialize_managed_family_coord(
            record, s, "Corner", "Y", self.corner.y, self.corner.y_frac
        )

        # Native blanket export always writes both LineStyle and LineStyleExt.
        # Import defaults missing LineStyle to SOLID, so omitting the dashed
        # default does not round-trip correctly.
        style_changed = self.line_style != self._source_line_style
        if raw is None or style_changed:
            s.remove_field(record, Fields.LINE_STYLE)
            s.remove_field(record, Fields.LINE_STYLE_EXT)
            if self.line_style is not LineStyle.SOLID:
                if self.line_style is not LineStyle.DASH_DOT:
                    s.write_int(
                        record,
                        Fields.LINE_STYLE,
                        self.line_style.value,
                        None,
                        force=True,
                    )
                s.write_int(
                    record,
                    Fields.LINE_STYLE_EXT,
                    self.line_style.value,
                    None,
                    force=True,
                )

        # Collapsed state
        self._serialize_managed_family_bool(
            record, s, Fields.COLLAPSED.canonical, self.is_collapsed
        )
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Corner.X",
                "Corner.X_Frac",
                "Corner.Y",
                "Corner.Y_Frac",
                "LineWidth",
                "Color",
                "AreaColor",
                "Collapsed",
                "LineStyle",
                "__Vertices__",
                "LineStyleExt",
                "UniqueID",
            ),
        )

    def _get_collapse_button_rectangle(self) -> tuple[float, float, float, float]:
        """Return managed painter rectangle edges in mils."""
        from ._altium_sch_component_bounds import _blanket_button_bounds
        from ._altium_record_sch__harness_layout import _unchecked_i32
        from .altium_sch_paint_order import _ManagedPaintRectangle

        if 4 * len(self.vertices) ** 2 > 1_000_000:
            raise ValueError("blanket render edge-test limit exceeded")
        rectangle = _ManagedPaintRectangle.from_bounds(
            _blanket_button_bounds(self.vertices)
        )
        return (
            rectangle.x / 100_000,
            rectangle.y / 100_000,
            _unchecked_i32(rectangle.x + rectangle.width) / 100_000,
            _unchecked_i32(rectangle.y + rectangle.height) / 100_000,
        )

    def _geometry_internal_vertices(
        self,
    ) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        from ._altium_sch_component_bounds import _blanket_button_bounds, _inflate
        from ._altium_record_sch__harness_layout import (
            _harness_internal_location,
            _trunc_i32_div,
            _unchecked_i32,
        )
        from .altium_sch_paint_order import _ManagedPaintRectangle

        button = _blanket_button_bounds(self.vertices)
        if self.is_collapsed:
            own = _inflate(button, 100_000)
            left, right = min(own.left, own.right), max(own.left, own.right)
            bottom, top = min(own.bottom, own.top), max(own.bottom, own.top)
            outline = [(left, bottom), (left, top), (right, top), (right, bottom)]
        else:
            outline = [_harness_internal_location(vertex) for vertex in self.vertices]
        rectangle = _ManagedPaintRectangle.from_bounds(button)
        left = rectangle.x
        right = _unchecked_i32(left + rectangle.width)
        top = rectangle.y
        bottom = _unchecked_i32(top + rectangle.height)
        middle = _trunc_i32_div(_unchecked_i32(left + right), 2)
        if self.is_collapsed:
            triangle = [
                (left, bottom),
                (right, bottom),
                (middle, _unchecked_i32(top + 100_000)),
            ]
        else:
            triangle = [
                (left, top),
                (right, top),
                (middle, _unchecked_i32(bottom - 100_000)),
            ]
        return outline, triangle

    def _geometry_vertices(
        self,
        ctx: SchSvgRenderContext,
        internal: tuple[list[tuple[int, int]], list[tuple[int, int]]] | None = None,
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        outline, triangle = internal or self._geometry_internal_vertices()
        return (
            [ctx.transform_point(x / 100_000, y / 100_000) for x, y in outline],
            [ctx.transform_point(x / 100_000, y / 100_000) for x, y in triangle],
        )

    def _render_dashed_border(
        self,
        points: list[tuple[float, float]],
        stroke: str,
        stroke_width: float,
        is_hairline: bool,
    ) -> list[str]:
        """
        Render dashed border as individual line segments.

        Each edge is processed independently, not as one continuous path.
        Segment count is based on edge length and stroke width, with separate
        spacing rules for dashed and dotted borders.

        Args:
            points: List of polygon vertices
            stroke: Stroke color
            stroke_width: Stroke width in pixels
            is_hairline: True if using hairline (0.5px) stroke

        Returns:
            List of SVG line elements
        """
        elements = []

        # Determine pattern based on line style
        # Altium treats LineStyle > 1 as DOTTED (see BlanketDrawGraphObject.DoDraw)
        is_dotted = self.line_style.value > 1

        # Period base: use stroke_width if > 1, else 1.0 (1 mil in SVG units).
        period_base = stroke_width if stroke_width > 1.0 else 1.0

        # Divisor and dash ratio per Altium algorithm
        if is_dotted:
            divisor = 2.0
            dash_ratio = 100.0  # dot_length = segment / 100
        else:
            divisor = 5.0
            dash_ratio = 1.6  # dash_length = segment / 1.6

        # Stroke attribute
        if is_hairline:
            stroke_attr = 'stroke-width="0.5px" vector-effect="non-scaling-stroke"'
        else:
            stroke_attr = f'stroke-width="{stroke_width:.0f}px"'

        # Build edge list as n-1 sequential edges plus a first-to-last closing edge.
        num_points = len(points)
        edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for i in range(num_points - 1):
            edges.append((points[i], points[i + 1]))
        # Closing edge goes from first to last (not last to first!)
        edges.append((points[0], points[-1]))

        for (x1, y1), (x2, y2) in edges:
            # Calculate edge length
            dx = x2 - x1
            dy = y2 - y1
            edge_length = (dx**2 + dy**2) ** 0.5

            if edge_length < 1e-6:
                continue

            # Calculate number of segments with integer truncation.
            num_segments = int(edge_length / period_base / divisor)

            if num_segments < 1:
                # If edge is too short for any segments, draw single line to end
                elements.append(
                    f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                    f'stroke="{stroke}" {stroke_attr}/>'
                )
                continue

            # Calculate step size per segment
            step_x = dx / num_segments
            step_y = dy / num_segments

            # Calculate dash/dot length (fraction of segment step)
            dash_x = step_x / dash_ratio
            dash_y = step_y / dash_ratio

            # Draw num_segments dashes/dots
            cx, cy = x1, y1
            for _ in range(num_segments):
                elements.append(
                    f'<line x1="{cx}" y1="{cy}" x2="{cx + dash_x}" y2="{cy + dash_y}" '
                    f'stroke="{stroke}" {stroke_attr}/>'
                )
                cx += step_x
                cy += step_y

            # For dashed edges, keep the explicit end-cap segment even when it
            # collapses to zero length at the corner.
            if not is_dotted:
                elements.append(
                    f'<line x1="{cx}" y1="{cy}" x2="{x2}" y2="{y2}" '
                    f'stroke="{stroke}" {stroke_attr}/>'
                )

        return elements

    def _render_collapse_triangle(
        self, min_x: float, min_y: float, border_color: str, collapsed: bool = False
    ) -> list[str]:
        """
        Render collapse indicator triangle.

        For blankets, native Altium renders STROKE ONLY (no fill).
        Triangle position depends on collapsed state:
        - Collapsed: base at min_y + 1, pointing DOWN (apex at base_y + 7)
        - Expanded: base at min_y + 9, pointing UP (apex at base_y - 7)

        Args:
            min_x, min_y: Top-left corner of blanket bounding box
            border_color: Border color for deriving triangle stroke color
            collapsed: True if blanket is collapsed

        Returns:
            List of SVG polygon elements
        """
        elements = []

        base_x = min_x + 1

        if collapsed:
            # Collapsed: base at min_y + 1, pointing DOWN
            # Native: 271,331 279,331 275,338
            base_y = min_y + 1
            p1 = f"{base_x},{base_y}"
            p2 = f"{base_x + 8},{base_y}"
            p3 = f"{base_x + 4},{base_y + 7}"
        else:
            # Expanded: base at min_y + 9, pointing UP
            # Native: 101,339 109,339 105,332
            base_y = min_y + 9
            p1 = f"{base_x},{base_y}"
            p2 = f"{base_x + 8},{base_y}"
            p3 = f"{base_x + 4},{base_y - 7}"

        points = f"{p1} {p2} {p3}"

        # Derive triangle stroke color from border color
        triangle_stroke = self._derive_triangle_stroke_color(border_color)

        # Stroke outline only (no fill for blankets)
        elements.append(
            f'<polygon points = "{points}" stroke="{triangle_stroke}" '
            f'stroke-width="0.5px" vector-effect="non-scaling-stroke"/>'
        )

        return elements

    def _derive_triangle_stroke_color(self, border_color: str) -> str:
        """Return the ordinary, unmodified collapse-button outline color."""
        return color_to_hex(
            _blanket_triangle_color(hex_to_win32_color(border_color), 0xFFFFFF)
        )

    def _derive_triangle_colors(self, border_color: str) -> tuple[str, str]:
        return derive_triangle_indicator_colors(
            border_color, area_color=self.area_color
        )

    def _get_fill_indicator_color(self) -> str:
        return fill_indicator_color_from_area_color(self.area_color)

    def _geometry_border_operations(
        self,
        ctx: SchSvgRenderContext,
        internal_points: list[tuple[int, int]],
        points: list[tuple[float, float]],
        coord: Callable[[float, float], list[float]],
        pen: dict[str, object],
    ) -> list["SchGeometryOp"]:
        from .altium_sch_geometry_oracle import SchGeometryOp

        if self.line_style == LineStyle.SOLID:
            return (
                [SchGeometryOp.polygons([[coord(x, y) for x, y in points]], pen=pen)]
                if len(points) > 2
                else []
            )
        if not (ctx.options.is_metafile or ctx._blanket_metafile_line_patterns):
            operations: list[SchGeometryOp] = []
            for first, second in _blanket_border_edges(internal_points):
                first_svg = ctx.transform_point(first[0] / 100_000, first[1] / 100_000)
                second_svg = ctx.transform_point(
                    second[0] / 100_000, second[1] / 100_000
                )
                operations.append(
                    SchGeometryOp.lines(
                        [coord(*first_svg), coord(*second_svg)], pen=pen
                    )
                )
            return operations
        solid_pen = {**pen, "dash_style": "pdsSolid"}
        operations: list[SchGeometryOp] = []
        dotted = self.line_style.value > LineStyle.DASHED.value
        width = LINE_WIDTH_MILS.get(self.line_width, 1.0) * 100_000
        for first, second in _blanket_border_edges(internal_points):
            for start, end in _blanket_pattern_segments(
                first, second, dotted=dotted, width=width
            ):
                start_svg = ctx.transform_point(start[0] / 100_000, start[1] / 100_000)
                end_svg = ctx.transform_point(end[0] / 100_000, end[1] / 100_000)
                operations.append(
                    SchGeometryOp.lines(
                        [coord(*start_svg), coord(*end_svg)], pen=solid_pen
                    )
                )
        return operations

    def _geometry_border_operation_count(
        self,
        ctx: SchSvgRenderContext,
        internal_points: list[tuple[int, int]],
        *,
        maximum: int,
    ) -> int:
        if self.line_style is LineStyle.SOLID:
            return int(self.is_collapsed or len(internal_points) > 2)
        if not (ctx.options.is_metafile or ctx._blanket_metafile_line_patterns):
            return len(internal_points)
        return _blanket_pattern_operation_count(
            internal_points,
            dotted=self.line_style.value > LineStyle.DASHED.value,
            width=LINE_WIDTH_MILS.get(self.line_width, 1.0) * 100_000,
            maximum=maximum,
        )

    def _geometry_record_bounds(self) -> "SchGeometryBounds":
        from ._altium_sch_component_bounds import _blanket_own_bounds

        return _blanket_own_bounds(
            self,
            max_vertices=len(self.vertices),
            max_edge_tests=4 * len(self.vertices) ** 2,
        )

    def _geometry_colors(self, ctx: SchSvgRenderContext) -> tuple[int, int]:
        from ._altium_record_sch__harness_layout import _harness_drawing_color

        source_line = int(self.color) if self.color is not None else 0x434343
        source_area = int(self.area_color) if self.area_color is not None else 0xFFFFFF
        line_color = (
            int(ctx.line_color_override)
            if ctx.line_color_override is not None
            else _harness_drawing_color(self, source_line, ctx)
        )
        area_color = (
            int(ctx.area_color_override)
            if ctx.area_color_override is not None
            else _harness_drawing_color(self, source_area, ctx)
        )
        return line_color, area_color

    def _geometry_fill_styles(
        self, ctx: SchSvgRenderContext, fill_color: int
    ) -> tuple[tuple[int, bool], ...]:
        styles: list[tuple[int, bool]] = []
        if not self.transparent and not self.is_collapsed:
            styles.append((0xFF, False))
        if (fill_color & 0xFFFFFF) != (ctx.sheet_area_color & 0xFFFFFF):
            alpha = 0x7D if self.transparent and not self.is_collapsed else 0xFF
            styles.append((alpha, ctx.transparent_back_group_present))
        return tuple(styles)

    @staticmethod
    def _geometry_pen_color(ctx: SchSvgRenderContext, color_raw: int) -> int:
        from ._altium_record_sch__harness_layout import _harness_state_gray_level
        from .altium_sch_svg_renderer import SchPaintColorMode

        options = ctx.options
        if options.is_metafile:
            if options.paint_color_mode is SchPaintColorMode.GRAYSCALE:
                return _harness_state_gray_level(color_raw)
            if options.paint_color_mode is SchPaintColorMode.MONOCHROME:
                return 0
        if options.emphasize:
            return 0x0000FF
        return color_raw

    @staticmethod
    def _geometry_brush_style(
        ctx: SchSvgRenderContext, color_raw: int, alpha: int
    ) -> tuple[int, int]:
        from ._altium_record_sch__harness_layout import _harness_state_gray_level
        from .altium_sch_svg_renderer import SchPaintColorMode

        if not ctx.options.is_metafile:
            return color_raw, alpha
        if ctx.options.paint_color_mode is SchPaintColorMode.GRAYSCALE:
            return _harness_state_gray_level(color_raw), alpha
        if ctx.options.paint_color_mode is SchPaintColorMode.MONOCHROME:
            return 0xFFFFFF, 0xFF
        return color_raw, alpha

    def _geometry_triangle_color(
        self, ctx: SchSvgRenderContext, stroke_raw: int
    ) -> int:
        from ._altium_record_sch__harness_layout import _harness_drawing_color

        button_raw = _harness_drawing_color(self, 0xFFFFFF, ctx)
        return self._geometry_pen_color(
            ctx, _blanket_triangle_color(stroke_raw, button_raw)
        )

    def _geometry_inverted_border_operations(
        self,
        ctx: SchSvgRenderContext,
        points: list[tuple[float, float]],
        coord: Callable[[float, float], list[float]],
        color_raw: int,
        units_per_px: int,
    ) -> list["SchGeometryOp"]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            _geometry_item_length,
            make_pen,
        )

        operations: list[SchGeometryOp] = []
        if len(points) > 4:
            style = "pdsDash" if self.line_style is LineStyle.DASHED else "pdsSolid"
            if self.line_style.value > LineStyle.DASHED.value:
                style = "pdsDot"
            operations.append(
                SchGeometryOp.lines(
                    [coord(x, y) for x, y in points[: len(points) - 3]],
                    pen=make_pen(
                        color_raw,
                        width=_geometry_item_length(
                            LINE_WIDTH_MILS.get(self.line_width, 1.0)
                            * ctx.get_stroke_scale(),
                            units_per_px=units_per_px,
                        ),
                        dash_style=style,
                    ),
                )
            )
        closing = [*points[len(points) - 4 :], points[0]]
        operations.append(
            SchGeometryOp.lines(
                [coord(x, y) for x, y in closing],
                pen=make_pen(color_raw, width=0, dash_style="pdsDash"),
            )
        )
        return operations

    def _geometry_should_draw(self, ctx: SchSvgRenderContext) -> bool:
        return ctx.owner_document_present and (
            not ctx.options.is_metafile or ctx.options.metafile_blankets
        )

    def _geometry_edge_test_count(self) -> int:
        scan_count = 2 if self.is_collapsed else 1
        return scan_count * 4 * len(self.vertices) ** 2

    def _geometry_is_inverted(self, ctx: SchSvgRenderContext) -> bool:
        return (
            ctx.options.inverted_objects_editor
            and not self._draw_original_is_in_container
            and not self.is_collapsed
            and len(self.vertices) > 3
        )

    def _blanket_geometry_operations(
        self,
        ctx: SchSvgRenderContext,
        border_points_internal: list[tuple[int, int]],
        border_points_svg: list[tuple[float, float]],
        triangle_points_svg: list[tuple[float, float]],
        fill_styles: tuple[tuple[int, bool], ...],
        *,
        stroke_raw: int,
        fill_raw: int,
        inverted: bool,
        units_per_px: int,
    ) -> list["SchGeometryOp"]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            _geometry_item_length,
            make_pen,
            make_solid_brush,
        )

        sheet_height_px = float(ctx.sheet_height or 0.0)

        def coord(px: float, py: float) -> list[float]:
            return geometry_coord_list(
                px, py, sheet_height_px=sheet_height_px, units_per_px=units_per_px
            )

        pen_width = (
            0
            if self.line_width == LineWidth.SMALLEST
            else _geometry_item_length(
                LINE_WIDTH_MILS.get(self.line_width, 1.0) * ctx.get_stroke_scale(),
                units_per_px=units_per_px,
            )
        )
        pen_color = self._geometry_pen_color(ctx, stroke_raw)
        dash_style = "pdsSolid"
        if self.line_style is LineStyle.DASHED:
            dash_style = "pdsDash"
        elif self.line_style.value > LineStyle.DASHED.value:
            dash_style = "pdsDot"
        border_pen = make_pen(pen_color, width=pen_width, dash_style=dash_style)
        operations: list[SchGeometryOp] = []
        for alpha, transparent_back in fill_styles:
            brush_color, brush_alpha = self._geometry_brush_style(ctx, fill_raw, alpha)
            fill = SchGeometryOp.polygons(
                [[coord(x, y) for x, y in border_points_svg]],
                brush=make_solid_brush(brush_color, alpha=brush_alpha),
            )
            fill.payload["transparent_back"] = transparent_back
            operations.append(fill)
        border_operations = (
            self._geometry_inverted_border_operations(
                ctx,
                border_points_svg,
                coord,
                pen_color,
                units_per_px,
            )
            if inverted
            else self._geometry_border_operations(
                ctx,
                border_points_internal,
                border_points_svg,
                coord,
                border_pen,
            )
        )
        operations.extend(border_operations)
        triangle_pen = make_pen(self._geometry_triangle_color(ctx, stroke_raw), width=0)
        operations.append(
            SchGeometryOp.polygons(
                [[coord(x, y) for x, y in triangle_points_svg]], pen=triangle_pen
            )
        )
        return operations

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        from .altium_sch_geometry_oracle import (
            SchGeometryRecord,
            wrap_record_operations,
        )

        if not self._geometry_should_draw(ctx):
            return None

        stroke_raw, fill_raw = self._geometry_colors(ctx)
        inverted = self._geometry_is_inverted(ctx)
        can_draw_polygon = self.is_collapsed or len(self.vertices) > 2
        fill_styles = (
            self._geometry_fill_styles(ctx, fill_raw) if can_draw_polygon else ()
        )
        if inverted:
            fill_styles = fill_styles[: int(not self.transparent)]
        edge_tests = self._geometry_edge_test_count()
        ctx._blanket_work_budget.preflight_edge_tests(edge_tests)
        internal_vertices = self._geometry_internal_vertices()
        border_points_internal, _ = internal_vertices
        fixed_operations = 9 + len(fill_styles)
        remaining = max(
            0,
            ctx._blanket_work_budget.max_operations
            - ctx._blanket_work_budget.operations
            - fixed_operations,
        )
        border_operations = (
            (2 if len(self.vertices) > 4 else 1)
            if inverted
            else self._geometry_border_operation_count(
                ctx,
                border_points_internal,
                maximum=remaining,
            )
        )
        ctx._blanket_work_budget.reserve_render_work(
            edge_tests=edge_tests,
            operations=fixed_operations + border_operations,
        )
        border_points_svg, triangle_points_svg = self._geometry_vertices(
            ctx, internal_vertices
        )
        operations = self._blanket_geometry_operations(
            ctx,
            border_points_internal,
            border_points_svg,
            triangle_points_svg,
            fill_styles,
            stroke_raw=stroke_raw,
            fill_raw=fill_raw,
            inverted=inverted,
            units_per_px=units_per_px,
        )
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="blanket",
            object_id="eBlanket",
            bounds=self._geometry_record_bounds(),
            operations=wrap_record_operations(
                self.unique_id, operations, units_per_px=units_per_px
            ),
        )

    def __repr__(self) -> str:
        vertex_count = len(self.vertices)
        return f"<AltiumSchBlanket vertices={vertex_count} line_style={self.line_style.name}>"


def _complete_blanket_record_bounds(
    records: Collection["SchGeometryRecord"],
    ctx: SchSvgRenderContext,
    *,
    document_kind: Literal["schematic", "harness_layout", "harness_wiring", "library"],
) -> list["SchGeometryRecord"]:
    """Apply managed visible-descendant bounds in one bounded source-tree pass."""
    from ._altium_sch_bounds_source_tree import _build_bounds_source_tree
    from ._altium_sch_bounds_traversal import _build_bounds_traversal_index

    admission = ctx._source_admission
    parents = admission.parent_by_source_id
    sources = admission.source_objects
    if parents is None or not any(
        type(source) is AltiumSchBlanket for source in sources
    ):
        return list(records)
    ctx._blanket_work_budget.reserve_bounds_sources(len(sources))
    tree = _build_bounds_source_tree(sources, parents, max_sources=len(sources))
    traversal = _build_bounds_traversal_index(tree, document_kind=document_kind)
    bounds_by_source = _blanket_geometry_bounds_by_source(records)
    admitted_ids = frozenset(
        id(source) for source in sources if admission.admits(source)
    )
    aggregate = _blanket_aggregate_bounds(
        sources,
        tree,
        traversal,
        bounds_by_source,
        admitted_ids,
    )
    replacements = _blanket_record_bounds_replacements(
        records, sources, tree, aggregate, bounds_by_source
    )
    return [replacements.get(id(record), record) for record in records]


def _blanket_geometry_bounds_by_source(
    records: Collection["SchGeometryRecord"],
) -> dict[int, "SchGeometryBounds | None"]:
    result: dict[int, SchGeometryBounds | None] = {}
    for record in records:
        source_id, bounds = record.render_source_id, record.bounds
        if source_id is None or bounds is None:
            continue
        result[source_id] = _enclose_blanket_bounds(
            result.get(source_id), _valid_blanket_bounds(bounds)
        )
    return result


def _blanket_aggregate_bounds(
    sources: tuple[object, ...],
    tree: "_BoundsSourceTree",
    traversal: "_BoundsTraversalIndex",
    bounds_by_source: dict[int, "SchGeometryBounds | None"],
    admitted_ids: frozenset[int],
) -> list["SchGeometryBounds | None"]:
    physical_models = _blanket_physical_model_index(sources, tree)
    aggregate: list[SchGeometryBounds | None] = [None] * len(sources)
    for index in _blanket_bounds_postorder(tree.parents, tree.children):
        if not _blanket_bounds_candidate_passes(index, traversal, admitted_ids):
            continue
        combined = _blanket_source_own_bounds(
            index,
            sources,
            physical_models,
            bounds_by_source,
            admitted_ids,
        )
        for child in tree.children[index]:
            combined = _enclose_blanket_bounds(combined, aggregate[child])
        aggregate[index] = combined
    return aggregate


def _blanket_physical_model_index(
    sources: tuple[object, ...], tree: "_BoundsSourceTree"
) -> "_BoundsPhysicalModelIndex | None":
    from ._altium_sch_bounds_physical import _build_bounds_physical_model_index
    from .altium_record_sch__component import AltiumSchHarnessComponent

    if not any(type(source) is AltiumSchHarnessComponent for source in sources):
        return None
    return _build_bounds_physical_model_index(
        tree,
        max_sources=len(sources),
        max_references=3 * len(sources),
    )


def _blanket_source_own_bounds(
    index: int,
    sources: tuple[object, ...],
    physical_models: "_BoundsPhysicalModelIndex | None",
    bounds_by_source: dict[int, "SchGeometryBounds | None"],
    admitted_ids: frozenset[int],
) -> "SchGeometryBounds | None":
    from .altium_record_sch__component import (
        AltiumSchComponent,
        AltiumSchHarnessComponent,
    )

    source = sources[index]
    if not isinstance(source, AltiumSchComponent):
        return bounds_by_source.get(id(source))
    if type(source) is not AltiumSchHarnessComponent:
        return None
    assert isinstance(source, AltiumSchHarnessComponent)
    return _blanket_harness_component_bounds(
        index,
        source,
        sources,
        physical_models,
        bounds_by_source,
        admitted_ids,
    )


def _blanket_harness_component_bounds(
    index: int,
    source: "AltiumSchHarnessComponent",
    sources: tuple[object, ...],
    physical_models: "_BoundsPhysicalModelIndex | None",
    bounds_by_source: dict[int, "SchGeometryBounds | None"],
    admitted_ids: frozenset[int],
) -> "SchGeometryBounds | None":
    from .altium_record_sch__parameter import AltiumSchImageParameter

    assert physical_models is not None
    main = physical_models.main_parameters[index]
    if main is None or id(sources[main]) not in admitted_ids:
        return None
    parameter = sources[main]
    assert isinstance(parameter, AltiumSchImageParameter)
    if parameter.is_hidden:
        return None
    model = physical_models.parameter_models[main]
    if model is not None and id(sources[model]) not in admitted_ids:
        model = None
    if model is not None:
        return _blanket_harness_model_bounds(
            parameter, sources[model], bounds_by_source
        )
    return _blanket_harness_component_fallback_bounds(source)


def _blanket_harness_model_bounds(
    parameter: object,
    model: object,
    bounds_by_source: dict[int, "SchGeometryBounds | None"],
) -> "SchGeometryBounds":
    bounds = bounds_by_source.get(id(parameter))
    if bounds is None:
        bounds = bounds_by_source.get(id(model))
    if bounds is None:
        raise NotImplementedError(
            "Blanket bounds require emitted harness physical-model bounds"
        )
    return bounds


def _blanket_harness_component_fallback_bounds(
    source: "AltiumSchHarnessComponent",
) -> "SchGeometryBounds":
    from ._altium_record_sch__harness_layout import (
        _harness_internal_location,
        _unchecked_i32,
    )
    from .altium_sch_geometry_oracle import SchGeometryBounds

    x, y = _harness_internal_location(source.location)
    return SchGeometryBounds(
        left=_unchecked_i32(x - 5),
        bottom=_unchecked_i32(y - 5),
        right=_unchecked_i32(x + 5),
        top=_unchecked_i32(y + 5),
    )


def _blanket_record_bounds_replacements(
    records: Collection["SchGeometryRecord"],
    sources: tuple[object, ...],
    tree: "_BoundsSourceTree",
    aggregate: list["SchGeometryBounds | None"],
    bounds_by_source: dict[int, "SchGeometryBounds | None"],
) -> dict[int, "SchGeometryRecord"]:
    replacements: dict[int, SchGeometryRecord] = {}
    source_indexes = {id(source): index for index, source in enumerate(sources)}
    for record in records:
        source_id = record.render_source_id
        if source_id is None:
            continue
        index = source_indexes.get(source_id)
        if index is None or type(sources[index]) is not AltiumSchBlanket:
            continue
        blanket = sources[index]
        assert isinstance(blanket, AltiumSchBlanket)
        bounds = bounds_by_source.get(source_id)
        for child in tree.children[index]:
            bounds = _enclose_blanket_bounds(bounds, aggregate[child])
        if bounds is None:
            bounds = _blanket_fallback_bounds(blanket)
        replacements[id(record)] = replace(record, bounds=bounds)
    return replacements


def _blanket_fallback_bounds(blanket: AltiumSchBlanket) -> "SchGeometryBounds":
    from ._altium_record_sch__harness_layout import (
        _harness_internal_location,
        _unchecked_i32,
    )
    from .altium_sch_geometry_oracle import SchGeometryBounds

    x, y = _harness_internal_location(blanket.location)
    return SchGeometryBounds(
        left=_unchecked_i32(x - 500_000),
        bottom=_unchecked_i32(y - 500_000),
        right=_unchecked_i32(x + 500_000),
        top=_unchecked_i32(y + 500_000),
    )


def _blanket_bounds_postorder(
    parents: tuple[int | None, ...], children: tuple[tuple[int, ...], ...]
) -> list[int]:
    result: list[int] = []
    for root, parent in enumerate(parents):
        if parent is not None:
            continue
        stack = [(root, False)]
        while stack:
            index, visited = stack.pop()
            if visited:
                result.append(index)
                continue
            stack.append((index, True))
            stack.extend((child, False) for child in reversed(children[index]))
    return result


def _blanket_bounds_candidate_passes(
    index: int,
    traversal: "_BoundsTraversalIndex",
    admitted_ids: frozenset[int],
) -> bool:
    source = traversal.tree.records[index]
    if id(source) not in admitted_ids:
        return False
    if not traversal.spatial_current_part[index]:
        return False
    if _blanket_is_complex_text_source(source):
        return False
    visible = traversal.ordinal_visible[index]
    if visible is None:
        raise NotImplementedError(
            "Blanket descendant bounds require prepared library pin visibility"
        )
    return visible


def _blanket_is_complex_text_source(source: object) -> bool:
    from .altium_record_sch__designator import AltiumSchDesignator
    from .altium_record_sch__file_name import AltiumSchFileName
    from .altium_record_sch__harness_type import AltiumSchHarnessType
    from .altium_record_sch__parameter import AltiumSchParameter
    from .altium_record_sch__sheet_name import AltiumSchSheetName
    from .altium_record_sch__template import AltiumSchTemplate

    return type(source) is AltiumSchParameter or isinstance(
        source,
        (
            AltiumSchTemplate,
            AltiumSchSheetName,
            AltiumSchFileName,
            AltiumSchDesignator,
            AltiumSchHarnessType,
        ),
    )


def _valid_blanket_bounds(
    bounds: "SchGeometryBounds",
) -> "SchGeometryBounds | None":
    if (
        bounds.left == 2_147_483_647
        and bounds.bottom == 2_147_483_647
        and bounds.right == -2_147_483_647
        and bounds.top == -2_147_483_647
    ):
        return None
    return bounds


def _enclose_blanket_bounds(
    first: "SchGeometryBounds | None", second: "SchGeometryBounds | None"
) -> "SchGeometryBounds | None":
    from .altium_sch_geometry_oracle import SchGeometryBounds

    if second is None:
        return first
    second_left, second_right = sorted((second.left, second.right))
    second_bottom, second_top = sorted((second.bottom, second.top))
    if first is None:
        return SchGeometryBounds(
            left=second_left,
            bottom=second_bottom,
            right=second_right,
            top=second_top,
        )
    left = min(first.left, second_left)
    bottom = min(first.bottom, second_bottom)
    right = max(first.right, second_right)
    top = max(first.top, second_top)
    return SchGeometryBounds(
        left=min(left, right),
        bottom=min(bottom, top),
        right=max(left, right),
        top=max(bottom, top),
    )
