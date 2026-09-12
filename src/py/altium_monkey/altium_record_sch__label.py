"""Schematic record model for SchRecordType.LABEL."""

import math
from functools import partial
from typing import TYPE_CHECKING, cast

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
from ._sch_managed_defaults import GRAPHICAL_FILL_COLOR, TEXT_COLOR
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    _RecordFields,
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
        self.color = TEXT_COLOR
        self.area_color = GRAPHICAL_FILL_COLOR
        self._init_family_dynamic_unique_id()
        self._init_single_font_binding()
        self.text: str = "Text"
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
        self._used_utf8_url: bool = False
        self._capture_graphical_source_state()
        self._capture_label_source_state()

    def _capture_label_source_state(self) -> None:
        self._source_text = self.text
        self._source_font_id = int(self.font_id)
        self._source_orientation = self.orientation
        self._source_justification = self.justification
        self._source_is_mirrored = self.is_mirrored
        self._source_url = self.url

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.LABEL

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

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()
        r = self._record
        self._parse_family_dynamic_unique_id(s, record)

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
        self.url, self._has_url, self._used_utf8_url = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.URL,
            default="",
        )
        self.is_hidden = False
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()
        self._capture_label_source_state()

    def serialize_to_record(self) -> _RecordFields:
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
        self._serialize_managed_family_color(
            record, s, Fields.COLOR.canonical, int(self.color or 0)
        )
        self._serialize_label_text(record, s, raw)
        self._serialize_label_style(record, s, raw)
        self._serialize_label_options(record, s, raw)
        self._normalize_synthesized_label_fields(record, s)
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Orientation",
                "Justification",
                "Color",
                "FontID",
                "Text",
                "IsMirrored",
                "URL",
                "UniqueID",
            ),
        )

    def _serialize_label_text(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self._has_text or self.text != self._source_text or raw_record is None:
            if self.text != self._source_text and not self.text:
                self._remove_fields_case_insensitively(record, ["Text", "%UTF8%Text"])
            else:
                write_dynamic_string_field(
                    serializer,
                    record,
                    Fields.TEXT,
                    self.text,
                    raw_record=raw_record,
                    used_utf8_sidecar=self._used_utf8_text,
                    was_present=self._has_text,
                    force=self.text != self._source_text,
                )
        else:
            write_dynamic_string_field(
                serializer,
                record,
                Fields.TEXT,
                "",
                raw_record=raw_record,
                used_utf8_sidecar=False,
                was_present=False,
            )

    def _serialize_label_style(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        font_id = cast(int, self.font_id)

        self._serialize_managed_font_id(
            record,
            serializer,
            Fields.FONT_ID.canonical,
            font_id,
            self._get_fallback_font_manager(),
        )
        self._serialize_managed_family_int(
            record,
            serializer,
            Fields.ORIENTATION.canonical,
            self.orientation.value,
        )
        self._serialize_managed_family_int(
            record,
            serializer,
            Fields.JUSTIFICATION.canonical,
            self.justification.value,
        )

    def _serialize_label_options(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        self._serialize_managed_family_bool(
            record,
            serializer,
            Fields.IS_MIRRORED.canonical,
            self.is_mirrored,
        )

        if self.url != self._source_url and not self.url:
            self._remove_fields_case_insensitively(record, ["URL", "%UTF8%URL"])
        else:
            write_dynamic_string_field(
                serializer,
                record,
                Fields.URL,
                self.url,
                raw_record=raw_record,
                used_utf8_sidecar=self._used_utf8_url,
                was_present=self._has_url,
                force=self.url != self._source_url,
            )

        if self.record_type not in {
            SchRecordType.SHEET_NAME,
            SchRecordType.FILE_NAME,
            SchRecordType.HARNESS_TYPE,
        }:
            serializer.remove_field(record, Fields.IS_HIDDEN)

    def _normalize_synthesized_label_fields(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
    ) -> None:
        if self._raw_record is None:
            if self.owner_index == 0:
                serializer.remove_field(record, Fields.OWNER_INDEX)
            if self.location.y == 0 and self.location.y_frac == 0:
                serializer.remove_field(record, Fields.LOCATION_Y)
                serializer.remove_field(record, Fields.LOCATION_Y_FRAC)

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
            make_font_payload,
            make_solid_brush,
            svg_coord_to_geometry,
        )

        if self.is_hidden or not self.text:
            return None

        display_text = ctx.substitute_parameters(self.text)
        if not display_text:
            return None

        anchor_x, anchor_y = ctx.transform_coord_precise(self.location)
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

            rotate_point = partial(
                rotate_point_about_origin,
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

        sheet_height = float(ctx.sheet_height or 0.0)
        geometry_x_units, geometry_y_units = svg_coord_to_geometry(
            geometry_x,
            geometry_y,
            sheet_height_px=sheet_height,
            units_per_px=units_per_px,
        )

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
                    x=geometry_x_units,
                    y=geometry_y_units,
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
                    brush=make_solid_brush(fill_color_raw),
                ),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.pop_transform(),
            ],
        )
