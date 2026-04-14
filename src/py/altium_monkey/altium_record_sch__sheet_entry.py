"""Schematic record model for SchRecordType.SHEET_ENTRY."""

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import SchPrimitive, SchRecordType
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    _basic_entry_distance_to_native_units,
    _basic_entry_distance_to_public_mils,
    _public_mils_to_basic_entry_distance,
)


class SchSheetEntryArrowKind(IntEnum):
    """
    Arrow shape for sheet entries.
    """

    BLOCK_TRIANGLE = 0  # Default pentagon shape
    TRIANGLE = 1  # Pure triangle
    ARROW = 2  # Arrow with curves
    ARROW_TAIL = 3  # Double-ended arrow tail


class SheetEntrySide(IntEnum):
    """
    Sheet entry side placement.
    """

    LEFT = 0
    RIGHT = 1
    TOP = 2
    BOTTOM = 3


class SchSheetEntryIOType(IntEnum):
    """
    I/O direction for sheet entries.
    """

    UNSPECIFIED = 0
    OUTPUT = 1
    INPUT = 2
    BIDIRECTIONAL = 3


@dataclass(frozen=True)
class _SheetEntryGeometryLayout:
    points: list[tuple[float, float]]
    shadow_points: list[tuple[float, float]]
    text_x: float
    text_y: float
    harness_fill: str | None = None
    harness_stroke: str | None = None
    shadow_fill: str | None = None
    shadow_stroke: str | None = None


def _sheet_entry_color_raw_from_hex(color_hex: str) -> int:
    color_text = str(color_hex or "").strip().lstrip("#")
    if len(color_text) != 6:
        return 0
    red = int(color_text[0:2], 16)
    green = int(color_text[2:4], 16)
    blue = int(color_text[4:6], 16)
    return red | (green << 8) | (blue << 16)


