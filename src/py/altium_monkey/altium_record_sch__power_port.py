"""Schematic record model for SchRecordType.POWER_PORT."""

import hashlib
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import (
    SchGraphicalObject,
    SchRecordType,
    TextOrientation,
    color_to_hex,
    rgb_to_win32_color,
)
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_sch_enums import PowerObjectStyle
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
)
from .altium_sch_record_helpers import geometry_coord_tuple


class AltiumSchPowerPort(SingleFontBindableRecordMixin, SchGraphicalObject):
    """
    Power port record.

    Power/ground symbols (VCC, GND, +5V, etc.).

    PROPERTIES:
        Direct properties:
        - Style: power symbol style (0-10)
        - ShowNetName: bool - whether to display net name

        Label-like properties:
        - Text: string - net name (e.g., "VCC", "GND")
        - FontId: int - font ID for text
        - Orientation: rotation by 90-degree steps (0-3)
        - Justification: text alignment (0-8)
        - IsMirrored: bool - text mirrored
        - OverrideDisplayString: string - override display text

    STYLE VALUES:
        0 = circle
        1 = triangle with base line
        2 = simple perpendicular line
        3 = two arcs forming S-curve
        4 = 4 decreasing horizontal lines (classic GND)
        5 = line ending in filled triangle
        6 = line with 3 diagonal lines
        7 = triangle without base line
        8 = GOST standard ground power
        9 = GOST standard earth ground
        10 = longer perpendicular line
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        self._use_pascal_case: bool = True
        # Core power port properties
        self.text: str = ""  # Net name (e.g., "VCC", "GND")
        self.font_id: int = 0
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.style: PowerObjectStyle = PowerObjectStyle.BAR
        self.show_net_name: bool = True
        self._has_show_net_name: bool = False

        # Inherited from ISch_Label interface
        self.justification: int = 0  # 0=BottomLeft through 8=TopRight
        self.is_mirrored: bool = False  # Text mirrored
        self.override_display_string: str = ""  # Override display text

        # Cross-sheet connector (off-sheet connector)
        # When True, renders as X-shaped symbol instead of power symbol
        self.is_cross_sheet_connector: bool = False

        # Custom power port graphics (from ObjectDefinitions stream)
        # When non-empty, overrides Style-based rendering with custom primitives
        self.object_definition_id: str = ""

    @staticmethod
    def _default_show_net_name(style: int | PowerObjectStyle) -> bool:
        return int(style) not in {
            PowerObjectStyle.GND_POWER.value,
            PowerObjectStyle.GND_SIGNAL.value,
            PowerObjectStyle.GND_EARTH.value,
            PowerObjectStyle.GOST_GND_POWER.value,
            PowerObjectStyle.GOST_GND_EARTH.value,
        }

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.POWER_PORT

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse from a record.

                Args:
                   record: Source record dictionary
                    font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record)
        self._font_manager = font_manager
        self._public_font_spec = None

        # Detect case mode for round-trip fidelity
        self._use_pascal_case = "Text" in record or "Style" in record

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()
        r = self._record

        # Core power port fields
        self.text, _, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT,
            default="",
        )
        # Use read_font_id for translation support
        self.font_id, _ = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=0
        )
        orient_val, _ = s.read_int(record, Fields.ORIENTATION, default=0)
        self.orientation = TextOrientation(orient_val)
        style_val, _ = s.read_int(record, Fields.STYLE, default=0)
        self.style = PowerObjectStyle(style_val)
        self.show_net_name, self._has_show_net_name = s.read_bool(
            record,
            Fields.SHOW_NET_NAME,
            default=self._default_show_net_name(self.style),
        )

        # Cross-sheet connector flag
        self.is_cross_sheet_connector, _ = s.read_bool(
            record, Fields.IS_CROSS_SHEET_CONNECTOR, default=False
        )

        # Custom power port definition (from OrCAD/Allegro import or custom symbols)
        self.object_definition_id, _ = s.read_str(
            record, Fields.OBJECT_DEFINITION_ID, default=""
        )

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()

        # Determine case mode
        mode = (
            CaseMode.PASCALCASE
            if getattr(self, "_use_pascal_case", False)
            else CaseMode.UPPERCASE
        )
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Core power port fields
        s.write_str(record, Fields.TEXT, self.text, raw)
        if self.font_id != 0:
            s.write_int(record, Fields.FONT_ID, self.font_id, raw, force=True)
        else:
            s.remove_field(record, Fields.FONT_ID)
        if self.orientation.value != 0:
            s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)
        else:
            s.remove_field(record, Fields.ORIENTATION)
        if int(self.style) != 0:
            s.write_int(record, Fields.STYLE, int(self.style), raw)
        else:
            s.remove_field(record, Fields.STYLE)
        default_show_net_name = self._default_show_net_name(self.style)
        if (
            raw is None
            or self._has_show_net_name
            or self.show_net_name != default_show_net_name
        ):
            s.write_bool(
                record, Fields.SHOW_NET_NAME, self.show_net_name, raw, force=True
            )
        else:
            s.remove_field(record, Fields.SHOW_NET_NAME)

        if self.is_cross_sheet_connector:
            s.write_bool(record, Fields.IS_CROSS_SHEET_CONNECTOR, True, raw, force=True)
        else:
            s.remove_field(record, Fields.IS_CROSS_SHEET_CONNECTOR)

        if self.object_definition_id:
            s.write_str(
                record,
                Fields.OBJECT_DEFINITION_ID,
                self.object_definition_id,
                raw,
                force=True,
            )
        else:
            s.remove_field(record, Fields.OBJECT_DEFINITION_ID)

        s.remove_field(record, Fields.JUSTIFICATION)
        s.remove_field(record, Fields.IS_MIRRORED)
        s.remove_field(record, Fields.OVERRIDE_DISPLAY_STRING)

        return record

    def _custom_power_port_text_layout(
        self,
        ctx: "SchSvgRenderContext",
        *,
        x: float,
        y: float,
        custom_prims: list[dict],
        font_name: str,
        font_size_px: float,
        is_bold: bool,
        is_italic: bool,
    ) -> tuple[tuple[float, float], float, float] | None:
        """
        Match native TextRect-based placement for custom power-port labels.
        """
        if not self.show_net_name or not self.text:
            return None

        from .altium_sch_geometry_oracle import split_overline_text
        from .altium_text_metrics import measure_text_height, measure_text_width

        orient = self.orientation

        def transform_local(lx: float, ly: float) -> tuple[float, float]:
            if orient == TextOrientation.DEGREES_0:
                dx, dy = lx, -ly
            elif orient == TextOrientation.DEGREES_90:
                dx, dy = -ly, -lx
            elif orient == TextOrientation.DEGREES_180:
                dx, dy = -lx, ly
            else:
                dx, dy = ly, lx
            return x + dx, y + dy

        min_tx = math.inf
        max_tx = -math.inf
        has_points = False

        def track_local_point(lx: float, ly: float) -> None:
            nonlocal min_tx, max_tx, has_points
            tx, _ = transform_local(lx, ly)
            min_tx = min(min_tx, tx)
            max_tx = max(max_tx, tx)
            has_points = True

        for rec in custom_prims:
            record_type = self._custom_primitive_record_type(rec)
            if record_type == SchRecordType.LINE:
                track_local_point(
                    float(rec.get("Location.X", rec.get("LOCATION.X", "0")) or 0),
                    float(rec.get("Location.Y", rec.get("LOCATION.Y", "0")) or 0),
                )
                track_local_point(
                    float(rec.get("Corner.X", rec.get("CORNER.X", "0")) or 0),
                    float(rec.get("Corner.Y", rec.get("CORNER.Y", "0")) or 0),
                )
            elif record_type == SchRecordType.POLYGON:
                count = int(rec.get("LocationCount", "0") or 0)
                for point_index in range(1, count + 1):
                    track_local_point(
                        float(rec.get(f"X{point_index}", "0") or 0),
                        float(rec.get(f"Y{point_index}", "0") or 0),
                    )

        if not has_points:
            return None

        clean_text, _ = split_overline_text(self.text)
        text_width = measure_text_width(
            clean_text,
            ctx.get_font_size_for_width(self.font_id),
            font_name,
            is_bold,
            is_italic,
        )
        text_height = measure_text_height(
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
            use_altium_algorithm=False,
        )
        bbox_width = (max_tx - min_tx) + ctx.scale
        gap = 2 * ctx.scale
        horizontal_y = y + text_height / 2.0
        # GeometryMaker's custom TextRect path always keeps an extra 200000-unit
        # left pad on eRotate180 text placement because x1 is the left side of
        # the full TextRect, not the text run itself.
        left_text_extent = (
            2 * ctx.scale if orient == TextOrientation.DEGREES_180 else 0.0
        )
        if orient in {TextOrientation.DEGREES_0, TextOrientation.DEGREES_180} and (
            self.style == 1 or (self.is_cross_sheet_connector and self.style == 0)
        ):
            gap += ctx.scale
            horizontal_y -= ctx.scale
        elif orient in {
            TextOrientation.DEGREES_0,
            TextOrientation.DEGREES_180,
        } and self.style in {4, 5, 6}:
            # GeometryMaker uses the custom visible-children width to seed TextRect
            # for imported ground symbols, which lands one pixel farther right than
            # the raw primitive span and centers the text using truncated baseline
            # font height rather than the measured glyph box.
            bbox_width += ctx.scale
            horizontal_y = y + float(int(font_size_px) // 2)
        elif orient == TextOrientation.DEGREES_90 and self.style in {4, 5, 6}:
            # Native GeometryMaker inflates BrVisibleChildrenNoParameter by an
            # extra 200000 units for this imported vertical ground family, and
            # CalculateTextRect feeds that width directly into TextRect.y2.
            bbox_width += 2 * ctx.scale

        if orient == TextOrientation.DEGREES_0:
            baseline = (x + bbox_width + gap, horizontal_y)
        elif orient == TextOrientation.DEGREES_90:
            baseline = (x - text_width / 2.0, y - bbox_width)
        elif orient == TextOrientation.DEGREES_180:
            baseline = (
                x - bbox_width - gap - text_width - left_text_extent,
                horizontal_y,
            )
        else:
            down_baseline_y = y + bbox_width + text_height
            if self.style == 1:
                down_baseline_y -= 2 * ctx.scale
            baseline = (x - text_width / 2.0, down_baseline_y)

        return baseline, text_width, text_height

    def _native_text_descent_offset_px(
        self,
        ctx: "SchSvgRenderContext",
        *,
        font_name: str,
        font_size_px: float,
        is_bold: bool,
        is_italic: bool,
    ) -> float:
        """
        Mirror the native/C++ descent offset used for bottom-aligned power text.
        """
        from .altium_ttf_metrics import get_font_factor

        font_factor = get_font_factor(font_name, is_bold, is_italic)
        if font_factor >= 1.0:
            return 1.0

        if ctx.font_manager:
            font_spec = ctx.font_manager.get_font_info(self.font_id)
            if font_spec:
                pt_size = float(font_spec.get("size", font_size_px / font_factor))
            else:
                pt_size = font_size_px / font_factor
        else:
            pt_size = font_size_px / font_factor

        return float(max(1, round((1 - font_factor) * pt_size + 0.5)))

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> Any:
        """
        Build an oracle-aligned geometry record for this power-port variant.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_font_payload,
            make_pen,
            make_solid_brush,
            make_text_with_overline_operations,
            split_overline_text,
            wrap_record_operations,
        )
        from .altium_text_metrics import measure_text_height, measure_text_width

        custom_prims: list[dict] | None = None
        if self.object_definition_id:
            if not getattr(ctx, "object_definitions", None):
                return None
            custom_prims = ctx.object_definitions.get(self.object_definition_id)
            if custom_prims is None:
                return None

        x, y = ctx.transform_point(self.location.x, self.location.y)
        sheet_height_px = float(ctx.sheet_height or 0.0)
        stroke_raw = int(self.color) if self.color is not None else 0
        pen = make_pen(stroke_raw, width=units_per_px)
        hairline_pen = make_pen(stroke_raw, width=0)

        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            self.font_id
        )
        font_spec = (
            ctx.font_manager.get_font_info(self.font_id) if ctx.font_manager else None
        )
        font_payload = make_font_payload(
            name=str(font_spec.get("name", font_name)) if font_spec else str(font_name),
            size_px=font_size_px,
            units_per_px=units_per_px,
            rotation=0.0,
            underline=bool(font_spec.get("underline", is_underline))
            if font_spec
            else bool(is_underline),
            italic=bool(font_spec.get("italic", is_italic))
            if font_spec
            else bool(is_italic),
            bold=bool(font_spec.get("bold", is_bold)) if font_spec else bool(is_bold),
            strikeout=bool(font_spec.get("strikeout", False)) if font_spec else False,
        )

        def geo_point(px: float, py: float) -> tuple[float, float]:
            return geometry_coord_tuple(
                px,
                py,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )

        operations: list[SchGeometryOp] = []
        min_x = math.inf
        max_x = -math.inf
        min_y = math.inf
        max_y = -math.inf

        def track_point(px: float, py: float) -> None:
            nonlocal min_x, max_x, min_y, max_y
            min_x = min(min_x, px)
            max_x = max(max_x, px)
            min_y = min(min_y, py)
            max_y = max(max_y, py)

        def add_line(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            *,
            line_pen: Any | None = None,
        ) -> None:
            track_point(x1, y1)
            track_point(x2, y2)
            operations.append(
                SchGeometryOp.lines(
                    [geo_point(x1, y1), geo_point(x2, y2)],
                    pen=pen if line_pen is None else line_pen,
                )
            )

        def add_arc(
            center_x: float,
            center_y: float,
            radius_px: float,
            start_angle: float,
            end_angle: float,
            *,
            arc_pen: Any | None = None,
        ) -> None:
            track_point(center_x - radius_px, center_y - radius_px)
            track_point(center_x + radius_px, center_y + radius_px)
            geometry_center_x, geometry_center_y = geo_point(center_x, center_y)
            diameter_units = radius_px * 2.0 * units_per_px
            operations.append(
                SchGeometryOp.arc(
                    center_x=geometry_center_x,
                    center_y=geometry_center_y,
                    width=diameter_units,
                    height=diameter_units,
                    start_angle=start_angle,
                    end_angle=end_angle,
                    pen=pen if arc_pen is None else arc_pen,
                )
            )

        def add_rounded_rect(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            *,
            radius_x_px: float = 0.0,
            radius_y_px: float = 0.0,
            rect_pen: Any | None = None,
        ) -> None:
            track_point(min(x1, x2), min(y1, y2))
            track_point(max(x1, x2), max(y1, y2))
            geometry_x1, geometry_y1 = geo_point(x1, y1)
            geometry_x2, geometry_y2 = geo_point(x2, y2)
            operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=geometry_x1,
                    y1=geometry_y1,
                    x2=geometry_x2,
                    y2=geometry_y2,
                    corner_x_radius=radius_x_px * units_per_px,
                    corner_y_radius=radius_y_px * units_per_px,
                    pen=pen if rect_pen is None else rect_pen,
                )
            )

        def add_native_junction() -> None:
            if not getattr(ctx, "native_svg_export", False):
                return
            if (self.location.x, self.location.y) not in ctx.connection_points:
                return
            junction_color_raw = 0x000000
            add_rounded_rect(
                x - 2 * ctx.scale,
                y - 2 * ctx.scale,
                x + 2 * ctx.scale,
                y + 2 * ctx.scale,
                radius_x_px=2 * ctx.scale,
                radius_y_px=2 * ctx.scale,
                rect_pen=make_pen(junction_color_raw, width=0),
            )
            operations.insert(
                len(operations) - 1,
                SchGeometryOp.rounded_rectangle(
                    x1=geo_point(x - 2 * ctx.scale, y - 2 * ctx.scale)[0],
                    y1=geo_point(x - 2 * ctx.scale, y - 2 * ctx.scale)[1],
                    x2=geo_point(x + 2 * ctx.scale, y + 2 * ctx.scale)[0],
                    y2=geo_point(x + 2 * ctx.scale, y + 2 * ctx.scale)[1],
                    corner_x_radius=2 * ctx.scale * units_per_px,
                    corner_y_radius=2 * ctx.scale * units_per_px,
                    brush=make_solid_brush(junction_color_raw),
                ),
            )

        def apply_masked_stroke(stub_end_ax: float, stub_end_ay: float) -> None:
            nonlocal stroke_raw, pen, hairline_pen
            if not ctx.is_segment_fully_under_compile_mask(
                self.location.x,
                self.location.y,
                stub_end_ax,
                stub_end_ay,
            ):
                return
            masked_hex = ctx.apply_compile_mask_color(color_to_hex(stroke_raw), True)
            stroke_raw = rgb_to_win32_color(
                int(masked_hex[1:3], 16),
                int(masked_hex[3:5], 16),
                int(masked_hex[5:7], 16),
            )
            pen = make_pen(stroke_raw, width=units_per_px)
            hairline_pen = make_pen(stroke_raw, width=0)

        orient = self.orientation
        text_baseline: tuple[float, float] | None = None
        text_width = 0.0
        text_height = 0.0

        if custom_prims is not None:
            return self._build_custom_power_port_geometry_record(
                ctx,
                document_id=document_id,
                custom_prims=custom_prims,
                x=x,
                y=y,
                orient=orient,
                sheet_height_px=sheet_height_px,
                stroke_raw=stroke_raw,
                font_name=font_name,
                font_size_px=font_size_px,
                is_bold=is_bold,
                is_italic=is_italic,
                font_payload=font_payload,
                units_per_px=units_per_px,
                operations=operations,
                track_point=track_point,
                geo_point=geo_point,
                add_line=add_line,
                add_native_junction=add_native_junction,
                begin_group=SchGeometryOp.begin_group,
                end_group=SchGeometryOp.end_group,
                polygons=SchGeometryOp.polygons,
                make_pen=make_pen,
                make_solid_brush=make_solid_brush,
                make_text_with_overline_operations=make_text_with_overline_operations,
                wrap_record_operations=wrap_record_operations,
                bounds_factory=SchGeometryBounds,
                record_factory=SchGeometryRecord,
                min_x_ref=lambda: min_x,
                min_y_ref=lambda: min_y,
                max_x_ref=lambda: max_x,
                max_y_ref=lambda: max_y,
            )

        if self.is_cross_sheet_connector:
            object_id, stub_start, stub_end, text_baseline, text_width = (
                self._build_cross_sheet_geometry(
                    orient=orient,
                    x=x,
                    y=y,
                    font_size_px=font_size_px,
                    font_name=font_name,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    add_line=add_line,
                    apply_masked_stroke=apply_masked_stroke,
                    split_overline_text=split_overline_text,
                    measure_text_width=measure_text_width,
                )
            )
        else:
            object_id, stub_start, stub_end, text_baseline, text_width = (
                self._build_standard_power_geometry(
                    ctx,
                    orient=orient,
                    x=x,
                    y=y,
                    font_size_px=font_size_px,
                    font_name=font_name,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    add_line=add_line,
                    add_arc=add_arc,
                    add_rounded_rect=add_rounded_rect,
                    apply_masked_stroke=apply_masked_stroke,
                    hairline_pen=hairline_pen,
                    split_overline_text=split_overline_text,
                    measure_text_width=measure_text_width,
                    measure_text_height=measure_text_height,
                )
            )

        if self.show_net_name and self.text and text_baseline is not None:
            if text_width <= 0.0:
                clean_text, _ = split_overline_text(self.text)
                text_width = measure_text_width(
                    clean_text, font_size_px, font_name, is_bold, is_italic
                )
            text_height = measure_text_height(
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
                use_altium_algorithm=False,
            )
            operations.extend(
                make_text_with_overline_operations(
                    text=self.text,
                    baseline_x_px=text_baseline[0],
                    baseline_y_px=text_baseline[1],
                    sheet_height_px=sheet_height_px,
                    font_payload=font_payload,
                    font_size_px=font_size_px,
                    font_name=font_name,
                    bold=is_bold,
                    italic=is_italic,
                    brush_color_raw=stroke_raw,
                    units_per_px=units_per_px,
                    geometry_step_px=font_size_px
                    if (
                        not self.is_cross_sheet_connector
                        and orient
                        in {
                            TextOrientation.DEGREES_0,
                            TextOrientation.DEGREES_180,
                            TextOrientation.DEGREES_270,
                        }
                    )
                    else None,
                )
            )
            track_point(text_baseline[0], text_baseline[1] - float(int(font_size_px)))
            track_point(
                text_baseline[0] + text_width,
                text_baseline[1] - float(int(font_size_px)) + text_height,
            )

        add_line(stub_start[0], stub_start[1], stub_end[0], stub_end[1])
        add_native_junction()

        if math.isinf(min_x) or math.isinf(min_y):
            track_point(x, y)

        return self._finalize_power_port_geometry_record(
            document_id=document_id,
            object_id=object_id,
            sheet_height_px=sheet_height_px,
            connection_point=geo_point(x, y),
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
            operations=operations,
            units_per_px=units_per_px,
            bounds_factory=SchGeometryBounds,
            record_factory=SchGeometryRecord,
            wrap_record_operations=wrap_record_operations,
        )

    def _build_custom_power_port_geometry_record(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        custom_prims: list[dict],
        x: float,
        y: float,
        orient: int,
        sheet_height_px: float,
        stroke_raw: int,
        font_name: str,
        font_size_px: float,
        is_bold: bool,
        is_italic: bool,
        font_payload: Any,
        units_per_px: int,
        operations: list[Any],
        track_point: Any,
        geo_point: Any,
        add_line: Any,
        add_native_junction: Any,
        begin_group: Any,
        end_group: Any,
        polygons: Any,
        make_pen: Any,
        make_solid_brush: Any,
        make_text_with_overline_operations: Any,
        wrap_record_operations: Any,
        bounds_factory: Any,
        record_factory: Any,
        min_x_ref: Any,
        min_y_ref: Any,
        max_x_ref: Any,
        max_y_ref: Any,
    ) -> Any:
        text_layout = self._custom_power_port_text_layout(
            ctx,
            x=x,
            y=y,
            custom_prims=custom_prims,
            font_name=font_name,
            font_size_px=font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
        )
        if text_layout is not None:
            text_baseline, text_width, text_height = text_layout
            operations.extend(
                make_text_with_overline_operations(
                    text=self.text,
                    baseline_x_px=text_baseline[0],
                    baseline_y_px=text_baseline[1],
                    sheet_height_px=sheet_height_px,
                    font_payload=font_payload,
                    font_size_px=font_size_px,
                    font_name=font_name,
                    bold=is_bold,
                    italic=is_italic,
                    brush_color_raw=stroke_raw,
                    units_per_px=units_per_px,
                )
            )
            self._track_text_geometry_bounds(
                track_point,
                text_baseline,
                text_width,
                text_height,
                font_size_px,
            )

        for idx, rec in enumerate(custom_prims):
            record_type = self._custom_primitive_record_type(rec)
            operations.append(
                begin_group(self._custom_power_port_child_group_id(rec, idx))
            )

            prim_color_raw = int(rec.get("Color", "0") or 0)
            line_width = float(rec.get("LineWidth", "1") or 1)
            prim_pen = make_pen(
                prim_color_raw,
                width=int(round(line_width * units_per_px)),
            )

            if record_type == SchRecordType.LINE:
                lx1 = float(rec.get("Location.X", rec.get("LOCATION.X", "0")) or 0)
                ly1 = float(rec.get("Location.Y", rec.get("LOCATION.Y", "0")) or 0)
                lx2 = float(rec.get("Corner.X", rec.get("CORNER.X", "0")) or 0)
                ly2 = float(rec.get("Corner.Y", rec.get("CORNER.Y", "0")) or 0)
                sx1, sy1 = self._transform_power_port_local_point(
                    orient, x, y, lx1, ly1
                )
                sx2, sy2 = self._transform_power_port_local_point(
                    orient, x, y, lx2, ly2
                )
                add_line(sx1, sy1, sx2, sy2, line_pen=prim_pen)
            elif record_type == SchRecordType.POLYGON:
                polygon_points = self._custom_polygon_svg_points(
                    rec,
                    orient=orient,
                    x=x,
                    y=y,
                    track_point=track_point,
                )
                if polygon_points:
                    geometry_polygon = [geo_point(px, py) for px, py in polygon_points]
                    is_solid = rec.get("IsSolid", "F") == "T"
                    area_color_raw = int(
                        rec.get("AreaColor", str(prim_color_raw)) or prim_color_raw
                    )
                    operations.extend(
                        self._custom_polygon_geometry_ops(
                            geometry_polygon,
                            is_solid=is_solid,
                            area_color_raw=area_color_raw,
                            prim_color_raw=prim_color_raw,
                            prim_pen=prim_pen,
                            polygons=polygons,
                            make_pen=make_pen,
                            make_solid_brush=make_solid_brush,
                        )
                    )

            operations.append(end_group())

        if math.isinf(min_x_ref()) or math.isinf(min_y_ref()):
            track_point(x, y)
        add_native_junction()

        return self._finalize_power_port_geometry_record(
            document_id=document_id,
            object_id="ePowerObject",
            sheet_height_px=sheet_height_px,
            connection_point=geo_point(x, y),
            min_x=min_x_ref(),
            min_y=min_y_ref(),
            max_x=max_x_ref(),
            max_y=max_y_ref(),
            operations=operations,
            units_per_px=units_per_px,
            bounds_factory=bounds_factory,
            record_factory=record_factory,
            wrap_record_operations=wrap_record_operations,
        )

    def _custom_power_port_child_group_id(self, record: dict, index: int) -> str:
        seed = f"{self.unique_id}:{index}:{record.get('RECORD', '')}:{record.get('UniqueID', '')}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()

    def _custom_primitive_record_type(self, record: dict) -> SchRecordType | None:
        try:
            return SchRecordType(int(record.get("RECORD", 0) or 0))
        except (TypeError, ValueError):
            return None

    def _transform_power_port_local_point(
        self,
        orient: TextOrientation,
        x: float,
        y: float,
        lx: float,
        ly: float,
    ) -> tuple[float, float]:
        if orient == TextOrientation.DEGREES_0:
            dx, dy = lx, -ly
        elif orient == TextOrientation.DEGREES_90:
            dx, dy = -ly, -lx
        elif orient == TextOrientation.DEGREES_180:
            dx, dy = -lx, ly
        else:
            dx, dy = ly, lx
        return x + dx, y + dy

    def _custom_polygon_svg_points(
        self,
        record: dict,
        *,
        orient: TextOrientation,
        x: float,
        y: float,
        track_point: Any,
    ) -> list[tuple[float, float]]:
        count = int(record.get("LocationCount", "0") or 0)
        polygon_points: list[tuple[float, float]] = []
        for point_index in range(1, count + 1):
            px = float(record.get(f"X{point_index}", "0") or 0)
            py = float(record.get(f"Y{point_index}", "0") or 0)
            sx, sy = self._transform_power_port_local_point(orient, x, y, px, py)
            polygon_points.append((sx, sy))
            track_point(sx, sy)
        return polygon_points

    def _custom_polygon_geometry_ops(
        self,
        geometry_polygon: list[tuple[float, float]],
        *,
        is_solid: bool,
        area_color_raw: int,
        prim_color_raw: int,
        prim_pen: Any,
        polygons: Any,
        make_pen: Any,
        make_solid_brush: Any,
    ) -> list[Any]:
        if is_solid:
            return [
                polygons([geometry_polygon], brush=make_solid_brush(area_color_raw)),
                polygons([geometry_polygon], pen=make_pen(prim_color_raw, width=0)),
            ]
        return [polygons([geometry_polygon], pen=prim_pen)]

    def _build_cross_sheet_geometry(
        self,
        *,
        orient: TextOrientation,
        x: float,
        y: float,
        font_size_px: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
        add_line: Any,
        apply_masked_stroke: Any,
        split_overline_text: Any,
        measure_text_width: Any,
    ) -> tuple[
        str, tuple[float, float], tuple[float, float], tuple[float, float] | None, float
    ]:
        stub_len = 2
        chevron_spacing = 5
        chevron_arm = 5
        stub_end_ax, stub_end_ay = self._cross_sheet_mask_stub_end(orient, stub_len)
        apply_masked_stroke(stub_end_ax, stub_end_ay)

        if orient == TextOrientation.DEGREES_0:
            c1x = x + stub_len
            c2x = c1x + chevron_spacing
            self._render_cross_sheet_chevrons_horizontal(
                add_line, c1x, c2x, y, chevron_arm, right=True
            )
            text_baseline = (
                (c2x + chevron_arm + 2, y + 4)
                if self.show_net_name and self.text
                else None
            )
            text_width = 0.0
            stub_end = (c1x, y)
        elif orient == TextOrientation.DEGREES_180:
            c1x = x - stub_len
            c2x = c1x - chevron_spacing
            self._render_cross_sheet_chevrons_horizontal(
                add_line, c1x, c2x, y, chevron_arm, right=False
            )
            text_width = self._measure_clean_power_text(
                split_overline_text,
                measure_text_width,
                font_size_px,
                font_name,
                is_bold,
                is_italic,
            )
            text_baseline = (
                (c2x - chevron_arm - 2 - text_width, y + 4)
                if self.show_net_name and self.text
                else None
            )
            stub_end = (c1x, y)
        elif orient == TextOrientation.DEGREES_90:
            c1y = y - stub_len
            c2y = c1y - chevron_spacing
            self._render_cross_sheet_chevrons_vertical(
                add_line, x, c1y, c2y, chevron_arm, up=True
            )
            text_width = self._measure_clean_power_text(
                split_overline_text,
                measure_text_width,
                font_size_px,
                font_name,
                is_bold,
                is_italic,
            )
            text_baseline = (
                (x - text_width / 2, c2y - chevron_arm - 2)
                if self.show_net_name and self.text
                else None
            )
            stub_end = (x, c1y)
        else:
            c1y = y + stub_len
            c2y = c1y + chevron_spacing
            self._render_cross_sheet_chevrons_vertical(
                add_line, x, c1y, c2y, chevron_arm, up=False
            )
            text_width = self._measure_clean_power_text(
                split_overline_text,
                measure_text_width,
                font_size_px,
                font_name,
                is_bold,
                is_italic,
            )
            text_baseline = (
                (x - text_width / 2, c2y + chevron_arm + font_size_px + 2)
                if self.show_net_name and self.text
                else None
            )
            stub_end = (x, c1y)
        return "eCrossSheetConnector", (x, y), stub_end, text_baseline, text_width

    def _cross_sheet_mask_stub_end(
        self, orient: TextOrientation, stub_len: int
    ) -> tuple[int, int]:
        if orient == TextOrientation.DEGREES_0:
            return self.location.x + stub_len, self.location.y
        if orient == TextOrientation.DEGREES_90:
            return self.location.x, self.location.y + stub_len
        if orient == TextOrientation.DEGREES_180:
            return self.location.x - stub_len, self.location.y
        return self.location.x, self.location.y - stub_len

    def _render_cross_sheet_chevrons_horizontal(
        self,
        add_line: Any,
        c1x: float,
        c2x: float,
        y: float,
        chevron_arm: float,
        *,
        right: bool,
    ) -> None:
        sign = 1 if right else -1
        add_line(c2x, y, c2x + sign * chevron_arm, y + chevron_arm)
        add_line(c2x, y, c2x + sign * chevron_arm, y - chevron_arm)
        add_line(c1x, y, c2x, y + chevron_arm)
        add_line(c1x, y, c2x, y - chevron_arm)

    def _render_cross_sheet_chevrons_vertical(
        self,
        add_line: Any,
        x: float,
        c1y: float,
        c2y: float,
        chevron_arm: float,
        *,
        up: bool,
    ) -> None:
        sign = -1 if up else 1
        add_line(x, c2y, x + chevron_arm, c2y + sign * chevron_arm)
        add_line(x, c2y, x - chevron_arm, c2y + sign * chevron_arm)
        add_line(x, c1y, x + chevron_arm, c2y)
        add_line(x, c1y, x - chevron_arm, c2y)

    def _measure_clean_power_text(
        self,
        split_overline_text: Any,
        measure_text_width: Any,
        font_size_px: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
    ) -> float:
        if not (self.show_net_name and self.text):
            return 0.0
        clean_text, _ = split_overline_text(self.text)
        return measure_text_width(
            clean_text, font_size_px, font_name, is_bold, is_italic
        )

    def _build_standard_power_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        orient: TextOrientation,
        x: float,
        y: float,
        font_size_px: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
        add_line: Any,
        add_arc: Any,
        add_rounded_rect: Any,
        apply_masked_stroke: Any,
        hairline_pen: Any,
        split_overline_text: Any,
        measure_text_width: Any,
        measure_text_height: Any,
    ) -> tuple[
        str, tuple[float, float], tuple[float, float], tuple[float, float] | None, float
    ]:
        stub_end_x, stub_end_y, stub_end_ax, stub_end_ay = self._power_port_stub_layout(
            orient,
            x,
            y,
            ctx.scale,
        )
        apply_masked_stroke(stub_end_ax, stub_end_ay)
        self._render_power_style_geometry(
            orient,
            stub_end_x,
            stub_end_y,
            ctx.scale,
            add_line,
            add_arc,
            add_rounded_rect,
            hairline_pen,
        )
        text_baseline, text_width = self._standard_power_text_layout(
            ctx,
            orient=orient,
            x=x,
            y=y,
            font_size_px=font_size_px,
            font_name=font_name,
            is_bold=is_bold,
            is_italic=is_italic,
            split_overline_text=split_overline_text,
            measure_text_width=measure_text_width,
            measure_text_height=measure_text_height,
        )
        return (
            "ePowerObject",
            (x, y),
            (stub_end_x, stub_end_y),
            text_baseline,
            text_width,
        )

    def _power_port_stub_layout(
        self,
        orient: TextOrientation,
        x: float,
        y: float,
        scale: float,
    ) -> tuple[float, float, int, int]:
        stub_lengths = {
            0: 4,
            1: 4,
            2: 10,
            3: 6,
            4: 10,
            5: 10,
            6: 10,
            7: 10,
            8: 16,
            9: 16,
            10: 20,
        }
        stub_len_units = stub_lengths.get(self.style, 10)
        stub_len = stub_len_units * scale
        if orient == TextOrientation.DEGREES_0:
            return x + stub_len, y, self.location.x + stub_len_units, self.location.y
        if orient == TextOrientation.DEGREES_90:
            return x, y - stub_len, self.location.x, self.location.y + stub_len_units
        if orient == TextOrientation.DEGREES_180:
            return x - stub_len, y, self.location.x - stub_len_units, self.location.y
        return x, y + stub_len, self.location.x, self.location.y - stub_len_units

    def _render_power_style_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        add_line: Any,
        add_arc: Any,
        add_rounded_rect: Any,
        hairline_pen: Any,
    ) -> None:
        if self.style == 0:
            self._render_power_circle_geometry(
                orient, stub_end_x, stub_end_y, scale, add_rounded_rect, hairline_pen
            )
        elif self.style == 1:
            self._render_power_arrow_geometry(
                orient, stub_end_x, stub_end_y, scale, add_line, include_base=True
            )
        elif self.style == 2:
            self._render_power_bar_geometry(
                orient, stub_end_x, stub_end_y, 5 * scale, add_line
            )
        elif self.style == 3:
            self._render_power_wave_geometry(
                orient, stub_end_x, stub_end_y, 4 * scale, add_arc, hairline_pen
            )
        elif self.style == 4:
            self._render_ground_line_stack_geometry(
                orient,
                stub_end_x,
                stub_end_y,
                scale,
                [(0, 10), (3, 7), (6, 4), (9, 1)],
                add_line,
            )
        elif self.style == 5:
            self._render_ground_signal_geometry(
                orient, stub_end_x, stub_end_y, 10 * scale, add_line
            )
        elif self.style == 6:
            self._render_ground_earth_geometry(
                orient, stub_end_x, stub_end_y, 10 * scale, 5 * scale, add_line
            )
        elif self.style == 7:
            self._render_power_arrow_geometry(
                orient, stub_end_x, stub_end_y, scale, add_line, include_base=False
            )
        elif self.style == 8:
            self._render_ground_line_stack_geometry(
                orient,
                stub_end_x,
                stub_end_y,
                scale,
                [(0, 10), (4, 6), (8, 2)],
                add_line,
            )
        elif self.style == 9:
            self._render_ground_line_stack_geometry(
                orient,
                stub_end_x,
                stub_end_y,
                scale,
                [(0, 10), (4, 6), (8, 2)],
                add_line,
            )
            add_rounded_rect(
                stub_end_x - 12 * scale,
                stub_end_y - 12 * scale,
                stub_end_x + 12 * scale,
                stub_end_y + 12 * scale,
                radius_x_px=12 * scale,
                radius_y_px=12 * scale,
            )
        elif self.style == 10:
            self._render_power_bar_geometry(
                orient, stub_end_x, stub_end_y, 8 * scale, add_line
            )

    def _render_power_circle_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        add_rounded_rect: Any,
        hairline_pen: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            rx, ry = stub_end_x, stub_end_y - 3 * scale
        elif orient == TextOrientation.DEGREES_90:
            rx, ry = stub_end_x - 3 * scale, stub_end_y - 6 * scale
        elif orient == TextOrientation.DEGREES_180:
            rx, ry = stub_end_x - 6 * scale, stub_end_y - 3 * scale
        else:
            rx, ry = stub_end_x - 3 * scale, stub_end_y
        add_rounded_rect(
            rx,
            ry,
            rx + 6 * scale,
            ry + 6 * scale,
            radius_x_px=3 * scale,
            radius_y_px=3 * scale,
            rect_pen=hairline_pen,
        )

    def _render_power_arrow_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        add_line: Any,
        *,
        include_base: bool,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            tip_x = stub_end_x + 6 * scale if include_base else stub_end_x
            base_x = stub_end_x if include_base else stub_end_x - 6 * scale
            add_line(tip_x, stub_end_y, base_x, stub_end_y - 3 * scale)
            add_line(tip_x, stub_end_y, base_x, stub_end_y + 3 * scale)
            if include_base:
                add_line(base_x, stub_end_y - 3 * scale, base_x, stub_end_y + 3 * scale)
        elif orient == TextOrientation.DEGREES_90:
            tip_y = stub_end_y - 6 * scale if include_base else stub_end_y
            base_y = stub_end_y if include_base else stub_end_y + 6 * scale
            add_line(stub_end_x, tip_y, stub_end_x - 3 * scale, base_y)
            add_line(stub_end_x, tip_y, stub_end_x + 3 * scale, base_y)
            if include_base:
                add_line(stub_end_x - 3 * scale, base_y, stub_end_x + 3 * scale, base_y)
        elif orient == TextOrientation.DEGREES_180:
            tip_x = stub_end_x - 6 * scale if include_base else stub_end_x
            base_x = stub_end_x if include_base else stub_end_x + 6 * scale
            add_line(tip_x, stub_end_y, base_x, stub_end_y - 3 * scale)
            add_line(tip_x, stub_end_y, base_x, stub_end_y + 3 * scale)
            if include_base:
                add_line(base_x, stub_end_y - 3 * scale, base_x, stub_end_y + 3 * scale)
        else:
            tip_y = stub_end_y + 6 * scale if include_base else stub_end_y
            base_y = stub_end_y if include_base else stub_end_y - 6 * scale
            add_line(stub_end_x, tip_y, stub_end_x - 3 * scale, base_y)
            add_line(stub_end_x, tip_y, stub_end_x + 3 * scale, base_y)
            if include_base:
                add_line(stub_end_x - 3 * scale, base_y, stub_end_x + 3 * scale, base_y)

    def _render_power_bar_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        half: float,
        add_line: Any,
    ) -> None:
        if orient in [TextOrientation.DEGREES_0, TextOrientation.DEGREES_180]:
            add_line(stub_end_x, stub_end_y - half, stub_end_x, stub_end_y + half)
        else:
            add_line(stub_end_x - half, stub_end_y, stub_end_x + half, stub_end_y)

    def _render_power_wave_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        arc_r: float,
        add_arc: Any,
        hairline_pen: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            add_arc(
                stub_end_x + arc_r, stub_end_y, arc_r, -180, -270, arc_pen=hairline_pen
            )
            add_arc(stub_end_x - arc_r, stub_end_y, arc_r, 0, -90, arc_pen=hairline_pen)
        elif orient == TextOrientation.DEGREES_90:
            add_arc(
                stub_end_x, stub_end_y - arc_r, arc_r, -270, -360, arc_pen=hairline_pen
            )
            add_arc(
                stub_end_x, stub_end_y + arc_r, arc_r, -90, -180, arc_pen=hairline_pen
            )
        elif orient == TextOrientation.DEGREES_180:
            add_arc(
                stub_end_x - arc_r, stub_end_y, arc_r, -90, -180, arc_pen=hairline_pen
            )
            add_arc(
                stub_end_x + arc_r, stub_end_y, arc_r, -270, -360, arc_pen=hairline_pen
            )
        else:
            add_arc(
                stub_end_x, stub_end_y + arc_r, arc_r, -90, -180, arc_pen=hairline_pen
            )
            add_arc(
                stub_end_x, stub_end_y - arc_r, arc_r, -270, -360, arc_pen=hairline_pen
            )

    def _render_ground_line_stack_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        line_specs: list[tuple[int, int]],
        add_line: Any,
    ) -> None:
        for off, half in line_specs:
            if orient == TextOrientation.DEGREES_0:
                lx = stub_end_x + off * scale
                add_line(lx, stub_end_y - half * scale, lx, stub_end_y + half * scale)
            elif orient == TextOrientation.DEGREES_90:
                ly = stub_end_y - off * scale
                add_line(stub_end_x - half * scale, ly, stub_end_x + half * scale, ly)
            elif orient == TextOrientation.DEGREES_180:
                lx = stub_end_x - off * scale
                add_line(lx, stub_end_y - half * scale, lx, stub_end_y + half * scale)
            else:
                ly = stub_end_y + off * scale
                add_line(stub_end_x - half * scale, ly, stub_end_x + half * scale, ly)

    def _render_ground_signal_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        num: float,
        add_line: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(stub_end_x, stub_end_y - num, stub_end_x + num, stub_end_y)
            add_line(stub_end_x, stub_end_y + num, stub_end_x + num, stub_end_y)
        elif orient == TextOrientation.DEGREES_90:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(stub_end_x - num, stub_end_y, stub_end_x, stub_end_y - num)
            add_line(stub_end_x + num, stub_end_y, stub_end_x, stub_end_y - num)
        elif orient == TextOrientation.DEGREES_180:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(stub_end_x, stub_end_y - num, stub_end_x - num, stub_end_y)
            add_line(stub_end_x, stub_end_y + num, stub_end_x - num, stub_end_y)
        else:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(stub_end_x - num, stub_end_y, stub_end_x, stub_end_y + num)
            add_line(stub_end_x + num, stub_end_y, stub_end_x, stub_end_y + num)

    def _render_ground_earth_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        num: float,
        half: float,
        add_line: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(
                stub_end_x, stub_end_y + num, stub_end_x + num, stub_end_y + num + half
            )
            add_line(stub_end_x, stub_end_y, stub_end_x + num, stub_end_y + half)
            add_line(
                stub_end_x, stub_end_y - num, stub_end_x + num, stub_end_y - num + half
            )
        elif orient == TextOrientation.DEGREES_90:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(
                stub_end_x - num, stub_end_y, stub_end_x - num + half, stub_end_y - num
            )
            add_line(stub_end_x, stub_end_y, stub_end_x + half, stub_end_y - num)
            add_line(
                stub_end_x + num, stub_end_y, stub_end_x + num + half, stub_end_y - num
            )
        elif orient == TextOrientation.DEGREES_180:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(
                stub_end_x, stub_end_y - num, stub_end_x - num, stub_end_y - num - half
            )
            add_line(stub_end_x, stub_end_y, stub_end_x - num, stub_end_y - half)
            add_line(
                stub_end_x, stub_end_y + num, stub_end_x - num, stub_end_y + num - half
            )
        else:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(
                stub_end_x - num, stub_end_y, stub_end_x - num - half, stub_end_y + num
            )
            add_line(stub_end_x, stub_end_y, stub_end_x - half, stub_end_y + num)
            add_line(stub_end_x + num, stub_end_y, stub_end_x + half, stub_end_y + num)

    def _standard_power_text_layout(
        self,
        ctx: "SchSvgRenderContext",
        *,
        orient: TextOrientation,
        x: float,
        y: float,
        font_size_px: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
        split_overline_text: Any,
        measure_text_width: Any,
        measure_text_height: Any,
    ) -> tuple[tuple[float, float] | None, float]:
        if not (self.show_net_name and self.text):
            return None, 0.0

        text_width = self._measure_clean_power_text(
            split_overline_text,
            measure_text_width,
            font_size_px,
            font_name,
            is_bold,
            is_italic,
        )
        text_height = measure_text_height(
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
            use_altium_algorithm=False,
        )
        power_len = self._standard_power_symbol_length(ctx.scale)
        text_offset = self._standard_power_text_offset(power_len, ctx.scale)
        horizontal_gap = 2 * ctx.scale
        line_height_px = (
            ctx.get_font_line_height(self.font_id)
            if hasattr(ctx, "get_font_line_height")
            else text_height
        )
        horizontal_y = y + font_size_px - (line_height_px / 2.0)
        descent_offset = self._native_text_descent_offset_px(
            ctx,
            font_name=font_name,
            font_size_px=font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
        )
        down_baseline_y = y + text_offset + font_size_px
        if orient == TextOrientation.DEGREES_0:
            return (x + text_offset + horizontal_gap, horizontal_y), text_width
        if orient == TextOrientation.DEGREES_90:
            return (x - text_width / 2, y - text_offset - descent_offset), text_width
        if orient == TextOrientation.DEGREES_180:
            return (
                x - text_offset - horizontal_gap - text_width,
                horizontal_y,
            ), text_width
        return (x - text_width / 2, down_baseline_y), text_width

    def _standard_power_symbol_length(self, scale: float) -> float:
        if self.style in [8, 9]:
            return 16 * scale
        if self.style == 10:
            return 20 * scale
        return 10 * scale

    def _standard_power_text_offset(self, power_len: float, scale: float) -> float:
        if self.style in [4, 5, 6]:
            return 2 * power_len
        if self.style == 8:
            return power_len + 8 * scale
        if self.style == 9:
            return power_len + 12 * scale
        return power_len

    def _track_text_geometry_bounds(
        self,
        track_point: Any,
        text_baseline: tuple[float, float],
        text_width: float,
        text_height: float,
        font_size_px: float,
    ) -> None:
        track_point(text_baseline[0], text_baseline[1] - float(int(font_size_px)))
        track_point(
            text_baseline[0] + text_width,
            text_baseline[1] - float(int(font_size_px)) + text_height,
        )

    def _finalize_power_port_geometry_record(
        self,
        *,
        document_id: str,
        object_id: str,
        sheet_height_px: float,
        connection_point: tuple[float, float],
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        operations: list[Any],
        units_per_px: int,
        bounds_factory: Any,
        record_factory: Any,
        wrap_record_operations: Any,
    ) -> Any:
        return record_factory(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="power",
            object_id=object_id,
            bounds=bounds_factory(
                left=int(math.floor(min_x * 100000)),
                top=int(math.floor((sheet_height_px - min_y) * 100000)),
                right=int(math.ceil(max_x * 100000)),
                bottom=int(math.ceil((sheet_height_px - max_y) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
            extras={
                "connection_points": [
                    {
                        "id": "power-port-hotspot",
                        "kind": "connection",
                        "role": "ratsnest-anchor",
                        "point": [connection_point[0], connection_point[1]],
                        "source_kind": "power_port_hotspot",
                    }
                ]
            },
        )
