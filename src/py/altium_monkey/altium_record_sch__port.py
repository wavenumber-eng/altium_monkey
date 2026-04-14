"""Schematic record model for SchRecordType.PORT."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import LineWidth, SchGraphicalObject, SchRecordType
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_sch_enums import PortIOType, PortStyle, SchHorizontalAlign
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
)
from .altium_text_metrics import measure_text_width


class AltiumSchPort(SingleFontBindableRecordMixin, SchGraphicalObject):
    """
    Port record.

    Generic hierarchical port (connects hierarchical blocks).

    TEXT FIELDS:
        - Name: Port name/label displayed inside the port body (e.g., "DATA_IN", "CLK_OUT")
        - Text: Cross-reference text (displayed when cross-reference mode is enabled)

        When CrossReference is hidden (CrossReference=F), the Name field is rendered.
        When CrossReference is shown (CrossReference=T), the Text field is rendered.

    PROPERTIES:
        - Name: Port name/label
        - Style: arrow shape (0-7)
        - IOType: Unspecified/Output/Input/Bidirectional (0-3)
        - Alignment: text alignment
        - TextColor: uint - Win32 BGR color
        - Width: int - port width in Altium units
        - Height: int - port height in Altium units
        - FontId: int - font ID for text
        - BorderWidth: border thickness enum (0-3)
        - AutoSize: bool - auto-size port to text
        - CrossRef: string - cross-reference text
        - ConnectedEnd: which end is connected (0-2)
        - OverrideDisplayString: string - override display text
        - HarnessType: string - harness type name
        - ShowNetName: bool - show net name on port
        - HarnessColor: uint - Win32 BGR color for harness
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        self._use_pascal_case: bool = True
        # Core port properties
        self.name: str = ""  # Port name displayed inside port body
        self.text: str = ""  # Cross-reference text (when CrossReference=T)
        self.font_id: int = 1
        self.io_type: PortIOType = PortIOType.UNSPECIFIED
        self.alignment: SchHorizontalAlign = SchHorizontalAlign.LEFT
        self.width: int = 10  # Port width in Altium units
        self.height: int = 10  # Port height in Altium units
        self.text_color: int = 0  # Text color (Win32 BGR format)
        self.cross_reference: bool = False  # Show cross-reference instead of name
        # Border width: 0=don't serialize (shows "smallest"), 1=Small, 2=Medium, 3=Large
        # When BORDERWIDTH field is absent, Altium defaults to "smallest"
        self.border_width: LineWidth = LineWidth.SMALLEST

        # Additional serialized properties for ports.
        self.style: PortStyle = PortStyle.NONE_HORIZONTAL
        self.auto_size: bool = True  # Auto-size port to fit text
        self.connected_end: int = (
            0  # Connected end: 0=Unconnected, 1=LeftEnd, 2=RightEnd
        )
        self.override_display_string: str = ""  # Override display text
        self.harness_type: str = ""  # Harness type name (for harness ports)
        self.show_net_name: bool = True  # File format stores this as PortNameIsHidden
        self.harness_color: int = 0  # Harness color (Win32 BGR format)
        self.object_definition_id: str = ""

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PORT

    @property
    def display_text(self) -> str:
        """
        Get the text to display inside the port.

                Returns the cross-reference text if CrossReference=T,
                otherwise returns the Name field.
        """
        return self.text if self.cross_reference else self.name

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
        self._use_pascal_case = "Name" in record or "Style" in record

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()
        r = self._record

        # Core port fields
        self.name, _, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.NAME,
            default="",
        )
        # Use read_font_id for translation support
        self.font_id, _ = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=1
        )
        io_val, _ = s.read_int(record, Fields.IO_TYPE, default=0)
        self.io_type = PortIOType(io_val)
        alignment_val, _ = s.read_int(record, Fields.ALIGNMENT, default=0)
        self.alignment = SchHorizontalAlign(alignment_val)
        self.width, _ = s.read_int(record, Fields.WIDTH, default=10)
        self.height, _ = s.read_int(record, Fields.HEIGHT, default=10)
        self.text_color, _ = s.read_int(record, Fields.TEXT_COLOR, default=0)
        # Border width: absence means "smallest" (0), values 1-3 are small/medium/large
        border_width_val, _ = s.read_int(record, Fields.BORDER_WIDTH, default=0)
        self.border_width = LineWidth(border_width_val)

        # Additional ISch_Port interface properties
        style_val, _ = s.read_int(record, Fields.STYLE, default=0)
        self.style = PortStyle(style_val)
        self.auto_size, _ = s.read_bool(record, Fields.AUTO_SIZE, default=False)
        self.harness_type, _, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.HARNESS_TYPE,
            default="",
        )
        port_name_is_hidden, _ = s.read_bool(
            record, Fields.PORT_NAME_IS_HIDDEN, default=False
        )
        self.show_net_name = not port_name_is_hidden
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

        # Core port fields
        if self.name:
            s.write_str(record, Fields.NAME, self.name, raw)
        else:
            s.remove_field(record, Fields.NAME)

        if self.font_id != 0:
            s.write_int(record, Fields.FONT_ID, self.font_id, raw)
        else:
            s.remove_field(record, Fields.FONT_ID)

        if self.io_type != 0:
            s.write_int(record, Fields.IO_TYPE, int(self.io_type), raw)
        else:
            s.remove_field(record, Fields.IO_TYPE)
        if self.alignment != 0:
            s.write_int(record, Fields.ALIGNMENT, int(self.alignment), raw)
        else:
            s.remove_field(record, Fields.ALIGNMENT)

        if self.width != 0:
            s.write_int(record, Fields.WIDTH, self.width, raw)
        else:
            s.remove_field(record, Fields.WIDTH)
        if self.height != 0:
            s.write_int(record, Fields.HEIGHT, self.height, raw)
        else:
            s.remove_field(record, Fields.HEIGHT)

        if self.text_color != 0:
            # TextColor is often absent in source files because black is the
            # native default. Non-default mutations must add the field.
            s.write_int(record, Fields.TEXT_COLOR, self.text_color, raw, force=True)
        else:
            s.remove_field(record, Fields.TEXT_COLOR)

        if self.border_width > 0:
            s.write_int(record, Fields.BORDER_WIDTH, int(self.border_width), raw)
        else:
            s.remove_field(record, Fields.BORDER_WIDTH)

        if self.style != 0:
            s.write_int(record, Fields.STYLE, int(self.style), raw)
        else:
            s.remove_field(record, Fields.STYLE)

        if self.auto_size:
            s.write_bool(record, Fields.AUTO_SIZE, True, raw)
        else:
            s.remove_field(record, Fields.AUTO_SIZE)

        if self.harness_type:
            s.write_str(record, Fields.HARNESS_TYPE, self.harness_type, raw)
        else:
            s.remove_field(record, Fields.HARNESS_TYPE)

        if not self.show_net_name:
            s.write_bool(
                record,
                Fields.PORT_NAME_IS_HIDDEN,
                True,
                raw,
                force=True,
            )
        else:
            s.remove_field(record, Fields.PORT_NAME_IS_HIDDEN)

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

        s.remove_field(record, Fields.TEXT)
        s.remove_field(record, Fields.CROSS_REFERENCE)
        s.remove_field(record, Fields.CONNECTED_END)
        s.remove_field(record, Fields.OVERRIDE_DISPLAY_STRING)
        s.remove_field(record, Fields.SHOW_NET_NAME)
        s.remove_field(record, Fields.HARNESS_COLOR)

        return record

    @property
    def width_mils(self) -> int:
        """
        Public port width helper expressed in mils.

        The persisted port width field uses native 10-mil units, so this helper
        converts to the public mil-based API surface.
        """
        return int(self.width) * 10

    @width_mils.setter
    def width_mils(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("width_mils must be an integer number of mils")
        if value <= 0:
            raise ValueError("width_mils must be greater than zero")
        self.width = max(1, int(round(value / 10.0)))

    @property
    def height_mils(self) -> int:
        """
        Public port height helper expressed in mils.

        The persisted port height field uses native 10-mil units, so this helper
        converts to the public mil-based API surface.
        """
        return int(self.height) * 10

    @height_mils.setter
    def height_mils(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("height_mils must be an integer number of mils")
        if value <= 0:
            raise ValueError("height_mils must be greater than zero")
        self.height = max(1, int(round(value / 10.0)))

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for a standard port.
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
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        x, y = ctx.transform_point(self.location.x, self.location.y)
        width = self.width * ctx.scale
        height = self.height * ctx.scale
        arrow_depth = height / 2
        harness_color = ctx.harness_port_colors.get(str(self.unique_id or ""))
        is_harness_port = bool(self.harness_type) or harness_color is not None
        effective_harness_color = (
            int(harness_color)
            if harness_color is not None
            else int(self.harness_color or 0)
        )

        arrow_style = self._resolve_arrow_style(is_harness_port)
        polygon_points = self._build_polygon_points(
            x, y, width, arrow_depth, arrow_style
        )
        geometry_polygon = self._geometry_polygon(
            polygon_points,
            svg_coord_to_geometry,
            ctx,
            units_per_px,
        )
        operations = self._build_port_shape_operations(
            ctx,
            polygon_points,
            geometry_polygon,
            is_harness_port,
            effective_harness_color,
            units_per_px,
            SchGeometryOp,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
        )

        text_to_render = self.display_text
        if text_to_render:
            operations.extend(
                self._build_port_text_operations(
                    ctx,
                    text_to_render,
                    x,
                    y,
                    width,
                    is_harness_port,
                    units_per_px,
                    make_font_payload,
                    make_text_with_overline_operations,
                    split_overline_text,
                )
            )

        half_height = float(self.height) / 2.0
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="port",
            object_id="ePort",
            bounds=SchGeometryBounds(
                left=int(round(float(self.location.x) * 100000)),
                top=int(round((float(self.location.y) + half_height) * 100000)),
                right=int(round((float(self.location.x) + float(self.width)) * 100000)),
                bottom=int(round((float(self.location.y) - half_height) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _resolve_arrow_style(self, is_harness_port: bool) -> int:
        if is_harness_port:
            return 3

        ce = getattr(self, "_computed_connected_end", 0)
        if self.io_type == 3:
            return 3
        if self.io_type == 0:
            return 0
        if ce == 0:
            return 2
        if ce == 1:
            return 2 if self.io_type == 1 else 1
        if ce == 2:
            return 1 if self.io_type == 1 else 2
        if ce == 3:
            return 0 if self.io_type == 1 else 3
        return 2

    def _build_polygon_points(
        self,
        x: float,
        y: float,
        width: float,
        arrow_depth: float,
        arrow_style: int,
    ) -> list[tuple[float, float]]:
        if arrow_style == 0:
            return [
                (x, y + arrow_depth),
                (x, y - arrow_depth),
                (x + width, y - arrow_depth),
                (x + width, y + arrow_depth),
            ]
        if arrow_style == 2:
            return [
                (x, y + arrow_depth),
                (x, y - arrow_depth),
                (x + width - arrow_depth, y - arrow_depth),
                (x + width, y),
                (x + width - arrow_depth, y + arrow_depth),
            ]
        if arrow_style == 1:
            return [
                (x + arrow_depth, y + arrow_depth),
                (x, y),
                (x + arrow_depth, y - arrow_depth),
                (x + width, y - arrow_depth),
                (x + width, y + arrow_depth),
            ]
        return [
            (x + arrow_depth, y + arrow_depth),
            (x, y),
            (x + arrow_depth, y - arrow_depth),
            (x + width - arrow_depth, y - arrow_depth),
            (x + width, y),
            (x + width - arrow_depth, y + arrow_depth),
        ]

    def _geometry_polygon(
        self,
        polygon_points: list[tuple[float, float]],
        svg_coord_to_geometry: Any,
        ctx: "SchSvgRenderContext",
        units_per_px: int,
    ) -> list[tuple[float, float]]:
        return [
            svg_coord_to_geometry(
                px,
                py,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for px, py in polygon_points
        ]

    def _build_port_shape_operations(
        self,
        ctx: "SchSvgRenderContext",
        polygon_points: list[tuple[float, float]],
        geometry_polygon: list[tuple[float, float]],
        is_harness_port: bool,
        harness_color: int,
        units_per_px: int,
        geometry_op_cls: Any,
        make_pen: Any,
        make_solid_brush: Any,
        svg_coord_to_geometry: Any,
    ) -> list[Any]:
        if not is_harness_port:
            return [
                geometry_op_cls.polygons(
                    [geometry_polygon],
                    brush=make_solid_brush(int(self.area_color or 0)),
                ),
                geometry_op_cls.polygons(
                    [geometry_polygon],
                    pen=make_pen(
                        int(self.color or 0),
                        width={0: 0, 1: 64, 2: 192, 3: 320}.get(self.border_width, 64),
                    ),
                ),
            ]

        from .altium_sch_svg_renderer import apply_dark, apply_light, modify_color

        main_fill_int = apply_light(harness_color, 40)
        main_stroke_int = apply_dark(main_fill_int, 100)
        shadow_base_int = modify_color(60, main_fill_int, 0)
        shadow_fill_int = apply_light(shadow_base_int, 100)
        shadow_stroke_int = apply_light(shadow_base_int, 80)
        shadow_polygon = [
            svg_coord_to_geometry(
                px + 0.5,
                py + 1.0,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for px, py in polygon_points
        ]
        return [
            geometry_op_cls.polygons(
                [shadow_polygon],
                brush=make_solid_brush(shadow_fill_int, alpha=125),
            ),
            geometry_op_cls.polygons(
                [shadow_polygon],
                pen=make_pen(shadow_stroke_int),
            ),
            geometry_op_cls.polygons(
                [geometry_polygon],
                brush=make_solid_brush(main_fill_int),
            ),
            geometry_op_cls.polygons(
                [geometry_polygon],
                pen=make_pen(main_stroke_int),
            ),
        ]

    def _build_port_text_operations(
        self,
        ctx: "SchSvgRenderContext",
        text_to_render: str,
        x: float,
        y: float,
        width: float,
        is_harness_port: bool,
        units_per_px: int,
        make_font_payload: Any,
        make_text_with_overline_operations: Any,
        split_overline_text: Any,
    ) -> list[Any]:
        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            self.font_id
        )
        font_size_for_width = (
            ctx.get_font_size_for_width(self.font_id)
            if hasattr(ctx, "get_font_size_for_width")
            else font_size_px
        )
        baseline_font_size = ctx.get_baseline_font_size(font_size_px)
        text_y = self._port_text_y(ctx, y, baseline_font_size)
        clean_text, _ = split_overline_text(text_to_render)
        text_width = measure_text_width(
            clean_text, font_size_for_width, font_name, bold=is_bold, italic=is_italic
        )
        text_x = self._port_text_x(x, width, text_width, ctx.scale)

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
        return make_text_with_overline_operations(
            text=text_to_render,
            baseline_x_px=text_x,
            baseline_y_px=text_y,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            font_payload=font_payload,
            font_size_px=font_size_px,
            font_name=font_name,
            bold=is_bold,
            italic=is_italic,
            brush_color_raw=int(self.text_color or 0),
            units_per_px=units_per_px,
        )

    def _port_text_y(
        self, ctx: "SchSvgRenderContext", y: float, baseline_font_size: float
    ) -> float:
        vertical_center_offset = int(baseline_font_size / 2)
        native_baseline_px = int(baseline_font_size)
        native_odd_baseline = getattr(ctx, "native_svg_export", False) and (
            native_baseline_px % 2 == 1
        )
        return y + vertical_center_offset - (0 if native_odd_baseline else 1)

    def _port_text_x(
        self, x: float, width: float, text_width: float, scale: float
    ) -> float:
        text_area_start = x
        text_area_end = x + width
        text_margin = 10.0 * scale
        if self.alignment == 1:
            return text_area_start + text_margin
        if self.alignment == 2:
            return text_area_end - text_width - text_margin
        return (text_area_start + text_area_end) / 2 - text_width / 2
