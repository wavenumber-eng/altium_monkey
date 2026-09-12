"""Schematic record model for SchRecordType.PARAMETER."""

import math
import re
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import (
        SchGeometryBounds,
        SchGeometryOp,
        SchGeometryRecord,
    )

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
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key, dotnet_trim
from ._sch_managed_defaults import TEXT_COLOR
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    _RecordFields,
    detect_case_mode_method_from_uppercase_fields,
    serialize_present_coord_point,
)
from .altium_sch_svg_renderer import SchSvgRenderContext
from ._sch_source_admission import _SourceAdmission
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
        self.owner_part_id: int | None = -1
        self._init_family_dynamic_unique_id()
        self._init_single_font_binding()
        self.location = CoordPoint()
        self.name: str = ""
        self.text: str = "*"
        self.font_id: int = 1
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.justification: TextJustification = TextJustification.BOTTOM_LEFT
        self.is_hidden: bool = False
        self.is_mirrored: bool = False
        self.color: int | None = TEXT_COLOR
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
        self._has_color: bool = False
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
        self._used_utf8_description: bool = False
        self._capture_parameter_source_state()

    def _capture_parameter_source_state(self) -> None:
        self._source_name = self.name
        self._source_text = self.text
        self._source_font_id = self.font_id
        self._source_orientation = self.orientation
        self._source_justification = self.justification
        self._source_is_hidden = self.is_hidden
        self._source_is_mirrored = self.is_mirrored
        self._source_color = self.color
        self._source_param_type = self.param_type
        self._source_show_name = self.show_name
        self._source_read_only_state = self.read_only_state
        self._source_description = self.description
        self._source_allow_library_synchronize = self.allow_library_synchronize
        self._source_allow_database_synchronize = self.allow_database_synchronize
        self._source_auto_position = self.auto_position
        self._source_text_horz_anchor = self.text_horz_anchor
        self._source_text_vert_anchor = self.text_vert_anchor
        self._source_is_image_parameter = self.is_image_parameter

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PARAMETER

    def parse_from_record(
        self,
        record: _RecordFields,
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
        self._parse_family_dynamic_unique_id(s, record)
        if self.owner_part_id is None:
            self.owner_part_id = 0

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
        color_val, self._has_color = s.read_color(record, Fields.COLOR, default=0)
        self.color = color_val

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

        (
            self.description,
            self._has_description,
            self._used_utf8_description,
        ) = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.DESCRIPTION,
            default="",
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
        self._capture_parameter_source_state()

    def serialize_to_record(self) -> _RecordFields:
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        self._serialize_location_and_text_fields(record, s, raw)
        self._serialize_text_style_fields(record, s, raw)
        self._serialize_visibility_and_sync_fields(record, s, raw)
        self._serialize_anchor_fields(record, s, raw)
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
                "IsHidden",
                "Text",
                "ParamType",
                "Name",
                "ShowName",
                "ReadOnlyState",
                "UniqueID",
                "Description",
                "NotAllowLibrarySynchronize",
                "NotAllowDatabaseSynchronize",
                "NotAutoPosition",
                "IsMirrored",
                "TextHorzAnchor",
                "TextVertAnchor",
                "IsImageParameter",
            ),
        )

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
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        serialize_present_coord_point(
            record,
            serializer,
            raw_record,
            self.location,
            prefix="Location",
            has_x=self._has_location_x,
            has_y=self._has_location_y,
        )
        self._serialize_parameter_text(record, serializer, raw_record)

    def _serialize_parameter_text(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self._has_name or self.name != self._source_name:
            serializer.write_str(
                record,
                Fields.NAME,
                self.name,
                raw_record,
                force=self.name != self._source_name,
            )
        if self.text != self._source_text and not self.text:
            self._remove_fields_case_insensitively(record, ["Text", "%UTF8%Text"])
        elif self._has_text or self.text != self._source_text or raw_record is None:
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
        if self.name.lower() == "probevaluedisplay":
            serializer.remove_field(record, Fields.TEXT)
            record.pop("%UTF8%Text", None)
            record.pop("%UTF8%TEXT", None)

    def _serialize_text_style_fields(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        self._serialize_managed_font_id(
            record,
            serializer,
            Fields.FONT_ID.canonical,
            self.font_id,
            self._get_fallback_font_manager(),
        )
        if self._has_orientation or self.orientation != self._source_orientation:
            serializer.write_int(
                record,
                Fields.ORIENTATION,
                self.orientation.value,
                raw_record,
                force=self.orientation != self._source_orientation,
            )
        if self._has_justification or self.justification != self._source_justification:
            serializer.write_int(
                record,
                Fields.JUSTIFICATION,
                self.justification.value,
                raw_record,
                force=self.justification != self._source_justification,
            )

        self._serialize_managed_optional_int(
            record,
            "COLOR",
            ["Color", "COLOR"],
            self.color,
            self._source_color,
        )

    def _serialize_visibility_and_sync_fields(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        self._serialize_visibility_fields(record, serializer, raw_record)
        self._serialize_sync_fields(record, serializer, raw_record)

    def _serialize_visibility_fields(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        self._serialize_parameter_visibility(record, serializer, raw_record)
        self._serialize_parameter_metadata(record, serializer, raw_record)

    def _serialize_parameter_visibility(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self.is_hidden != self._source_is_hidden:
            serializer.write_bool(
                record, Fields.IS_HIDDEN, self.is_hidden, raw_record, force=True
            )
        elif self.is_hidden:
            serializer.write_bool(record, Fields.IS_HIDDEN, True, raw_record)
        else:
            serializer.remove_field(record, Fields.IS_HIDDEN)

        if self.is_mirrored != self._source_is_mirrored:
            serializer.write_bool(
                record, Fields.IS_MIRRORED, self.is_mirrored, raw_record, force=True
            )
        elif self.is_mirrored:
            serializer.write_bool(record, Fields.IS_MIRRORED, True, raw_record)
        else:
            serializer.remove_field(record, Fields.IS_MIRRORED)

    def _serialize_parameter_metadata(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self._has_param_type or self.param_type != self._source_param_type:
            serializer.write_int(
                record,
                Fields.PARAM_TYPE,
                self.param_type,
                raw_record,
                force=self.param_type != self._source_param_type,
            )

        if self.show_name != self._source_show_name:
            serializer.write_bool(
                record, Fields.SHOW_NAME, self.show_name, raw_record, force=True
            )
        elif self.show_name:
            serializer.write_bool(record, Fields.SHOW_NAME, True, raw_record)
        else:
            serializer.remove_field(record, Fields.SHOW_NAME)

        if (
            self._has_read_only_state
            or self.read_only_state != self._source_read_only_state
        ):
            serializer.write_int(
                record,
                Fields.READ_ONLY_STATE,
                self.read_only_state.value,
                raw_record,
                force=self.read_only_state != self._source_read_only_state,
            )

        if self.description != self._source_description and not self.description:
            self._remove_fields_case_insensitively(
                record, ["Description", "%UTF8%Description"]
            )
        else:
            write_dynamic_string_field(
                serializer,
                record,
                Fields.DESCRIPTION,
                self.description,
                raw_record=raw_record,
                used_utf8_sidecar=self._used_utf8_description,
                was_present=self._has_description,
                force=self.description != self._source_description,
            )

    def _serialize_sync_fields(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if (
            not self.allow_library_synchronize
            or self.allow_library_synchronize != self._source_allow_library_synchronize
        ):
            serializer.write_bool(
                record,
                Fields.NOT_ALLOW_LIBRARY_SYNCHRONIZE,
                not self.allow_library_synchronize,
                raw_record,
                force=True,
            )
        else:
            serializer.remove_field(record, Fields.NOT_ALLOW_LIBRARY_SYNCHRONIZE)

        if (
            not self.allow_database_synchronize
            or self.allow_database_synchronize
            != self._source_allow_database_synchronize
        ):
            serializer.write_bool(
                record,
                Fields.NOT_ALLOW_DATABASE_SYNCHRONIZE,
                not self.allow_database_synchronize,
                raw_record,
                force=True,
            )
        else:
            serializer.remove_field(record, Fields.NOT_ALLOW_DATABASE_SYNCHRONIZE)

        if not self.auto_position or self.auto_position != self._source_auto_position:
            serializer.write_bool(
                record,
                Fields.NOT_AUTO_POSITION,
                not self.auto_position,
                raw_record,
                force=True,
            )
        else:
            serializer.remove_field(record, Fields.NOT_AUTO_POSITION)

    def _serialize_anchor_fields(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if (
            self._has_text_horz_anchor
            or self.text_horz_anchor != self._source_text_horz_anchor
        ):
            serializer.write_int(
                record,
                Fields.TEXT_HORZ_ANCHOR,
                self.text_horz_anchor,
                raw_record,
                force=self.text_horz_anchor != self._source_text_horz_anchor,
            )
        else:
            serializer.remove_field(record, Fields.TEXT_HORZ_ANCHOR)

        if (
            self._has_text_vert_anchor
            or self.text_vert_anchor != self._source_text_vert_anchor
        ):
            serializer.write_int(
                record,
                Fields.TEXT_VERT_ANCHOR,
                self.text_vert_anchor,
                raw_record,
                force=self.text_vert_anchor != self._source_text_vert_anchor,
            )
        else:
            serializer.remove_field(record, Fields.TEXT_VERT_ANCHOR)

        if self.is_image_parameter != self._source_is_image_parameter:
            serializer.write_bool(
                record,
                Fields.IS_IMAGE_PARAMETER,
                self.is_image_parameter,
                raw_record,
                force=True,
            )
        elif self.is_image_parameter:
            serializer.write_bool(record, Fields.IS_IMAGE_PARAMETER, True, raw_record)
        else:
            serializer.remove_field(record, Fields.IS_IMAGE_PARAMETER)

    def _display_text(self, ctx: SchSvgRenderContext) -> str:
        from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key

        override = ctx._parameter_render_overrides.get(id(self))
        if override is not None:
            return (
                f"{self.name}: {override.text}"
                if override.apply_show_name and self.show_name
                else override.text
            )
        if dotnet_ordinal_ignore_case_key(self.name) == "RULE":
            return self.description
        text = ctx.substitute_parameters(self.text)
        return f"{self.name}: {text}" if self.show_name else text

    def to_geometry(
        self,
        ctx: SchSvgRenderContext | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord | list[SchGeometryOp] | None":
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

        if (
            ctx is not None
            and not ctx._parameter_is_imported(self)
            and (not self.is_hidden or id(self) in ctx._visible_hidden_parameter_ids)
        ):
            display_text = self._display_text(ctx)
            if not display_text or display_text == "<no parameter>":
                return None
            baseline_x, baseline_y = ctx.transform_coord_precise(self.location)

            render_override = ctx._parameter_render_overrides.get(id(self))
            use_variant_style = bool(
                render_override is not None and render_override.use_variant_style
            )
            fill_raw = (
                0x008000
                if use_variant_style
                else int(self.color)
                if self.color is not None
                else 0
            )
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

            if use_variant_style:
                is_bold = is_italic = True
                is_underline = False
                font_name = ctx._get_display_font_name(
                    "Times New Roman", is_bold, is_italic
                )
                font_size_px = ctx._pt_to_px(10.0, font_name, is_bold, is_italic)
                font_size_for_width = font_size_px
            else:
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


def _physical_model_state(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for token in text.split("|"):
        name, separator, value = token.partition("=")
        normalized_name = dotnet_ordinal_ignore_case_key(dotnet_trim(name))
        if separator and normalized_name and normalized_name not in values:
            values[normalized_name] = dotnet_trim(value)
    return values


_PHYSICAL_MODEL_VIEW_TYPES = ("ePhysicalView", "eLineView", "eCavityView")
_PHYSICAL_MODEL_VIEW_SIDES = (
    "eFrontView",
    "eLeftSideView",
    "eRightSideView",
    "eTopView",
    "eBottomView",
    "eRearView",
    "eTopLeftBackView",
    "eTopLeftFrontView",
    "eTopRightBackView",
    "eTopRightFrontView",
    "eBottomLeftBackView",
    "eBottomLeftFrontView",
    "eBottomRightBackView",
    "eBottomRightFrontView",
)
_PHYSICAL_MODEL_ROTATIONS = ("eRotate0", "eRotate90", "eRotate180", "eRotate270")
_PHYSICAL_MODEL_CAVITY_VIEWS = ("eConnectivity", "ePinNumbers")
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
_FIFTY_MM_INTERNAL = 19_685_039
_DOTNET_INTEGER_RE = re.compile(r"[+-]?[0-9]+", re.ASCII)
_DOTNET_GUID_D_RE = re.compile(
    r"(?P<a>[0-9a-fA-F]{8})-(?P<b>[0-9a-fA-F]{4})-"
    r"(?P<c>[0-9a-fA-F]{4})-(?P<d>[0-9a-fA-F]{4})-"
    r"(?P<e>[0-9a-fA-F]{12})",
    re.ASCII,
)
_DOTNET_GUID_N_RE = re.compile(r"[0-9a-fA-F]{32}", re.ASCII)
_DOTNET_GUID_X_RE = re.compile(
    r"\{\s*0[xX](?P<a>[0-9a-fA-F]{1,8})\s*,\s*"
    r"0[xX](?P<b>[0-9a-fA-F]{1,4})\s*,\s*"
    r"0[xX](?P<c>[0-9a-fA-F]{1,4})\s*,\s*\{\s*"
    r"0[xX](?P<d1>[0-9a-fA-F]{1,2})\s*,\s*"
    r"0[xX](?P<d2>[0-9a-fA-F]{1,2})\s*,\s*"
    r"0[xX](?P<e1>[0-9a-fA-F]{1,2})\s*,\s*"
    r"0[xX](?P<e2>[0-9a-fA-F]{1,2})\s*,\s*"
    r"0[xX](?P<e3>[0-9a-fA-F]{1,2})\s*,\s*"
    r"0[xX](?P<e4>[0-9a-fA-F]{1,2})\s*,\s*"
    r"0[xX](?P<e5>[0-9a-fA-F]{1,2})\s*,\s*"
    r"0[xX](?P<e6>[0-9a-fA-F]{1,2})\s*\}\s*\}",
    re.ASCII,
)


def _physical_model_float_text(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "-0" if math.copysign(1.0, value) < 0 else "0"
    text = repr(value)
    if text.endswith(".0"):
        text = text[:-2]
    elif text.lower().endswith("e+16"):
        text = format(Decimal(text), "f")
    return text.upper()


def _physical_model_value(values: dict[str, str], name: str) -> str | None:
    return values.get(dotnet_ordinal_ignore_case_key(name))


def _parse_dotnet_integer(value: str | None) -> int | None:
    if value is None or _DOTNET_INTEGER_RE.fullmatch(value) is None:
        return None
    return int(value, 10)


def _strip_invariant_currency(value: str) -> str | None:
    currency = "\u00a4"
    if currency not in value:
        return value
    if value.count(currency) != 1:
        return None
    before, after = value.split(currency)
    if not dotnet_trim(before):
        return _compact_currency_prefix(after)
    if not dotnet_trim(after):
        return _compact_currency_suffix(before)
    return _strip_internal_invariant_currency(before, after)


def _strip_internal_invariant_currency(before: str, after: str) -> str | None:
    if before in {"+", "-"}:
        suffix = _compact_currency_prefix(after)
        return None if suffix is None else f"{before}{suffix}"
    trailing = dotnet_trim(after)
    if trailing in {"+", "-"}:
        return f"{dotnet_trim(before)}{trailing}"
    return _compact_internal_currency_parentheses(before, after)


def _compact_internal_currency_parentheses(
    before: str,
    after: str,
) -> str | None:
    if before == "(" and after.endswith(")"):
        compact = _compact_currency_prefix(after[:-1])
        return None if compact is None else f"({compact})"
    if dotnet_trim(after) == ")" and before.startswith("("):
        compact = _compact_currency_suffix(before[1:])
        return None if compact is None else f"({compact})"
    return None


def _compact_currency_prefix(value: str) -> str | None:
    parenthesized = re.fullmatch(r"\s*\(\s*(?P<number>\S+)\s*\)\s*", value, re.ASCII)
    if parenthesized is not None:
        return f"({parenthesized.group('number')})"
    match = re.fullmatch(
        r"\s*(?P<leading>[+-]?)\s*(?P<number>\S+?)\s*(?P<trailing>[+-]?)\s*",
        value,
        re.ASCII,
    )
    if match is None:
        return None
    if match.group("leading") and match.group("trailing"):
        return None
    return f"{match.group('leading')}{match.group('number')}{match.group('trailing')}"


def _compact_currency_suffix(value: str) -> str | None:
    return _compact_currency_prefix(value)


def _parse_dotnet_enum_names(value: str, members: tuple[str, ...]) -> int | None:
    numeric = 0
    for token in value.split(","):
        name = dotnet_trim(token)
        if name not in members:
            return None
        numeric |= members.index(name)
    return numeric


def _strip_dotnet_parentheses(value: str) -> tuple[str, bool] | None:
    parenthesized = value.startswith("(") and value.endswith(")")
    normalized = dotnet_trim(value[1:-1]) if parenthesized else value
    if "(" in normalized or ")" in normalized:
        return None
    if any(char.isspace() for char in normalized):
        return None
    return normalized, parenthesized


def _strip_dotnet_double_sign(
    value: str,
    *,
    parenthesized: bool,
) -> tuple[str, bool] | None:
    normalized = value
    leading_sign = normalized[:1] if normalized[:1] in {"+", "-"} else ""
    trailing_sign = normalized[-1:] if normalized[-1:] in {"+", "-"} else ""
    sign_count = int(bool(leading_sign)) + int(bool(trailing_sign))
    if parenthesized and sign_count:
        return None
    if sign_count > 1:
        return None
    start = 1 if leading_sign else 0
    end = -1 if trailing_sign else None
    normalized = normalized[start:end]
    negative = parenthesized or "-" in (leading_sign, trailing_sign)
    return normalized, negative


def _normalize_dotnet_double_sign(value: str) -> tuple[str, bool] | None:
    normalized = _strip_invariant_currency(value)
    if normalized is None:
        return None
    unwrapped = _strip_dotnet_parentheses(normalized)
    if unwrapped is None:
        return None
    normalized, parenthesized = unwrapped
    return _strip_dotnet_double_sign(normalized, parenthesized=parenthesized)


def _normalize_dotnet_double_number(value: str) -> str | None:
    exponent_parts = re.split(r"[eE]", value)
    if len(exponent_parts) > 2:
        return None
    mantissa = exponent_parts[0]
    if mantissa.startswith(","):
        return None
    if "." in mantissa and "," in mantissa.split(".", 1)[1]:
        return None
    mantissa = mantissa.replace(",", "")
    if re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", mantissa, re.ASCII) is None:
        return None
    exponent = ""
    if len(exponent_parts) == 2:
        if _DOTNET_INTEGER_RE.fullmatch(exponent_parts[1]) is None:
            return None
        exponent = f"e{exponent_parts[1]}"
    return f"{mantissa}{exponent}"


def _parse_dotnet_invariant_double(value: str | None) -> float | None:
    special_values = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}
    if value in special_values:
        return special_values[value]
    if value is None:
        return None
    signed = _normalize_dotnet_double_sign(value)
    if signed is None:
        return None
    unsigned_value, negative = signed
    number = _normalize_dotnet_double_number(unsigned_value)
    if number is None:
        return None
    return float(f"{'-' if negative else ''}{number}")


def _parse_dotnet_guid(value: str | None) -> str | None:
    if value is None:
        return None
    match = _DOTNET_GUID_D_RE.fullmatch(value)
    if (
        match is None
        and len(value) >= 2
        and ((value[0], value[-1]) in {("{", "}"), ("(", ")")})
    ):
        match = _DOTNET_GUID_D_RE.fullmatch(value[1:-1])
    if match is not None:
        return str(uuid.UUID("".join(match.group(name) for name in "abcde")))
    if _DOTNET_GUID_N_RE.fullmatch(value) is not None:
        return str(uuid.UUID(value))
    match = _DOTNET_GUID_X_RE.fullmatch(value)
    if match is None:
        return None
    compact = "".join(
        f"{int(match.group(name), 16):0{width}x}"
        for name, width in (
            ("a", 8),
            ("b", 4),
            ("c", 4),
            ("d1", 2),
            ("d2", 2),
            ("e1", 2),
            ("e2", 2),
            ("e3", 2),
            ("e4", 2),
            ("e5", 2),
            ("e6", 2),
        )
    )
    return str(uuid.UUID(compact))


class AltiumSchImageParameter(AltiumSchParameter):
    """Managed RECORD 41 image-parameter subtype used by harness physical models."""

    def __init__(self) -> None:
        super().__init__()
        self.is_image_parameter = True
        self.view_name = "Physical View"
        self.view_type = "ePhysicalView"
        self.view_side = "eTopView"
        self.rotation = "eRotate0"
        self.zoom = 1.0
        self.model_width = _FIFTY_MM_INTERNAL
        self.model_height = _FIFTY_MM_INTERNAL
        self.keep_aspect_ratio = False
        self.is_main_model = False
        self.associated_part_unique_id = ""
        self.model_file_id = _EMPTY_GUID
        self.mcad_model_hash = ""
        self.cavity_view_type = "eConnectivity"
        self.is_defined_view = False
        self.children: list[SchPrimitive] = []
        self._capture_parameter_source_state()

    @property
    def model(self) -> SchPrimitive | None:
        """Return the first recursively reachable managed physical model."""
        return self._model_source(_SourceAdmission())

    def _model_source(self, source_admission: _SourceAdmission) -> SchPrimitive | None:
        model_types = {
            SchRecordType.IMAGE,
            SchRecordType.LINE_VIEW,
            SchRecordType.HARNESS_CAVITY_COMPONENT,
        }
        stack = list(reversed(tuple(source_admission.children(self, self.children))))
        seen = {id(self)}
        while stack:
            child = stack.pop()
            child_id = id(child)
            if child_id in seen:
                continue
            seen.add(child_id)
            if not source_admission.admits(child):
                continue
            if isinstance(child, SchPrimitive) and child.record_type in model_types:
                return child
            stack.extend(
                reversed(
                    tuple(
                        source_admission.children(child, getattr(child, "children", ()))
                    )
                )
            )
        return None

    def parse_from_record(
        self,
        record: _RecordFields,
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self.is_image_parameter = True
        values = _physical_model_state(self.text)
        view_name = _physical_model_value(values, "ViewName")
        if view_name is not None:
            self.view_name = view_name
        self.view_type = self._state_enum(
            values, "ViewType", self.view_type, _PHYSICAL_MODEL_VIEW_TYPES, byte=True
        )
        self.view_side = self._state_enum(
            values, "ViewSide", self.view_side, _PHYSICAL_MODEL_VIEW_SIDES, byte=True
        )
        self.rotation = self._state_enum(
            values, "Rotation", self.rotation, _PHYSICAL_MODEL_ROTATIONS, byte=False
        )
        self.zoom = self._state_float(values, "Zoom", self.zoom)
        self.model_width = self._state_int(values, "Width", self.model_width)
        self.model_height = self._state_int(values, "Height", self.model_height)
        self.keep_aspect_ratio = self._state_bool(
            values, "KeepAspectRatio", self.keep_aspect_ratio
        )
        self.is_main_model = self._state_bool(values, "MainModel", self.is_main_model)
        associated_part = _physical_model_value(values, "AssociatedPartUniqueId")
        if associated_part is not None:
            self.associated_part_unique_id = associated_part
        self.model_file_id = self._state_guid(values, "ModelFileId", self.model_file_id)
        mcad_hash = _physical_model_value(values, "McadModelHash")
        if mcad_hash is not None:
            self.mcad_model_hash = mcad_hash
        self.cavity_view_type = self._state_enum(
            values,
            "CavityViewType",
            self.cavity_view_type,
            _PHYSICAL_MODEL_CAVITY_VIEWS,
            byte=True,
        )
        self.is_defined_view = self._state_bool(
            values, "IsDefinedView", self.is_defined_view
        )
        self._capture_parameter_source_state()

    @staticmethod
    def _state_bool(values: dict[str, str], name: str, default: bool) -> bool:
        value = dotnet_ordinal_ignore_case_key(
            _physical_model_value(values, name) or ""
        )
        if value == "TRUE":
            return True
        if value == "FALSE":
            return False
        return default

    @staticmethod
    def _state_int(values: dict[str, str], name: str, default: int) -> int:
        value = _parse_dotnet_integer(_physical_model_value(values, name))
        if value is None:
            return default
        return value if -(1 << 31) <= value <= (1 << 31) - 1 else default

    @staticmethod
    def _state_enum(
        values: dict[str, str],
        name: str,
        default: str,
        members: tuple[str, ...],
        *,
        byte: bool,
    ) -> str:
        value = _physical_model_value(values, name)
        if value in members:
            return value
        if value is not None and "," in value:
            combined = _parse_dotnet_enum_names(value, members)
            if combined is not None:
                return members[combined] if combined < len(members) else str(combined)
        numeric = _parse_dotnet_integer(value)
        if numeric is None:
            return default
        minimum, maximum = (0, 255) if byte else (-(1 << 31), (1 << 31) - 1)
        if not minimum <= numeric <= maximum:
            return default
        return members[numeric] if 0 <= numeric < len(members) else str(numeric)

    @staticmethod
    def _state_guid(values: dict[str, str], name: str, default: str) -> str:
        return _parse_dotnet_guid(_physical_model_value(values, name)) or default

    @staticmethod
    def _state_float(values: dict[str, str], name: str, default: float) -> float:
        value = _parse_dotnet_invariant_double(_physical_model_value(values, name))
        return default if value is None else value

    def serialize_to_record(self) -> _RecordFields:
        values = (
            ("ViewName", self.view_name),
            ("ViewSide", self.view_side),
            ("Rotation", self.rotation),
            ("Zoom", _physical_model_float_text(self.zoom)),
            ("Width", str(self.model_width)),
            ("Height", str(self.model_height)),
            ("KeepAspectRatio", str(self.keep_aspect_ratio)),
            ("MainModel", str(self.is_main_model)),
            ("AssociatedPartUniqueId", self.associated_part_unique_id),
            ("ViewType", self.view_type),
            ("ModelFileId", self.model_file_id),
            ("McadModelHash", self.mcad_model_hash),
            ("CavityViewType", self.cavity_view_type),
            ("IsDefinedView", str(self.is_defined_view)),
        )
        self.text = "|".join(f"{name}={value}" for name, value in values)
        return super().serialize_to_record()

    def to_geometry(
        self,
        ctx: SchSvgRenderContext | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord | list[SchGeometryOp] | None":
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            unwrap_record_operations,
            wrap_record_operations,
        )

        if ctx is None:
            ctx = SchSvgRenderContext()

        operations: list[SchGeometryOp] = []
        model_record: SchGeometryRecord | None = None
        model = self._model_source(ctx._source_admission)
        to_geometry = getattr(model, "to_geometry", None)
        if callable(to_geometry):
            candidate = to_geometry(
                ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if isinstance(candidate, SchGeometryRecord):
                model_record = candidate
            if isinstance(candidate, SchGeometryRecord) and not self.is_hidden:
                operations.append(
                    SchGeometryOp.begin_group(
                        getattr(model, "unique_id", ""),
                        render_group_id=ctx.render_group_id(model) or None,
                        render_group_identity=ctx.render_group_identity(model),
                        render_source_id=id(model),
                    )
                )
                operations.extend(
                    unwrap_record_operations(
                        candidate,
                        unique_id=getattr(model, "unique_id", ""),
                    )
                )
                operations.append(SchGeometryOp.end_group())

        if not wrap_record:
            return operations
        bounds = self._geometry_own_bounds(ctx, model_record)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="image_parameter",
            object_id="eImageParameter",
            bounds=bounds or SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _geometry_own_bounds(
        self,
        ctx: SchSvgRenderContext,
        model_record: "SchGeometryRecord | None",
    ) -> "SchGeometryBounds":
        if model_record is not None and model_record.bounds is not None:
            return model_record.bounds
        from .altium_schdoc import AltiumSchDoc

        return AltiumSchDoc._harness_parameter_text_bounds(self, ctx)

    def _to_component_comment_geometry(
        self,
        ctx: SchSvgRenderContext | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord | list[SchGeometryOp] | None":
        """Render through the plain Comment wrapper created by the engine."""
        return super().to_geometry(
            ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            wrap_record=wrap_record,
        )
