"""Schematic record model for SchRecordType.PARAMETER_SET."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_sch_enums import ParameterSetStyle, Rotation90
from .altium_record_types import (
    SchGraphicalObject,
    SchRecordType,
    color_to_hex,
    hex_to_win32_color,
)
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    RotatedLocalPointMixin,
    detect_case_mode_from_uppercase_fields,
)
from .altium_sch_svg_renderer import svg_text
from .altium_text_metrics import measure_text_width


def _transform_trace_points(
    record: RotatedLocalPointMixin,
    x: float,
    y: float,
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    return [
        record._transform_local_point(x, y, point_x, point_y)
        for point_x, point_y in points
    ]


class AltiumSchParameterSet(RotatedLocalPointMixin, SchGraphicalObject):
    """
    PARAMETER_SET record.

    A container for grouping related parameters together.
    Used for organizing component parameters on schematics.

    Differential Pair Detection:
        ParameterSets are rendered as differential pair zigzag symbols when they
        have a child Parameter with name="DifferentialPair" and text="True".
        This is determined by the is_differential_pair() method.

        See: native implementation:
            if (parameterSet.IsDifferentialPair()) { DrawDiffPair(...); }

        See: native implementation:
            public bool IsDifferentialPair() {
                ISchParameter param = GetState_ParameterByName("DifferentialPair");
                return param != null && param.GetState_Text() == "True";
            }
    """

    def __init__(self) -> None:
        super().__init__()
        self._geometry_hairline_width = 0.6399999856948853
        self._geometry_stroke_width = 64.0
        self._geometry_tiny_stroke_width = 21.333120346069336
        self._geometry_font_size_px = 577.9188842773438 / 64.0
        self.name: str = "Parameter Set"
        self.orientation: Rotation90 = Rotation90.DEG_0
        self.style: ParameterSetStyle = ParameterSetStyle.LARGE
        # Track field presence
        self._has_name: bool = False
        self._has_orientation: bool = False
        self._has_style: bool = False
        # Child parameters (populated by hierarchy builder)
        self.parameters: list = []

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PARAMETER_SET

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        s = AltiumSerializer()
        r = self._record

        # Parameter set name
        self.name, self._has_name, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.NAME,
            default="Parameter Set",
        )

        # Orientation (0-3)
        orientation_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = Rotation90(orientation_val)

        # Display style
        style_val, self._has_style = s.read_int(record, Fields.STYLE, default=0)
        self.style = ParameterSetStyle(style_val)

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        mode = detect_case_mode_from_uppercase_fields(self._raw_record)
        s = AltiumSerializer(mode)
        raw = self._raw_record

        s.write_str(record, Fields.NAME, self.name, raw)
        s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)
        s.write_int(record, Fields.STYLE, self.style.value, raw)
        return record

    def is_differential_pair(self) -> bool:
        """
        Check if this ParameterSet represents a differential pair directive.

        Detection is based on having a child Parameter record
        with name="DifferentialPair" and text="True" (case-insensitive comparison).

        This matches Altium's IsDifferentialPair() implementation in native implementation:
            ISchParameter param = GetState_ParameterByName("DifferentialPair");
            return param != null && string.Equals(param.GetState_Text(), "True", StringComparison.OrdinalIgnoreCase);

        Returns:
            True if this is a differential pair directive, False otherwise.
        """
        for param in self.parameters:
            # Check for DifferentialPair parameter with value "True"
            param_name = getattr(param, "name", "")
            param_text = getattr(param, "text", "")
            if (
                param_name.lower() == "differentialpair"
                and param_text.lower() == "true"
            ):
                return True
        return False

    def _get_display_string(self) -> str:
        """
        Return the visible parameter-set label text.
        """
        if self.name and self.name != "Parameter Set":
            return self.name

        for param in self.parameters:
            param_name = getattr(param, "name", "").strip().lower()
            param_text = getattr(param, "text", "")
            if not param_text:
                continue

            if param_name in {
                "classname",
                "class name",
                "net class",
                "net class name",
            }:
                return param_text

        return self.name

    def _rotate_svg_offset(self, dx: float, dy: float) -> tuple[float, float]:
        """
        Rotate a local SVG-space offset using schematic 90-degree orientation.
        """
        match self.orientation:
            case Rotation90.DEG_0:
                return dx, dy
            case Rotation90.DEG_90:
                return dy, -dx
            case Rotation90.DEG_180:
                return -dx, -dy
            case Rotation90.DEG_270:
                return -dy, dx

        return dx, dy

    def _render_standard(self, x: float, y: float) -> list[str]:
        stroke = color_to_hex(self.color)
        fill = stroke
        display_string = self._get_display_string()
        is_tiny = self.style == ParameterSetStyle.TINY

        line_length = 2.0 if is_tiny else 6.0
        circle_radius = 2.0 if is_tiny else 6.0
        circle_diameter = circle_radius * 2.0
        circle_center_offset = 4.0 if is_tiny else 12.0

        line_end_x, line_end_y = self._transform_local_point(x, y, line_length, 0.0)
        circle_center_x, circle_center_y = self._transform_local_point(
            x, y, circle_center_offset, 0.0
        )

        elements = [
            f'<line x1="{x}" y1="{y}" x2="{line_end_x}" y2="{line_end_y}" '
            f'stroke="{stroke}" stroke-width="1px"/>',
            f'<rect x = "{circle_center_x - circle_radius}" y="{circle_center_y - circle_radius}" '
            f'width="{circle_diameter}" height="{circle_diameter}" '
            f'stroke="{stroke}" stroke-width="1px" '
            f'rx="{circle_radius}" ry="{circle_radius}"/>',
        ]

        if is_tiny:
            return elements

        text_width = measure_text_width(display_string, 9.0, "Times New Roman")

        match self.orientation:
            case Rotation90.DEG_0:
                info_x = x + 10.8558
                info_y = y + 4.0
                label_x = x + 20.0
                label_y = y + 4.0
            case Rotation90.DEG_90:
                info_x = x - 1.14418
                info_y = y - 8.0
                label_x = x - text_width / 2.0
                label_y = y - 21.0
            case Rotation90.DEG_180:
                info_x = x - 13.1442
                info_y = y + 4.0
                label_x = x - 20.0 - text_width
                label_y = y + 4.0
            case Rotation90.DEG_270:
                info_x = x - 1.14418
                info_y = y + 16.0
                label_x = x - text_width / 2.0
                label_y = y + 29.0
            case _:
                info_x = x + 10.8558
                info_y = y + 4.0
                label_x = x + 20.0
                label_y = y + 4.0

        elements.append(
            svg_text(
                info_x,
                info_y,
                "i",
                font_size=9,
                font_family="Times New Roman",
                fill=fill,
            )
        )
        elements.append(
            svg_text(
                label_x,
                label_y,
                display_string,
                font_size=9,
                font_family="Times New Roman",
                fill=fill,
            )
        )
        return elements

    def _render_diffpair(
        self, x: float, y: float, ctx: "SchSvgRenderContext"
    ) -> list[str]:
        """
        Render differential pair directive as zigzag traces.

        Render differential pair directive as two zigzag traces.

        The shadow color is blended against the active sheet area color, so
        different sheet backgrounds can produce different muted shadow tones.

        Args:
            x, y: Transformed location coordinates
            ctx: Render context with sheet_area_color for shadow calculation

        Returns:
            List of SVG line elements
        """
        elements = []

        # Get document area color from context for shadow calculation.
        doc_area_color = getattr(ctx, "sheet_area_color", 0xFFFFFF) or 0xFFFFFF

        # Determine colors based on directive color and document area
        # Main color: directive color or black
        effective_color = self.color if self.color is not None else 0
        main_color = color_to_hex(effective_color) if effective_color else "#000000"

        # Shadow color: calculated using ApplyDark + ModifyColor algorithm
        # This now correctly accounts for different document area colors
        shadow_color = self._derive_diffpair_shadow_color(
            effective_color, doc_area_color
        )

        def trace_segments(
            points: list[tuple[float, float]],
        ) -> list[tuple[float, float, float, float]]:
            return [
                (
                    points[index][0],
                    points[index][1],
                    points[index + 1][0],
                    points[index + 1][1],
                )
                for index in range(len(points) - 1)
            ]

        main_upper = _transform_trace_points(
            self,
            x,
            y,
            [
                (5.0, -7.5),
                (9.0, -7.5),
                (11.0, -9.5),
                (15.0, -9.5),
                (17.0, -7.5),
                (21.0, -7.5),
            ],
        )
        main_lower = _transform_trace_points(
            self,
            x,
            y,
            [
                (5.0, -5.0),
                (9.0, -5.0),
                (11.0, -3.0),
                (15.0, -3.0),
                (17.0, -5.0),
                (21.0, -5.0),
            ],
        )
        main_connector_end = self._transform_local_point(x, y, 5.0, -5.0)
        shadow_upper = [
            (point_x + 1.0, point_y + 1.0) for point_x, point_y in main_upper
        ]
        shadow_lower = [
            (point_x + 1.0, point_y + 1.0) for point_x, point_y in main_lower
        ]
        shadow_connector_end = (
            main_connector_end[0] + 1.0,
            main_connector_end[1] + 1.0,
        )

        # ====================================================================
        # SHADOW LAYER FIRST (muted color, offset +1px in both X and Y)
        # ====================================================================
        # In native SVG, shadow traces start at x+6, main traces at x+5 (1px difference)
        # Shadow Y is +1px from main (in SVG coords where Y increases downward)

        for x1, y1, x2, y2 in trace_segments(shadow_upper):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{shadow_color}" stroke-width="1px"/>'
            )

        for x1, y1, x2, y2 in trace_segments(shadow_lower):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{shadow_color}" stroke-width="1px"/>'
            )

        elements.append(
            f'<line x1="{x}" y1="{y}" x2="{shadow_connector_end[0]}" y2="{shadow_connector_end[1]}" '
            f'stroke="{shadow_color}" stroke-width="0.5px" vector-effect="non-scaling-stroke"/>'
        )

        # ====================================================================
        # MAIN LAYER SECOND (directive color, offset -1px from shadow)
        # ====================================================================
        # Main traces are 1px left and 1px up from shadow in SVG coords

        for x1, y1, x2, y2 in trace_segments(main_upper):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{main_color}" stroke-width="1px"/>'
            )

        for x1, y1, x2, y2 in trace_segments(main_lower):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{main_color}" stroke-width="1px"/>'
            )

        elements.append(
            f'<line x1="{x}" y1="{y}" x2="{main_connector_end[0]}" y2="{main_connector_end[1]}" '
            f'stroke="{main_color}" stroke-width="0.5px" vector-effect="non-scaling-stroke"/>'
        )

        return elements

    def _derive_diffpair_shadow_color(
        self, color: int, doc_area_color: int = 0xFFFFFF
    ) -> str:
        """
        Derive muted shadow color for diffpair traces from directive color.

        Implements Altium's exact algorithm from native implementation:
            ColorManager.ModifyColor(64, ColorManager.ApplyDark(color, 128), docAreaColor)

        From native implementation:
        - ApplyDark(color, howMuch): Subtract howMuch from each channel (min 0)
        - ModifyColor(percent, color, bgColor): Blend color toward bgColor by percent%
          result = color + (bgColor - color) * percent / 100

        Args:
            color: Altium BGR color value
            doc_area_color: Document area background color (default white)

        Returns:
            Hex color string for shadow
        """
        # Extract RGB from BGR format
        b = (color >> 16) & 0xFF
        g = (color >> 8) & 0xFF
        r = color & 0xFF

        # Step 1: ApplyDark(color, 128) - subtract 128 from each channel, min 0
        r_dark = max(0, r - 128)
        g_dark = max(0, g - 128)
        b_dark = max(0, b - 128)

        # Document area color (typically white)
        doc_r = doc_area_color & 0xFF
        doc_g = (doc_area_color >> 8) & 0xFF
        doc_b = (doc_area_color >> 16) & 0xFF

        # Step 2: ModifyColor(64, darkColor, docAreaColor)
        # result = darkColor + (docAreaColor - darkColor) * 64 / 100
        r_out = r_dark + (doc_r - r_dark) * 64 // 100
        g_out = g_dark + (doc_g - g_dark) * 64 // 100
        b_out = b_dark + (doc_b - b_dark) * 64 // 100

        return f"#{r_out:02X}{g_out:02X}{b_out:02X}"

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
            make_font_payload,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        x, y = ctx.transform_point(self.location.x, self.location.y)
        sheet_height_px = float(ctx.sheet_height or 0.0)
        display_string = self._get_display_string()
        is_tiny = self.style == ParameterSetStyle.TINY
        effective_color = int(self.color) if self.color is not None else 0

        def coord(px: float, py: float) -> tuple[float, float]:
            return svg_coord_to_geometry(
                px,
                py,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )

        if self.is_differential_pair():
            doc_area_color = getattr(ctx, "sheet_area_color", 0xFFFFFF) or 0xFFFFFF
            shadow_color = self._derive_diffpair_shadow_color(
                effective_color, doc_area_color
            )
            shadow_color_raw = hex_to_win32_color(shadow_color)
            main_pen = make_pen(effective_color, width=self._geometry_stroke_width)
            shadow_pen = make_pen(shadow_color_raw, width=self._geometry_stroke_width)
            main_connector_pen = make_pen(effective_color, width=0)
            shadow_connector_pen = make_pen(shadow_color_raw, width=0)

            main_upper = _transform_trace_points(
                self,
                x,
                y,
                [
                    (5.0, -7.5),
                    (9.0, -7.5),
                    (11.0, -9.5),
                    (15.0, -9.5),
                    (17.0, -7.5),
                    (21.0, -7.5),
                ],
            )
            main_lower = _transform_trace_points(
                self,
                x,
                y,
                [
                    (5.0, -5.0),
                    (9.0, -5.0),
                    (11.0, -3.0),
                    (15.0, -3.0),
                    (17.0, -5.0),
                    (21.0, -5.0),
                ],
            )
            main_connector_end = self._transform_local_point(x, y, 5.0, -5.0)
            shadow_upper = [
                (point_x + 1.0, point_y + 1.0) for point_x, point_y in main_upper
            ]
            shadow_lower = [
                (point_x + 1.0, point_y + 1.0) for point_x, point_y in main_lower
            ]
            shadow_connector_end = (
                main_connector_end[0] + 1.0,
                main_connector_end[1] + 1.0,
            )

            operations = [
                SchGeometryOp.lines(
                    [coord(point_x, point_y) for point_x, point_y in shadow_upper],
                    pen=shadow_pen,
                ),
                SchGeometryOp.lines(
                    [coord(point_x, point_y) for point_x, point_y in shadow_lower],
                    pen=shadow_pen,
                ),
                SchGeometryOp.lines(
                    [
                        coord(x, y),
                        coord(shadow_connector_end[0], shadow_connector_end[1]),
                    ],
                    pen=shadow_connector_pen,
                ),
                SchGeometryOp.lines(
                    [coord(point_x, point_y) for point_x, point_y in main_upper],
                    pen=main_pen,
                ),
                SchGeometryOp.lines(
                    [coord(point_x, point_y) for point_x, point_y in main_lower],
                    pen=main_pen,
                ),
                SchGeometryOp.lines(
                    [coord(x, y), coord(main_connector_end[0], main_connector_end[1])],
                    pen=main_connector_pen,
                ),
            ]

            return SchGeometryRecord(
                handle=f"{document_id}\\{self.unique_id}",
                unique_id=self.unique_id,
                kind="parameterset",
                object_id="eParameterSet",
                bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
                operations=wrap_record_operations(
                    self.unique_id,
                    operations,
                    units_per_px=units_per_px,
                ),
            )

        stroke_width = (
            self._geometry_tiny_stroke_width if is_tiny else self._geometry_stroke_width
        )
        pen = make_pen(effective_color, width=stroke_width)
        brush = make_solid_brush(effective_color)

        line_length = 2.0 if is_tiny else 6.0
        circle_radius = 2.0 if is_tiny else 6.0
        circle_center_offset = 4.0 if is_tiny else 12.0
        line_end_x, line_end_y = self._transform_local_point(x, y, line_length, 0.0)
        circle_center_x, circle_center_y = self._transform_local_point(
            x, y, circle_center_offset, 0.0
        )
        circle_x1, circle_y1 = coord(
            circle_center_x - circle_radius, circle_center_y - circle_radius
        )
        circle_x2, circle_y2 = coord(
            circle_center_x + circle_radius, circle_center_y + circle_radius
        )

        operations = [
            SchGeometryOp.lines(
                [coord(x, y), coord(line_end_x, line_end_y)],
                pen=pen,
            ),
            SchGeometryOp.rounded_rectangle(
                x1=circle_x1,
                y1=circle_y1,
                x2=circle_x2,
                y2=circle_y2,
                corner_x_radius=circle_radius * units_per_px,
                corner_y_radius=circle_radius * units_per_px,
                pen=pen,
            ),
        ]

        if not is_tiny:
            text_width = measure_text_width(
                display_string,
                self._geometry_font_size_px,
                "Times New Roman",
            )
            match self.orientation:
                case Rotation90.DEG_0:
                    info_x = x + 10.8558
                    info_y = y + 4.0
                    label_x = x + 20.0
                    label_y = y + 4.0
                case Rotation90.DEG_90:
                    info_x = x - 1.14418
                    info_y = y - 8.0
                    label_x = x - text_width / 2.0
                    label_y = y - 21.0
                case Rotation90.DEG_180:
                    info_x = x - 13.1442
                    info_y = y + 4.0
                    label_x = x - 20.0 - text_width
                    label_y = y + 4.0
                case Rotation90.DEG_270:
                    info_x = x - 1.14418
                    info_y = y + 16.0
                    label_x = x - text_width / 2.0
                    label_y = y + 29.0
                case _:
                    info_x = x + 10.8558
                    info_y = y + 4.0
                    label_x = x + 20.0
                    label_y = y + 4.0

            font_payload = make_font_payload(
                name="Times New Roman",
                size_px=self._geometry_font_size_px,
                units_per_px=units_per_px,
                rotation=0.0,
            )
            baseline_step = float(int(self._geometry_font_size_px))

            def text_op(
                baseline_x: float, baseline_y: float, text: str
            ) -> SchGeometryOp:
                geometry_x_px = baseline_x
                geometry_y_px = baseline_y - baseline_step
                gx, gy = coord(geometry_x_px, geometry_y_px)
                return SchGeometryOp.string(
                    x=gx,
                    y=gy,
                    text=text,
                    font=font_payload,
                    brush=brush,
                )

            operations.extend(
                [
                    text_op(info_x, info_y, "i"),
                    text_op(label_x, label_y, display_string),
                ]
            )

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="parameterset",
            object_id="eParameterSet",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<AltiumSchParameterSet name='{self.name}' "
            f"style={self.style.name} orientation={self.orientation.value * 90}deg>"
        )
