"""Schematic record model for SchRecordType.HARNESS_ENTRY."""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import SchPrimitive, SchRecordType
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    FieldDef,
    Fields,
    _read_param_boolean,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    BasicEntryDistanceMilsMixin,
    _effective_basic_entry_distance_frac1,
    detect_case_mode_method_from_uppercase_fields,
    validate_basic_entry_distance_fields,
    validate_record_enum_value,
)
from ._sch_managed_defaults import (
    HARNESS_ENTRY_COLOR,
    SHEET_ENTRY_FILL_COLOR,
)


class BusTextStyle(IntEnum):
    """
    Bus/harness text display style.
    """

    FULL = 0
    PREFIX = 1
    # Compatibility aliases for the pre-AD26 Python enum. V5 persists both
    # aliases canonically as Prefix.
    ABBREVIATED = PREFIX
    SHORT = PREFIX


class AltiumSchHarnessEntry(
    BasicEntryDistanceMilsMixin,
    SingleFontBindableRecordMixin,
    SchPrimitive,
):
    """
    HARNESS_ENTRY record.

    Individual entry/pin on a harness connector.
    Similar to SHEET_ENTRY but for harnesses.
    """

    def __init__(self) -> None:
        super().__init__()
        self._apply_authored_graphical_metadata_defaults()
        self._init_single_font_binding()
        # From SchDataBasicEntry
        self.name: str = "0"  # Authored SchDataBasicEntry default
        self.text_font_id: int = 1  # TextFontID (NOT "font_id")
        self.text_style: BusTextStyle = BusTextStyle.FULL
        self.harness_type: str = ""  # Associated harness type
        self.side: int = 0  # 0=Left, 1=Right
        self.distance_from_top: int = 0
        self.distance_from_top_frac: int = 0
        # Optional fractional component from DistanceFromTop_Frac1 (1,000,000ths of a step)
        self.distance_from_top_frac1: int = 0
        self.color: int = HARNESS_ENTRY_COLOR
        self.area_color: int = SHEET_ENTRY_FILL_COLOR
        self.text_color: int = HARNESS_ENTRY_COLOR
        # Hierarchy flag - indicates this is a child of the preceding object
        self.owner_index_additional_list: bool = False
        # Child index in parent's child list:
        # - -2 (SUPPRESS_INDEX): First child (no IndexInSheet in record)
        # - 1, 2, 3...: Subsequent children
        # Using -2 as sentinel to prevent add_object auto-assignment
        self.index_in_sheet: int = -2  # Default: suppress (first child)
        # Track field presence
        self._has_name: bool = False
        self._has_text_font_id: bool = False
        self._has_text_style: bool = False
        self._has_side: bool = False
        self._has_distance_from_top: bool = False
        self._has_distance_from_top_frac: bool = False
        self._has_distance_from_top_frac1: bool = False
        self._has_color: bool = False
        self._has_area_color: bool = False
        self._has_text_color: bool = False
        self._has_harness_type: bool = False
        self._has_unique_id: bool = False
        self._used_utf8_name: bool = False
        self._used_utf8_text_style: bool = False
        self._used_utf8_harness_type: bool = False
        self._used_utf8_unique_id: bool = False
        self._source_name: str = self.name
        self._source_text_style: BusTextStyle = self.text_style
        self._source_harness_type: str = self.harness_type
        self._source_unique_id: str = str(self.unique_id or "")
        self._source_distance: tuple[int, int, int] = (0, 0, 0)
        self._source_area_color: int = self.area_color

    def _font_binding_slot_name(self) -> str:
        return "text_font_id"

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_ENTRY

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse harness entry from record.

                Args:
                   record: Source record dictionary
                    font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record)
        self._apply_imported_graphical_metadata_defaults()
        self._font_manager = font_manager
        self._public_font_spec = None

        # Use serializer for field reading
        s = AltiumSerializer()
        r = self._record
        self.name, self._has_name, self._used_utf8_name = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.NAME,
            default="",
        )
        self.text_font_id, self._has_text_font_id = s.read_font_id(
            record, Fields.TEXT_FONT_ID, font_manager, default=1
        )

        (
            text_style_raw,
            self._has_text_style,
            self._used_utf8_text_style,
        ) = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT_STYLE,
            default="",
        )
        self.text_style = (
            BusTextStyle.PREFIX
            if text_style_raw.lower() == "prefix"
            else BusTextStyle.FULL
        )

        # Parse HarnessType
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

        # Parse Side
        self.side, self._has_side = s.read_int(record, Fields.SIDE, default=0)
        validate_record_enum_value("Side", self.side, 3)

        # Parse DistanceFromTop
        self.distance_from_top, self._has_distance_from_top = s.read_int(
            record, Fields.DISTANCE_FROM_TOP, default=0
        )
        self.distance_from_top_frac, self._has_distance_from_top_frac = s.read_int(
            record, Fields.DISTANCE_FROM_TOP_FRAC, default=0
        )
        # Fractional distance component used by some harness-entry records.
        # Example: DistanceFromTop=1, DistanceFromTop_Frac1=500000 -> 1.5 steps.
        self.distance_from_top_frac1, self._has_distance_from_top_frac1 = s.read_int(
            record, Fields.DISTANCE_FROM_TOP_FRAC1, default=0
        )
        validate_basic_entry_distance_fields(
            self.distance_from_top,
            self.distance_from_top_frac,
            self.distance_from_top_frac1,
        )

        # Parse colors using read_color
        color, self._has_color = s.read_color(record, Fields.COLOR, default=0)
        area_color, self._has_area_color = s.read_color(
            record, Fields.AREA_COLOR, default=0
        )
        text_color, self._has_text_color = s.read_color(
            record, Fields.TEXT_COLOR, default=0
        )
        self.color = int(color or 0)
        self.area_color = int(area_color or 0)
        self.text_color = int(text_color or 0)

        # Parse hierarchy flag
        self.owner_index_additional_list = _read_param_boolean(
            record, Fields.OWNER_INDEX_ADDITIONAL_LIST
        )

        # Parse child index (if present)
        index_val, has_index = s.read_int(record, Fields.INDEX_IN_SHEET, default=0)
        if has_index:
            self.index_in_sheet = index_val
        else:
            self.index_in_sheet = -2  # Suppress - first child has no IndexInSheet

        unique_id, self._has_unique_id, self._used_utf8_unique_id = (
            read_dynamic_string_field(s, record, r, "UniqueID", default="")
        )
        self.unique_id = unique_id or None
        self._source_name = self.name
        self._source_text_style = self.text_style
        self._source_harness_type = self.harness_type
        self._source_unique_id = str(self.unique_id or "")
        self._source_distance = (
            self.distance_from_top,
            self.distance_from_top_frac,
            self.distance_from_top_frac1,
        )
        self._source_area_color = self.area_color

    def serialize_to_record(self) -> dict[str, Any]:
        self._ensure_bound_public_font_ready()
        validate_record_enum_value("Side", self.side, 3)
        validate_basic_entry_distance_fields(
            self.distance_from_top,
            self.distance_from_top_frac,
            self.distance_from_top_frac1,
        )
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        write_dynamic_string_field(
            s,
            record,
            Fields.NAME,
            self.name,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_name,
            was_present=self._has_name,
            force=self.name != self._source_name,
        )
        self._serialize_managed_font_id(
            record,
            s,
            Fields.TEXT_FONT_ID.canonical,
            self.text_font_id,
            self._get_fallback_font_manager(),
        )
        write_dynamic_string_field(
            s,
            record,
            Fields.TEXT_STYLE,
            self._text_style_to_string(),
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_text_style,
            was_present=self._has_text_style,
            force=self.text_style != self._source_text_style,
        )

        write_dynamic_string_field(
            s,
            record,
            Fields.HARNESS_TYPE,
            self.harness_type,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_harness_type,
            was_present=self._has_harness_type,
            force=self.harness_type != self._source_harness_type,
        )

        # Only serialize Side when non-zero (0 is default, omitted in real files)
        self._write_optional_int(s, record, raw, Fields.SIDE, self.side)

        for field, value in (
            (Fields.DISTANCE_FROM_TOP, self.distance_from_top),
            (Fields.DISTANCE_FROM_TOP_FRAC, self.distance_from_top_frac),
            (Fields.DISTANCE_FROM_TOP_FRAC1, self.distance_from_top_frac1),
        ):
            self._write_optional_int(s, record, raw, field, value)
        for field, value in (
            (Fields.COLOR, self.color),
            (Fields.AREA_COLOR, self.area_color),
            (Fields.TEXT_COLOR, self.text_color),
        ):
            self._write_optional_color(s, record, raw, field, value)

        # Hierarchy flag - must be present for Altium to attach entry to connector
        self._write_optional_bool(
            s,
            record,
            raw,
            Fields.OWNER_INDEX_ADDITIONAL_LIST,
            self.owner_index_additional_list,
        )

        # Handle OwnerIndex for harness entry objects
        # Logic:
        # - owner_index == 0: First group, use file order hierarchy (no OWNERINDEX needed)
        # - owner_index > 0: Second+ group, MUST have OWNERINDEX pointing to parent connector
        # Always remove parent class's OWNERINDEX first to avoid duplicates
        record.pop("OWNERINDEX", None)
        record.pop("OwnerIndex", None)
        owner_index = cast(int, self.owner_index)
        if owner_index != 0:
            s.write_int(record, Fields.OWNER_INDEX, owner_index, raw)

        # Handle IndexInSheet for child objects
        # -2 = suppress (first child), 1+ = subsequent children
        # Always remove both cases first to avoid duplicates
        record.pop("INDEXINSHEET", None)
        record.pop("IndexInSheet", None)
        if self.index_in_sheet >= 0:
            s.write_int(record, Fields.INDEX_IN_SHEET, self.index_in_sheet, raw)
        # Note: -2 means no IndexInSheet (already removed above)

        unique_id = str(self.unique_id or "")
        if self._used_utf8_unique_id and raw is not None:
            for key, value in raw.items():
                if key.lower() == "uniqueid":
                    record[key] = value
                    break
        write_dynamic_string_field(
            s,
            record,
            "UniqueID",
            unique_id,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_unique_id,
            was_present=self._has_unique_id,
            force=unique_id != self._source_unique_id,
        )

        record.pop("TEXT", None)
        record.pop("Text", None)
        if raw is None:
            return self._authored_managed_order(record)
        return record

    @staticmethod
    def _write_optional_int(
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
        field_name: FieldDef | str,
        value: int,
    ) -> None:
        field = field_name.canonical if isinstance(field_name, FieldDef) else field_name
        source, _ = serializer.read_int(raw or {}, field, default=0)
        if raw is not None and value == source:
            return
        serializer.remove_field(record, field)
        if value != 0:
            serializer.write_int(record, field, value, None, force=True)

    @staticmethod
    def _write_optional_color(
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
        field_name: FieldDef | str,
        value: int,
    ) -> None:
        field = field_name.canonical if isinstance(field_name, FieldDef) else field_name
        source, _ = serializer.read_color(raw or {}, field, default=0)
        if raw is not None and value == source:
            return
        serializer.remove_field(record, field)
        if value != 0:
            serializer.write_color(record, field, value, None, force=True)

    @staticmethod
    def _write_optional_bool(
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
        field_name: FieldDef | str,
        value: bool,
    ) -> None:
        field = field_name.canonical if isinstance(field_name, FieldDef) else field_name
        source = _read_param_boolean(raw or {}, field)
        if raw is not None and value == source:
            return
        serializer.remove_field(record, field)
        if value:
            serializer.write_bool(record, field, True, None, force=True)

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def _text_style_to_string(self) -> str:
        """
        Convert TextStyle enum to Altium string format.
        """
        if self.text_style == BusTextStyle.FULL:
            return "Full"
        if self.text_style == BusTextStyle.PREFIX:
            return "Prefix"
        return "Full"

    @staticmethod
    def _authored_managed_order(record: dict[str, object]) -> dict[str, object]:
        managed_order = (
            "RECORD",
            "OwnerIndex",
            "IsNotAccesible",
            "OwnerIndexAdditionalList",
            "IndexInSheet",
            "IgnoreOnLoad",
            "WiringDiagramOriginUniqueId",
            "IsSchematicBlockObject",
            "UniqueIDInReuseBlock",
            "OwnerPartId",
            "OwnerPartDisplayMode",
            "SelectionMemory",
            "UnionIndex",
            "GraphicallyLocked",
            "Side",
            "DistanceFromTop",
            "DistanceFromTop_Frac",
            "DistanceFromTop_Frac1",
            "Color",
            "AreaColor",
            "TextColor",
            "TextFontID",
            "TextStyle",
            "%UTF8%TextStyle",
            "Name",
            "%UTF8%Name",
            "HarnessType",
            "%UTF8%HarnessType",
            "UniqueID",
            "%UTF8%UniqueID",
        )
        family_names = {name.lower(): name for name in managed_order}
        family_values: dict[str, tuple[str, object]] = {}
        result: dict[str, Any] = {}
        for key, value in record.items():
            family_name = family_names.get(key.lower())
            if family_name is None:
                result[key] = value
            else:
                family_values[family_name] = (key, value)
        for family_name in managed_order:
            if family_name in family_values:
                key, value = family_values[family_name]
                result[key] = value
        return result

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        parent_x: float,
        parent_y: float,
        parent_width: float,
        parent_height: float,
        parent_orientation: int,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this harness entry.
        """
        import math

        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_font_payload,
            make_pen,
            make_solid_brush,
            make_text_with_overline_operations,
            split_overline_text,
            svg_coord_to_geometry,
            wrap_record_operations,
        )
        from .altium_text_metrics import measure_text_width

        dot_size = 2.0
        offset = self._distance_from_top_native_units() * ctx.scale

        font_name, font_size_px, font_bold, font_italic, _ = ctx.get_font_info(
            self.text_font_id
        )
        font_size_for_width = ctx.get_font_size_for_width(self.text_font_id)
        font_spec = (
            ctx.font_manager.get_font_info(self.text_font_id)
            if ctx.font_manager
            else None
        )
        font_payload = make_font_payload(
            name=str(font_spec.get("name", font_name)) if font_spec else str(font_name),
            size_px=font_size_px,
            units_per_px=units_per_px,
            rotation=-90.0 if parent_orientation in (2, 3) else 0.0,
            underline=bool(font_spec.get("underline", False)) if font_spec else False,
            italic=bool(font_spec.get("italic", font_italic))
            if font_spec
            else bool(font_italic),
            bold=bool(font_spec.get("bold", font_bold))
            if font_spec
            else bool(font_bold),
            strikeout=bool(font_spec.get("strikeout", False)) if font_spec else False,
        )

        dot_color_raw = int(self.text_color) if self.text_color is not None else 0
        text_to_render = self.name or ""
        clean_text, _ = split_overline_text(
            text_to_render,
            single_slash_negation=ctx.options.single_slash_negation,
        )
        text_width_px = (
            measure_text_width(
                clean_text,
                font_size_for_width,
                font_name,
                bold=font_bold,
                italic=font_italic,
            )
            if clean_text
            else 0.0
        )
        text_transform_rotation = -90 if parent_orientation in (2, 3) else 0
        baseline_font_size = float(int(font_size_px))
        text_center_offset = int((baseline_font_size - 1) / 2)

        if parent_orientation == 1:
            dot_x = int(parent_x - 1)
            dot_y = int(parent_y + offset - 1)
            text_x = int(parent_x + 5)
            text_y = math.ceil(parent_y + offset + text_center_offset)
            if ctx.native_svg_export and _effective_basic_entry_distance_frac1(self):
                text_y += 1
        elif parent_orientation == 0:
            dot_x = int(parent_x + parent_width - 1)
            dot_y = int(parent_y + offset - 1)
            text_x = parent_x + parent_width - text_width_px - 5
            text_y = math.ceil(parent_y + offset + text_center_offset)
            if ctx.native_svg_export and _effective_basic_entry_distance_frac1(self):
                text_y += 1
        elif parent_orientation == 2:
            dot_x = int(parent_x + offset - 1)
            dot_y = int(parent_y + parent_height - 1)
            text_x = int(parent_x + offset + 4)
            text_y = int(parent_y + parent_height - 5)
        else:
            dot_x = int(parent_x + offset - 1)
            dot_y = int(parent_y - 1)
            text_x = int(parent_x + offset + 4)
            text_y = parent_y + text_width_px + 5

        dot_center_x = dot_x + dot_size / 2.0
        dot_center_y = dot_y + dot_size / 2.0
        dot_geometry_x, dot_geometry_y = svg_coord_to_geometry(
            dot_center_x,
            dot_center_y,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        dot_radius_units = _geometry_item_length(1.0, units_per_px=units_per_px)

        operations = [
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=dot_geometry_x,
                center_y=dot_geometry_y,
                half_width=dot_radius_units,
                half_height=dot_radius_units,
                corner_x_radius=dot_radius_units,
                corner_y_radius=dot_radius_units,
                brush=make_solid_brush(dot_color_raw),
            ),
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=dot_geometry_x,
                center_y=dot_geometry_y,
                half_width=dot_radius_units,
                half_height=dot_radius_units,
                corner_x_radius=dot_radius_units,
                corner_y_radius=dot_radius_units,
                pen=make_pen(dot_color_raw, width=0),
            ),
        ]

        if text_to_render:
            font_payload["rotation"] = float(text_transform_rotation)
            operations.extend(
                make_text_with_overline_operations(
                    text=text_to_render,
                    baseline_x_px=text_x,
                    baseline_y_px=text_y,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    font_payload=font_payload,
                    font_size_px=font_size_px,
                    font_name=font_name,
                    bold=font_bold,
                    italic=font_italic,
                    brush_color_raw=dot_color_raw,
                    rotation_deg=float(text_transform_rotation),
                    units_per_px=units_per_px,
                    single_slash_negation=ctx.options.single_slash_negation,
                )
            )

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="harnessentry",
            object_id="eHarnessEntry",
            bounds=SchGeometryBounds(
                left=int(round(min(dot_x, text_x) * 100000)),
                top=int(round(max(dot_y + dot_size, text_y + font_size_px) * 100000)),
                right=int(
                    round(max(dot_x + dot_size, text_x + text_width_px) * 100000)
                ),
                bottom=int(round(min(dot_y, text_y - font_size_px) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
            extras={
                "connection_points": [
                    {
                        "id": "harness-entry-hotspot",
                        "kind": "connection",
                        "role": "ratsnest-anchor",
                        "point": [dot_geometry_x, dot_geometry_y],
                        "source_kind": "harness_entry_hotspot",
                    }
                ]
            },
        )

    def __repr__(self) -> str:
        return f"<AltiumSchHarnessEntry '{self.name}'>"
