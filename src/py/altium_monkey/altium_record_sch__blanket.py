"""Schematic record model for SchRecordType.BLANKET."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_sch__polygon import AltiumSchPolygon
from .altium_record_types import (
    LineStyle,
    LineWidth,
    SchRecordType,
    color_to_hex,
    hex_to_win32_color,
)
from .altium_serializer import AltiumSerializer, CaseMode, Fields
from .altium_sch_record_helpers import (
    derive_triangle_indicator_colors,
    detect_case_mode_from_uppercase_fields,
    fill_indicator_color_from_area_color,
    geometry_coord_list,
)
from .altium_sch_svg_renderer import LINE_WIDTH_MILS, SchSvgRenderContext


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
        self.is_solid = False  # Blankets are not filled by default
        self.transparent = True
        # Blanket has its own LineStyle support (unlike parent Polygon)
        self.line_style: LineStyle = LineStyle.DASHED  # Dashed border by default
        self.is_collapsed: bool = False
        # Track field presence
        self._has_line_style: bool = False
        self._has_collapsed: bool = False

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

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize blanket record, including LineStyle fields.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = detect_case_mode_from_uppercase_fields(self._raw_record)
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Native blanket export always writes both LineStyle and LineStyleExt.
        # Import defaults missing LineStyle to SOLID, so omitting the dashed
        # default does not round-trip correctly.
        clamped_style = self.line_style.value if self.line_style.value < 3 else 0
        s.write_int(record, Fields.LINE_STYLE, clamped_style, raw)
        s.write_int(record, Fields.LINE_STYLE_EXT, self.line_style.value, raw)

        # Collapsed state
        if self._has_collapsed or self.is_collapsed:
            s.write_bool(record, Fields.COLLAPSED, self.is_collapsed, raw)

        return record

    @staticmethod
    def _point_in_polygon(
        x: float, y: float, polygon: list[tuple[float, float]]
    ) -> bool:
        inside = False
        if len(polygon) < 3:
            return False

        x1, y1 = polygon[-1]
        for x2, y2 in polygon:
            intersects = ((y1 > y) != (y2 > y)) and (
                x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
            )
            if intersects:
                inside = not inside
            x1, y1 = x2, y2
        return inside

    def _get_collapse_button_rectangle(self) -> tuple[float, float, float, float]:
        """
        Mirror SchCollapsiblePolygon.GetState_CollapseButtonRectangle().

        Native scans four 8-mil button boxes around each vertex, keeps only the
        candidates whose center lies inside the polygon, then chooses the
        bottom-most candidate with a left-most tie-break.
        """
        polygon = [
            (
                float(vertex.x) + vertex.x_frac / 100000.0,
                float(vertex.y) + vertex.y_frac / 100000.0,
            )
            for vertex in self.vertices
        ]
        if not polygon:
            return (1.0, 1.0, 9.0, 9.0)

        selected_rect: tuple[float, float, float, float] | None = None
        selected_bottom: float | None = None
        selected_left: float | None = None

        for vertex_x, vertex_y in polygon:
            candidates = (
                (vertex_x + 1.0, vertex_y - 9.0, vertex_x + 9.0, vertex_y - 1.0),
                (vertex_x - 1.0, vertex_y - 9.0, vertex_x - 9.0, vertex_y - 1.0),
                (vertex_x - 1.0, vertex_y + 9.0, vertex_x - 9.0, vertex_y + 1.0),
                (vertex_x + 1.0, vertex_y + 9.0, vertex_x + 9.0, vertex_y + 1.0),
            )
            for raw_left, raw_bottom, raw_right, raw_top in candidates:
                center_x = (raw_left + raw_right) / 2.0
                center_y = (raw_top + raw_bottom) / 2.0
                if not self._point_in_polygon(center_x, center_y, polygon):
                    continue

                if (
                    selected_rect is None
                    or selected_bottom <= raw_bottom
                    and (selected_bottom != raw_bottom or selected_left >= raw_left)
                ):
                    selected_rect = (
                        min(raw_left, raw_right),
                        min(raw_top, raw_bottom),
                        max(raw_left, raw_right),
                        max(raw_top, raw_bottom),
                    )
                    selected_bottom = raw_bottom
                    selected_left = raw_left

        if selected_rect is not None:
            return selected_rect

        min_x = min(point_x for point_x, _ in polygon)
        min_y = min(point_y for _, point_y in polygon)
        return (min_x + 1.0, min_y + 1.0, min_x + 9.0, min_y + 9.0)

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
        """
        Derive collapse triangle stroke color from border color.

        Native Altium uses a slightly different shade for the triangle.
        E.g., #434343 border -> #696969 triangle

        Args:
            border_color: Border color in hex format

        Returns:
            Triangle stroke color in hex format
        """
        border_upper = border_color.upper()

        # Map based on common border colors
        if border_upper in ("#434343", "#000000"):
            return "#696969"
        elif border_upper == "#800000":
            return "#990000"
        else:
            return border_color

    def _derive_triangle_colors(self, border_color: str) -> tuple[str, str]:
        return derive_triangle_indicator_colors(
            border_color, area_color=self.area_color
        )

    def _get_fill_indicator_color(self) -> str:
        return fill_indicator_color_from_area_color(self.area_color)

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if len(self.vertices) < 3:
            return None

        svg_points = [ctx.transform_coord_precise(vertex) for vertex in self.vertices]
        min_x = min(point_x for point_x, _ in svg_points)
        min_y = min(point_y for _, point_y in svg_points)
        max_x = max(point_x for point_x, _ in svg_points)
        max_y = max(point_y for _, point_y in svg_points)
        sheet_height_px = float(ctx.sheet_height or 0.0)

        coord = lambda px, py: geometry_coord_list(
            px,
            py,
            sheet_height_px=sheet_height_px,
            units_per_px=units_per_px,
        )

        stroke_hex = color_to_hex(self.color) if self.color is not None else "#434343"
        stroke_raw = (
            int(self.color)
            if self.color is not None
            else hex_to_win32_color(stroke_hex)
        )
        fill_raw = int(self.area_color) if self.area_color is not None else 0xFFFFFF
        pen_width = (
            0
            if self.line_width == LineWidth.SMALLEST
            else int(round(LINE_WIDTH_MILS.get(self.line_width, 1.0) * units_per_px))
        )
        border_pen = make_pen(stroke_raw, width=pen_width)
        triangle_pen = make_pen(
            hex_to_win32_color(self._derive_triangle_stroke_color(stroke_hex)),
            width=0,
        )
        fill_brush = make_solid_brush(
            fill_raw, alpha=0xFF if self.is_collapsed else 0x7D
        )
        operations: list[SchGeometryOp] = []

        if self.is_collapsed:
            border_points_svg = [
                (min_x, min_y + 10.0),
                (min_x, min_y),
                (min_x + 10.0, min_y),
                (min_x + 10.0, min_y + 10.0),
            ]
        else:
            border_points_svg = list(svg_points)

        operations.append(
            SchGeometryOp.polygons(
                [[coord(point_x, point_y) for point_x, point_y in border_points_svg]],
                brush=fill_brush,
            )
        )

        if self.line_style == LineStyle.SOLID:
            operations.append(
                SchGeometryOp.polygons(
                    [
                        [
                            coord(point_x, point_y)
                            for point_x, point_y in border_points_svg
                        ]
                    ],
                    pen=border_pen,
                )
            )
        else:
            is_dotted = self.line_style.value > 1
            stroke_width = LINE_WIDTH_MILS.get(self.line_width, 1.0)
            period_base = stroke_width if stroke_width > 1.0 else 1.0
            divisor = 2.0 if is_dotted else 5.0
            dash_ratio = 100.0 if is_dotted else 1.6

            edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
            for index in range(len(border_points_svg) - 1):
                edges.append((border_points_svg[index], border_points_svg[index + 1]))
            edges.append((border_points_svg[0], border_points_svg[-1]))

            for (x1, y1), (x2, y2) in edges:
                dx = x2 - x1
                dy = y2 - y1
                edge_length = (dx**2 + dy**2) ** 0.5
                if edge_length < 1e-6:
                    continue

                num_segments = int(edge_length / period_base / divisor)
                if num_segments < 1:
                    operations.append(
                        SchGeometryOp.lines(
                            [coord(x1, y1), coord(x2, y2)],
                            pen=border_pen,
                        )
                    )
                    continue

                step_x = dx / num_segments
                step_y = dy / num_segments
                dash_x = step_x / dash_ratio
                dash_y = step_y / dash_ratio
                cx, cy = x1, y1
                for _ in range(num_segments):
                    operations.append(
                        SchGeometryOp.lines(
                            [coord(cx, cy), coord(cx + dash_x, cy + dash_y)],
                            pen=border_pen,
                        )
                    )
                    cx += step_x
                    cy += step_y

                if not is_dotted:
                    operations.append(
                        SchGeometryOp.lines(
                            [coord(cx, cy), coord(x2, y2)],
                            pen=border_pen,
                        )
                    )

        rect_left, rect_top, rect_right, rect_bottom = (
            self._get_collapse_button_rectangle()
        )
        mid_x = (rect_left + rect_right) / 2.0
        if self.is_collapsed:
            triangle_points_svg = [
                ctx.transform_point(rect_left, rect_bottom),
                ctx.transform_point(rect_right, rect_bottom),
                ctx.transform_point(mid_x, rect_top + 1.0),
            ]
        else:
            triangle_points_svg = [
                ctx.transform_point(rect_left, rect_top),
                ctx.transform_point(rect_right, rect_top),
                ctx.transform_point(mid_x, rect_bottom - 1.0),
            ]
        operations.append(
            SchGeometryOp.polygons(
                [[coord(point_x, point_y) for point_x, point_y in triangle_points_svg]],
                pen=triangle_pen,
            )
        )

        xs = [float(vertex.x) for vertex in self.vertices]
        ys = [float(vertex.y) for vertex in self.vertices]

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="blanket",
            object_id="eBlanket",
            bounds=SchGeometryBounds(
                left=int(round(min(xs) * 100000)),
                top=int(round(max(ys) * 100000)),
                right=int(round(max(xs) * 100000)),
                bottom=int(round(min(ys) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        vertex_count = len(self.vertices)
        return f"<AltiumSchBlanket vertices={vertex_count} line_style={self.line_style.name}>"
