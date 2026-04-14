"""Schematic record model for SchRecordType.DESIGNATOR."""

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    CoordPoint,
    ReadOnlyState,
    SchPrimitive,
    SchRecordType,
    TextJustification,
    TextOrientation,
    color_to_hex,
    rgb_to_win32_color,
)
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    detect_case_mode_method_from_dotted_uppercase_fields,
)
from .altium_sch_svg_renderer import SchSvgRenderContext
from .altium_text_metrics import (
    get_baseline_offset,
    measure_text_height,
    measure_text_width,
)


class AltiumSchDesignator(SingleFontBindableRecordMixin, SchPrimitive):
    """
    Altium component designator record.

    The reference designator text (e.g., "U1", "R5").
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        self.location = CoordPoint()
        self.name: str = "Designator"
        self.text: str = "U?"
        self.font_id: int = 1
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.justification: TextJustification = TextJustification.BOTTOM_LEFT
        self.is_hidden: bool = False
        self.is_mirrored: bool = False
        self.color: int | None = None  # None means not specified
        self.read_only_state: ReadOnlyState = ReadOnlyState.NONE
        self.auto_position: bool = True
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
        self._has_read_only_state: bool = False
        self._has_auto_position: bool = False

    @staticmethod
    def _sanitize_text(text: str) -> str:
        return text.replace("\b", "")

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.DESIGNATOR

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

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()
        r = self._record

        # Parse location with presence tracking
        loc_x, loc_x_frac, self._has_location_x = s.read_coord(record, "Location", "X")
        loc_y, loc_y_frac, self._has_location_y = s.read_coord(record, "Location", "Y")
        self.location = CoordPoint(loc_x, loc_y, loc_x_frac, loc_y_frac)

        # Core fields
        self.name, self._has_name = s.read_str(
            record, Fields.NAME, default="Designator"
        )
        self.name = "Designator"
        text_value, self._has_text, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT,
            default="U?",
        )
        self.text = self._sanitize_text(text_value)
        # Use read_font_id for translation support
        self.font_id, self._has_font_id = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=1
        )
        orient_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = TextOrientation(orient_val)
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
        self.color = color_val if has_color else None

        # ReadOnlyState (0=None, 1=Name, 2=Value, 3=NameAndValue)
        ro_val, self._has_read_only_state = s.read_int(
            record, Fields.READ_ONLY_STATE, default=0
        )
        if self._has_read_only_state:
            self.read_only_state = ReadOnlyState(ro_val)

        not_auto_position, self._has_auto_position = s.read_bool(
            record,
            Fields.NOT_AUTO_POSITION,
            default=False,
        )
        self.auto_position = not not_auto_position

        override_not_auto_position, has_override_not_auto_position = s.read_bool(
            record,
            Fields.OVERRIDE_NOT_AUTO_POSITION,
            default=False,
        )
        if has_override_not_auto_position:
            self._has_auto_position = True
            self.auto_position = not override_not_auto_position

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Location - only write if present or non-zero
        if self._has_location_x or self.location.x != 0:
            s.write_coord(
                record, "Location", "X", self.location.x, self.location.x_frac, raw
            )
        if self._has_location_y or self.location.y != 0:
            s.write_coord(
                record, "Location", "Y", self.location.y, self.location.y_frac, raw
            )

        s.write_str(record, Fields.NAME, "Designator", raw)

        # Text and font
        text_value = self._sanitize_text(self.text)
        if self._has_text or text_value:
            s.write_str(record, Fields.TEXT, text_value, raw)
        else:
            s.remove_field(record, Fields.TEXT)
        if self._has_font_id or self.font_id != 1:
            s.write_int(record, Fields.FONT_ID, self.font_id, raw, force=True)
        else:
            s.remove_field(record, Fields.FONT_ID)
        if self._has_orientation or self.orientation != TextOrientation.DEGREES_0:
            s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)
        else:
            s.remove_field(record, Fields.ORIENTATION)
        if (
            self._has_justification
            or self.justification != TextJustification.BOTTOM_LEFT
        ):
            s.write_int(record, Fields.JUSTIFICATION, self.justification.value, raw)
        else:
            s.remove_field(record, Fields.JUSTIFICATION)

        # Color field
        if self.color is not None:
            s.write_int(record, Fields.COLOR, self.color, raw, force=True)
        else:
            s.remove_field(record, Fields.COLOR)

        # Boolean fields
        if self.is_hidden:
            s.write_bool(record, Fields.IS_HIDDEN, self.is_hidden, raw)
        else:
            s.remove_field(record, Fields.IS_HIDDEN)
        if self.is_mirrored:
            s.write_bool(record, Fields.IS_MIRRORED, self.is_mirrored, raw)
        else:
            s.remove_field(record, Fields.IS_MIRRORED)

        # ReadOnlyState - only serialize if present or non-default
        if self._has_read_only_state or self.read_only_state != ReadOnlyState.NONE:
            s.write_int(record, Fields.READ_ONLY_STATE, self.read_only_state.value, raw)
        else:
            s.remove_field(record, Fields.READ_ONLY_STATE)

        if not self.auto_position:
            s.write_bool(
                record, Fields.OVERRIDE_NOT_AUTO_POSITION, True, raw, force=True
            )
            s.remove_field(record, Fields.NOT_AUTO_POSITION)
        else:
            s.remove_field(record, Fields.OVERRIDE_NOT_AUTO_POSITION)
            s.remove_field(record, Fields.NOT_AUTO_POSITION)

        return record

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord":
        """
        Build the geometry record emitted for this designator.
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
                self.text,
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
                    text=self.text,
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
                kind="designator",
                object_id="eDesignator",
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
            kind="designator",
            object_id="eDesignator",
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
