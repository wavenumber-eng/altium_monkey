"""Schematic record model for SchRecordType.LABEL."""

import math
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    IntField,
    SchGraphicalObject,
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
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    detect_case_mode_method_from_dotted_uppercase_fields,
    rotate_point_about_origin,
)
from .altium_sch_svg_renderer import SchSvgRenderContext
from .altium_text_metrics import (
    get_baseline_offset,
    measure_text_height,
    measure_text_width,
)


class AltiumSchLabel(SingleFontBindableRecordMixin, SchGraphicalObject):
    """
    Altium text label record.

    Common text element in symbols with font, orientation, and justification.

    font_id enforces integer type at assignment.
    """

    # Integer fields with type enforcement
    font_id = IntField(default=1)

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        self.text: str = ""
        self.font_id = 1  # Descriptor handles type enforcement
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.justification: TextJustification = TextJustification.BOTTOM_LEFT
        self.is_mirrored: bool = False
        self.is_hidden: bool = False
        self.url: str = ""
        # Track which fields were present
        self._has_text: bool = False
        self._has_font_id: bool = False
        self._has_orientation: bool = False
        self._has_justification: bool = False
        self._has_url: bool = False
        self._used_utf8_text: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.LABEL

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

        # Parse text fields with presence tracking
        self.text, self._has_text, self._used_utf8_text = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT,
            default="",
        )
        # Use read_font_id for translation support
        font_id_val, self._has_font_id = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=1
        )
        self.font_id = font_id_val  # Descriptor handles int conversion

        orientation_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = TextOrientation(orientation_val)

        justification_val, self._has_justification = s.read_int(
            record, Fields.JUSTIFICATION, default=0
        )
        self.justification = TextJustification(justification_val)

        # Parse boolean properties
        self.is_mirrored, _ = s.read_bool(record, Fields.IS_MIRRORED, default=False)
        self.url, self._has_url = s.read_str(record, Fields.URL, default="")
        self.is_hidden = False

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()

        # Determine case mode from raw record (if present)
        # SchLib uses UPPERCASE, SchDoc uses PascalCase
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Write text fields - only if present or non-default
        if self._has_text or self.text:
            write_dynamic_string_field(
                s,
                record,
                Fields.TEXT,
                self.text,
                raw_record=raw,
                used_utf8_sidecar=self._used_utf8_text,
                was_present=self._has_text,
            )
        else:
            write_dynamic_string_field(
                s,
                record,
                Fields.TEXT,
                "",
                raw_record=raw,
                used_utf8_sidecar=False,
                was_present=False,
            )
        font_id = cast(int, self.font_id)

        if self._has_font_id or font_id != 1:
            s.write_int(record, Fields.FONT_ID, font_id, raw)
        if self._has_orientation or self.orientation != TextOrientation.DEGREES_0:
            s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)
        if (
            self._has_justification
            or self.justification != TextJustification.BOTTOM_LEFT
        ):
            s.write_int(record, Fields.JUSTIFICATION, self.justification.value, raw)

        # Write boolean properties (only if True)
        if self.is_mirrored:
            s.write_bool(record, Fields.IS_MIRRORED, True, raw)
        else:
            s.remove_field(record, Fields.IS_MIRRORED)

        if self._has_url or self.url:
            s.write_str(record, Fields.URL, self.url, raw)
        else:
            s.remove_field(record, Fields.URL)

        # V5 label export/import does not persist IsHidden.
        s.remove_field(record, Fields.IS_HIDDEN)

        # For SchLib Labels (synthesis mode - no raw record), remove fields that
        # Altium doesn't include in its Make SchLib output
        if self._raw_record is None:
            # OwnerIndex is NOT exported for Labels in SchLib (when owner_index=0)
            # BUT for SchDoc SheetName/FileName, owner_index > 0 means it has a parent
            if self.owner_index == 0:
                s.remove_field(record, Fields.OWNER_INDEX)
            # Location.Y is NOT exported if it's 0
            if self.location.y == 0 and self.location.y_frac == 0:
                s.remove_field(record, Fields.LOCATION_Y)
                s.remove_field(record, Fields.LOCATION_Y_FRAC)

        return record

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for this label.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_solid_brush,
        )

        if self.is_hidden or not self.text:
            return None

        display_text = ctx.substitute_parameters(self.text)
        if not display_text:
            return None

        anchor_x, anchor_y = ctx.transform_coord_precise(self.location)
        anchor_x, anchor_y = round(anchor_x, 3), round(anchor_y, 3)
        baseline_x, baseline_y = anchor_x, anchor_y

        fill_color_raw = int(self.color) if self.color is not None else 0
        fill_hex = color_to_hex(fill_color_raw)
        fill_hex = ctx.apply_compile_mask_color(
            fill_hex, ctx.component_compile_masked is True
        )
        fill_color_raw = rgb_to_win32_color(
            int(fill_hex[1:3], 16),
            int(fill_hex[3:5], 16),
            int(fill_hex[5:7], 16),
        )

        angle = self.orientation.value * 90
        font_id = cast(int, self.font_id)
        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            font_id
        )
        font_size_for_width = ctx.get_font_size_for_width(font_id)
        line_height = ctx.get_font_line_height(font_id)

        justification = self.justification.value
        h_align = justification % 3
        v_align = justification // 3

        text_width = measure_text_width(
            display_text,
            font_size_for_width,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        baseline_offset = get_baseline_offset(font_size_px, font_name)
        text_height = measure_text_height(
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
            use_altium_algorithm=False,
        )

        h_offset = 0.0
        if h_align == 1:
            h_offset = text_width / 2.0
        elif h_align == 2:
            h_offset = text_width

        v_offset = 0.0
        if v_align == 0:
            v_offset = baseline_offset
        elif v_align == 1:
            v_offset = -(line_height / 2.0 - baseline_offset)
        elif v_align == 2:
            v_offset = -(line_height - baseline_offset)

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

        baseline_font_size = float(int(font_size_px))
        rotation_deg = float(-angle)
        theta = math.radians(rotation_deg)
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)

        if angle == 0 and h_align == 0 and v_align == 0:
            # Native LabelDrawGraphObject uses the label's own bounding rect
            # top-left plus DrawSingleLineText(TextAlignment.Bottom). For the
            # common non-rotated bottom-left case, GeometryMaker stores the
            # text operation at the text box top edge, not at a float baseline
            # derived from ascent/descent.
            geometry_x = anchor_x
            geometry_y = anchor_y - text_height
            corners = [
                (anchor_x, anchor_y - text_height),
                (anchor_x + text_width, anchor_y - text_height),
                (anchor_x + text_width, anchor_y),
                (anchor_x, anchor_y),
            ]
        else:
            # GeometryMaker stores string coordinates offset from the text baseline
            # by the truncated baseline font size in the rendered rotation direction.
            geometry_x = baseline_x + sin_theta * baseline_font_size
            geometry_y = baseline_y - cos_theta * baseline_font_size

            rotate_point = lambda px, py: rotate_point_about_origin(
                px,
                py,
                origin_x=baseline_x,
                origin_y=baseline_y,
                cos_theta=cos_theta,
                sin_theta=sin_theta,
            )

            unrotated_top = baseline_y - baseline_font_size
            corners = [
                rotate_point(baseline_x, unrotated_top),
                rotate_point(baseline_x + text_width, unrotated_top),
                rotate_point(baseline_x + text_width, unrotated_top + text_height),
                rotate_point(baseline_x, unrotated_top + text_height),
            ]
        min_x = min(point[0] for point in corners)
        max_x = max(point[0] for point in corners)
        min_y = min(point[1] for point in corners)
        max_y = max(point[1] for point in corners)

        font_payload = {
            "name": str(font_name),
            "size": float(font_size_px) * units_per_px,
            "rotation": rotation_deg,
            "underline": bool(is_underline),
            "italic": bool(is_italic),
            "bold": bool(is_bold),
            "strikeout": False,
        }

        sheet_height = float(ctx.sheet_height or 0.0)

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="label",
            object_id="eLabel",
            bounds=SchGeometryBounds(
                left=math.floor(min_x * 100000),
                top=math.floor((sheet_height - min_y) * 100000),
                right=math.ceil(max_x * 100000),
                bottom=math.ceil((sheet_height - max_y) * 100000),
            ),
            operations=[
                SchGeometryOp.push_transform([1, 0, 0, 1, 0, -(units_per_px * 1000)]),
                SchGeometryOp.begin_group(),
                SchGeometryOp.begin_group("DocumentMainGroup"),
                SchGeometryOp.begin_group(self.unique_id),
                SchGeometryOp.string(
                    x=geometry_x * units_per_px,
                    y=(geometry_y - sheet_height) * units_per_px + units_per_px * 1000,
                    text=display_text,
                    font=font_payload,
                    brush=make_solid_brush(fill_color_raw),
                ),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.pop_transform(),
            ],
        )