class AltiumSchSheetEntry(SingleFontBindableRecordMixin, SchPrimitive):
    """
    Sheet entry record.

    Port tab on hierarchical sheet symbol (connects to PORT in sub-sheet).
    Inherits from SchDataBasicEntry in native which provides:
    - Name, TextFontID, TextStyle, HarnessType, Side, DistanceFromTop
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        # Core entry properties (from SchDataBasicEntry)
        self.name: str = ""  # Entry signal name (NOT "text")
        self.text_font_id: int = 1  # TextFontID (NOT "font_id")
        self.side: int = 0  # 0=Left, 1=Right, 2=Top, 3=Bottom
        self.io_type: int = (
            0  # IOType: 0=Unspecified, 1=Output, 2=Input, 3=Bidirectional
        )
        self.distance_from_top: int = (
            0  # Offset from top/left (1 unit = 10 CoordPoint units = 100 mils)
        )
        self.distance_from_top_frac1: int = 0
        # Visual properties
        self.color: int = 0x000000  # Border color
        self.area_color: int = 0xFFFFFF  # Fill color
        self.text_color: int = 0x000000  # Text color (usually same as color)
        # Arrow properties
        self.arrow_kind: int = (
            0  # Arrow kind: 0=Block&Triangle, 1=Triangle, 2=Arrow, 3=ArrowTail
        )
        self.style: int = 0  # Port arrow style (determines arrow position)
        self.text_style: str = "Full"  # Display style: Full, Short, Abbreviated
        # Harness support
        self.harness_type: str = ""  # Associated harness type name
        # Track field presence for round-trip fidelity
        self._has_name: bool = False
        self._has_text_font_id: bool = False
        self._has_side: bool = False
        self._has_io_type: bool = False
        self._has_distance_from_top: bool = False
        self._has_distance_from_top_frac1: bool = False
        self._has_color: bool = False
        self._has_area_color: bool = False
        self._has_text_color: bool = False
        self._has_arrow_kind: bool = False
        self._has_style: bool = False
        self._has_text_style: bool = False
        self._has_harness_type: bool = False
        self._legacy_text: str = ""
        self._has_legacy_text: bool = False

    def _font_binding_slot_name(self) -> str:
        return "text_font_id"

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.SHEET_ENTRY

    @property
    def distance_from_top_mils(self) -> float:
        """
        Distance from the symbol edge in mils.

        Native sheet-entry spacing uses 100-mil steps, matching the visible
        sheet-entry placement grid used by Altium. Fractional native storage
        is preserved when present.
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

    @property
    def display_name(self) -> str:
        """
        Get the name to display (name field takes precedence).
        """
        return self.name if self.name else self._legacy_text

    @property
    def is_harness_entry(self) -> bool:
        """
        Check if this entry is associated with a harness.
        """
        return bool(self.harness_type)

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse sheet entry from record.

                Args:
                   record: Source record dictionary
                    font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record)
        self._font_manager = font_manager
        self._public_font_spec = None
        s = AltiumSerializer()
        r = self._record
        self.name, self._has_name, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.NAME,
            default="",
        )
        self._legacy_text, self._has_legacy_text = s.read_str(
            record, Fields.TEXT, default=""
        )
        if not self._has_name and self._legacy_text:
            self.name = self._legacy_text
            self._has_name = True

        # Parse TextFontID using the record field name used by this serializer.
        self.text_font_id, self._has_text_font_id = s.read_font_id(
            record, Fields.TEXT_FONT_ID, font_manager, default=1
        )

        # Parse Side
        self.side, self._has_side = s.read_int(record, Fields.SIDE, default=0)

        # Parse IOType
        self.io_type, self._has_io_type = s.read_int(record, Fields.IO_TYPE, default=0)

        # Parse DistanceFromTop. Native V5 exports the whole 100-mil step
        # count plus optional DistanceFromTop_Frac1 millionths of one step.
        self.distance_from_top, self._has_distance_from_top = s.read_int(
            record, Fields.DISTANCE_FROM_TOP, default=0
        )
        self.distance_from_top_frac1, self._has_distance_from_top_frac1 = s.read_int(
            record, Fields.DISTANCE_FROM_TOP_FRAC1, default=0
        )

        # Parse colors
        self.color, self._has_color = s.read_int(record, Fields.COLOR, default=0)
        self.area_color, self._has_area_color = s.read_int(
            record, Fields.AREA_COLOR, default=0xFFFFFF
        )
        self.text_color, self._has_text_color = s.read_int(
            record, Fields.TEXT_COLOR, default=0
        )

        # Parse arrow properties
        # ArrowKind can be string ("Block & Triangle") or int
        arrow_kind_raw, self._has_arrow_kind = s.read_str(
            record, Fields.ARROW_KIND, default="0"
        )
        if isinstance(arrow_kind_raw, str) and not arrow_kind_raw.isdigit():
            arrow_kind_map = {
                "block & triangle": SchSheetEntryArrowKind.BLOCK_TRIANGLE,
                "triangle": SchSheetEntryArrowKind.TRIANGLE,
                "arrow": SchSheetEntryArrowKind.ARROW,
                "arrow tail": SchSheetEntryArrowKind.ARROW_TAIL,
                "arrowtail": SchSheetEntryArrowKind.ARROW_TAIL,
            }
            self.arrow_kind = arrow_kind_map.get(
                arrow_kind_raw.lower(),
                SchSheetEntryArrowKind.BLOCK_TRIANGLE,
            ).value
        else:
            self.arrow_kind = int(arrow_kind_raw)
        self.style, self._has_style = s.read_int(record, Fields.STYLE, default=0)

        # Parse TextStyle - may be string or int
        text_style_raw, self._has_text_style = s.read_str(
            record, Fields.TEXT_STYLE, default="Full"
        )
        if isinstance(text_style_raw, str) and not text_style_raw.isdigit():
            self.text_style = text_style_raw
        else:
            # Convert numeric to string
            style_map = {0: "Full", 1: "Abbreviated", 2: "Short"}
            self.text_style = style_map.get(int(text_style_raw), "Full")

        # Parse harness type
        self.harness_type, self._has_harness_type, _ = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.HARNESS_TYPE,
            default="",
        )

    def serialize_to_record(self) -> dict[str, Any]:
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # ArrowKind must be serialized as string, not integer (Issue #3)
        arrow_kind_strings = {
            0: "Block & Triangle",
            1: "Triangle",
            2: "Arrow",
            3: "Arrow Tail",
        }
        arrow_kind_str = arrow_kind_strings.get(self.arrow_kind, "Block & Triangle")

        s.write_str(record, Fields.NAME, self.name, raw)
        if self._has_legacy_text or self._legacy_text:
            s.write_str(record, Fields.TEXT, self._legacy_text, raw)
        s.write_int(record, Fields.TEXT_FONT_ID, self.text_font_id, raw)

        # Only serialize Side when non-zero (Issue #6)
        if self._has_side or self.side != 0:
            s.write_int(record, Fields.SIDE, self.side, raw)

        record.pop("DISTANCEFROMTOP_FRAC1", None)
        record.pop("DistanceFromTop_Frac1", None)
        if self._has_distance_from_top or self.distance_from_top != 0:
            s.write_int(record, Fields.DISTANCE_FROM_TOP, self.distance_from_top, raw)
        else:
            s.remove_field(record, Fields.DISTANCE_FROM_TOP)
        if self._has_distance_from_top_frac1 or self.distance_from_top_frac1 != 0:
            record["DistanceFromTop_Frac1"] = str(self.distance_from_top_frac1)
        s.write_int(record, Fields.COLOR, self.color, raw, force=self.color != 0)
        s.write_int(
            record,
            Fields.AREA_COLOR,
            self.area_color,
            raw,
            force=self.area_color != 0xFFFFFF,
        )
        if self._has_text_color or self.text_color:
            # TextColor is optional in native records. Non-default mutations,
            # such as white text on a black sheet-entry fill, must add it.
            s.write_int(record, Fields.TEXT_COLOR, self.text_color, raw, force=True)
        s.write_str(
            record, Fields.ARROW_KIND, arrow_kind_str, raw
        )  # Issue #3: string format
        s.write_str(record, Fields.TEXT_STYLE, self.text_style, raw)

        if self._has_io_type or self.io_type != 0:
            s.write_int(record, Fields.IO_TYPE, self.io_type, raw)
        if self._has_style or self.style != 0:
            s.write_int(record, Fields.STYLE, self.style, raw)

        if self._has_harness_type or self.harness_type:
            s.write_str(record, Fields.HARNESS_TYPE, self.harness_type, raw)

        s.remove_field(record, Fields.TEXT)
        return record

    def _detect_case_mode(self) -> CaseMode:
        """
        Detect case mode from raw record fields.
        """
        if self._raw_record is None:
            return CaseMode.PASCALCASE  # Default for new records
        for key in self._raw_record:
            if key == "RECORD":
                continue
            if key.isupper() and len(key) > 2:
                return CaseMode.UPPERCASE
        return CaseMode.PASCALCASE

    def _harness_palette(
        self, harness_color: int | None = None
    ) -> tuple[str, str, str, str]:
        from .altium_record_types import color_to_hex
        from .altium_sch_svg_renderer import apply_dark, apply_light, modify_color

        base_color = 0xE7BCAD if harness_color is None else int(harness_color)
        main_fill = apply_light(base_color, 40)
        main_stroke = apply_dark(main_fill, 100)
        shadow_base = modify_color(60, main_fill, 0)
        shadow_fill = apply_light(shadow_base, 100)
        shadow_stroke = apply_light(shadow_base, 80)
        return (
            color_to_hex(main_fill),
            color_to_hex(main_stroke),
            color_to_hex(shadow_fill),
            color_to_hex(shadow_stroke),
        )

    def _effective_is_harness_entry(self, ctx: "SchSvgRenderContext") -> bool:
        return (
            self.is_harness_entry
            or str(self.unique_id or "") in ctx.harness_sheet_entry_colors
        )

    def _effective_harness_color(self, ctx: "SchSvgRenderContext") -> int | None:
        return ctx.harness_sheet_entry_colors.get(str(self.unique_id or ""))

    def _entry_symbol_width(self) -> float:
        return {
            int(SchSheetEntryArrowKind.BLOCK_TRIANGLE): 15.0,
            int(SchSheetEntryArrowKind.TRIANGLE): 8.0,
            int(SchSheetEntryArrowKind.ARROW): 12.0,
            int(SchSheetEntryArrowKind.ARROW_TAIL): 15.0,
        }.get(int(self.arrow_kind), 15.0)

    def _build_harness_left_layout(
        self,
        *,
        parent_x: float,
        center_y: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        x_left = parent_x
        x_inner_left = parent_x + arrow_depth
        x_inner_right = parent_x + entry_width - arrow_depth
        x_right = parent_x + entry_width
        points = [
            (x_inner_right, center_y - entry_half_height),
            (x_right, center_y),
            (x_inner_right, center_y + entry_half_height),
            (x_inner_left, center_y + entry_half_height),
            (x_left, center_y),
            (x_inner_left, center_y - entry_half_height),
        ]
        shadow_points = [
            (x_inner_right + 0.5, center_y - entry_half_height + 1),
            (x_right + 0.5, center_y + 1),
            (x_inner_right + 0.5, center_y + entry_half_height + 1),
            (x_inner_left + 0.5, center_y + entry_half_height + 1),
            (x_left + 0.5, center_y + 1),
            (x_inner_left + 0.5, center_y - entry_half_height + 1),
        ]
        return _SheetEntryGeometryLayout(
            points=points,
            shadow_points=shadow_points,
            text_x=x_right + 5,
            text_y=center_y,
            harness_fill=self._harness_palette()[0],
            harness_stroke=self._harness_palette()[1],
            shadow_fill=self._harness_palette()[2],
            shadow_stroke=self._harness_palette()[3],
        )

    def _build_harness_right_layout(
        self,
        *,
        parent_x: float,
        parent_width: float,
        center_y: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        x_right = parent_x + parent_width
        x_inner_right = x_right - arrow_depth
        x_inner_left = x_right - entry_width + arrow_depth
        x_left = x_right - entry_width
        points = [
            (x_inner_right, center_y - entry_half_height),
            (x_right, center_y),
            (x_inner_right, center_y + entry_half_height),
            (x_inner_left, center_y + entry_half_height),
            (x_left, center_y),
            (x_inner_left, center_y - entry_half_height),
        ]
        shadow_points = [
            (x_inner_right + 0.5, center_y - entry_half_height + 1),
            (x_right + 0.5, center_y + 1),
            (x_inner_right + 0.5, center_y + entry_half_height + 1),
            (x_inner_left + 0.5, center_y + entry_half_height + 1),
            (x_left + 0.5, center_y + 1),
            (x_inner_left + 0.5, center_y - entry_half_height + 1),
        ]
        return _SheetEntryGeometryLayout(
            points=points,
            shadow_points=shadow_points,
            text_x=x_left - 5,
            text_y=center_y,
            harness_fill=self._harness_palette()[0],
            harness_stroke=self._harness_palette()[1],
            shadow_fill=self._harness_palette()[2],
            shadow_stroke=self._harness_palette()[3],
        )

    def _build_harness_top_layout(
        self,
        *,
        parent_x: float,
        parent_y: float,
        offset: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        center_x = parent_x + offset
        y_top = parent_y
        y_inner_top = parent_y + arrow_depth
        y_inner_bottom = parent_y + entry_width - arrow_depth
        y_bottom = parent_y + entry_width
        points = [
            (center_x - entry_half_height, y_inner_top),
            (center_x, y_top),
            (center_x + entry_half_height, y_inner_top),
            (center_x + entry_half_height, y_inner_bottom),
            (center_x, y_bottom),
            (center_x - entry_half_height, y_inner_bottom),
        ]
        shadow_points = [
            (center_x - entry_half_height + 0.5, y_inner_top + 1),
            (center_x + 0.5, y_top + 1),
            (center_x + entry_half_height + 0.5, y_inner_top + 1),
            (center_x + entry_half_height + 0.5, y_inner_bottom + 1),
            (center_x + 0.5, y_bottom + 1),
            (center_x - entry_half_height + 0.5, y_inner_bottom + 1),
        ]
        return _SheetEntryGeometryLayout(
            points=points,
            shadow_points=shadow_points,
            text_x=center_x + entry_half_height - 1,
            text_y=y_bottom + 5,
            harness_fill=self._harness_palette()[0],
            harness_stroke=self._harness_palette()[1],
            shadow_fill=self._harness_palette()[2],
            shadow_stroke=self._harness_palette()[3],
        )

    def _build_harness_bottom_layout(
        self,
        *,
        parent_x: float,
        parent_y: float,
        parent_height: float,
        offset: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        center_x = parent_x + offset
        y_bottom = parent_y + parent_height
        y_inner_bottom = y_bottom - arrow_depth
        y_inner_top = y_bottom - entry_width + arrow_depth
        y_top = y_bottom - entry_width
        points = [
            (center_x - entry_half_height, y_inner_top),
            (center_x, y_top),
            (center_x + entry_half_height, y_inner_top),
            (center_x + entry_half_height, y_inner_bottom),
            (center_x, y_bottom),
            (center_x - entry_half_height, y_inner_bottom),
        ]
        shadow_points = [
            (center_x - entry_half_height + 0.5, y_inner_top + 1),
            (center_x + 0.5, y_top + 1),
            (center_x + entry_half_height + 0.5, y_inner_top + 1),
            (center_x + entry_half_height + 0.5, y_inner_bottom + 1),
            (center_x + 0.5, y_bottom + 1),
            (center_x - entry_half_height + 0.5, y_inner_bottom + 1),
        ]
        return _SheetEntryGeometryLayout(
            points=points,
            shadow_points=shadow_points,
            text_x=center_x + entry_half_height - 1,
            text_y=y_top - 5,
            harness_fill=self._harness_palette()[0],
            harness_stroke=self._harness_palette()[1],
            shadow_fill=self._harness_palette()[2],
            shadow_stroke=self._harness_palette()[3],
        )

    def _build_harness_layout(
        self,
        *,
        parent_x: float,
        parent_y: float,
        parent_width: float,
        parent_height: float,
        offset: float,
        center_y: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        if self.side == SheetEntrySide.LEFT:
            return self._build_harness_left_layout(
                parent_x=parent_x,
                center_y=center_y,
                entry_half_height=entry_half_height,
                entry_width=entry_width,
                arrow_depth=arrow_depth,
            )
        if self.side == SheetEntrySide.RIGHT:
            return self._build_harness_right_layout(
                parent_x=parent_x,
                parent_width=parent_width,
                center_y=center_y,
                entry_half_height=entry_half_height,
                entry_width=entry_width,
                arrow_depth=arrow_depth,
            )
        if self.side == SheetEntrySide.TOP:
            return self._build_harness_top_layout(
                parent_x=parent_x,
                parent_y=parent_y,
                offset=offset,
                entry_half_height=entry_half_height,
                entry_width=entry_width,
                arrow_depth=arrow_depth,
            )
        return self._build_harness_bottom_layout(
            parent_x=parent_x,
            parent_y=parent_y,
            parent_height=parent_height,
            offset=offset,
            entry_half_height=entry_half_height,
            entry_width=entry_width,
            arrow_depth=arrow_depth,
        )

    def _build_standard_left_layout(
        self,
        *,
        parent_x: float,
        center_y: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        x_left = parent_x
        x_right = parent_x + entry_width
        x_inner_left = x_left + arrow_depth
        x_inner_right = x_right - arrow_depth
        if (
            self.arrow_kind == SchSheetEntryArrowKind.BLOCK_TRIANGLE
            and self.io_type == SchSheetEntryIOType.OUTPUT
        ):
            points = [
                (x_right, center_y - entry_half_height),
                (x_right, center_y + entry_half_height),
                (x_inner_left, center_y + entry_half_height),
                (x_left, center_y),
                (x_inner_left, center_y - entry_half_height),
            ]
        elif (
            self.arrow_kind == SchSheetEntryArrowKind.BLOCK_TRIANGLE
            and self.io_type == SchSheetEntryIOType.INPUT
        ):
            points = [
                (x_inner_right, center_y - entry_half_height),
                (x_right, center_y),
                (x_inner_right, center_y + entry_half_height),
                (x_left, center_y + entry_half_height),
                (x_left, center_y - entry_half_height),
            ]
        elif (
            self.arrow_kind == SchSheetEntryArrowKind.BLOCK_TRIANGLE
            and self.io_type == SchSheetEntryIOType.BIDIRECTIONAL
        ):
            points = [
                (x_inner_right, center_y - entry_half_height),
                (x_right, center_y),
                (x_inner_right, center_y + entry_half_height),
                (x_inner_left, center_y + entry_half_height),
                (x_left, center_y),
                (x_inner_left, center_y - entry_half_height),
            ]
        elif (
            self.arrow_kind == SchSheetEntryArrowKind.BLOCK_TRIANGLE
            and self.io_type == SchSheetEntryIOType.UNSPECIFIED
        ):
            points = [
                (x_right, center_y - entry_half_height),
                (x_right, center_y + entry_half_height),
                (x_left, center_y + entry_half_height),
                (x_left, center_y + entry_half_height),
                (x_left, center_y - entry_half_height),
            ]
        elif self.io_type == SchSheetEntryIOType.OUTPUT:
            x_arrow_start = x_right - arrow_depth
            points = [
                (x_left, center_y - entry_half_height),
                (x_arrow_start, center_y - entry_half_height),
                (x_right, center_y),
                (x_arrow_start, center_y + entry_half_height),
                (x_left, center_y + entry_half_height),
            ]
        elif self.io_type == SchSheetEntryIOType.INPUT:
            x_arrow_end = x_left + arrow_depth
            points = [
                (x_left, center_y),
                (x_arrow_end, center_y - entry_half_height),
                (x_right, center_y - entry_half_height),
                (x_right, center_y + entry_half_height),
                (x_arrow_end, center_y + entry_half_height),
            ]
        elif self.io_type == SchSheetEntryIOType.BIDIRECTIONAL:
            x_arrow_left = x_left + arrow_depth
            x_arrow_right = x_right - arrow_depth
            points = [
                (x_arrow_right, center_y - entry_half_height),
                (x_right, center_y),
                (x_arrow_right, center_y + entry_half_height),
                (x_arrow_left, center_y + entry_half_height),
                (x_left, center_y),
                (x_arrow_left, center_y - entry_half_height),
            ]
        else:
            points = [
                (x_left, center_y - entry_half_height),
                (x_right, center_y - entry_half_height),
                (x_right, center_y + entry_half_height),
                (x_left, center_y + entry_half_height),
            ]
        return _SheetEntryGeometryLayout(
            points=points, shadow_points=[], text_x=x_right + 5, text_y=center_y
        )

    def _build_standard_right_layout(
        self,
        *,
        parent_x: float,
        parent_width: float,
        center_y: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        x_right = parent_x + parent_width
        x_left = x_right - entry_width
        if self.io_type == SchSheetEntryIOType.OUTPUT:
            x_arrow_start = x_right - arrow_depth
            points = [
                (x_arrow_start, center_y - entry_half_height),
                (x_right, center_y),
                (x_arrow_start, center_y + entry_half_height),
                (x_left, center_y + entry_half_height),
                (x_left, center_y - entry_half_height),
            ]
        elif self.io_type == SchSheetEntryIOType.INPUT:
            x_arrow_end = x_left + arrow_depth
            points = [
                (x_left, center_y),
                (x_arrow_end, center_y - entry_half_height),
                (x_right, center_y - entry_half_height),
                (x_right, center_y + entry_half_height),
                (x_arrow_end, center_y + entry_half_height),
            ]
        elif self.io_type == SchSheetEntryIOType.BIDIRECTIONAL:
            x_arrow_left = x_left + arrow_depth
            x_arrow_right = x_right - arrow_depth
            points = [
                (x_arrow_right, center_y - entry_half_height),
                (x_right, center_y),
                (x_arrow_right, center_y + entry_half_height),
                (x_arrow_left, center_y + entry_half_height),
                (x_left, center_y),
                (x_arrow_left, center_y - entry_half_height),
            ]
        else:
            points = [
                (x_left, center_y - entry_half_height),
                (x_right, center_y - entry_half_height),
                (x_right, center_y + entry_half_height),
                (x_left, center_y + entry_half_height),
            ]
        return _SheetEntryGeometryLayout(
            points=points, shadow_points=[], text_x=x_left - 5, text_y=center_y
        )

    def _build_standard_top_layout(
        self,
        *,
        parent_x: float,
        parent_y: float,
        offset: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        center_x = parent_x + offset
        y_top = parent_y
        y_bottom = parent_y + entry_width
        if self.io_type == SchSheetEntryIOType.OUTPUT:
            y_arrow_start = y_top + arrow_depth
            points = [
                (center_x - entry_half_height, y_arrow_start),
                (center_x, y_top),
                (center_x + entry_half_height, y_arrow_start),
                (center_x + entry_half_height, y_bottom),
                (center_x - entry_half_height, y_bottom),
            ]
        elif self.io_type == SchSheetEntryIOType.INPUT:
            y_arrow_end = y_bottom - arrow_depth
            points = [
                (center_x - entry_half_height, y_top),
                (center_x + entry_half_height, y_top),
                (center_x + entry_half_height, y_arrow_end),
                (center_x, y_bottom),
                (center_x - entry_half_height, y_arrow_end),
            ]
        elif self.io_type == SchSheetEntryIOType.BIDIRECTIONAL:
            y_arrow_top = y_top + arrow_depth
            y_arrow_bottom = y_bottom - arrow_depth
            points = [
                (center_x, y_top),
                (center_x + entry_half_height, y_arrow_top),
                (center_x + entry_half_height, y_arrow_bottom),
                (center_x, y_bottom),
                (center_x - entry_half_height, y_arrow_bottom),
                (center_x - entry_half_height, y_arrow_top),
            ]
        else:
            points = [
                (center_x - entry_half_height, y_top),
                (center_x + entry_half_height, y_top),
                (center_x + entry_half_height, y_bottom),
                (center_x - entry_half_height, y_bottom),
            ]
        return _SheetEntryGeometryLayout(
            points=points,
            shadow_points=[],
            text_x=center_x + entry_half_height - 1,
            text_y=y_bottom + 5,
        )

    def _build_standard_bottom_layout(
        self,
        *,
        parent_x: float,
        parent_y: float,
        parent_height: float,
        offset: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        center_x = parent_x + offset
        y_bottom = parent_y + parent_height
        y_top = y_bottom - entry_width
        if self.io_type == SchSheetEntryIOType.OUTPUT:
            y_arrow_start = y_bottom - arrow_depth
            points = [
                (center_x - entry_half_height, y_top),
                (center_x + entry_half_height, y_top),
                (center_x + entry_half_height, y_arrow_start),
                (center_x, y_bottom),
                (center_x - entry_half_height, y_arrow_start),
            ]
        elif self.io_type == SchSheetEntryIOType.INPUT:
            y_arrow_end = y_top + arrow_depth
            points = [
                (center_x - entry_half_height, y_arrow_end),
                (center_x, y_top),
                (center_x + entry_half_height, y_arrow_end),
                (center_x + entry_half_height, y_bottom),
                (center_x - entry_half_height, y_bottom),
            ]
        elif self.io_type == SchSheetEntryIOType.BIDIRECTIONAL:
            y_arrow_top = y_top + arrow_depth
            y_arrow_bottom = y_bottom - arrow_depth
            points = [
                (center_x, y_top),
                (center_x + entry_half_height, y_arrow_top),
                (center_x + entry_half_height, y_arrow_bottom),
                (center_x, y_bottom),
                (center_x - entry_half_height, y_arrow_bottom),
                (center_x - entry_half_height, y_arrow_top),
            ]
        else:
            points = [
                (center_x - entry_half_height, y_top),
                (center_x + entry_half_height, y_top),
                (center_x + entry_half_height, y_bottom),
                (center_x - entry_half_height, y_bottom),
            ]
        return _SheetEntryGeometryLayout(
            points=points,
            shadow_points=[],
            text_x=center_x + entry_half_height - 1,
            text_y=y_top - 5,
        )

    def _build_standard_layout(
        self,
        *,
        parent_x: float,
        parent_y: float,
        parent_width: float,
        parent_height: float,
        offset: float,
        center_y: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        if self.side == SheetEntrySide.LEFT:
            return self._build_standard_left_layout(
                parent_x=parent_x,
                center_y=center_y,
                entry_half_height=entry_half_height,
                entry_width=entry_width,
                arrow_depth=arrow_depth,
            )
        if self.side == SheetEntrySide.RIGHT:
            return self._build_standard_right_layout(
                parent_x=parent_x,
                parent_width=parent_width,
                center_y=center_y,
                entry_half_height=entry_half_height,
                entry_width=entry_width,
                arrow_depth=arrow_depth,
            )
        if self.side == SheetEntrySide.TOP:
            return self._build_standard_top_layout(
                parent_x=parent_x,
                parent_y=parent_y,
                offset=offset,
                entry_half_height=entry_half_height,
                entry_width=entry_width,
                arrow_depth=arrow_depth,
            )
        return self._build_standard_bottom_layout(
            parent_x=parent_x,
            parent_y=parent_y,
            parent_height=parent_height,
            offset=offset,
            entry_half_height=entry_half_height,
            entry_width=entry_width,
            arrow_depth=arrow_depth,
        )

    def _entry_location(
        self,
        *,
        parent_x: float,
        parent_y: float,
        parent_width: float,
        parent_height: float,
        offset: float,
        center_y: float,
    ) -> tuple[float, float]:
        if self.side == SheetEntrySide.RIGHT:
            return (parent_x + parent_width, center_y)
        if self.side == SheetEntrySide.TOP:
            return (parent_x + offset, parent_y)
        if self.side == SheetEntrySide.BOTTOM:
            return (parent_x + offset, parent_y + parent_height)
        return (parent_x, center_y)

    def _effective_port_style(self, is_harness_entry: bool) -> int:
        if is_harness_entry:
            return 3
        style = int(self.style or 0)
        if self.side in (SheetEntrySide.LEFT, SheetEntrySide.RIGHT):
            if self.io_type == SchSheetEntryIOType.BIDIRECTIONAL:
                return 3
            if (
                self.io_type == SchSheetEntryIOType.OUTPUT
                and self.side == SheetEntrySide.RIGHT
            ) or (
                self.io_type == SchSheetEntryIOType.INPUT
                and self.side == SheetEntrySide.LEFT
            ):
                return 2
            if (
                self.io_type == SchSheetEntryIOType.INPUT
                and self.side == SheetEntrySide.RIGHT
            ) or (
                self.io_type == SchSheetEntryIOType.OUTPUT
                and self.side == SheetEntrySide.LEFT
            ):
                return 1
            return style

        if self.io_type == SchSheetEntryIOType.BIDIRECTIONAL:
            return 3
        if (
            self.io_type == SchSheetEntryIOType.OUTPUT
            and self.side == SheetEntrySide.TOP
        ) or (
            self.io_type == SchSheetEntryIOType.INPUT
            and self.side == SheetEntrySide.BOTTOM
        ):
            return 1
        if (
            self.io_type == SchSheetEntryIOType.INPUT
            and self.side == SheetEntrySide.TOP
        ) or (
            self.io_type == SchSheetEntryIOType.OUTPUT
            and self.side == SheetEntrySide.BOTTOM
        ):
            return 2
        return style

    def _convert_style_to_left_right(self, style: int) -> int:
        converted = style
        if self.side in (SheetEntrySide.TOP, SheetEntrySide.BOTTOM):
            if converted == 1:
                converted = 2
            elif converted == 2:
                converted = 1
        return {4: 0, 5: 2, 6: 1, 7: 3}.get(converted, converted)

    @staticmethod
    def _move_points(
        points: list[tuple[float, float]], dx: float, dy: float
    ) -> list[tuple[float, float]]:
        return [(x + dx, y + dy) for x, y in points]

    @staticmethod
    def _rotate_points_90(
        points: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        return [(-y, x) for x, y in points]

    @staticmethod
    def _native_local_to_svg_points(
        points: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        return [(x, -y) for x, y in points]

    def _finish_native_points(
        self,
        points: list[tuple[float, float]],
        *,
        location_x: float,
        location_y: float,
        symbol_width: float,
    ) -> list[tuple[float, float]]:
        if self.side in (SheetEntrySide.RIGHT, SheetEntrySide.TOP):
            points = self._move_points(points, -symbol_width, 0)
        if self.side in (SheetEntrySide.BOTTOM, SheetEntrySide.TOP):
            points = self._rotate_points_90(points)
        points = self._native_local_to_svg_points(points)
        return self._move_points(points, location_x, location_y)

    def _native_triangle_points(
        self,
        *,
        style: int,
        location_x: float,
        location_y: float,
        symbol_width: float,
    ) -> list[tuple[float, float]]:
        arrow_height = 4.0
        half_width = symbol_width / 2.0
        converted = self._convert_style_to_left_right(style)
        if converted in (2, 3):
            points = [
                (0, -arrow_height),
                (0, arrow_height),
                (half_width, 0),
                (0, -arrow_height),
            ]
            x_extent = half_width
        else:
            points = [(0, arrow_height), (0, -arrow_height)]
            x_extent = 0.0
        if converted in (1, 3):
            points = self._move_points(points, half_width, 0)
            points.extend(
                [
                    (half_width, -arrow_height),
                    (0, 0),
                    (half_width, arrow_height),
                ]
            )
            x_extent += half_width
        else:
            if converted not in (2, 3):
                points = self._move_points(points, half_width * 2, 0)
                x_extent += half_width * 2
            points.extend([(0, -arrow_height), (0, arrow_height)])
        if self.side in (SheetEntrySide.RIGHT, SheetEntrySide.TOP):
            points = self._move_points(points, -x_extent, 0)
        if self.side in (SheetEntrySide.BOTTOM, SheetEntrySide.TOP):
            points = self._rotate_points_90(points)
        points = self._native_local_to_svg_points(points)
        return self._move_points(points, location_x, location_y)

    @staticmethod
    def _create_arrow_points(
        height: float,
        width: float,
        thickness: float,
    ) -> list[tuple[float, float]]:
        import math

        half_height = height / 2.0
        half_thickness = thickness / 2.0
        angle = math.atan2(half_height, width)
        arrow_len = thickness / math.sin(angle)
        points: list[tuple[float, float]] = [
            (
                width - arrow_len - half_thickness / math.tan(angle),
                half_thickness,
            )
        ]
        inner_height = half_height - half_thickness
        arc_center_x = width - arrow_len / 2.0 - inner_height / math.tan(angle)
        for degree in range(180, -1, -1):
            theta = degree / 180.0 * math.pi + math.pi / 2.0 - angle
            points.append(
                (
                    arc_center_x + half_thickness * math.cos(theta),
                    inner_height + half_thickness * math.sin(theta),
                )
            )
        points.append((width, 0))
        points.extend((x, -y) for x, y in reversed(points))
        return points

    def _native_arrow_points(
        self,
        *,
        style: int,
        location_x: float,
        location_y: float,
        symbol_width: float,
    ) -> list[tuple[float, float]]:
        converted = self._convert_style_to_left_right(style)
        width_third = symbol_width / 3.0
        half_thickness = 1.0
        points: list[tuple[float, float]] = []
        if converted in (2, 3):
            right_arrow = self._create_arrow_points(8.0, width_third, 2.0)
            points.extend(self._move_points(right_arrow, width_third * 2, 0))
        else:
            points.extend(
                [
                    (width_third * 3, half_thickness),
                    (width_third * 3, -half_thickness),
                ]
            )
        if converted in (1, 3):
            left_arrow = [
                (-x, y) for x, y in self._create_arrow_points(8.0, width_third, 2.0)
            ]
            left_arrow = self._move_points(left_arrow, width_third, 0)
            points.extend(reversed(left_arrow))
        else:
            points.extend([(0, -half_thickness), (0, half_thickness)])
        return self._finish_native_points(
            points,
            location_x=location_x,
            location_y=location_y,
            symbol_width=symbol_width,
        )

    def _native_arrow_tail_points(
        self,
        *,
        style: int,
        location_x: float,
        location_y: float,
        symbol_width: float,
    ) -> list[tuple[float, float]]:
        arrow_height = 4.0
        arrow_depth = 4.0
        converted = self._convert_style_to_left_right(style)
        points: list[tuple[float, float]] = []
        if converted in (2, 3):
            points.extend(
                [
                    (symbol_width - arrow_depth, arrow_height),
                    (symbol_width, 0),
                    (symbol_width - arrow_depth, -arrow_height),
                ]
            )
        else:
            points.extend(
                [
                    (symbol_width, arrow_height),
                    (symbol_width - arrow_depth, 0),
                    (symbol_width, -arrow_height),
                ]
            )
        if converted in (1, 3):
            points.extend(
                [(arrow_depth, -arrow_height), (0, 0), (arrow_depth, arrow_height)]
            )
        else:
            points.extend([(0, -arrow_height), (arrow_depth, 0), (0, arrow_height)])
        return self._finish_native_points(
            points,
            location_x=location_x,
            location_y=location_y,
            symbol_width=symbol_width,
        )

    def _native_rect_and_triangle_points(
        self,
        *,
        style: int,
        location_x: float,
        location_y: float,
        symbol_width: float,
    ) -> list[tuple[float, float]]:
        arrow_height = 4.0
        arrow_depth = 4.0
        converted = self._convert_style_to_left_right(style)
        points: list[tuple[float, float]] = []
        if converted in (2, 3):
            points.extend(
                [
                    (symbol_width - arrow_depth, arrow_height),
                    (symbol_width, 0),
                    (symbol_width - arrow_depth, -arrow_height),
                ]
            )
        else:
            points.extend([(symbol_width, arrow_height), (symbol_width, -arrow_height)])
        if converted in (1, 3):
            points.extend(
                [(arrow_depth, -arrow_height), (0, 0), (arrow_depth, arrow_height)]
            )
        else:
            points.extend([(0, -arrow_height), (0, arrow_height)])
        return self._finish_native_points(
            points,
            location_x=location_x,
            location_y=location_y,
            symbol_width=symbol_width,
        )

    def _build_native_entry_layout(
        self,
        ctx: "SchSvgRenderContext",
        *,
        parent_x: float,
        parent_y: float,
        parent_width: float,
        parent_height: float,
        offset: float,
        center_y: float,
    ) -> _SheetEntryGeometryLayout:
        is_harness_entry = self._effective_is_harness_entry(ctx)
        harness_color = self._effective_harness_color(ctx)
        style = self._effective_port_style(is_harness_entry)
        symbol_width = self._entry_symbol_width()
        location_x, location_y = self._entry_location(
            parent_x=parent_x,
            parent_y=parent_y,
            parent_width=parent_width,
            parent_height=parent_height,
            offset=offset,
            center_y=center_y,
        )

        if self.arrow_kind == SchSheetEntryArrowKind.TRIANGLE:
            points = self._native_triangle_points(
                style=style,
                location_x=location_x,
                location_y=location_y,
                symbol_width=symbol_width,
            )
        elif self.arrow_kind == SchSheetEntryArrowKind.ARROW:
            points = self._native_arrow_points(
                style=style,
                location_x=location_x,
                location_y=location_y,
                symbol_width=symbol_width,
            )
        elif self.arrow_kind == SchSheetEntryArrowKind.ARROW_TAIL:
            points = self._native_arrow_tail_points(
                style=style,
                location_x=location_x,
                location_y=location_y,
                symbol_width=symbol_width,
            )
        else:
            points = self._native_rect_and_triangle_points(
                style=style,
                location_x=location_x,
                location_y=location_y,
                symbol_width=symbol_width,
            )

        if self.side == SheetEntrySide.RIGHT:
            text_x = location_x - (symbol_width + 5)
            text_y = location_y
        elif self.side == SheetEntrySide.TOP:
            text_x = location_x + 3
            text_y = location_y + (symbol_width + 5)
        elif self.side == SheetEntrySide.BOTTOM:
            text_x = location_x + 3
            text_y = location_y - (symbol_width + 5)
        else:
            text_x = location_x + symbol_width + 5
            text_y = location_y

        if not is_harness_entry:
            return _SheetEntryGeometryLayout(
                points=points,
                shadow_points=[],
                text_x=text_x,
                text_y=text_y,
            )

        palette = self._harness_palette(harness_color)
        return _SheetEntryGeometryLayout(
            points=points,
            shadow_points=[(x + 0.5, y + 1.0) for x, y in points],
            text_x=text_x,
            text_y=text_y,
            harness_fill=palette[0],
            harness_stroke=palette[1],
            shadow_fill=palette[2],
            shadow_stroke=palette[3],
        )

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        parent_x: float = 0,
        parent_y: float = 0,
        parent_width: float = 400,
        parent_height: float = 300,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this sheet entry.
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

        offset = self._distance_from_top_native_units() * ctx.scale

        center_y = parent_y + offset
        stroke_color_raw, fill_color_raw = self._sheet_entry_fill_colors()
        layout = self._build_native_entry_layout(
            ctx,
            parent_x=parent_x,
            parent_y=parent_y,
            parent_width=parent_width,
            parent_height=parent_height,
            offset=offset,
            center_y=center_y,
        )
        points = layout.points
        shadow_points = layout.shadow_points
        text_x = layout.text_x
        text_y = layout.text_y

        display_text = self.display_name
        (
            clean_text,
            text_width,
            text_x,
            text_y,
            text_rotation,
            baseline_font_size,
            font_payload,
        ) = self._sheet_entry_text_layout(
            ctx,
            display_text,
            text_x,
            text_y,
            units_per_px,
            split_overline_text,
            measure_text_width,
            make_font_payload,
        )
        operations = self._sheet_entry_polygon_operations(
            ctx,
            layout,
            points,
            shadow_points,
            fill_color_raw,
            stroke_color_raw,
            units_per_px,
            SchGeometryOp,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
        )
        if clean_text:
            operations.append(
                self._sheet_entry_text_operation(
                    ctx,
                    clean_text,
                    text_x,
                    text_y,
                    text_rotation,
                    baseline_font_size,
                    font_payload,
                    units_per_px,
                    SchGeometryOp,
                    make_solid_brush,
                    svg_coord_to_geometry,
                )
            )

        all_points = [*points, *shadow_points]
        min_x = min((point[0] for point in all_points), default=parent_x)
        max_x = max((point[0] for point in all_points), default=parent_x)
        min_y = min((point[1] for point in all_points), default=parent_y)
        max_y = max((point[1] for point in all_points), default=parent_y)

        if clean_text:
            min_x, max_x, min_y, max_y = self._expand_text_bounds(
                min_x,
                max_x,
                min_y,
                max_y,
                text_x,
                text_y,
                text_width,
                baseline_font_size,
                text_rotation,
            )

        sheet_height = float(ctx.sheet_height or 0.0)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="sheetentry",
            object_id="eSheetEntry",
            bounds=SchGeometryBounds(
                left=int(math.floor(min_x * 100000)),
                top=int(math.floor((sheet_height - min_y) * 100000)),
                right=int(math.ceil(max_x * 100000)),
                bottom=int(math.ceil((sheet_height - max_y) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _sheet_entry_fill_colors(self) -> tuple[int, int]:
        stroke_color_raw = int(self.color) if self.color is not None else 0x101010
        fill_color_raw = (
            int(self.area_color) if self._has_area_color else stroke_color_raw
        )
        return stroke_color_raw, fill_color_raw

    def _build_geometry_layout(
        self,
        *,
        parent_x: float,
        parent_y: float,
        parent_width: float,
        parent_height: float,
        offset: float,
        center_y: float,
        entry_half_height: float,
        entry_width: float,
        arrow_depth: float,
    ) -> _SheetEntryGeometryLayout:
        if self.is_harness_entry:
            return self._build_harness_layout(
                parent_x=parent_x,
                parent_y=parent_y,
                parent_width=parent_width,
                parent_height=parent_height,
                offset=offset,
                center_y=center_y,
                entry_half_height=entry_half_height,
                entry_width=entry_width,
                arrow_depth=arrow_depth,
            )
        return self._build_standard_layout(
            parent_x=parent_x,
            parent_y=parent_y,
            parent_width=parent_width,
            parent_height=parent_height,
            offset=offset,
            center_y=center_y,
            entry_half_height=entry_half_height,
            entry_width=entry_width,
            arrow_depth=arrow_depth,
        )

    def _sheet_entry_text_layout(
        self,
        ctx: "SchSvgRenderContext",
        display_text: str,
        text_x: float,
        text_y: float,
        units_per_px: int,
        split_overline_text: Any,
        measure_text_width: Any,
        make_font_payload: Any,
    ) -> tuple[str, float, float, float, float, float, Any]:
        font_name, font_size_px, font_bold, font_italic, _ = ctx.get_font_info(
            self.text_font_id
        )
        font_size_for_width = ctx.get_font_size_for_width(self.text_font_id)
        font_spec = (
            ctx.font_manager.get_font_info(self.text_font_id)
            if ctx.font_manager
            else None
        )
        baseline_font_size = float(int(font_size_px))
        clean_text, _ = split_overline_text(display_text)
        text_width = (
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
        vertical_center_offset = max(0, (int(baseline_font_size) - 1) // 2)
        text_rotation = (
            -90.0 if self.side in (SheetEntrySide.TOP, SheetEntrySide.BOTTOM) else 0.0
        )
        if display_text:
            if self.side == SheetEntrySide.LEFT:
                text_y = text_y + vertical_center_offset
            elif self.side == SheetEntrySide.RIGHT:
                text_x = text_x - text_width
                text_y = text_y + vertical_center_offset
            elif self.side == SheetEntrySide.TOP:
                text_y = text_y + text_width
        font_payload = make_font_payload(
            name=str(font_spec.get("name", font_name)) if font_spec else str(font_name),
            size_px=font_size_px,
            units_per_px=units_per_px,
            rotation=text_rotation,
            underline=bool(font_spec.get("underline", False)) if font_spec else False,
            italic=bool(font_spec.get("italic", font_italic))
            if font_spec
            else bool(font_italic),
            bold=bool(font_spec.get("bold", font_bold))
            if font_spec
            else bool(font_bold),
            strikeout=bool(font_spec.get("strikeout", False)) if font_spec else False,
        )
        return (
            clean_text,
            text_width,
            text_x,
            text_y,
            text_rotation,
            baseline_font_size,
            font_payload,
        )

    def _sheet_entry_polygon_operations(
        self,
        ctx: "SchSvgRenderContext",
        layout: _SheetEntryGeometryLayout,
        points: list[tuple[float, float]],
        shadow_points: list[tuple[float, float]],
        fill_color_raw: int,
        stroke_color_raw: int,
        units_per_px: int,
        geometry_op_cls: Any,
        make_pen: Any,
        make_solid_brush: Any,
        svg_coord_to_geometry: Any,
    ) -> list[Any]:
        def geometry_polygon(
            poly_points: list[tuple[float, float]],
        ) -> list[list[float]]:
            return [
                list(
                    svg_coord_to_geometry(
                        point_x,
                        point_y,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                    )
                )
                for point_x, point_y in poly_points
            ]

        operations: list[Any] = []
        if self._effective_is_harness_entry(ctx):
            if shadow_points:
                shadow_polygon = geometry_polygon(shadow_points)
                operations.append(
                    geometry_op_cls.polygons(
                        [shadow_polygon],
                        brush=make_solid_brush(
                            _sheet_entry_color_raw_from_hex(
                                layout.shadow_fill or "#000000"
                            ),
                            alpha=0x7D,
                        ),
                    )
                )
                operations.append(
                    geometry_op_cls.polygons(
                        [shadow_polygon],
                        pen=make_pen(
                            _sheet_entry_color_raw_from_hex(
                                layout.shadow_stroke or "#000000"
                            ),
                            width=0,
                        ),
                    )
                )
            if points:
                main_polygon = geometry_polygon(points)
                operations.append(
                    geometry_op_cls.polygons(
                        [main_polygon],
                        brush=make_solid_brush(
                            _sheet_entry_color_raw_from_hex(
                                layout.harness_fill or "#000000"
                            )
                        ),
                    )
                )
                operations.append(
                    geometry_op_cls.polygons(
                        [main_polygon],
                        pen=make_pen(
                            _sheet_entry_color_raw_from_hex(
                                layout.harness_stroke or "#000000"
                            ),
                            width=0,
                        ),
                    )
                )
            return operations

        if points:
            main_polygon = geometry_polygon(points)
            operations.append(
                geometry_op_cls.polygons(
                    [main_polygon],
                    brush=make_solid_brush(fill_color_raw),
                )
            )
            operations.append(
                geometry_op_cls.polygons(
                    [main_polygon],
                    pen=make_pen(stroke_color_raw, width=0),
                )
            )
        return operations

    def _sheet_entry_text_operation(
        self,
        ctx: "SchSvgRenderContext",
        clean_text: str,
        text_x: float,
        text_y: float,
        text_rotation: float,
        baseline_font_size: float,
        font_payload: Any,
        units_per_px: int,
        geometry_op_cls: Any,
        make_solid_brush: Any,
        svg_coord_to_geometry: Any,
    ) -> Any:
        if text_rotation != 0.0:
            geometry_text_x_px = text_x - baseline_font_size
            geometry_text_y_px = text_y
        else:
            geometry_text_x_px = text_x
            geometry_text_y_px = text_y - baseline_font_size
        geometry_text_x, geometry_text_y = svg_coord_to_geometry(
            geometry_text_x_px,
            geometry_text_y_px,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        return geometry_op_cls.string(
            x=geometry_text_x,
            y=geometry_text_y,
            text=clean_text,
            font=font_payload,
            brush=make_solid_brush(int(self.text_color or 0)),
        )

    def _expand_text_bounds(
        self,
        min_x: float,
        max_x: float,
        min_y: float,
        max_y: float,
        text_x: float,
        text_y: float,
        text_width: float,
        baseline_font_size: float,
        text_rotation: float,
    ) -> tuple[float, float, float, float]:
        if text_rotation != 0.0:
            return (
                min(min_x, text_x - baseline_font_size),
                max(max_x, text_x),
                min(min_y, text_y - text_width),
                max(max_y, text_y),
            )
        return (
            min(min_x, text_x),
            max(max_x, text_x + text_width),
            min(min_y, text_y - baseline_font_size),
            max(max_y, text_y),
        )

    def __repr__(self) -> str:
        side_name = ["Left", "Right", "Top", "Bottom"][min(self.side, 3)]
        return f"<AltiumSchSheetEntry '{self.display_name}' side={side_name} io_type={self.io_type}>"


# =============================================================================
# Factory Function
# =============================================================================
