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
    Fields,
    read_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    _basic_entry_distance_to_native_units,
    _basic_entry_distance_to_public_mils,
    _public_mils_to_basic_entry_distance,
    detect_case_mode_method_from_uppercase_fields,
)


class BusTextStyle(IntEnum):
    """
    Bus/harness text display style.
    """

    FULL = 0
    ABBREVIATED = 1
    SHORT = 2


class AltiumSchHarnessEntry(SingleFontBindableRecordMixin, SchPrimitive):
    """
    HARNESS_ENTRY record.

    Individual entry/pin on a harness connector.
    Similar to SHEET_ENTRY but for harnesses.
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        # From SchDataBasicEntry
        self.name: str = ""  # Entry signal name (NOT "text")
        self.text_font_id: int = 1  # TextFontID (NOT "font_id")
        self.text_style: BusTextStyle = BusTextStyle.FULL
        self.harness_type: str = ""  # Associated harness type
        self.side: int = 0  # 0=Left, 1=Right
        self.distance_from_top: int = 0
        # Optional fractional component from DistanceFromTop_Frac1 (1,000,000ths of a step)
        self.distance_from_top_frac1: int = 0
        self.color: int = 0x000000  # Border color
        self.area_color: int = 0xFFFFFF  # Fill color
        self.text_color: int = 0x000000  # Text color
        # Hierarchy flag - indicates this is a child of the preceding object
        self.owner_index_additional_list: bool = True
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
        self._has_distance_from_top_frac1: bool = False
        self._has_color: bool = False
        self._has_area_color: bool = False
        self._has_text_color: bool = False

    def _font_binding_slot_name(self) -> str:
        return "text_font_id"

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_ENTRY

    @property
    def distance_from_top_mils(self) -> float:
        """
        Distance from the connector top/left edge in mils.

        Harness-entry ``DistanceFromTop`` does not use the ordinary 10-mil
        CoordPoint unit. Native harness-entry spacing uses 100-mil steps, with
        ``DistanceFromTop_Frac1`` carrying 1,000,000ths of one such step.
        """
        return _basic_entry_distance_to_public_mils(
            self.distance_from_top,
            self.distance_from_top_frac1,
        )

    @distance_from_top_mils.setter
    def distance_from_top_mils(self, value: float) -> None:
        """
        Set distance from a value in mils.
        """
        self.distance_from_top, self.distance_from_top_frac1 = (
            _public_mils_to_basic_entry_distance(value)
        )

    def _distance_from_top_native_units(self) -> float:
        return _basic_entry_distance_to_native_units(
            self.distance_from_top,
            self.distance_from_top_frac1,
        )

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
        self._font_manager = font_manager
        self._public_font_spec = None

        # Use serializer for field reading
        s = AltiumSerializer()
        r = self._record
        self.name, self._has_name, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.NAME,
            default="",
        )
        self.text_font_id, self._has_text_font_id = s.read_font_id(
            record, Fields.TEXT_FONT_ID, font_manager, default=1
        )

        # Parse TextStyle - may be string ("Full"/"Short") or int
        text_style_raw, self._has_text_style = s.read_str(
            record, Fields.TEXT_STYLE, default="Full"
        )
        if isinstance(text_style_raw, str) and not text_style_raw.isdigit():
            # String value like "Full", "Short", "Abbreviated"
            if text_style_raw.lower() == "full":
                self.text_style = BusTextStyle.FULL
            elif text_style_raw.lower() == "short":
                self.text_style = BusTextStyle.SHORT
            elif text_style_raw.lower() in ("abbreviated", "abbrev"):
                self.text_style = BusTextStyle.ABBREVIATED
            else:
                self.text_style = BusTextStyle.FULL
        else:
            # Numeric value
            self.text_style = BusTextStyle(int(text_style_raw))

        # Parse HarnessType
        self.harness_type, _, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.HARNESS_TYPE,
            default="",
        )

        # Parse Side
        self.side, self._has_side = s.read_int(record, Fields.SIDE, default=0)

        # Parse DistanceFromTop
        self.distance_from_top, self._has_distance_from_top = s.read_int(
            record, Fields.DISTANCE_FROM_TOP, default=0
        )
        # Fractional distance component used by some harness-entry records.
        # Example: DistanceFromTop=1, DistanceFromTop_Frac1=500000 -> 1.5 steps.
        self.distance_from_top_frac1, self._has_distance_from_top_frac1 = s.read_int(
            record, Fields.DISTANCE_FROM_TOP_FRAC1, default=0
        )

        # Parse colors using read_color
        self.color, self._has_color = s.read_int(record, Fields.COLOR, default=0)
        self.area_color, self._has_area_color = s.read_int(
            record, Fields.AREA_COLOR, default=0xFFFFFF
        )
        if self.area_color == 0:
            self.area_color = 0xFFFFFF
        self.text_color, self._has_text_color = s.read_int(
            record, Fields.TEXT_COLOR, default=0
        )

        # Parse hierarchy flag
        owner_additional, _ = s.read_bool(
            record, Fields.OWNER_INDEX_ADDITIONAL_LIST, default=False
        )
        self.owner_index_additional_list = owner_additional

        # Parse child index (if present)
        index_val, has_index = s.read_int(record, Fields.INDEX_IN_SHEET, default=0)
        if has_index:
            self.index_in_sheet = index_val
        else:
            self.index_in_sheet = -2  # Suppress - first child has no IndexInSheet

    def serialize_to_record(self) -> dict[str, Any]:
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        s.write_str(record, Fields.NAME, self.name, raw)
        s.write_int(record, Fields.TEXT_FONT_ID, self.text_font_id, raw)
        s.write_str(record, Fields.TEXT_STYLE, self._text_style_to_string(), raw)

        if self.harness_type:
            s.write_str(record, Fields.HARNESS_TYPE, self.harness_type, raw)

        # Only serialize Side when non-zero (0 is default, omitted in real files)
        if self._has_side or self.side != 0:
            s.write_int(record, Fields.SIDE, self.side, raw)

        if self._has_distance_from_top or self.distance_from_top != 0:
            s.write_int(record, Fields.DISTANCE_FROM_TOP, self.distance_from_top, raw)
        else:
            s.remove_field(record, Fields.DISTANCE_FROM_TOP)

        record.pop("DISTANCEFROMTOP_FRAC1", None)
        record.pop("DistanceFromTop_Frac1", None)
        if self._has_distance_from_top_frac1 or self.distance_from_top_frac1 != 0:
            record["DistanceFromTop_Frac1"] = str(self.distance_from_top_frac1)
        if self._has_color or self.color != 0:
            s.write_int(record, Fields.COLOR, self.color, raw, force=self.color != 0)
        else:
            s.remove_field(record, Fields.COLOR)
        if (
            self._raw_record is None
            or self._has_area_color
            or self.area_color != 0xFFFFFF
        ):
            s.write_int(
                record,
                Fields.AREA_COLOR,
                self.area_color,
                raw,
                force=self._raw_record is None or self.area_color != 0xFFFFFF,
            )
        else:
            s.remove_field(record, Fields.AREA_COLOR)

        # Only serialize TextColor when non-zero
        if self._has_text_color or self.text_color != 0:
            # TextColor is optional in native records. Non-default mutations
            # must add it even when the source record omitted the field.
            s.write_int(record, Fields.TEXT_COLOR, self.text_color, raw, force=True)

        # Hierarchy flag - must be present for Altium to attach entry to connector
        if self.owner_index_additional_list:
            s.write_bool(record, Fields.OWNER_INDEX_ADDITIONAL_LIST, True, raw)

        # Handle OwnerIndex for harness entry objects
        # Logic:
        # - owner_index == 0: First group, use file order hierarchy (no OWNERINDEX needed)
        # - owner_index > 0: Second+ group, MUST have OWNERINDEX pointing to parent connector
        # Always remove parent class's OWNERINDEX first to avoid duplicates
        record.pop("OWNERINDEX", None)
        record.pop("OwnerIndex", None)
        owner_index = cast(int, self.owner_index)
        if owner_index > 0:
            s.write_int(record, Fields.OWNER_INDEX, owner_index, raw)

        # Handle IndexInSheet for child objects
        # -2 = suppress (first child), 1+ = subsequent children
        # Always remove both cases first to avoid duplicates
        record.pop("INDEXINSHEET", None)
        record.pop("IndexInSheet", None)
        if self.index_in_sheet >= 0:
            s.write_int(record, Fields.INDEX_IN_SHEET, self.index_in_sheet, raw)
        # Note: -2 means no IndexInSheet (already removed above)

        record.pop("TEXT", None)
        record.pop("Text", None)
        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def _text_style_to_string(self) -> str:
        """
        Convert TextStyle enum to Altium string format.
        """
        if self.text_style == BusTextStyle.FULL:
            return "Full"
        elif self.text_style == BusTextStyle.SHORT:
            return "Short"
        elif self.text_style == BusTextStyle.ABBREVIATED:
            return "Abbreviated"
        return "Full"

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
            make_font_payload,
            make_pen,
            make_solid_brush,
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
        clean_text, _ = split_overline_text(text_to_render)
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
            if ctx.native_svg_export and self.distance_from_top_frac1:
                text_y += 1
        elif parent_orientation == 0:
            dot_x = int(parent_x + parent_width - 1)
            dot_y = int(parent_y + offset - 1)
            text_x = parent_x + parent_width - text_width_px - 5
            text_y = math.ceil(parent_y + offset + text_center_offset)
            if ctx.native_svg_export and self.distance_from_top_frac1:
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
        dot_radius_units = units_per_px

        operations = [
            SchGeometryOp.rounded_rectangle(
                x1=dot_geometry_x - dot_radius_units,
                y1=dot_geometry_y - dot_radius_units,
                x2=dot_geometry_x + dot_radius_units,
                y2=dot_geometry_y + dot_radius_units,
                corner_x_radius=dot_radius_units,
                corner_y_radius=dot_radius_units,
                brush=make_solid_brush(dot_color_raw),
            ),
            SchGeometryOp.rounded_rectangle(
                x1=dot_geometry_x - dot_radius_units,
                y1=dot_geometry_y - dot_radius_units,
                x2=dot_geometry_x + dot_radius_units,
                y2=dot_geometry_y + dot_radius_units,
                corner_x_radius=dot_radius_units,
                corner_y_radius=dot_radius_units,
                pen=make_pen(dot_color_raw, width=0),
            ),
        ]

        if text_to_render:
            if parent_orientation in (2, 3):
                geometry_text_x_px = text_x - baseline_font_size
                geometry_text_y_px = text_y
            else:
                geometry_text_x_px = text_x
                geometry_text_y_px = text_y - baseline_font_size
            text_geometry_x, text_geometry_y = svg_coord_to_geometry(
                geometry_text_x_px,
                geometry_text_y_px,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            font_payload["rotation"] = float(text_transform_rotation)
            operations.append(
                SchGeometryOp.string(
                    x=text_geometry_x,
                    y=text_geometry_y,
                    text=clean_text,
                    font=font_payload,
                    brush=make_solid_brush(dot_color_raw),
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
