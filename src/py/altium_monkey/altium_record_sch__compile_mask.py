"""Schematic record model for SchRecordType.COMPILE_MASK."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_sch__rectangle import AltiumSchRectangle
from .altium_record_types import (
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
from .altium_sch_svg_renderer import LINE_WIDTH_MILS


class AltiumSchCompileMask(AltiumSchRectangle):
    """
    COMPILE_MASK record.

    Compile mask region to exclude from DRC.
    Inherits from RECTANGLE.
    """

    def __init__(self) -> None:
        super().__init__()
        self.transparent = True  # Default for compile masks
        self.is_collapsed: bool = False
        # Track field presence
        self._has_collapsed: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.COMPILE_MASK

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse compile mask record, including Collapsed field.
        """
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading
        s = AltiumSerializer()

        # Parse collapsed state (field is 'Collapsed')
        self.is_collapsed, self._has_collapsed = s.read_bool(
            record, Fields.COLLAPSED, default=False
        )

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize compile mask record, including Collapsed field.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = detect_case_mode_from_uppercase_fields(self._raw_record)
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Collapsed state
        if self._has_collapsed or self.is_collapsed:
            s.write_bool(record, Fields.COLLAPSED, self.is_collapsed, raw)

        return record

    def _render_dashed_border(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        stroke: str,
        stroke_width: float,
        is_hairline: bool,
    ) -> list[str]:
        """
        Render dashed border as individual line segments.

        Native Altium renders dashed borders as separate line elements rather than
        using stroke-dasharray. This ensures proper dash alignment at corners.

        Args:
            min_x, min_y, max_x, max_y: Rectangle bounds
            stroke: Stroke color
            stroke_width: Stroke width in pixels
            is_hairline: True if using hairline (0.5px) stroke

        Returns:
            List of SVG line elements
        """
        elements = []

        # Dash pattern parameters. Hairline uses the baseline dash/gap pair,
        # and thicker lines scale proportionally.
        if is_hairline:
            dash_length = 3.125
            gap_length = 1.875
            stroke_attr = 'stroke-width="0.5px" vector-effect="non-scaling-stroke"'
        else:
            # Scale dash pattern with line width
            scale_factor = stroke_width / 0.5
            dash_length = 3.125 * scale_factor
            gap_length = 1.875 * scale_factor
            stroke_attr = f'stroke-width="{stroke_width:.0f}px"'

        period = dash_length + gap_length

        # Helper to render dashed line
        def render_dashed_line(x1: float, y1: float, x2: float, y2: float) -> list[str]:
            lines = []
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if length == 0:
                return lines

            # Direction vector
            dx = (x2 - x1) / length
            dy = (y2 - y1) / length

            # Generate dash segments
            pos = 0.0
            while pos < length:
                # Dash start and end
                dash_start = pos
                dash_end = min(pos + dash_length, length)

                # Coordinates
                sx = x1 + dx * dash_start
                sy = y1 + dy * dash_start
                ex = x1 + dx * dash_end
                ey = y1 + dy * dash_end

                lines.append(
                    f'<line x1="{sx}" y1="{sy}" x2="{ex}" y2="{ey}" '
                    f'stroke="{stroke}" {stroke_attr}/>'
                )

                pos += period

            return lines

        # Render all four edges (clockwise from top edge)
        # Top edge: left to right
        elements.extend(render_dashed_line(min_x, min_y, max_x, min_y))
        # Right edge: top to bottom
        elements.extend(render_dashed_line(max_x, min_y, max_x, max_y))
        # Bottom edge: right to left
        elements.extend(render_dashed_line(max_x, max_y, min_x, max_y))
        # Left edge: bottom to top
        elements.extend(render_dashed_line(min_x, max_y, min_x, min_y))

        return elements

    def _render_collapse_triangle(
        self, min_x: float, min_y: float, stroke: str, is_collapsed: bool
    ) -> list[str]:
        """
        Render collapse indicator triangle.

        For compile masks, native Altium renders both STROKE and FILL.
        Small triangle in top-left corner indicating collapsed/expanded state.

        Args:
            min_x, min_y: Top-left corner of rectangle
            stroke: Border color for deriving triangle colors
            is_collapsed: True if collapsed (triangle points down)

        Returns:
            List of SVG polygon elements
        """
        elements = []

        # Triangle position (offset 1px from corner)
        base_x = min_x + 1
        base_y = min_y + 9

        # Triangle vertices (8px wide, 7px tall)
        if is_collapsed:
            # Pointing down (inverted)
            p1 = f"{base_x},{base_y - 8}"
            p2 = f"{base_x + 8},{base_y - 8}"
            p3 = f"{base_x + 4},{base_y - 1}"
        else:
            # Pointing up (normal)
            p1 = f"{base_x},{base_y}"
            p2 = f"{base_x + 8},{base_y}"
            p3 = f"{base_x + 4},{base_y - 7}"

        points = f"{p1} {p2} {p3}"

        # Derive triangle colors from border color
        triangle_stroke, triangle_fill = self._derive_triangle_colors(stroke)

        # Stroke outline
        elements.append(
            f'<polygon points = "{points}" stroke="{triangle_stroke}" '
            f'stroke-width="0.5px" vector-effect="non-scaling-stroke"/>'
        )

        # Fill
        elements.append(
            f'<polygon points = "{points}" fill="{triangle_fill}" fill-opacity="1"/>'
        )

        return elements

    def _derive_triangle_colors(self, border_color: str) -> tuple[str, str]:
        return derive_triangle_indicator_colors(
            border_color, area_color=self.area_color
        )

    def _get_fill_indicator_color(self) -> str:
        return fill_indicator_color_from_area_color(self.area_color)

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        x1, y1 = ctx.transform_coord_precise(self.location)
        x2, y2 = ctx.transform_coord_precise(self.corner)
        min_x = min(float(x1), float(x2))
        max_x = max(float(x1), float(x2))
        min_y = min(float(y1), float(y2))
        max_y = max(float(y1), float(y2))
        sheet_height_px = float(ctx.sheet_height or 0.0)

        coord = lambda px, py: geometry_coord_list(
            px,
            py,
            sheet_height_px=sheet_height_px,
            units_per_px=units_per_px,
        )

        if self.is_collapsed:
            border_points_svg = [
                (min_x, min_y + 10.0),
                (min_x, min_y),
                (min_x + 10.0, min_y),
                (min_x + 10.0, min_y + 10.0),
            ]
        else:
            border_points_svg = [
                (min_x, max_y),
                (min_x, min_y),
                (max_x, min_y),
                (max_x, max_y),
            ]

        stroke_hex = color_to_hex(self.color) if self.color else "#000000"
        stroke_raw = (
            int(self.color)
            if self.color is not None
            else hex_to_win32_color(stroke_hex)
        )
        pen_width = (
            0
            if self.line_width == LineWidth.SMALLEST
            else int(round(LINE_WIDTH_MILS.get(self.line_width, 1.0) * units_per_px))
        )
        border_pen = make_pen(stroke_raw, width=pen_width)
        fill_raw = int(self.area_color) if self.area_color is not None else 0xFFFFFF
        fill_brush = make_solid_brush(fill_raw, alpha=0x7D)
        triangle_stroke_hex, triangle_fill_hex = self._derive_triangle_colors(
            stroke_hex
        )
        triangle_pen = make_pen(hex_to_win32_color(triangle_stroke_hex), width=0)
        triangle_brush = make_solid_brush(hex_to_win32_color(triangle_fill_hex))

        operations = [
            SchGeometryOp.polygons(
                [[coord(point_x, point_y) for point_x, point_y in border_points_svg]],
                pen=border_pen,
            ),
            SchGeometryOp.polygons(
                [[coord(point_x, point_y) for point_x, point_y in border_points_svg]],
                brush=fill_brush,
            ),
        ]

        base_x = min_x + 1.0
        base_y = min_y + 9.0
        if self.is_collapsed:
            triangle_points_svg = [
                (base_x, base_y - 8.0),
                (base_x + 8.0, base_y - 8.0),
                (base_x + 4.0, base_y - 1.0),
            ]
        else:
            triangle_points_svg = [
                (base_x, base_y),
                (base_x + 8.0, base_y),
                (base_x + 4.0, base_y - 7.0),
            ]
        triangle_points = [
            [coord(point_x, point_y) for point_x, point_y in triangle_points_svg]
        ]
        operations.extend(
            [
                SchGeometryOp.polygons(triangle_points, pen=triangle_pen),
                SchGeometryOp.polygons(triangle_points, brush=triangle_brush),
            ]
        )

        left = min(float(self.location.x), float(self.corner.x))
        right = max(float(self.location.x), float(self.corner.x))
        bottom = min(float(self.location.y), float(self.corner.y))
        top = max(float(self.location.y), float(self.corner.y))

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="compilemask",
            object_id="eCompileMask",
            bounds=SchGeometryBounds(
                left=int(round(left * 100000)),
                top=int(round(top * 100000)),
                right=int(round(right * 100000)),
                bottom=int(round(bottom * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        return f"<AltiumSchCompileMask ({self.location.x},{self.location.y})->({self.corner.x},{self.corner.y})>"
