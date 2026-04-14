"""Schematic record model for SchRecordType.PARAMETER."""

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    CoordPoint,
    ReadOnlyState,
    SchPointMils,
    SchPrimitive,
    SchRecordType,
    TextJustification,
    TextOrientation,
    _set_record_location_mils,
    color_to_hex,
    rgb_to_win32_color,
)
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import detect_case_mode_method_from_uppercase_fields
from .altium_sch_svg_renderer import SchSvgRenderContext
from .altium_text_metrics import (
    get_baseline_offset,
    measure_text_height,
    measure_text_width,
)


class AltiumSchParameter(SingleFontBindableRecordMixin, SchPrimitive):
    """
    Altium component parameter record.

    Key-value metadata attached to components.
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        self.location = CoordPoint()
        self.name: str = ""
        self.text: str = ""
        self.font_id: int = 1
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.justification: TextJustification = TextJustification.BOTTOM_LEFT
        self.is_hidden: bool = False
        self.is_mirrored: bool = False
        self.color: int | None = None  # None means not specified
        self.param_type: int = 0
        self.show_name: bool = False
        self.read_only_state: ReadOnlyState = ReadOnlyState.NONE
        self.description: str = ""
        self.allow_library_synchronize: bool = True
        self.allow_database_synchronize: bool = True
        self.auto_position: bool = True
        self.text_horz_anchor: int = 0
        self.text_vert_anchor: int = 0
        self.is_image_parameter: bool = False
        # Track which fields were present
        self._has_location_x: bool = False
        self._has_location_y: bool = False
        self._has_name: bool = False
        self._has_text: bool = False
        self._has_font_id: bool = False
        self._has_orientation: bool = False
        self._has_justification: bool = False
        self._has_is_hidden: bool = False
        self._has_is_mirrored: bool = False
        self._has_param_type: bool = False
        self._has_show_name: bool = False
        self._has_read_only_state: bool = False
        self._has_description: bool = False
        self._has_allow_library_synchronize: bool = False
        self._has_allow_database_synchronize: bool = False
        self._has_auto_position: bool = False
        self._has_text_horz_anchor: bool = False
        self._has_text_vert_anchor: bool = False
        self._has_is_image_parameter: bool = False
        self._used_utf8_text: bool = False  # True if original used %UTF8%Text key

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PARAMETER

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse parameter from record.

                Args:
                   record: Source record dictionary
                    font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record)
        self._font_manager = font_manager
        self._public_font_spec = None
        s = AltiumSerializer()
        r = self._record  # Case-insensitive view (still needed for UTF8 handling)

        # Location (SchDoc uses TitleCase, SchLib uses UPPERCASE)
        x, x_frac, self._has_location_x = s.read_coord(record, "Location", "X")
        y, y_frac, self._has_location_y = s.read_coord(record, "Location", "Y")
        self.location = CoordPoint(x, y, x_frac, y_frac)

        # Name and Text
        self.name, self._has_name = s.read_str(record, Fields.NAME, default="")
        # Prefer %UTF8%Text for Unicode support (Greek letters like Omega, +/- , micro, deg, etc.)
        # Fall back to Text/TEXT for ASCII-only content
        self.text, self._has_text, self._used_utf8_text = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT,
            default="",
        )

        # Use read_font_id for translation support
        self.font_id, self._has_font_id = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=1
        )
        orientation_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = TextOrientation(orientation_val)
        justification_val, self._has_justification = s.read_int(
            record, Fields.JUSTIFICATION, default=0
        )
        self.justification = TextJustification(justification_val)

        self.is_hidden, self._has_is_hidden = s.read_bool(
            record, Fields.IS_HIDDEN, default=False
        )
        self.is_mirrored, self._has_is_mirrored = s.read_bool(
            record, Fields.IS_MIRRORED, default=False
        )

        # Color field
        color_val, has_color = s.read_int(record, Fields.COLOR, default=0)
        if has_color:
            self.color = color_val
        # Keep None if not present

        self.param_type, self._has_param_type = s.read_int(
            record, Fields.PARAM_TYPE, default=0
        )
        self.show_name, self._has_show_name = s.read_bool(
            record, Fields.SHOW_NAME, default=False
        )

        # ReadOnlyState (0=None, 1=Name, 2=Value, 3=NameAndValue)
        ro_val, self._has_read_only_state = s.read_int(
            record, Fields.READ_ONLY_STATE, default=0
        )
        if self._has_read_only_state:
            self.read_only_state = ReadOnlyState(ro_val)

        self.description, self._has_description = s.read_str(
            record, Fields.DESCRIPTION, default=""
        )

        not_allow_library_sync, self._has_allow_library_synchronize = s.read_bool(
            record,
            Fields.NOT_ALLOW_LIBRARY_SYNCHRONIZE,
            default=False,
        )
        self.allow_library_synchronize = not not_allow_library_sync

        not_allow_database_sync, self._has_allow_database_synchronize = s.read_bool(
            record,
            Fields.NOT_ALLOW_DATABASE_SYNCHRONIZE,
            default=False,
        )
        self.allow_database_synchronize = not not_allow_database_sync

        not_auto_position, self._has_auto_position = s.read_bool(
            record,
            Fields.NOT_AUTO_POSITION,
            default=False,
        )
        self.auto_position = not not_auto_position

        self.text_horz_anchor, self._has_text_horz_anchor = s.read_int(
            record,
            Fields.TEXT_HORZ_ANCHOR,
            default=0,
        )
        self.text_vert_anchor, self._has_text_vert_anchor = s.read_int(
            record,
            Fields.TEXT_VERT_ANCHOR,
            default=0,
        )
        self.is_image_parameter, self._has_is_image_parameter = s.read_bool(
            record,
            Fields.IS_IMAGE_PARAMETER,
            default=False,
        )

    def serialize_to_record(self) -> dict[str, Any]:
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        self._serialize_location_and_text_fields(record, s, raw)
        self._serialize_text_style_fields(record, s, raw)
        self._serialize_visibility_and_sync_fields(record, s, raw)
        self._serialize_anchor_fields(record, s, raw)
        return record

    @property
    def location_mils(self) -> SchPointMils:
        """
        Public parameter location helper expressed in mils.
        """
        return SchPointMils.from_mils(self.location.x_mils, self.location.y_mils)

    @location_mils.setter
    def location_mils(self, value: SchPointMils) -> None:
        return _set_record_location_mils(self, value)

    def _serialize_location_and_text_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw_record: dict[str, Any] | None,
    ) -> None:
        if self._has_location_x or self.location.x != 0:
            serializer.write_coord(
                record,
                "Location",
                "X",
                self.location.x,
                self.location.x_frac,
                raw_record,
            )
        if self._has_location_y or self.location.y != 0:
            serializer.write_coord(
                record,
                "Location",
                "Y",
                self.location.y,
                self.location.y_frac,
                raw_record,
            )

        if self._has_name or self.name:
            serializer.write_str(record, Fields.NAME, self.name, raw_record)
        if self._has_text or self.text:
            write_dynamic_string_field(
                serializer,
                record,
                Fields.TEXT,
                self.text,
                raw_record=raw_record,
                used_utf8_sidecar=self._used_utf8_text,
                was_present=self._has_text,
            )
        if self.name.lower() == "probevaluedisplay":
            write_dynamic_string_field(
                serializer,
                record,
                Fields.TEXT,
                "",
                raw_record=raw_record,
                used_utf8_sidecar=self._used_utf8_text,
                was_present=True,
            )

    def _serialize_text_style_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw_record: dict[str, Any] | None,
    ) -> None:
        if self._has_font_id or self.font_id != 1:
            serializer.write_int(
                record, Fields.FONT_ID, self.font_id, raw_record, force=True
            )
        if self._has_orientation or self.orientation != TextOrientation.DEGREES_0:
            serializer.write_int(
                record, Fields.ORIENTATION, self.orientation.value, raw_record
            )
        if (
            self._has_justification
            or self.justification != TextJustification.BOTTOM_LEFT
        ):
            serializer.write_int(
                record, Fields.JUSTIFICATION, self.justification.value, raw_record
            )

        if self.color is not None:
            serializer.write_int(
                record, Fields.COLOR, self.color, raw_record, force=True
            )

    def _serialize_visibility_and_sync_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw_record: dict[str, Any] | None,
    ) -> None:
        if self.is_hidden:
            serializer.write_bool(record, Fields.IS_HIDDEN, True, raw_record)
        else:
            serializer.remove_field(record, Fields.IS_HIDDEN)

        if self.is_mirrored:
            serializer.write_bool(record, Fields.IS_MIRRORED, True, raw_record)
        else:
            serializer.remove_field(record, Fields.IS_MIRRORED)

        if self._has_param_type or self.param_type != 0:
            serializer.write_int(record, Fields.PARAM_TYPE, self.param_type, raw_record)

        if self.show_name:
            serializer.write_bool(record, Fields.SHOW_NAME, True, raw_record)
        else:
            serializer.remove_field(record, Fields.SHOW_NAME)

        if self._has_read_only_state or self.read_only_state != ReadOnlyState.NONE:
            serializer.write_int(
                record, Fields.READ_ONLY_STATE, self.read_only_state.value, raw_record
            )

        if self._has_description or self.description:
            serializer.write_str(
                record, Fields.DESCRIPTION, self.description, raw_record
            )
        else:
            serializer.remove_field(record, Fields.DESCRIPTION)

        if not self.allow_library_synchronize:
            serializer.write_bool(
                record, Fields.NOT_ALLOW_LIBRARY_SYNCHRONIZE, True, raw_record
            )
        else:
            serializer.remove_field(record, Fields.NOT_ALLOW_LIBRARY_SYNCHRONIZE)

        if not self.allow_database_synchronize:
            serializer.write_bool(
                record, Fields.NOT_ALLOW_DATABASE_SYNCHRONIZE, True, raw_record
            )
        else:
            serializer.remove_field(record, Fields.NOT_ALLOW_DATABASE_SYNCHRONIZE)

        if not self.auto_position:
            serializer.write_bool(record, Fields.NOT_AUTO_POSITION, True, raw_record)
        else:
            serializer.remove_field(record, Fields.NOT_AUTO_POSITION)

    def _serialize_anchor_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw_record: dict[str, Any] | None,
    ) -> None:
        if self._has_text_horz_anchor or self.text_horz_anchor != 0:
            serializer.write_int(
                record,
                Fields.TEXT_HORZ_ANCHOR,
                self.text_horz_anchor,
                raw_record,
            )
        else:
            serializer.remove_field(record, Fields.TEXT_HORZ_ANCHOR)

        if self._has_text_vert_anchor or self.text_vert_anchor != 0:
            serializer.write_int(
                record,
                Fields.TEXT_VERT_ANCHOR,
                self.text_vert_anchor,
                raw_record,
            )
        else:
            serializer.remove_field(record, Fields.TEXT_VERT_ANCHOR)

        if self.is_image_parameter:
            serializer.write_bool(record, Fields.IS_IMAGE_PARAMETER, True, raw_record)
        else:
            serializer.remove_field(record, Fields.IS_IMAGE_PARAMETER)

    def to_geometry(
        self,
        ctx: SchSvgRenderContext | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord | None":
        """
        Build the geometry record emitted for this parameter.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_font_payload,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if ctx is not None and not self.is_hidden and self.text:
            display_text = ctx.substitute_parameters(self.text)
            if not display_text:
                return None
            baseline_x, baseline_y = ctx.transform_coord_precise(self.location)
            baseline_x, baseline_y = round(baseline_x, 3), round(baseline_y, 3)

            fill_raw = int(self.color) if self.color is not None else 0
            fill_hex = color_to_hex(fill_raw)
            fill_hex = ctx.apply_compile_mask_color(
                fill_hex, ctx.component_compile_masked is True
            )
            fill_raw = rgb_to_win32_color(
                int(fill_hex[1:3], 16),
                int(fill_hex[3:5], 16),
                int(fill_hex[5:7], 16),
            )
            angle = self.orientation.value * 90
            rotation_deg = float(-angle)

            font_name, font_size_px, is_bold, is_italic, is_underline = (
                ctx.get_font_info(self.font_id)
            )
            font_size_for_width = ctx.get_font_size_for_width(self.font_id)
            text_width = measure_text_width(
                display_text,
                font_size_for_width,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )
            text_height = measure_text_height(
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
                use_altium_algorithm=False,
            )
            baseline_offset = get_baseline_offset(font_size_px, font_name)

            justification = self.justification.value
            h_align = justification % 3
            v_align = justification // 3

            h_offset = 0.0
            if h_align == 1:
                h_offset = text_width / 2.0
            elif h_align == 2:
                h_offset = text_width

            v_offset = 0.0
            if v_align == 0:
                v_offset = baseline_offset
            elif v_align == 1:
                v_offset = -(text_height / 2.0 - baseline_offset)
            elif v_align == 2:
                v_offset = -(text_height - baseline_offset)

            if angle == 0:
                baseline_x -= h_offset
                baseline_y -= v_offset
            elif angle == 90:
                baseline_y += h_offset
                baseline_x -= v_offset
            elif angle == 180:
                baseline_x += h_offset
                baseline_y += v_offset
            elif angle == 270:
                baseline_y -= h_offset
                baseline_x += v_offset

            point_size = float(int(font_size_px))

            theta = math.radians(rotation_deg)
            geometry_x_px = baseline_x + point_size * math.sin(theta)
            geometry_y_px = baseline_y - point_size * math.cos(theta)
            geometry_x, geometry_y = svg_coord_to_geometry(
                geometry_x_px,
                geometry_y_px,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )

            operations = [
                SchGeometryOp.string(
                    x=geometry_x,
                    y=geometry_y,
                    text=display_text,
                    font=make_font_payload(
                        name=font_name,
                        size_px=font_size_px,
                        units_per_px=units_per_px,
                        rotation=rotation_deg,
                        underline=is_underline,
                        italic=is_italic,
                        bold=is_bold,
                        strikeout=False,
                    ),
                    brush=make_solid_brush(fill_raw),
                )
            ]
            if not wrap_record:
                return operations

            return SchGeometryRecord(
                handle=f"{document_id}\\{self.unique_id}",
                unique_id=self.unique_id,
                kind="parameter",
                object_id="eParameter",
                bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
                operations=wrap_record_operations(
                    self.unique_id,
                    operations,
                    units_per_px=units_per_px,
                ),
            )

        if not wrap_record:
            return []
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="parameter",
            object_id="eParameter",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=[
                SchGeometryOp.push_transform([1, 0, 0, 1, 0, -(units_per_px * 1000)]),
                SchGeometryOp.begin_group(),
                SchGeometryOp.begin_group("DocumentMainGroup"),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.pop_transform(),
            ],
        )

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields
