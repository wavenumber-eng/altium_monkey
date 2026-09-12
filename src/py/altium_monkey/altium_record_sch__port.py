"""Schematic record model for SchRecordType.PORT."""

import math

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
)
from ._sch_managed_defaults import (
    PORT_BORDER_COLOR,
    PORT_FILL_COLOR,
    PORT_TEXT_COLOR,
)
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_sch_enums import PortIOType, PortStyle, SchHorizontalAlign
from .altium_sch_record_helpers import _RecordFields
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    FieldDef,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
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
        self.color = PORT_BORDER_COLOR
        self.area_color = PORT_FILL_COLOR
        self._init_family_dynamic_unique_id()
        self._init_single_font_binding()
        self._use_pascal_case: bool = True
        # Core port properties
        self.name: str = "Port"  # Port name displayed inside port body
        self.text: str = ""  # Cross-reference text (when CrossReference=T)
        self.font_id: int = 1
        self.io_type: PortIOType = PortIOType.UNSPECIFIED
        self.alignment: SchHorizontalAlign = SchHorizontalAlign.LEFT
        self.width: int = 50  # Port width in Altium units
        self._width_frac: int = 0
        self.height: int = 10  # Port height in Altium units
        self._height_frac: int = 0
        self.text_color: int = PORT_TEXT_COLOR  # Text color (Win32 BGR format)
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
        self._has_name = False
        self._used_utf8_name = False
        self._has_font_id = False
        self._has_io_type = False
        self._has_alignment = False
        self._has_width = False
        self._has_height = False
        self._height_was_clamped = False
        self._has_text_color = False
        self._has_border_width = False
        self._has_style = False
        self._has_auto_size = False
        self._has_harness_type = False
        self._used_utf8_harness_type = False
        self._has_show_net_name = False
        self._has_object_definition_id = False
        self._used_utf8_object_definition_id = False
        self._capture_graphical_source_state()
        self._capture_port_source_state()

    def _capture_port_source_state(self) -> None:
        self._source_name = self.name
        self._source_font_id = self.font_id
        self._source_io_type = self.io_type
        self._source_alignment = self.alignment
        self._source_width = (self.width, self._width_frac)
        self._source_height = (self.height, self._height_frac)
        self._source_text_color = self.text_color
        self._source_border_width = self.border_width
        self._source_style = self.style
        self._source_auto_size = self.auto_size
        self._source_harness_type = self.harness_type
        self._source_show_net_name = self.show_net_name
        self._source_object_definition_id = self.object_definition_id

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
        record: _RecordFields,
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
        self._parse_family_dynamic_unique_id(s, record)

        # Core port fields
        self.name, self._has_name, self._used_utf8_name = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.NAME,
            default="",
        )
        # Use read_font_id for translation support
        self.font_id, self._has_font_id = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=1
        )
        io_val, self._has_io_type = s.read_int(record, Fields.IO_TYPE, default=0)
        self.io_type = PortIOType(io_val)
        alignment_val, self._has_alignment = s.read_int(
            record, Fields.ALIGNMENT, default=0
        )
        self.alignment = SchHorizontalAlign(alignment_val)
        self.width, self._width_frac, self._has_width = s.read_coord(record, "Width")
        imported_height, imported_height_frac, self._has_height = s.read_coord(
            record, "Height"
        )
        self._height_was_clamped = False
        if imported_height * 100_000 + imported_height_frac > 0:
            self.height = imported_height
            self._height_frac = imported_height_frac
        else:
            self.height = 10
            self._height_frac = 0
            self._height_was_clamped = self._has_height
        text_color, self._has_text_color = s.read_color(
            record, Fields.TEXT_COLOR, default=0
        )
        self.text_color = int(text_color or 0)
        # Border width: absence means "smallest" (0), values 1-3 are small/medium/large
        border_width_val, self._has_border_width = s.read_int(
            record, Fields.BORDER_WIDTH, default=0
        )
        self.border_width = LineWidth(border_width_val)

        # Additional ISch_Port interface properties
        style_val, self._has_style = s.read_int(record, Fields.STYLE, default=0)
        if not 0 <= style_val <= 255:
            raise ValueError("Style must fit an unsigned byte")
        self.style = (
            PortStyle.NONE_HORIZONTAL
            if style_val in {0, 1, 2, 3}
            else PortStyle.NONE_VERTICAL
        )
        self.auto_size, self._has_auto_size = s.read_bool(
            record, Fields.AUTO_SIZE, default=False
        )
        (
            self.harness_type,
            self._has_harness_type,
            self._used_utf8_harness_type,
        ) = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.HARNESS_TYPE,
            default="",
        )
        port_name_is_hidden, self._has_show_net_name = s.read_bool(
            record, Fields.PORT_NAME_IS_HIDDEN, default=False
        )
        self.show_net_name = not port_name_is_hidden
        (
            self.object_definition_id,
            self._has_object_definition_id,
            self._used_utf8_object_definition_id,
        ) = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.OBJECT_DEFINITION_ID,
            default="",
        )
        self._apply_imported_color_defaults(area_color=True)
        self._capture_port_source_state()

    def serialize_to_record(self) -> _RecordFields:
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
        self._serialize_managed_family_color(
            record, s, Fields.COLOR.canonical, int(self.color or 0)
        )
        self._serialize_managed_family_color(
            record, s, Fields.AREA_COLOR.canonical, int(self.area_color or 0)
        )
        self._serialize_port_identity(record, s, raw)
        self._serialize_port_dimensions(record, s, raw)
        self._serialize_port_style(record, s, raw)
        self._serialize_port_options(record, s, raw)
        self._remove_nonpersisted_port_fields(record, s)
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(
            record,
            (
                "Style",
                "IOType",
                "Alignment",
                "Width",
                "Width_Frac",
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Color",
                "FontID",
                "AreaColor",
                "TextColor",
                "Name",
                "HarnessType",
                "UniqueID",
                "Height",
                "Height_Frac",
                "BorderWidth",
                "AutoSize",
                "ObjectDefinitionId",
                "PortNameIsHidden",
            ),
        )

    def _serialize_port_identity(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self.name != self._source_name and not self.name:
            self._remove_fields_case_insensitively(record, ["Name", "%UTF8%Name"])
        else:
            write_dynamic_string_field(
                serializer,
                record,
                Fields.NAME,
                self.name,
                raw_record=raw_record,
                used_utf8_sidecar=self._used_utf8_name,
                was_present=self._has_name,
                force=self.name != self._source_name,
            )
        self._serialize_managed_font_id(
            record,
            serializer,
            Fields.FONT_ID.canonical,
            self.font_id,
            self._get_fallback_font_manager(),
        )
        self._write_port_int(
            record,
            serializer,
            raw_record,
            field=Fields.IO_TYPE,
            value=int(self.io_type),
            source_value=int(self._source_io_type),
            was_present=self._has_io_type,
        )
        self._write_port_int(
            record,
            serializer,
            raw_record,
            field=Fields.ALIGNMENT,
            value=int(self.alignment),
            source_value=int(self._source_alignment),
            was_present=self._has_alignment,
            authored_nonzero=True,
        )

    def _serialize_port_dimensions(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        width = (self.width, self._width_frac)
        if (
            self._has_width
            or width != self._source_width
            or (raw_record is None and width != (0, 0))
        ):
            serializer.write_coord(
                record,
                "Width",
                "",
                self.width,
                self._width_frac,
                raw_record,
                force=width != self._source_width,
            )
        else:
            serializer.remove_field(record, Fields.WIDTH)
        height = (self.height, self._height_frac)
        if (
            self._has_height
            or height != self._source_height
            or (raw_record is None and height != (0, 0))
        ):
            serializer.write_coord(
                record,
                "Height",
                "",
                self.height,
                self._height_frac,
                raw_record,
                force=height != self._source_height or self._height_was_clamped,
            )
            if self._height_frac == 0:
                serializer.remove_field(record, "Height_Frac")
        else:
            serializer.remove_field(record, Fields.HEIGHT)
            serializer.remove_field(record, "Height_Frac")
        self._write_port_color(
            record,
            serializer,
            raw_record,
            field=Fields.TEXT_COLOR,
            value=self.text_color,
            source_value=self._source_text_color,
            was_present=self._has_text_color,
            authored_nonzero=True,
        )
        self._write_port_int(
            record,
            serializer,
            raw_record,
            field=Fields.BORDER_WIDTH,
            value=int(self.border_width),
            source_value=int(self._source_border_width),
            was_present=self._has_border_width,
        )

    def _write_port_int(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
        *,
        field: FieldDef | str,
        value: int,
        source_value: int,
        was_present: bool,
        authored_nonzero: bool = False,
    ) -> None:
        if value == 0:
            if raw_record is None or not was_present or value != source_value:
                serializer.remove_field(record, field)
            return
        should_write = (
            was_present
            or value != source_value
            or (authored_nonzero and raw_record is None and value != 0)
        )
        if should_write:
            serializer.write_int(
                record, field, value, raw_record, force=value != source_value
            )
        else:
            serializer.remove_field(record, field)

    @staticmethod
    def _write_port_color(
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
        *,
        field: FieldDef | str,
        value: int,
        source_value: int,
        was_present: bool,
        authored_nonzero: bool = False,
    ) -> None:
        if value == 0:
            if raw_record is None or not was_present or value != source_value:
                serializer.remove_field(record, field)
            return
        should_write = (
            was_present
            or value != source_value
            or (authored_nonzero and raw_record is None)
        )
        if should_write:
            serializer.write_color(
                record, field, value, raw_record, force=value != source_value
            )
        else:
            serializer.remove_field(record, field)

    def _serialize_port_style(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self._has_style or self.style != self._source_style:
            serializer.write_int(
                record,
                Fields.STYLE,
                int(self.style),
                raw_record,
                force=self.style != self._source_style,
            )
        else:
            serializer.remove_field(record, Fields.STYLE)
        if self._has_auto_size or self.auto_size:
            serializer.write_bool(
                record,
                Fields.AUTO_SIZE,
                self.auto_size,
                raw_record,
                force=self.auto_size,
            )
        else:
            serializer.remove_field(record, Fields.AUTO_SIZE)
        write_dynamic_string_field(
            serializer,
            record,
            Fields.HARNESS_TYPE,
            self.harness_type,
            raw_record=raw_record,
            used_utf8_sidecar=self._used_utf8_harness_type,
            was_present=self._has_harness_type,
            force=self.harness_type != self._source_harness_type,
        )

    def _serialize_port_options(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self._has_show_net_name or self.show_net_name != self._source_show_net_name:
            serializer.write_bool(
                record,
                Fields.PORT_NAME_IS_HIDDEN,
                not self.show_net_name,
                raw_record,
                force=self.show_net_name != self._source_show_net_name,
            )
        else:
            serializer.remove_field(record, Fields.PORT_NAME_IS_HIDDEN)
        write_dynamic_string_field(
            serializer,
            record,
            Fields.OBJECT_DEFINITION_ID,
            self.object_definition_id,
            raw_record=raw_record,
            used_utf8_sidecar=self._used_utf8_object_definition_id,
            was_present=self._has_object_definition_id,
            force=self.object_definition_id != self._source_object_definition_id,
        )

    @staticmethod
    def _remove_nonpersisted_port_fields(
        record: _RecordFields, serializer: AltiumSerializer
    ) -> None:
        for field in (
            Fields.TEXT,
            Fields.CROSS_REFERENCE,
            Fields.CONNECTED_END,
            Fields.OVERRIDE_DISPLAY_STRING,
            Fields.SHOW_NET_NAME,
            Fields.HARNESS_COLOR,
        ):
            serializer.remove_field(record, field)

    @property
    def width_mils(self) -> float:
        """
        Public port width helper expressed in mils.

        The persisted port width field uses native 10-mil units, so this helper
        converts to the public mil-based API surface.
        """
        return self.width * 10.0 + self._width_frac / 10000.0

    @width_mils.setter
    def width_mils(self, value: int | float) -> None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("width_mils must be numeric")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("width_mils must be finite and greater than zero")
        point = CoordPoint.from_mils(float(value), 0.0)
        self.width = point.x
        self._width_frac = point.x_frac

    @property
    def height_mils(self) -> float:
        """
        Public port height helper expressed in mils.

        The persisted port height field uses native 10-mil units, so this helper
        converts to the public mil-based API surface.
        """
        return self.height * 10.0 + self._height_frac / 10000.0

    @height_mils.setter
    def height_mils(self, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("height_mils must be a number")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError("height_mils must be finite and greater than zero")
        point = CoordPoint.from_mils(numeric, 0.0)
        self.height = point.x
        self._height_frac = point.x_frac

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

        x, y = ctx.transform_coord_precise(self.location)
        width = self.width * ctx.scale
        height = self.height * ctx.scale
        arrow_depth = height / 2
        is_vertical = self._is_vertical_style()
        harness_color = ctx.harness_port_colors.get(str(self.unique_id or ""))
        is_harness_port = bool(self.harness_type) or harness_color is not None
        effective_harness_color = (
            int(harness_color)
            if harness_color is not None
            else int(self.harness_color or 0)
        )

        arrow_style = self._resolve_arrow_style(is_harness_port, ctx)
        polygon_points = self._build_polygon_points(
            x, y, width, arrow_depth, arrow_style
        )
        geometry_polygon = self._geometry_polygon(
            polygon_points,
            svg_coord_to_geometry,
            ctx,
            units_per_px,
        )
        connection_x, connection_y = self._connection_anchor_from_polygon(
            polygon_points, x, y, width, is_vertical
        )
        connection_point = svg_coord_to_geometry(
            connection_x,
            connection_y,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
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
                    arrow_depth,
                    is_vertical,
                    is_harness_port,
                    units_per_px,
                    make_font_payload,
                    make_text_with_overline_operations,
                    split_overline_text,
                )
            )

        half_height = float(self.height) / 2.0
        if is_vertical:
            bounds = SchGeometryBounds(
                left=int(round((float(self.location.x) - half_height) * 100000)),
                top=int(round((float(self.location.y) + float(self.width)) * 100000)),
                right=int(round((float(self.location.x) + half_height) * 100000)),
                bottom=int(round(float(self.location.y) * 100000)),
            )
        else:
            bounds = SchGeometryBounds(
                left=int(round(float(self.location.x) * 100000)),
                top=int(round((float(self.location.y) + half_height) * 100000)),
                right=int(round((float(self.location.x) + float(self.width)) * 100000)),
                bottom=int(round((float(self.location.y) - half_height) * 100000)),
            )
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="port",
            object_id="ePort",
            bounds=bounds,
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
            extras={
                "connection_points": [
                    {
                        "id": "port-hotspot",
                        "kind": "connection",
                        "role": "ratsnest-anchor",
                        "point": [connection_point[0], connection_point[1]],
                        "source_kind": "port_hotspot",
                    }
                ]
            },
        )

    def _connection_anchor_from_polygon(
        self,
        polygon_points: list[tuple[float, float]],
        x: float,
        y: float,
        width: float,
        is_vertical: bool,
    ) -> tuple[float, float]:
        computed_end = int(getattr(self, "_computed_connected_end", 0) or 0)
        if is_vertical:
            if computed_end == 2:
                return (x, y - width)
            return (x, y)
        if not polygon_points:
            return (float(self.location.x), float(self.location.y))
        min_x = min(point[0] for point in polygon_points)
        max_x = max(point[0] for point in polygon_points)
        min_y = min(point[1] for point in polygon_points)
        max_y = max(point[1] for point in polygon_points)
        center_y = (min_y + max_y) / 2.0
        left_count = sum(1 for point in polygon_points if abs(point[0] - min_x) < 1e-6)
        right_count = sum(1 for point in polygon_points if abs(point[0] - max_x) < 1e-6)
        if left_count == 1 and right_count != 1:
            return (min_x, center_y)
        if right_count == 1 and left_count != 1:
            return (max_x, center_y)
        if computed_end == 1:
            return (min_x, center_y)
        if computed_end == 2:
            return (max_x, center_y)
        return (min_x, center_y)

    def _is_vertical_style(self) -> bool:
        return self.style in {
            PortStyle.NONE_VERTICAL,
            PortStyle.TOP,
            PortStyle.BOTTOM,
            PortStyle.TOP_BOTTOM,
        }

    def _resolve_arrow_style(
        self, is_harness_port: bool, ctx: "SchSvgRenderContext"
    ) -> PortStyle:
        if self._is_vertical_style():
            return self._resolve_vertical_arrow_style(is_harness_port, ctx)
        return self._resolve_horizontal_arrow_style(is_harness_port, ctx)

    def _resolve_horizontal_arrow_style(
        self, is_harness_port: bool, ctx: "SchSvgRenderContext"
    ) -> PortStyle:
        if is_harness_port:
            return PortStyle.LEFT_RIGHT

        ce = int(getattr(self, "_computed_connected_end", 0) or 0)
        is_on_left = float(self.location.x) < (float(ctx.sheet_width or 0.0) / 2.0)
        if self.io_type == PortIOType.BIDIRECTIONAL:
            return PortStyle.LEFT_RIGHT
        if self.io_type == PortIOType.INPUT and ce == 3:
            return PortStyle.LEFT_RIGHT
        if self.io_type == PortIOType.OUTPUT and ce == 1:
            return PortStyle.RIGHT
        if self.io_type == PortIOType.INPUT and ce == 2:
            return PortStyle.RIGHT
        if self.io_type == PortIOType.OUTPUT and ce == 0 and not is_on_left:
            return PortStyle.RIGHT
        if self.io_type == PortIOType.INPUT and ce == 0 and is_on_left:
            return PortStyle.RIGHT
        if self.io_type == PortIOType.INPUT and ce == 1:
            return PortStyle.LEFT
        if self.io_type == PortIOType.OUTPUT and ce == 2:
            return PortStyle.LEFT
        if self.io_type == PortIOType.INPUT and ce == 0 and not is_on_left:
            return PortStyle.LEFT
        if self.io_type == PortIOType.OUTPUT and ce == 0 and is_on_left:
            return PortStyle.LEFT
        if self.io_type == PortIOType.OUTPUT and ce == 3:
            return PortStyle.NONE_HORIZONTAL
        return self.style

    def _resolve_vertical_arrow_style(
        self, is_harness_port: bool, ctx: "SchSvgRenderContext"
    ) -> PortStyle:
        if is_harness_port:
            return PortStyle.TOP_BOTTOM

        ce = int(getattr(self, "_computed_connected_end", 0) or 0)
        is_on_top = float(self.location.y) > (float(ctx.sheet_height or 0.0) / 2.0)
        if self.io_type == PortIOType.BIDIRECTIONAL:
            return PortStyle.TOP_BOTTOM
        if self.io_type == PortIOType.INPUT and ce == 3:
            return PortStyle.TOP_BOTTOM
        if self.io_type == PortIOType.OUTPUT and ce == 1:
            return PortStyle.TOP
        if self.io_type == PortIOType.INPUT and ce == 2:
            return PortStyle.TOP
        if self.io_type == PortIOType.OUTPUT and ce == 0 and is_on_top:
            return PortStyle.TOP
        if self.io_type == PortIOType.INPUT and ce == 0 and not is_on_top:
            return PortStyle.TOP
        if self.io_type == PortIOType.INPUT and ce == 1:
            return PortStyle.BOTTOM
        if self.io_type == PortIOType.OUTPUT and ce == 2:
            return PortStyle.BOTTOM
        if self.io_type == PortIOType.INPUT and ce == 0 and is_on_top:
            return PortStyle.BOTTOM
        if self.io_type == PortIOType.OUTPUT and ce == 0 and not is_on_top:
            return PortStyle.BOTTOM
        if self.io_type == PortIOType.OUTPUT and ce == 3:
            return PortStyle.NONE_VERTICAL
        return self.style

    def _build_polygon_points(
        self,
        x: float,
        y: float,
        width: float,
        arrow_depth: float,
        arrow_style: PortStyle,
    ) -> list[tuple[float, float]]:
        if arrow_style == PortStyle.NONE_VERTICAL:
            return [
                (x + arrow_depth, y),
                (x - arrow_depth, y),
                (x - arrow_depth, y - width),
                (x + arrow_depth, y - width),
            ]
        if arrow_style == PortStyle.TOP:
            return [
                (x + arrow_depth, y),
                (x - arrow_depth, y),
                (x - arrow_depth, y - width + arrow_depth),
                (x, y - width),
                (x + arrow_depth, y - width + arrow_depth),
            ]
        if arrow_style == PortStyle.BOTTOM:
            return [
                (x + arrow_depth, y - arrow_depth),
                (x, y),
                (x - arrow_depth, y - arrow_depth),
                (x - arrow_depth, y - width),
                (x + arrow_depth, y - width),
            ]
        if arrow_style == PortStyle.TOP_BOTTOM:
            return [
                (x + arrow_depth, y - arrow_depth),
                (x, y),
                (x - arrow_depth, y - arrow_depth),
                (x - arrow_depth, y - width + arrow_depth),
                (x, y - width),
                (x + arrow_depth, y - width + arrow_depth),
            ]
        if arrow_style == PortStyle.NONE_HORIZONTAL:
            return [
                (x, y + arrow_depth),
                (x, y - arrow_depth),
                (x + width, y - arrow_depth),
                (x + width, y + arrow_depth),
            ]
        if arrow_style == PortStyle.RIGHT:
            return [
                (x, y + arrow_depth),
                (x, y - arrow_depth),
                (x + width - arrow_depth, y - arrow_depth),
                (x + width, y),
                (x + width - arrow_depth, y + arrow_depth),
            ]
        if arrow_style == PortStyle.LEFT:
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
        from .altium_sch_geometry_oracle import _geometry_item_length

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
                        width=_geometry_item_length(
                            {0: 0.0, 1: 1.0, 2: 3.0, 3: 5.0}.get(
                                self.border_width, 1.0
                            ),
                            units_per_px=units_per_px,
                        ),
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
        arrow_depth: float,
        is_vertical: bool,
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
        clean_text, _ = split_overline_text(
            text_to_render,
            single_slash_negation=ctx.options.single_slash_negation,
        )
        text_width = measure_text_width(
            clean_text, font_size_for_width, font_name, bold=is_bold, italic=is_italic
        )
        font_spec = (
            ctx.font_manager.get_font_info(self.font_id) if ctx.font_manager else None
        )
        geometry_step = float(int(font_size_px))
        rotation_deg = -90.0 if is_vertical else 0.0
        if is_vertical:
            native_font_size = (
                float(font_spec.get("size", font_size_px))
                if font_spec
                else font_size_px
            )
            text_x = x - float(int(native_font_size) // 2) + geometry_step
            text_y = self._vertical_port_text_y(y, width, text_width, ctx.scale)
        else:
            text_x = self._port_text_x(x, width, text_width, ctx.scale)

        font_payload = make_font_payload(
            name=str(font_spec.get("name", font_name)) if font_spec else str(font_name),
            size_px=font_size_px,
            units_per_px=units_per_px,
            rotation=rotation_deg,
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
            rotation_deg=rotation_deg,
            geometry_step_px=geometry_step,
            single_slash_negation=ctx.options.single_slash_negation,
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

    def _vertical_port_text_y(
        self, y: float, width: float, text_width: float, scale: float
    ) -> float:
        text_margin = 10.0 * scale
        if self.alignment == SchHorizontalAlign.LEFT:
            return y - width + text_margin + text_width
        if self.alignment == SchHorizontalAlign.RIGHT:
            return y - text_margin
        return y - width / 2.0 + text_width / 2.0
