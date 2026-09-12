"""Schematic record model for SchRecordType.POWER_PORT."""

import hashlib
import math
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryOp
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import (
    SchGraphicalObject,
    SchRecordType,
    TextOrientation,
    color_to_hex,
    rgb_to_win32_color,
)
from ._sch_managed_defaults import GRAPHICAL_FILL_COLOR, POWER_COLOR
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_sch_enums import PowerObjectStyle
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import _RecordFields
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key


def _is_bus_string(text: str) -> bool:
    opening, separator, closing = text.find("["), text.find(".."), text.find("]")
    return 0 <= opening < separator < closing


def _iter_utf16_code_units(text: str) -> Iterator[int]:
    for character in text:
        code_point = ord(character)
        if code_point <= 0xFFFF:
            yield code_point
            continue
        scalar = code_point - 0x10000
        yield 0xD800 + (scalar >> 10)
        yield 0xDC00 + (scalar & 0x3FF)


def _text_from_utf16_code_units(code_units: Sequence[int]) -> str:
    encoded = bytearray(len(code_units) * 2)
    for index, code_unit in enumerate(code_units):
        encoded[index * 2 : index * 2 + 2] = code_unit.to_bytes(2, "little")
    return encoded.decode("utf-16-le", errors="surrogatepass")


class AltiumSchPowerPort(SingleFontBindableRecordMixin, SchGraphicalObject):
    """
    Power port record.

    Power/ground symbols (VCC, GND, +5V, etc.).

    PROPERTIES:
        Direct properties:
        - Style: power symbol style (0-10)
        - ShowNetName: bool - whether to display net name

        Label-like properties:
        - Text: string - net name (e.g., "VCC", "GND")
        - FontId: int - font ID for text
        - Orientation: rotation by 90-degree steps (0-3)
        - Justification: text alignment (0-8)
        - IsMirrored: bool - text mirrored
        - OverrideDisplayString: string - override display text

    STYLE VALUES:
        0 = circle
        1 = triangle with base line
        2 = simple perpendicular line
        3 = two arcs forming S-curve
        4 = 4 decreasing horizontal lines (classic GND)
        5 = line ending in filled triangle
        6 = line with 3 diagonal lines
        7 = triangle without base line
        8 = GOST standard ground power
        9 = GOST standard earth ground
        10 = longer perpendicular line
    """

    def __init__(self) -> None:
        super().__init__()
        self.color = POWER_COLOR
        self.area_color = GRAPHICAL_FILL_COLOR
        self._init_family_dynamic_unique_id()
        self._init_single_font_binding()
        self._use_pascal_case: bool = True
        # Core power port properties
        self.text: str = "Text"  # Net name (e.g., "VCC", "GND")
        self.font_id: int = 1
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.style: PowerObjectStyle = PowerObjectStyle.BAR
        self.show_net_name: bool = True
        self._has_show_net_name: bool = False

        # Inherited from ISch_Label interface
        self.justification: int = 0  # 0=BottomLeft through 8=TopRight
        self.is_mirrored: bool = False  # Text mirrored
        self.override_display_string: str = ""  # Override display text

        # Cross-sheet connector (off-sheet connector)
        # When True, renders as X-shaped symbol instead of power symbol
        self.is_cross_sheet_connector: bool = False

        # Custom power port graphics (from ObjectDefinitions stream)
        # When non-empty, overrides Style-based rendering with custom primitives
        self.object_definition_id: str = ""
        self._has_text = False
        self._used_utf8_text = False
        self._has_font_id = False
        self._has_orientation = False
        self._has_style = False
        self._has_is_cross_sheet_connector = False
        self._has_object_definition_id = False
        self._used_utf8_object_definition_id = False
        self._capture_graphical_source_state()
        self._capture_power_source_state()

    def _capture_power_source_state(self) -> None:
        self._source_text = self.text
        self._source_font_id = self.font_id
        self._source_orientation = self.orientation
        self._source_style = self.style
        self._source_show_net_name = self.show_net_name
        self._source_is_cross_sheet_connector = self.is_cross_sheet_connector
        self._source_object_definition_id = self.object_definition_id

    @staticmethod
    def _default_show_net_name(style: int | PowerObjectStyle) -> bool:
        return int(style) not in {
            PowerObjectStyle.GND_POWER.value,
            PowerObjectStyle.GND_SIGNAL.value,
            PowerObjectStyle.GND_EARTH.value,
            PowerObjectStyle.GOST_GND_POWER.value,
            PowerObjectStyle.GOST_GND_EARTH.value,
        }

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.POWER_PORT

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
        self._use_pascal_case = "Text" in record or "Style" in record

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()
        r = self._record
        self._parse_family_dynamic_unique_id(s, record)

        # Core power port fields
        self.text, self._has_text, self._used_utf8_text = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT,
            default="",
        )
        # Use read_font_id for translation support
        self.font_id, self._has_font_id = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=0
        )
        orient_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = TextOrientation(orient_val)
        style_val, self._has_style = s.read_int(record, Fields.STYLE, default=0)
        self.style = PowerObjectStyle(style_val)
        self.show_net_name, self._has_show_net_name = s.read_bool(
            record,
            Fields.SHOW_NET_NAME,
            default=self._default_show_net_name(self.style),
        )

        # Cross-sheet connector flag
        self.is_cross_sheet_connector, self._has_is_cross_sheet_connector = s.read_bool(
            record, Fields.IS_CROSS_SHEET_CONNECTOR, default=False
        )

        # Custom power port definition (from OrCAD/Allegro import or custom symbols)
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
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()
        self._capture_power_source_state()

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
        self._serialize_power_text(record, s, raw)
        self._serialize_power_style(record, s, raw)
        self._serialize_power_options(record, s, raw)
        s.remove_field(record, Fields.JUSTIFICATION)
        s.remove_field(record, Fields.IS_MIRRORED)
        s.remove_field(record, Fields.URL)
        self._remove_fields_case_insensitively(record, ["%UTF8%URL"])
        s.remove_field(record, Fields.IS_HIDDEN)
        s.remove_field(record, Fields.OVERRIDE_DISPLAY_STRING)
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(
            record,
            (
                "Style",
                "ShowNetName",
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Orientation",
                "Color",
                "FontID",
                "Text",
                "IsCrossSheetConnector",
                "UniqueID",
                "ObjectDefinitionId",
            ),
        )

    def _serialize_power_text(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
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
        self._serialize_managed_font_id(
            record,
            serializer,
            Fields.FONT_ID.canonical,
            self.font_id,
            self._get_fallback_font_manager(),
            default=0,
        )

    def _serialize_power_style(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self._has_orientation or self.orientation != self._source_orientation:
            serializer.write_int(
                record,
                Fields.ORIENTATION,
                self.orientation.value,
                raw_record,
                force=self.orientation != self._source_orientation,
            )
        else:
            serializer.remove_field(record, Fields.ORIENTATION)
        if (
            self._has_style
            or self.style != self._source_style
            or (raw_record is None and int(self.style) != 0)
        ):
            serializer.write_int(
                record,
                Fields.STYLE,
                int(self.style),
                raw_record,
                force=self.style != self._source_style,
            )
        else:
            serializer.remove_field(record, Fields.STYLE)
        if (
            raw_record is None
            or self._has_show_net_name
            or self.show_net_name != self._source_show_net_name
        ):
            serializer.write_bool(
                record,
                Fields.SHOW_NET_NAME,
                self.show_net_name,
                raw_record,
                force=self.show_net_name != self._source_show_net_name,
            )
        else:
            serializer.remove_field(record, Fields.SHOW_NET_NAME)

    def _serialize_power_options(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if (
            self._has_is_cross_sheet_connector
            or self.is_cross_sheet_connector != self._source_is_cross_sheet_connector
        ):
            serializer.write_bool(
                record,
                Fields.IS_CROSS_SHEET_CONNECTOR,
                self.is_cross_sheet_connector,
                raw_record,
                force=self.is_cross_sheet_connector
                != self._source_is_cross_sheet_connector,
            )
        else:
            serializer.remove_field(record, Fields.IS_CROSS_SHEET_CONNECTOR)

        if (
            self.object_definition_id != self._source_object_definition_id
            and not self.object_definition_id
        ):
            self._remove_fields_case_insensitively(
                record, ["ObjectDefinitionId", "%UTF8%ObjectDefinitionId"]
            )
        else:
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

    def _get_display_string(self) -> str:
        """Apply power-bus display aliases without changing the stored net name."""
        text = self.override_display_string or self.text
        if not _is_bus_string(text):
            return text
        folded = dotnet_ordinal_ignore_case_key(text)
        if "GNDBUS" in folded:
            return "GND"
        if "VCCBUS" in folded:
            return "VCC"
        return text

    def _has_visible_display_string(self, ctx: "SchSvgRenderContext") -> bool:
        if self.is_cross_sheet_connector:
            if ctx.options.optimized:
                return False
            # The managed default symbol ignores ShowNetName; custom symbols do not.
            enabled = not self.object_definition_id or self.show_net_name
        else:
            enabled = self.show_net_name
        return enabled and bool(self._get_display_string())

    def _display_font_id(self, ctx: "SchSvgRenderContext", *, custom: bool) -> int:
        if self.is_cross_sheet_connector and not custom:
            return ctx._horizontal_system_font_id
        return int(self.font_id or 0) or ctx._horizontal_system_font_id

    @staticmethod
    def _text_world_transform(ctx: "SchSvgRenderContext") -> tuple[float, float]:
        origin_x, origin_y = ctx.transform_point(0.0, 0.0)
        basis_x, basis_y = ctx.transform_point(1.0, 0.0)
        dx = basis_x - origin_x
        dy = basis_y - origin_y
        rotation = 0.0
        if ctx.mirror:
            rotation = AltiumSchPowerPort._normalize_managed_angle(180.0 - rotation)
        rotation += float(ctx.rotation)
        if ctx.sheet_height > 0 or ctx.flip_y:
            rotation = -rotation
        return math.hypot(dx, dy), rotation

    @staticmethod
    def _normalize_managed_angle(angle: float) -> float:
        while angle > 360.0:
            angle -= 360.0
        while angle < 0.0:
            angle += 360.0
        return angle

    def _custom_power_port_text_layout(
        self,
        ctx: "SchSvgRenderContext",
        *,
        custom_prims: list[dict],
        font_name: str,
        font_size_px: float,
        is_bold: bool,
        is_italic: bool,
        font_id: int | None = None,
    ) -> tuple[tuple[float, float], float, float] | None:
        """
        Match native TextRect-based placement for custom power-port labels.
        """
        if not self._has_visible_display_string(ctx):
            return None

        child_width = self._custom_visible_children_width_internal(custom_prims)

        from ._altium_record_sch__harness_layout import (
            _abs_safe_i32,
            _float_to_i32,
            _harness_internal_location,
            _trunc_i32_div,
            _unchecked_i32,
            _utf16_code_unit_prefix,
        )
        from .altium_sch_geometry_oracle import split_overline_text
        from .altium_text_metrics import (
            measure_gdi_typographic_bounds,
            measure_text_height,
            measure_text_width,
        )

        orient = self.orientation
        display_string = self._get_display_string()
        clean_text, _ = split_overline_text(
            self._get_display_string(),
            single_slash_negation=ctx.options.single_slash_negation,
        )
        effective_font_id = (
            self._display_font_id(ctx, custom=True) if font_id is None else font_id
        )
        text_width = measure_text_width(
            clean_text,
            ctx.get_font_size_for_width(effective_font_id),
            font_name,
            is_bold,
            is_italic,
        )
        text_height = measure_text_height(
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
            use_altium_algorithm=False,
        )
        bounds_name, bounds_size, bounds_bold, bounds_italic = (
            self._custom_bounds_font_info(ctx)
        )
        raw_bounds_width, raw_bounds_height = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix(display_string, 8192),
            bounds_size,
            bounds_name,
            bold=bounds_bold,
            italic=bounds_italic,
        )
        bounds_width = _float_to_i32(raw_bounds_width * 100_000, rounded=True)
        bounds_height = _float_to_i32(raw_bounds_height * 100_000, rounded=True)
        origin_x, origin_y = _harness_internal_location(self.location)
        child_width = _abs_safe_i32(_unchecked_i32(child_width))
        half_width = _trunc_i32_div(bounds_width, 2)
        half_height = _trunc_i32_div(bounds_height, 2)
        if orient == TextOrientation.DEGREES_0:
            source_x = _unchecked_i32(origin_x + child_width + 200_000) / 100_000
            source_y = _unchecked_i32(origin_y + half_height) / 100_000
        elif orient == TextOrientation.DEGREES_90:
            source_x = _unchecked_i32(origin_x - half_width) / 100_000
            source_y = _unchecked_i32(origin_y + child_width + bounds_height) / 100_000
        elif orient == TextOrientation.DEGREES_180:
            text_left = _unchecked_i32(origin_x - child_width - 200_000)
            text_left = _unchecked_i32(text_left - bounds_width - 200_000)
            source_x = text_left / 100_000
            source_y = _unchecked_i32(origin_y + half_height) / 100_000
        else:
            source_x = _unchecked_i32(origin_x - half_width) / 100_000
            source_y = _unchecked_i32(origin_y - child_width) / 100_000

        return ctx.transform_point(source_x, source_y), text_width, text_height

    def _custom_bounds_font_info(
        self, ctx: "SchSvgRenderContext"
    ) -> tuple[str, float, bool, bool]:
        if int(self.font_id or 0) != 0:
            name, _, bold, italic, _ = ctx.get_font_info(int(self.font_id))
            return (
                name,
                ctx.get_font_size_for_width(int(self.font_id)),
                bold,
                italic,
            )
        name, _, bold, italic, _ = ctx.get_system_font_info()
        return name, ctx.get_system_font_size_for_width(), bold, italic

    def _native_text_descent_offset_px(
        self,
        ctx: "SchSvgRenderContext",
        *,
        font_name: str,
        font_size_px: float,
        is_bold: bool,
        is_italic: bool,
        font_id: int,
    ) -> float:
        """
        Mirror the native/C++ descent offset used for bottom-aligned power text.
        """
        from .altium_ttf_metrics import get_font_factor

        font_factor = get_font_factor(font_name, is_bold, is_italic)
        if font_factor >= 1.0:
            return 1.0

        if ctx.font_manager:
            font_spec = ctx.font_manager.get_font_info(font_id)
            if font_spec:
                pt_size = float(font_spec.get("size", font_size_px / font_factor))
            else:
                pt_size = font_size_px / font_factor
        else:
            pt_size = font_size_px / font_factor

        return float(max(1, round((1 - font_factor) * pt_size + 0.5)))

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> Any:
        """
        Build an oracle-aligned geometry record for this power-port variant.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_point,
            _geometry_units_scale,
            _svg_coord_to_geometry_exact,
            make_font_payload,
            make_pen,
            make_solid_brush,
            make_text_with_overline_operations,
            split_overline_text,
            wrap_record_operations,
        )
        from .altium_text_metrics import measure_text_height, measure_text_width

        def split_managed_overline_text(text: str) -> tuple[str, list[int]]:
            return split_overline_text(
                text,
                single_slash_negation=ctx.options.single_slash_negation,
            )

        custom_prims: list[dict] | None = None
        if self.object_definition_id:
            if not getattr(ctx, "object_definitions", None):
                return None
            custom_prims = ctx.object_definitions.get(self.object_definition_id)
            if custom_prims is None:
                return None

        location_x, location_y = self._cross_sheet_source_point(0, 0)
        x, y = ctx.transform_point(location_x, location_y)
        sheet_height_px = float(ctx.sheet_height or 0.0)
        stroke_raw = int(self.color) if self.color is not None else 0
        # Bus classification uses stored Text, independently of display aliases.
        is_bus = (
            custom_prims is None
            and not self.is_cross_sheet_connector
            and _is_bus_string(self.text)
        )
        geometry_scale = _geometry_units_scale(units_per_px)
        ordinary_width = geometry_scale * ctx.get_stroke_scale()
        bus_width = ordinary_width * (3 if is_bus else 1)
        symbol_width = bus_width if self.style in (2, 10) else ordinary_width
        pen = make_pen(stroke_raw, width=symbol_width)
        bus_pen = make_pen(stroke_raw, width=bus_width)
        hairline_pen = make_pen(stroke_raw, width=0)

        display_font_id = self._display_font_id(ctx, custom=custom_prims is not None)
        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            display_font_id
        )
        world_scale, render_font_rotation = self._text_world_transform(ctx)
        render_font_size_px = font_size_px * world_scale
        font_spec = (
            ctx.font_manager.get_font_info(display_font_id)
            if ctx.font_manager
            else None
        )
        font_payload = make_font_payload(
            name=str(font_spec.get("name", font_name)) if font_spec else str(font_name),
            size_px=render_font_size_px,
            units_per_px=units_per_px,
            rotation=render_font_rotation,
            underline=bool(font_spec.get("underline", is_underline))
            if font_spec
            else bool(is_underline),
            italic=bool(font_spec.get("italic", is_italic))
            if font_spec
            else bool(is_italic),
            bold=bool(font_spec.get("bold", is_bold)) if font_spec else bool(is_bold),
            strikeout=bool(font_spec.get("strikeout", False)) if font_spec else False,
        )

        def geo_point(px: float, py: float) -> tuple[float, float]:
            return _svg_coord_to_geometry_exact(
                px,
                py,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )

        def item_geo_point(px: float, py: float) -> tuple[float, float]:
            return _geometry_item_point(
                *geo_point(px, py),
                units_per_px=units_per_px,
            )

        operations: list[SchGeometryOp] = []
        min_x = math.inf
        max_x = -math.inf
        min_y = math.inf
        max_y = -math.inf

        def track_point(px: float, py: float) -> None:
            nonlocal min_x, max_x, min_y, max_y
            min_x = min(min_x, px)
            max_x = max(max_x, px)
            min_y = min(min_y, py)
            max_y = max(max_y, py)

        def add_line(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            *,
            line_pen: Any | None = None,
        ) -> None:
            track_point(x1, y1)
            track_point(x2, y2)
            operations.append(
                SchGeometryOp.lines(
                    [item_geo_point(x1, y1), item_geo_point(x2, y2)],
                    pen=pen if line_pen is None else line_pen,
                )
            )

        def add_arc(
            center_x: float,
            center_y: float,
            radius_px: float,
            start_angle: float,
            end_angle: float,
            *,
            arc_pen: Any | None = None,
        ) -> None:
            for bound_x, bound_y in self._arc_bounds_points(
                center_x,
                center_y,
                radius_px,
                start_angle,
                end_angle,
            ):
                track_point(bound_x, bound_y)
            geometry_center_x, geometry_center_y = item_geo_point(center_x, center_y)
            diameter_units = radius_px * 2.0 * geometry_scale
            operations.append(
                SchGeometryOp.arc(
                    center_x=geometry_center_x,
                    center_y=geometry_center_y,
                    width=diameter_units,
                    height=diameter_units,
                    start_angle=start_angle,
                    end_angle=end_angle,
                    pen=pen if arc_pen is None else arc_pen,
                )
            )

        def add_rounded_rect(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            *,
            radius_x_px: float = 0.0,
            radius_y_px: float = 0.0,
            rect_pen: Any | None = None,
        ) -> None:
            track_point(min(x1, x2), min(y1, y2))
            track_point(max(x1, x2), max(y1, y2))
            geometry_center_x, geometry_center_y = item_geo_point(
                (x1 + x2) / 2.0,
                (y1 + y2) / 2.0,
            )
            half_width_px = abs(x2 - x1) / 2.0
            half_height_px = abs(y2 - y1) / 2.0
            operations.append(
                SchGeometryOp.rounded_rectangle_from_item(
                    center_x=geometry_center_x,
                    center_y=geometry_center_y,
                    half_width=half_width_px * geometry_scale,
                    half_height=half_height_px * geometry_scale,
                    corner_x_radius=min(half_width_px, abs(radius_x_px))
                    * geometry_scale,
                    corner_y_radius=min(half_height_px, abs(radius_y_px))
                    * geometry_scale,
                    pen=pen if rect_pen is None else rect_pen,
                )
            )

        def add_native_junction() -> None:
            if not getattr(ctx, "native_svg_export", False):
                return
            if (self.location.x, self.location.y) not in ctx.connection_points:
                return
            junction_color_raw = 0x000000
            add_rounded_rect(
                x - 2 * ctx.scale,
                y - 2 * ctx.scale,
                x + 2 * ctx.scale,
                y + 2 * ctx.scale,
                radius_x_px=2 * ctx.scale,
                radius_y_px=2 * ctx.scale,
                rect_pen=make_pen(junction_color_raw, width=0),
            )
            operations.insert(
                len(operations) - 1,
                SchGeometryOp.rounded_rectangle_from_item(
                    center_x=item_geo_point(x, y)[0],
                    center_y=item_geo_point(x, y)[1],
                    half_width=2 * ctx.scale * geometry_scale,
                    half_height=2 * ctx.scale * geometry_scale,
                    corner_x_radius=2 * ctx.scale * geometry_scale,
                    corner_y_radius=2 * ctx.scale * geometry_scale,
                    brush=make_solid_brush(junction_color_raw),
                ),
            )

        def apply_masked_stroke(stub_end_ax: float, stub_end_ay: float) -> None:
            nonlocal stroke_raw, pen, bus_pen
            if not ctx.is_segment_fully_under_compile_mask(
                location_x,
                location_y,
                stub_end_ax,
                stub_end_ay,
            ):
                return
            masked_hex = ctx.apply_compile_mask_color(color_to_hex(stroke_raw), True)
            stroke_raw = rgb_to_win32_color(
                int(masked_hex[1:3], 16),
                int(masked_hex[3:5], 16),
                int(masked_hex[5:7], 16),
            )
            pen = make_pen(stroke_raw, width=symbol_width)
            bus_pen = make_pen(stroke_raw, width=bus_width)
            # The style dispatcher already holds this request-local pen.
            hairline_pen.update(make_pen(stroke_raw, width=0))

        orient = self.orientation
        text_baseline: tuple[float, float] | None = None
        text_width = 0.0
        text_height = 0.0
        standard_overline_origin: tuple[int, int] | None = None
        standard_text_height: float | None = None

        if custom_prims is not None:
            return self._build_custom_power_port_geometry_record(
                ctx,
                document_id=document_id,
                custom_prims=custom_prims,
                x=x,
                y=y,
                orient=orient,
                sheet_height_px=sheet_height_px,
                stroke_raw=stroke_raw,
                font_name=font_name,
                font_size_px=font_size_px,
                render_font_size_px=render_font_size_px,
                render_font_rotation=render_font_rotation,
                is_bold=is_bold,
                is_italic=is_italic,
                font_id=display_font_id,
                font_payload=font_payload,
                units_per_px=units_per_px,
                operations=operations,
                track_point=track_point,
                item_geo_point=item_geo_point,
                geometry_scale=geometry_scale,
                add_line=add_line,
                add_native_junction=add_native_junction,
                begin_group=SchGeometryOp.begin_group,
                end_group=SchGeometryOp.end_group,
                make_pen=make_pen,
                make_text_with_overline_operations=make_text_with_overline_operations,
                wrap_record_operations=wrap_record_operations,
                bounds_factory=SchGeometryBounds,
                record_factory=SchGeometryRecord,
                min_x_ref=lambda: min_x,
                min_y_ref=lambda: min_y,
                max_x_ref=lambda: max_x,
                max_y_ref=lambda: max_y,
            )

        if self.is_cross_sheet_connector:
            object_id, stub_start, stub_end, text_baseline, text_width = (
                self._build_cross_sheet_geometry(
                    ctx,
                    orient=orient,
                    x=x,
                    y=y,
                    font_size_px=font_size_px,
                    render_font_size_px=render_font_size_px,
                    render_font_rotation=render_font_rotation,
                    font_name=font_name,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    font_id=display_font_id,
                    add_line=add_line,
                    apply_masked_stroke=apply_masked_stroke,
                    split_overline_text=split_managed_overline_text,
                    measure_text_width=measure_text_width,
                    measure_text_height=measure_text_height,
                )
            )
        else:
            (
                object_id,
                stub_start,
                stub_end,
                text_baseline,
                text_width,
                standard_overline_origin,
                standard_text_height,
            ) = self._build_standard_power_geometry(
                ctx,
                orient=orient,
                x=x,
                y=y,
                font_size_px=font_size_px,
                render_font_size_px=render_font_size_px,
                render_font_rotation=render_font_rotation,
                font_name=font_name,
                is_bold=is_bold,
                is_italic=is_italic,
                font_id=display_font_id,
                add_line=add_line,
                add_arc=add_arc,
                add_rounded_rect=add_rounded_rect,
                apply_masked_stroke=apply_masked_stroke,
                hairline_pen=hairline_pen,
                split_overline_text=split_managed_overline_text,
            )

        if self._has_visible_display_string(ctx) and text_baseline is not None:
            if text_width <= 0.0 and standard_overline_origin is None:
                clean_text, _ = split_managed_overline_text(self._get_display_string())
                text_width = measure_text_width(
                    clean_text, font_size_px, font_name, is_bold, is_italic
                )
            text_height = (
                standard_text_height
                if standard_text_height is not None
                else measure_text_height(
                    render_font_size_px,
                    font_name,
                    bold=is_bold,
                    italic=is_italic,
                    use_altium_algorithm=False,
                )
            )
            if standard_overline_origin is None:
                operations.extend(
                    make_text_with_overline_operations(
                        text=self._get_display_string(),
                        baseline_x_px=text_baseline[0],
                        baseline_y_px=text_baseline[1],
                        sheet_height_px=sheet_height_px,
                        font_payload=font_payload,
                        font_size_px=render_font_size_px,
                        font_name=font_name,
                        bold=is_bold,
                        italic=is_italic,
                        brush_color_raw=stroke_raw,
                        rotation_deg=render_font_rotation,
                        units_per_px=units_per_px,
                        overline_pen_width=geometry_scale * world_scale,
                        single_slash_negation=ctx.options.single_slash_negation,
                    )
                )
            else:
                operations.extend(
                    self._standard_power_text_operations(
                        text=self._get_display_string(),
                        baseline=text_baseline,
                        overline_origin_internal=standard_overline_origin,
                        ctx=ctx,
                        source_font_size_px=ctx.get_font_size_for_width(
                            display_font_id
                        ),
                        render_font_size_px=render_font_size_px,
                        render_font_rotation=render_font_rotation,
                        world_scale=world_scale,
                        sheet_height_px=sheet_height_px,
                        font_name=font_name,
                        is_bold=is_bold,
                        is_italic=is_italic,
                        font_payload=font_payload,
                        stroke_raw=stroke_raw,
                        units_per_px=units_per_px,
                        track_point=track_point,
                    )
                )
            self._track_transformed_text_bounds(
                track_point,
                baseline=text_baseline,
                width=text_width,
                height=text_height,
                font_size=render_font_size_px,
                rotation=render_font_rotation,
            )

        add_line(
            stub_start[0], stub_start[1], stub_end[0], stub_end[1], line_pen=bus_pen
        )
        add_native_junction()

        if math.isinf(min_x) or math.isinf(min_y):
            track_point(x, y)

        return self._finalize_power_port_geometry_record(
            document_id=document_id,
            object_id=object_id,
            sheet_height_px=sheet_height_px,
            connection_point=item_geo_point(x, y),
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
            operations=operations,
            units_per_px=units_per_px,
            bounds_factory=SchGeometryBounds,
            record_factory=SchGeometryRecord,
            wrap_record_operations=wrap_record_operations,
        )

    def _build_custom_power_port_geometry_record(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        custom_prims: list[dict],
        x: float,
        y: float,
        orient: TextOrientation,
        sheet_height_px: float,
        stroke_raw: int,
        font_name: str,
        font_size_px: float,
        render_font_size_px: float,
        render_font_rotation: float,
        is_bold: bool,
        is_italic: bool,
        font_id: int,
        font_payload: Any,
        units_per_px: int,
        operations: list[Any],
        track_point: Any,
        item_geo_point: Any,
        geometry_scale: float,
        add_line: Any,
        add_native_junction: Any,
        begin_group: Any,
        end_group: Any,
        make_pen: Any,
        make_text_with_overline_operations: Any,
        wrap_record_operations: Any,
        bounds_factory: Any,
        record_factory: Any,
        min_x_ref: Any,
        min_y_ref: Any,
        max_x_ref: Any,
        max_y_ref: Any,
    ) -> Any:
        visible_custom_prims = self._custom_visible_render_primitives(custom_prims)
        text_layout = self._custom_power_port_text_layout(
            ctx,
            custom_prims=visible_custom_prims,
            font_name=font_name,
            font_size_px=font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
            font_id=font_id,
        )
        if text_layout is not None:
            text_origin, text_width, text_height = text_layout
            theta = math.radians(render_font_rotation)
            geometry_step = float(int(render_font_size_px))
            text_baseline = (
                text_origin[0] - math.sin(theta) * geometry_step,
                text_origin[1] + math.cos(theta) * geometry_step,
            )
            world_scale = render_font_size_px / font_size_px if font_size_px else 1.0
            text_width *= world_scale
            text_height *= world_scale
            operations.extend(
                make_text_with_overline_operations(
                    text=self._get_display_string(),
                    baseline_x_px=text_baseline[0],
                    baseline_y_px=text_baseline[1],
                    sheet_height_px=sheet_height_px,
                    font_payload=font_payload,
                    font_size_px=render_font_size_px,
                    font_name=font_name,
                    bold=is_bold,
                    italic=is_italic,
                    brush_color_raw=stroke_raw,
                    rotation_deg=render_font_rotation,
                    units_per_px=units_per_px,
                    overline_pen_width=geometry_scale * world_scale,
                    single_slash_negation=ctx.options.single_slash_negation,
                )
            )
            self._track_transformed_text_bounds(
                track_point,
                baseline=text_baseline,
                width=text_width,
                height=text_height,
                font_size=render_font_size_px,
                rotation=render_font_rotation,
            )

        for idx, rec in enumerate(visible_custom_prims):
            record_type = self._custom_primitive_record_type(rec)
            operations.append(
                begin_group(self._custom_power_port_child_group_id(rec, idx))
            )

            prim_color_raw = int(rec.get("Color", "0") or 0)
            prim_pen = (
                make_pen(
                    prim_color_raw,
                    width=self._custom_primitive_pen_width(rec, ctx, units_per_px),
                )
                if record_type in {SchRecordType.LINE, SchRecordType.POLYGON}
                else None
            )

            if record_type == SchRecordType.LINE:
                sx1, sy1 = self._custom_primitive_svg_point(
                    rec, ctx, "Location.X", "Location.Y"
                )
                sx2, sy2 = self._custom_primitive_svg_point(
                    rec, ctx, "Corner.X", "Corner.Y"
                )
                add_line(sx1, sy1, sx2, sy2, line_pen=prim_pen)
            elif record_type == SchRecordType.POLYGON:
                polygon_points = self._custom_polygon_svg_points(
                    rec,
                    ctx=ctx,
                    track_point=track_point,
                )
                if polygon_points:
                    geometry_polygon = [
                        item_geo_point(px, py) for px, py in polygon_points
                    ]
                    operations.extend(
                        self._custom_polygon_geometry_ops(
                            rec,
                            geometry_polygon,
                            prim_pen=prim_pen,
                        )
                    )

            operations.append(end_group())

        if math.isinf(min_x_ref()) or math.isinf(min_y_ref()):
            track_point(x, y)
        add_native_junction()

        return self._finalize_power_port_geometry_record(
            document_id=document_id,
            object_id="ePowerObject",
            sheet_height_px=sheet_height_px,
            connection_point=item_geo_point(x, y),
            min_x=min_x_ref(),
            min_y=min_y_ref(),
            max_x=max_x_ref(),
            max_y=max_y_ref(),
            operations=operations,
            units_per_px=units_per_px,
            bounds_factory=bounds_factory,
            record_factory=record_factory,
            wrap_record_operations=wrap_record_operations,
        )

    def _custom_power_port_child_group_id(self, record: dict, index: int) -> str:
        seed = f"{self.unique_id}:{index}:{record.get('RECORD', '')}:{record.get('UniqueID', '')}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()

    def _custom_primitive_record_type(self, record: dict) -> SchRecordType | None:
        try:
            return SchRecordType(int(record.get("RECORD", 0) or 0))
        except (TypeError, ValueError):
            return None

    def _custom_visible_children_width_internal(self, custom_prims: list[dict]) -> int:
        """Return managed copied-child bounds width for supported custom primitives."""
        from ._altium_record_sch__harness_layout import _abs_safe_i32, _unchecked_i32

        child_bounds: list[tuple[int, int, int, int]] = []
        for record in self._custom_visible_render_primitives(custom_prims):
            _, bounds = self._custom_supported_child_bounds_internal(record)
            if bounds is not None:
                child_bounds.append(bounds)

        if not child_bounds:
            return 1_000_000
        left = min(bounds[0] for bounds in child_bounds)
        right = max(bounds[2] for bounds in child_bounds)
        return _abs_safe_i32(_unchecked_i32(left - right))

    def _custom_supported_child_bounds_internal(
        self, record: dict[str, object]
    ) -> tuple[bool, tuple[int, int, int, int] | None]:
        from ._altium_record_sch__harness_layout import (
            _harness_internal_location,
            _rotate_layout_label_point,
            _unchecked_i32,
        )

        serializer = AltiumSerializer()
        hidden, _ = serializer.read_bool(record, Fields.IS_HIDDEN, default=False)
        record_type = self._custom_primitive_record_type(record)
        if hidden:
            return False, None
        if record_type not in {SchRecordType.LINE, SchRecordType.POLYGON}:
            raise NotImplementedError(
                f"custom power primitive record {record.get('RECORD', '')!r} "
                "is not supported"
            )
        assert record_type is not None
        points = self._custom_primitive_internal_points(record, record_type)
        if not points:
            return True, None
        rotated = [
            _rotate_layout_label_point(
                point,
                anchor=(0, 0),
                orientation=self.orientation,
            )
            for point in points
        ]
        origin_x, origin_y = _harness_internal_location(self.location)
        xs = [_unchecked_i32(origin_x + point[0]) for point in rotated]
        ys = [_unchecked_i32(origin_y + point[1]) for point in rotated]
        left, right = min(xs), max(xs)
        bottom, top = min(ys), max(ys)
        if record_type == SchRecordType.LINE or len(points) > 1:
            inflation = self._custom_line_width_inflation_internal(record)
            left = _unchecked_i32(left - inflation)
            right = _unchecked_i32(right + inflation)
            bottom = _unchecked_i32(bottom - inflation)
            top = _unchecked_i32(top + inflation)
        left, right = min(left, right), max(left, right)
        bottom, top = min(bottom, top), max(bottom, top)
        return True, (left, bottom, right, top)

    def _custom_visible_render_primitives(self, custom_prims: list[dict]) -> list[dict]:
        serializer = AltiumSerializer()
        visible: list[dict] = []
        for record in custom_prims:
            hidden, _ = serializer.read_bool(record, Fields.IS_HIDDEN, default=False)
            if hidden:
                continue
            record_type = self._custom_primitive_record_type(record)
            if record_type not in {
                SchRecordType.LINE,
                SchRecordType.POLYGON,
            }:
                raise NotImplementedError(
                    f"custom power primitive record {record.get('RECORD', '')!r} "
                    "is not supported"
                )
            if record_type == SchRecordType.POLYGON:
                self._custom_polygon_counts(record)
            visible.append(record)
        return visible

    @staticmethod
    def _custom_polygon_counts(record: dict[str, object]) -> tuple[int, int]:
        from .altium_sch_record_helpers import _validate_schematic_vertex_counts

        serializer = AltiumSerializer()
        count, _ = serializer.read_int(record, Fields.LOCATION_COUNT, default=0)
        extra_count, _ = serializer.read_int(record, "EXTRALOCATIONCOUNT", default=0)
        _validate_schematic_vertex_counts(count, extra_count)
        return count, extra_count

    @staticmethod
    def _custom_line_width_inflation_internal(record: dict[str, object]) -> int:
        from .altium_record_types import LineWidth
        from .altium_sch_svg_renderer import LINE_WIDTH_INTERNAL

        width_value, _ = AltiumSerializer().read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        try:
            return LINE_WIDTH_INTERNAL[LineWidth(width_value)]
        except ValueError:
            return 0

    @staticmethod
    def _custom_primitive_internal_points(
        record: dict[str, object], record_type: SchRecordType
    ) -> list[tuple[int, int]]:
        from ._altium_record_sch__harness_layout import _unchecked_i32

        serializer = AltiumSerializer()

        def point(x_field: str, y_field: str) -> tuple[int, int]:
            x, x_frac, _ = serializer.read_coord(record, x_field)
            y, y_frac, _ = serializer.read_coord(record, y_field)
            return (
                _unchecked_i32(x * 100_000 + x_frac),
                _unchecked_i32(y * 100_000 + y_frac),
            )

        if record_type == SchRecordType.LINE:
            return [point("Location.X", "Location.Y"), point("Corner.X", "Corner.Y")]
        count, extra_count = AltiumSchPowerPort._custom_polygon_counts(record)
        points = [point(f"X{index}", f"Y{index}") for index in range(1, count + 1)]
        points.extend(
            point(f"EX{index}", f"EY{index}")
            for index in range(count + 1, count + extra_count + 1)
        )
        return points

    def _transform_power_port_local_point(
        self,
        orient: TextOrientation,
        x: float,
        y: float,
        lx: float,
        ly: float,
    ) -> tuple[float, float]:
        if orient == TextOrientation.DEGREES_0:
            dx, dy = lx, -ly
        elif orient == TextOrientation.DEGREES_90:
            dx, dy = -ly, -lx
        elif orient == TextOrientation.DEGREES_180:
            dx, dy = -lx, ly
        else:
            dx, dy = ly, lx
        return x + dx, y + dy

    def _custom_polygon_svg_points(
        self,
        record: dict,
        *,
        ctx: "SchSvgRenderContext",
        track_point: Any,
    ) -> list[tuple[float, float]]:
        count, extra_count = self._custom_polygon_counts(record)
        polygon_points: list[tuple[float, float]] = []
        for point_index in range(1, count + 1):
            sx, sy = self._custom_primitive_svg_point(
                record, ctx, f"X{point_index}", f"Y{point_index}"
            )
            polygon_points.append((sx, sy))
            track_point(sx, sy)
        for point_index in range(count + 1, count + extra_count + 1):
            sx, sy = self._custom_primitive_svg_point(
                record, ctx, f"EX{point_index}", f"EY{point_index}"
            )
            polygon_points.append((sx, sy))
            track_point(sx, sy)
        return polygon_points

    def _custom_primitive_svg_point(
        self,
        record: dict[str, object],
        ctx: "SchSvgRenderContext",
        x_field: str,
        y_field: str,
    ) -> tuple[float, float]:
        from ._altium_record_sch__harness_layout import (
            _harness_internal_location,
            _rotate_layout_label_point,
            _unchecked_i32,
        )

        serializer = AltiumSerializer()
        x, x_frac, _ = serializer.read_coord(record, x_field)
        y, y_frac, _ = serializer.read_coord(record, y_field)
        dx, dy = _rotate_layout_label_point(
            (
                _unchecked_i32(x * 100_000 + x_frac),
                _unchecked_i32(y * 100_000 + y_frac),
            ),
            anchor=(0, 0),
            orientation=self.orientation,
        )
        origin_x, origin_y = _harness_internal_location(self.location)
        return ctx.transform_point(
            _unchecked_i32(origin_x + dx) / 100_000.0,
            _unchecked_i32(origin_y + dy) / 100_000.0,
        )

    def _custom_polygon_geometry_ops(
        self,
        record: dict[str, object],
        geometry_polygon: list[tuple[float, float]],
        *,
        prim_pen: dict[str, object] | None,
    ) -> list["SchGeometryOp"]:
        from .altium_sch_geometry_oracle import SchGeometryOp, make_solid_brush
        from .altium_sch_svg_renderer import SEMI_TRANSPARENT_ALPHA

        if len(geometry_polygon) < 2:
            return []
        if len(geometry_polygon) == 2:
            return [SchGeometryOp.lines(geometry_polygon, pen=prim_pen)]

        serializer = AltiumSerializer()
        is_solid, _ = serializer.read_str(record, Fields.IS_SOLID, default="F")
        operations: list[SchGeometryOp] = []
        if is_solid == "T":
            area_color_raw, _ = serializer.read_color(
                record, Fields.AREA_COLOR, default=0
            )
            assert area_color_raw is not None
            transparent, _ = serializer.read_str(
                record, Fields.TRANSPARENT, default="F"
            )
            operations.append(
                SchGeometryOp.polygons(
                    [geometry_polygon],
                    brush=make_solid_brush(
                        area_color_raw,
                        alpha=SEMI_TRANSPARENT_ALPHA if transparent == "T" else 0xFF,
                    ),
                )
            )
        operations.append(SchGeometryOp.polygons([geometry_polygon], pen=prim_pen))
        return operations

    @staticmethod
    def _custom_primitive_pen_width(
        record: dict[str, object], ctx: "SchSvgRenderContext", units_per_px: int
    ) -> float:
        from .altium_record_types import LineWidth
        from .altium_sch_geometry_oracle import _geometry_units_scale
        from .altium_sch_svg_renderer import LINE_WIDTH_INTERNAL

        width, _ = AltiumSerializer().read_int(record, Fields.LINE_WIDTH, default=0)
        return (
            LINE_WIDTH_INTERNAL[LineWidth(width)]
            / 100_000
            * _geometry_units_scale(units_per_px)
            * ctx.get_stroke_scale()
        )

    def _build_cross_sheet_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        orient: TextOrientation,
        x: float,
        y: float,
        font_size_px: float,
        render_font_size_px: float,
        render_font_rotation: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
        font_id: int,
        add_line: Any,
        apply_masked_stroke: Any,
        split_overline_text: Any,
        measure_text_width: Any,
        measure_text_height: Any,
    ) -> tuple[
        str, tuple[float, float], tuple[float, float], tuple[float, float] | None, float
    ]:
        stub_length = 7 if self.style == 1 else 2
        stub_dx, stub_dy = self._cross_sheet_local_point(orient, stub_length, 0)
        stub_ax, stub_ay = self._cross_sheet_source_point(stub_dx, stub_dy)
        apply_masked_stroke(stub_ax, stub_ay)
        arm = 5 if self.style == 1 else -5
        # Keep the managed endpoint and operation order in source coordinates.
        for x1, y1, x2, y2 in (
            (7, 0, 7 - arm, -5),
            (7, 0, 7 - arm, 5),
            (7 + arm, 0, 7, -5),
            (7 + arm, 0, 7, 5),
        ):
            dx1, dy1 = self._cross_sheet_local_point(orient, x1, y1)
            dx2, dy2 = self._cross_sheet_local_point(orient, x2, y2)
            start = ctx.transform_point(*self._cross_sheet_source_point(dx1, dy1))
            end = ctx.transform_point(*self._cross_sheet_source_point(dx2, dy2))
            add_line(*start, *end)

        text_width = self._measure_clean_power_text(
            ctx,
            split_overline_text,
            measure_text_width,
            ctx.get_font_size_for_width(font_id),
            font_name,
            is_bold,
            is_italic,
        )
        baseline = self._cross_sheet_text_baseline(
            ctx,
            orient,
            text_width,
            measure_text_height(
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
                use_altium_algorithm=False,
            ),
            render_font_size_px,
            render_font_rotation,
        )
        world_scale, _ = self._text_world_transform(ctx)
        return (
            "eCrossSheetConnector",
            (x, y),
            ctx.transform_point(stub_ax, stub_ay),
            baseline if self._has_visible_display_string(ctx) else None,
            text_width * world_scale,
        )

    @staticmethod
    def _cross_sheet_local_point(
        orient: TextOrientation, x: int, y: int
    ) -> tuple[int, int]:
        # These are source-order mappings, not a rotation of an emitted polyline.
        if orient == TextOrientation.DEGREES_180:
            return -x, y
        if orient == TextOrientation.DEGREES_90:
            return y, x
        if orient == TextOrientation.DEGREES_270:
            return y, -x
        return x, y

    def _cross_sheet_source_point(self, dx: int, dy: int) -> tuple[float, float]:
        from ._altium_record_sch__harness_layout import (
            _harness_internal_location,
            _unchecked_i32,
        )

        x, y = _harness_internal_location(self.location)
        return (
            _unchecked_i32(x + dx * 100_000) / 100_000.0,
            _unchecked_i32(y + dy * 100_000) / 100_000.0,
        )

    def _cross_sheet_text_baseline(
        self,
        ctx: "SchSvgRenderContext",
        orient: TextOrientation,
        width: float,
        height: float,
        render_font_size: float,
        render_font_rotation: float,
    ) -> tuple[float, float]:
        source_dx: float
        source_dy: float
        if orient == TextOrientation.DEGREES_180:
            anchor_x, anchor_y = self._cross_sheet_source_point(-14, -5)
            source_dx, source_dy = -width, height
        elif orient == TextOrientation.DEGREES_90:
            anchor_x, anchor_y = self._cross_sheet_source_point(0, 12)
            source_dx, source_dy = -width / 2.0, height
        elif orient == TextOrientation.DEGREES_270:
            anchor_x, anchor_y = self._cross_sheet_source_point(0, -12)
            source_dx, source_dy = -width / 2.0, 0.0
        else:
            anchor_x, anchor_y = self._cross_sheet_source_point(14, -5)
            source_dx, source_dy = 0.0, height

        geometry_x, geometry_y = ctx.transform_point(
            anchor_x + source_dx, anchor_y + source_dy
        )
        theta = math.radians(render_font_rotation)
        geometry_step = float(int(render_font_size))
        return (
            geometry_x - math.sin(theta) * geometry_step,
            geometry_y + math.cos(theta) * geometry_step,
        )

    @staticmethod
    def _track_transformed_text_bounds(
        track_point: Any,
        *,
        baseline: tuple[float, float],
        width: float,
        height: float,
        font_size: float,
        rotation: float,
    ) -> None:
        theta = math.radians(rotation)
        cos_theta = math.cos(theta)
        sin_theta = math.sin(theta)
        geometry_step = float(int(font_size))
        origin_x = baseline[0] + sin_theta * geometry_step
        origin_y = baseline[1] - cos_theta * geometry_step
        for local_x, local_y in (
            (0.0, 0.0),
            (width, 0.0),
            (0.0, height),
            (width, height),
        ):
            track_point(
                origin_x + local_x * cos_theta - local_y * sin_theta,
                origin_y + local_x * sin_theta + local_y * cos_theta,
            )

    def _measure_clean_power_text(
        self,
        ctx: "SchSvgRenderContext",
        split_overline_text: Any,
        measure_text_width: Any,
        font_size_px: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
    ) -> float:
        if not self._has_visible_display_string(ctx):
            return 0.0
        clean_text, _ = split_overline_text(self._get_display_string())
        return measure_text_width(
            clean_text, font_size_px, font_name, is_bold, is_italic
        )

    def _build_standard_power_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        orient: TextOrientation,
        x: float,
        y: float,
        font_size_px: float,
        render_font_size_px: float,
        render_font_rotation: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
        font_id: int,
        add_line: Any,
        add_arc: Any,
        add_rounded_rect: Any,
        apply_masked_stroke: Any,
        hairline_pen: Any,
        split_overline_text: Any,
    ) -> tuple[
        str,
        tuple[float, float],
        tuple[float, float],
        tuple[float, float] | None,
        float,
        tuple[int, int] | None,
        float | None,
    ]:
        stub_end_local_x, stub_end_local_y, _, _ = self._power_port_stub_layout(
            orient, 0.0, 0.0, 1.0
        )
        stub_end_source = self._standard_power_source_point(
            stub_end_local_x, stub_end_local_y
        )
        stub_end_x, stub_end_y = ctx.transform_point(*stub_end_source)
        apply_masked_stroke(*stub_end_source)
        world_scale, _ = self._text_world_transform(ctx)

        def transform_local(local_x: float, local_y: float) -> tuple[float, float]:
            return ctx.transform_point(
                *self._standard_power_source_point(local_x, local_y)
            )

        def add_local_line(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            *,
            line_pen: Any | None = None,
        ) -> None:
            start = transform_local(x1, y1)
            end = transform_local(x2, y2)
            add_line(*start, *end, line_pen=line_pen)

        def add_local_arc(
            center_x: float,
            center_y: float,
            radius_px: float,
            start_angle: float,
            end_angle: float,
            *,
            arc_pen: Any | None = None,
        ) -> None:
            center = transform_local(center_x, center_y)
            transformed_start, transformed_end = self._standard_power_arc_angles(
                ctx, start_angle, end_angle
            )
            add_arc(
                *center,
                radius_px * world_scale,
                transformed_start,
                transformed_end,
                arc_pen=arc_pen,
            )

        def add_local_rounded_rect(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            *,
            radius_x_px: float = 0.0,
            radius_y_px: float = 0.0,
            rect_pen: Any | None = None,
        ) -> None:
            center = transform_local((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            half_width = abs(x2 - x1) * world_scale / 2.0
            half_height = abs(y2 - y1) * world_scale / 2.0
            add_rounded_rect(
                center[0] - half_width,
                center[1] - half_height,
                center[0] + half_width,
                center[1] + half_height,
                radius_x_px=radius_x_px * world_scale,
                radius_y_px=radius_y_px * world_scale,
                rect_pen=rect_pen,
            )

        self._render_power_style_geometry(
            orient,
            stub_end_local_x,
            stub_end_local_y,
            1.0,
            add_local_line,
            add_local_arc,
            add_local_rounded_rect,
            hairline_pen,
        )
        text_origin_source, overline_origin_source, text_width, text_height = (
            self._standard_power_text_source_layout(
                ctx,
                orient=orient,
                font_size_px=font_size_px,
                glyph_font_size_px=render_font_size_px,
                font_name=font_name,
                is_bold=is_bold,
                is_italic=is_italic,
                font_id=font_id,
                split_overline_text=split_overline_text,
            )
        )
        text_baseline: tuple[float, float] | None = None
        if text_origin_source is not None:
            text_origin = ctx.transform_point(*text_origin_source)
            assert overline_origin_source is not None
            theta = math.radians(render_font_rotation)
            geometry_step = float(int(render_font_size_px))
            text_baseline = (
                text_origin[0] - math.sin(theta) * geometry_step,
                text_origin[1] + math.cos(theta) * geometry_step,
            )
        return (
            "ePowerObject",
            (x, y),
            (stub_end_x, stub_end_y),
            text_baseline,
            text_width,
            overline_origin_source,
            text_height,
        )

    def _standard_power_source_point(
        self, local_screen_x: float, local_screen_y: float
    ) -> tuple[float, float]:
        from ._altium_record_sch__harness_layout import (
            _float_to_i32,
            _harness_internal_location,
            _unchecked_i32,
        )

        origin_x, origin_y = _harness_internal_location(self.location)
        offset_x = _float_to_i32(local_screen_x * 100_000, rounded=False)
        offset_y = _float_to_i32(local_screen_y * 100_000, rounded=False)
        return (
            _unchecked_i32(origin_x + offset_x) / 100_000.0,
            _unchecked_i32(origin_y - offset_y) / 100_000.0,
        )

    @staticmethod
    def _standard_power_arc_angles(
        ctx: "SchSvgRenderContext", start_angle: float, end_angle: float
    ) -> tuple[float, float]:
        transformed_start = start_angle
        transformed_end = end_angle
        is_full_circle = abs(end_angle - start_angle - 360.0) <= 0.01
        if ctx.mirror and not is_full_circle:
            transformed_start = AltiumSchPowerPort._normalize_managed_angle(
                -transformed_start + 180.0
            )
            transformed_end = AltiumSchPowerPort._normalize_managed_angle(
                -transformed_end + 180.0
            )
            transformed_start, transformed_end = transformed_end, transformed_start
        transformed_start += float(ctx.rotation)
        transformed_end += float(ctx.rotation)
        if ctx.sheet_height > 0 or ctx.flip_y:
            transformed_start = -transformed_start
            transformed_end = -transformed_end
        return transformed_start, transformed_end

    @staticmethod
    def _arc_bounds_points(
        center_x: float,
        center_y: float,
        radius_px: float,
        start_angle: float,
        end_angle: float,
    ) -> list[tuple[float, float]]:
        def normalized(angle: float) -> float:
            return (360.0 + math.fmod(angle, 360.0)) % 360.0

        def is_between(angle: float) -> bool:
            start = normalized(start_angle)
            end = normalized(end_angle)
            candidate = normalized(angle)
            if start < end:
                return start <= candidate <= end
            if start > candidate:
                return candidate <= end
            return True

        # CalcArcBounds receives ArcGeometryItem's stored full width as though it
        # were a radius. Preserve that managed bounds bug independently of draw size.
        extent = radius_px * 2.0

        def point(angle: float) -> tuple[float, float]:
            radians = math.radians(angle)
            return (
                center_x + extent * math.cos(radians),
                center_y + extent * math.sin(radians),
            )

        angles = [start_angle, end_angle]
        angles.extend(angle for angle in (0.0, 90.0, 180.0, 270.0) if is_between(angle))
        return [point(angle) for angle in angles]

    def _power_port_stub_layout(
        self,
        orient: TextOrientation,
        x: float,
        y: float,
        scale: float,
    ) -> tuple[float, float, int, int]:
        stub_lengths = {
            0: 4,
            1: 4,
            2: 10,
            3: 6,
            4: 10,
            5: 10,
            6: 10,
            7: 10,
            8: 16,
            9: 16,
            10: 20,
        }
        stub_len_units = stub_lengths.get(self.style, 10)
        stub_len = stub_len_units * scale
        if orient == TextOrientation.DEGREES_0:
            return x + stub_len, y, self.location.x + stub_len_units, self.location.y
        if orient == TextOrientation.DEGREES_90:
            return x, y - stub_len, self.location.x, self.location.y + stub_len_units
        if orient == TextOrientation.DEGREES_180:
            return x - stub_len, y, self.location.x - stub_len_units, self.location.y
        return x, y + stub_len, self.location.x, self.location.y - stub_len_units

    def _render_power_style_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        add_line: Any,
        add_arc: Any,
        add_rounded_rect: Any,
        hairline_pen: Any,
    ) -> None:
        if self.style == 0:
            self._render_power_circle_geometry(
                orient, stub_end_x, stub_end_y, scale, add_rounded_rect, hairline_pen
            )
        elif self.style == 1:
            self._render_power_arrow_geometry(
                orient, stub_end_x, stub_end_y, scale, add_line, include_base=True
            )
        elif self.style == 2:
            self._render_power_bar_geometry(
                orient, stub_end_x, stub_end_y, 5 * scale, add_line
            )
        elif self.style == 3:
            self._render_power_wave_geometry(
                orient, stub_end_x, stub_end_y, 4 * scale, add_arc, hairline_pen
            )
        elif self.style == 4:
            self._render_ground_line_stack_geometry(
                orient,
                stub_end_x,
                stub_end_y,
                scale,
                [(0, 10), (3, 7), (6, 4), (9, 1)],
                add_line,
            )
        elif self.style == 5:
            self._render_ground_signal_geometry(
                orient, stub_end_x, stub_end_y, 10 * scale, add_line
            )
        elif self.style == 6:
            self._render_ground_earth_geometry(
                orient, stub_end_x, stub_end_y, 10 * scale, 5 * scale, add_line
            )
        elif self.style == 7:
            self._render_power_arrow_geometry(
                orient, stub_end_x, stub_end_y, scale, add_line, include_base=False
            )
        elif self.style == 8:
            self._render_ground_line_stack_geometry(
                orient,
                stub_end_x,
                stub_end_y,
                scale,
                [(0, 10), (4, 6), (8, 2)],
                add_line,
            )
        elif self.style == 9:
            self._render_ground_line_stack_geometry(
                orient,
                stub_end_x,
                stub_end_y,
                scale,
                [(0, 10), (4, 6), (8, 2)],
                add_line,
            )
            add_rounded_rect(
                stub_end_x - 12 * scale,
                stub_end_y - 12 * scale,
                stub_end_x + 12 * scale,
                stub_end_y + 12 * scale,
                radius_x_px=12 * scale,
                radius_y_px=12 * scale,
            )
        elif self.style == 10:
            self._render_power_bar_geometry(
                orient, stub_end_x, stub_end_y, 8 * scale, add_line
            )

    def _render_power_circle_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        add_rounded_rect: Any,
        hairline_pen: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            rx, ry = stub_end_x, stub_end_y - 3 * scale
        elif orient == TextOrientation.DEGREES_90:
            rx, ry = stub_end_x - 3 * scale, stub_end_y - 6 * scale
        elif orient == TextOrientation.DEGREES_180:
            rx, ry = stub_end_x - 6 * scale, stub_end_y - 3 * scale
        else:
            rx, ry = stub_end_x - 3 * scale, stub_end_y
        add_rounded_rect(
            rx,
            ry,
            rx + 6 * scale,
            ry + 6 * scale,
            radius_x_px=3 * scale,
            radius_y_px=3 * scale,
            rect_pen=hairline_pen,
        )

    def _render_power_arrow_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        add_line: Any,
        *,
        include_base: bool,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            tip_x = stub_end_x + 6 * scale if include_base else stub_end_x
            base_x = stub_end_x if include_base else stub_end_x - 6 * scale
            add_line(tip_x, stub_end_y, base_x, stub_end_y - 3 * scale)
            add_line(tip_x, stub_end_y, base_x, stub_end_y + 3 * scale)
            if include_base:
                add_line(base_x, stub_end_y - 3 * scale, base_x, stub_end_y + 3 * scale)
        elif orient == TextOrientation.DEGREES_90:
            tip_y = stub_end_y - 6 * scale if include_base else stub_end_y
            base_y = stub_end_y if include_base else stub_end_y + 6 * scale
            add_line(stub_end_x, tip_y, stub_end_x - 3 * scale, base_y)
            add_line(stub_end_x, tip_y, stub_end_x + 3 * scale, base_y)
            if include_base:
                add_line(stub_end_x - 3 * scale, base_y, stub_end_x + 3 * scale, base_y)
        elif orient == TextOrientation.DEGREES_180:
            tip_x = stub_end_x - 6 * scale if include_base else stub_end_x
            base_x = stub_end_x if include_base else stub_end_x + 6 * scale
            add_line(tip_x, stub_end_y, base_x, stub_end_y - 3 * scale)
            add_line(tip_x, stub_end_y, base_x, stub_end_y + 3 * scale)
            if include_base:
                add_line(base_x, stub_end_y - 3 * scale, base_x, stub_end_y + 3 * scale)
        else:
            tip_y = stub_end_y + 6 * scale if include_base else stub_end_y
            base_y = stub_end_y if include_base else stub_end_y - 6 * scale
            add_line(stub_end_x, tip_y, stub_end_x - 3 * scale, base_y)
            add_line(stub_end_x, tip_y, stub_end_x + 3 * scale, base_y)
            if include_base:
                add_line(stub_end_x - 3 * scale, base_y, stub_end_x + 3 * scale, base_y)

    def _render_power_bar_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        half: float,
        add_line: Any,
    ) -> None:
        if orient in [TextOrientation.DEGREES_0, TextOrientation.DEGREES_180]:
            add_line(stub_end_x, stub_end_y - half, stub_end_x, stub_end_y + half)
        else:
            add_line(stub_end_x - half, stub_end_y, stub_end_x + half, stub_end_y)

    def _render_power_wave_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        arc_r: float,
        add_arc: Any,
        hairline_pen: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            add_arc(
                stub_end_x + arc_r, stub_end_y, arc_r, 180, 270, arc_pen=hairline_pen
            )
            add_arc(stub_end_x - arc_r, stub_end_y, arc_r, 0, 90, arc_pen=hairline_pen)
        elif orient == TextOrientation.DEGREES_90:
            add_arc(
                stub_end_x, stub_end_y - arc_r, arc_r, 270, 360, arc_pen=hairline_pen
            )
            add_arc(
                stub_end_x, stub_end_y + arc_r, arc_r, 90, 180, arc_pen=hairline_pen
            )
        elif orient == TextOrientation.DEGREES_180:
            add_arc(stub_end_x - arc_r, stub_end_y, arc_r, 0, 90, arc_pen=hairline_pen)
            add_arc(
                stub_end_x + arc_r, stub_end_y, arc_r, 180, 270, arc_pen=hairline_pen
            )
        else:
            add_arc(
                stub_end_x, stub_end_y + arc_r, arc_r, 90, 180, arc_pen=hairline_pen
            )
            add_arc(
                stub_end_x, stub_end_y - arc_r, arc_r, 270, 360, arc_pen=hairline_pen
            )

    def _render_ground_line_stack_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        scale: float,
        line_specs: list[tuple[int, int]],
        add_line: Any,
    ) -> None:
        for off, half in line_specs:
            if orient == TextOrientation.DEGREES_0:
                lx = stub_end_x + off * scale
                add_line(lx, stub_end_y - half * scale, lx, stub_end_y + half * scale)
            elif orient == TextOrientation.DEGREES_90:
                ly = stub_end_y - off * scale
                add_line(stub_end_x - half * scale, ly, stub_end_x + half * scale, ly)
            elif orient == TextOrientation.DEGREES_180:
                lx = stub_end_x - off * scale
                add_line(lx, stub_end_y - half * scale, lx, stub_end_y + half * scale)
            else:
                ly = stub_end_y + off * scale
                add_line(stub_end_x - half * scale, ly, stub_end_x + half * scale, ly)

    def _render_ground_signal_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        num: float,
        add_line: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(stub_end_x, stub_end_y - num, stub_end_x + num, stub_end_y)
            add_line(stub_end_x, stub_end_y + num, stub_end_x + num, stub_end_y)
        elif orient == TextOrientation.DEGREES_90:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(stub_end_x - num, stub_end_y, stub_end_x, stub_end_y - num)
            add_line(stub_end_x + num, stub_end_y, stub_end_x, stub_end_y - num)
        elif orient == TextOrientation.DEGREES_180:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(stub_end_x, stub_end_y - num, stub_end_x - num, stub_end_y)
            add_line(stub_end_x, stub_end_y + num, stub_end_x - num, stub_end_y)
        else:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(stub_end_x - num, stub_end_y, stub_end_x, stub_end_y + num)
            add_line(stub_end_x + num, stub_end_y, stub_end_x, stub_end_y + num)

    def _render_ground_earth_geometry(
        self,
        orient: TextOrientation,
        stub_end_x: float,
        stub_end_y: float,
        num: float,
        half: float,
        add_line: Any,
    ) -> None:
        if orient == TextOrientation.DEGREES_0:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(
                stub_end_x, stub_end_y + num, stub_end_x + num, stub_end_y + num + half
            )
            add_line(stub_end_x, stub_end_y, stub_end_x + num, stub_end_y + half)
            add_line(
                stub_end_x, stub_end_y - num, stub_end_x + num, stub_end_y - num + half
            )
        elif orient == TextOrientation.DEGREES_90:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(
                stub_end_x - num, stub_end_y, stub_end_x - num + half, stub_end_y - num
            )
            add_line(stub_end_x, stub_end_y, stub_end_x + half, stub_end_y - num)
            add_line(
                stub_end_x + num, stub_end_y, stub_end_x + num + half, stub_end_y - num
            )
        elif orient == TextOrientation.DEGREES_180:
            add_line(stub_end_x, stub_end_y - num, stub_end_x, stub_end_y + num)
            add_line(
                stub_end_x, stub_end_y - num, stub_end_x - num, stub_end_y - num - half
            )
            add_line(stub_end_x, stub_end_y, stub_end_x - num, stub_end_y - half)
            add_line(
                stub_end_x, stub_end_y + num, stub_end_x - num, stub_end_y + num - half
            )
        else:
            add_line(stub_end_x - num, stub_end_y, stub_end_x + num, stub_end_y)
            add_line(
                stub_end_x - num, stub_end_y, stub_end_x - num - half, stub_end_y + num
            )
            add_line(stub_end_x, stub_end_y, stub_end_x - half, stub_end_y + num)
            add_line(stub_end_x + num, stub_end_y, stub_end_x + half, stub_end_y + num)

    def _standard_power_text_source_layout(
        self,
        ctx: "SchSvgRenderContext",
        *,
        orient: TextOrientation,
        font_size_px: float,
        glyph_font_size_px: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
        font_id: int,
        split_overline_text: Any,
    ) -> tuple[
        tuple[float, float] | None,
        tuple[int, int] | None,
        float,
        float | None,
    ]:
        if not self._has_visible_display_string(ctx):
            return None, None, 0.0, None

        from ._altium_record_sch__harness_layout import (
            _f32,
            _float_to_i32,
            _harness_internal_location,
            _trunc_i32_div,
            _unchecked_i32,
            _utf16_code_unit_prefix,
        )
        from .altium_text_metrics import measure_gdi_typographic_bounds

        display_text = self._get_display_string()
        measured_display_text = _utf16_code_unit_prefix(display_text, 8192)
        clean_text, _ = split_overline_text(display_text)
        if not clean_text:
            return None, None, 0.0, None
        measured_clean_text = _utf16_code_unit_prefix(clean_text, 8192)
        measure_size = ctx.get_font_size_for_width(font_id)
        clean_width, clean_height = measure_gdi_typographic_bounds(
            measured_clean_text,
            measure_size,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        _, raw_display_height = measure_gdi_typographic_bounds(
            measured_display_text,
            measure_size,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        clean_width_internal = _f32(clean_width * 100_000.0)
        clean_height_internal = _f32(clean_height * 100_000.0)
        clean_width_i32 = _float_to_i32(clean_width_internal, rounded=False)
        raw_height_i32 = _float_to_i32(
            _f32(raw_display_height * 100_000.0), rounded=False
        )
        origin_x, origin_y = _harness_internal_location(self.location)
        power_len = _float_to_i32(
            self._standard_power_symbol_length(1.0) * 100_000.0,
            rounded=False,
        )
        text_offset = _float_to_i32(
            self._standard_power_text_offset(power_len, 100_000.0),
            rounded=False,
        )

        anchor_x = origin_x
        anchor_y = origin_y
        if orient == TextOrientation.DEGREES_0:
            anchor_x = _unchecked_i32(origin_x + _unchecked_i32(text_offset + 200_000))
            anchor_y = _unchecked_i32(origin_y - _trunc_i32_div(raw_height_i32, 2))
        elif orient == TextOrientation.DEGREES_90:
            anchor_y = _unchecked_i32(origin_y + text_offset)
        elif orient == TextOrientation.DEGREES_180:
            anchor_x = _unchecked_i32(origin_x - _unchecked_i32(text_offset + 200_000))
            anchor_y = _unchecked_i32(origin_y - _trunc_i32_div(raw_height_i32, 2))
        else:
            anchor_y = _unchecked_i32(origin_y - text_offset)

        text_x = float(anchor_x)
        text_y = float(anchor_y)
        overline_x = anchor_x
        overline_y = anchor_y
        if orient == TextOrientation.DEGREES_0:
            text_y += clean_height_internal
            overline_y = _unchecked_i32(anchor_y + raw_height_i32)
        elif orient == TextOrientation.DEGREES_90:
            text_x -= clean_width_internal / 2.0
            text_y += clean_height_internal
            overline_x = _unchecked_i32(anchor_x - _trunc_i32_div(clean_width_i32, 2))
            overline_y = _unchecked_i32(anchor_y + raw_height_i32)
        elif orient == TextOrientation.DEGREES_180:
            text_x -= clean_width_internal
            text_y += clean_height_internal
            overline_x = _unchecked_i32(anchor_x - clean_width_i32)
            overline_y = _unchecked_i32(anchor_y + raw_height_i32)
        else:
            text_x -= clean_width_internal / 2.0
            overline_x = _unchecked_i32(anchor_x - _trunc_i32_div(clean_width_i32, 2))

        from .altium_text_metrics import _measure_text_geometry_glyph_box

        glyph_width, glyph_height = _measure_text_geometry_glyph_box(
            clean_text,
            glyph_font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        return (
            (text_x / 100_000.0, text_y / 100_000.0),
            (overline_x, overline_y),
            glyph_width,
            glyph_height,
        )

    @staticmethod
    def _standard_power_text_operations(
        *,
        text: str,
        baseline: tuple[float, float],
        overline_origin_internal: tuple[int, int],
        ctx: "SchSvgRenderContext",
        source_font_size_px: float,
        render_font_size_px: float,
        render_font_rotation: float,
        world_scale: float,
        sheet_height_px: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
        font_payload: dict[str, object],
        stroke_raw: int,
        units_per_px: int,
        track_point: Any,
    ) -> list[Any]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            _apply_single_slash_negation,
            _geometry_units_scale,
            make_pen,
            make_text_with_overline_operations,
            split_overline_text,
            svg_coord_to_geometry,
        )
        from .altium_text_metrics import measure_text_width

        from ._altium_record_sch__harness_layout import (
            _f32,
            _float_to_i32,
            _unchecked_i32,
        )

        text = _apply_single_slash_negation(
            text, enabled=ctx.options.single_slash_negation
        )
        clean_text, _ = split_overline_text(text)
        raw_unit_count = sum(1 for _ in _iter_utf16_code_units(text))
        clean_unit_count = 0
        previous_clean_unit = 0
        measured_clean_units: list[int] = []
        overline_markers: list[tuple[int, bool, int]] = []
        for source_index, code_unit in enumerate(_iter_utf16_code_units(text)):
            if code_unit != ord("\\"):
                clean_unit_count += 1
                previous_clean_unit = code_unit
                if len(measured_clean_units) < 8192:
                    measured_clean_units.append(code_unit)
                continue
            if not clean_unit_count:
                continue
            if len(overline_markers) == 8192:
                raise ValueError("power-port overline operation limit exceeded")
            overline_markers.append(
                (
                    clean_unit_count - 1,
                    source_index < raw_unit_count - 1,
                    previous_clean_unit,
                )
            )
        operations: list[Any] = []
        if overline_markers:
            line_pen = make_pen(
                stroke_raw,
                width=_geometry_units_scale(units_per_px) * world_scale,
            )
            prefix_width_cache: dict[tuple[int, bool], float] = {}
            for character_index, include_extent, character_unit in overline_markers:
                character = _text_from_utf16_code_units([character_unit])
                prefix_length = min(character_index + 1, len(measured_clean_units))
                prefix_key = (prefix_length, include_extent)
                prefix_width = prefix_width_cache.get(prefix_key)
                if prefix_width is None:
                    prefix_width = measure_text_width(
                        _text_from_utf16_code_units(
                            measured_clean_units[:prefix_length]
                        ),
                        source_font_size_px,
                        font_name,
                        bold=is_bold,
                        italic=is_italic,
                        include_rsb=include_extent,
                    )
                    prefix_width_cache[prefix_key] = prefix_width
                prefix_width_internal = _float_to_i32(
                    _f32(prefix_width * 100_000.0),
                    rounded=False,
                )
                character_width_internal = _float_to_i32(
                    _f32(
                        measure_text_width(
                            character,
                            source_font_size_px,
                            font_name,
                            bold=is_bold,
                            italic=is_italic,
                            include_rsb=include_extent,
                        )
                        * 100_000.0
                    ),
                    rounded=False,
                )
                endpoint_x = _unchecked_i32(
                    overline_origin_internal[0] + prefix_width_internal
                )
                startpoint_x = _unchecked_i32(endpoint_x - character_width_internal)
                endpoint = ctx.transform_point(
                    endpoint_x / 100_000.0,
                    overline_origin_internal[1] / 100_000.0,
                )
                startpoint = ctx.transform_point(
                    startpoint_x / 100_000.0,
                    overline_origin_internal[1] / 100_000.0,
                )
                track_point(*endpoint)
                track_point(*startpoint)
                line_start = svg_coord_to_geometry(
                    *endpoint,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                line_end = svg_coord_to_geometry(
                    *startpoint,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                operations.append(
                    SchGeometryOp.lines([line_start, line_end], pen=line_pen)
                )

        operations.extend(
            make_text_with_overline_operations(
                text=clean_text,
                baseline_x_px=baseline[0],
                baseline_y_px=baseline[1],
                sheet_height_px=sheet_height_px,
                font_payload=font_payload,
                font_size_px=render_font_size_px,
                font_name=font_name,
                bold=is_bold,
                italic=is_italic,
                brush_color_raw=stroke_raw,
                rotation_deg=render_font_rotation,
                units_per_px=units_per_px,
                overline_pen_width=_geometry_units_scale(units_per_px) * world_scale,
            )
        )
        return operations

    def _standard_power_symbol_length(self, scale: float) -> float:
        if self.style in [8, 9]:
            return 16 * scale
        if self.style == 10:
            return 20 * scale
        return 10 * scale

    def _standard_power_text_offset(self, power_len: float, scale: float) -> float:
        if self.style in [4, 5, 6]:
            return 2 * power_len
        if self.style == 8:
            return power_len + 8 * scale
        if self.style == 9:
            return power_len + 12 * scale
        return power_len

    def _track_text_geometry_bounds(
        self,
        track_point: Any,
        text_baseline: tuple[float, float],
        text_width: float,
        text_height: float,
        font_size_px: float,
    ) -> None:
        track_point(text_baseline[0], text_baseline[1] - float(int(font_size_px)))
        track_point(
            text_baseline[0] + text_width,
            text_baseline[1] - float(int(font_size_px)) + text_height,
        )

    def _finalize_power_port_geometry_record(
        self,
        *,
        document_id: str,
        object_id: str,
        sheet_height_px: float,
        connection_point: tuple[float, float],
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        operations: list[Any],
        units_per_px: int,
        bounds_factory: Any,
        record_factory: Any,
        wrap_record_operations: Any,
    ) -> Any:
        return record_factory(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="power",
            object_id=object_id,
            bounds=bounds_factory(
                left=int(math.floor(min_x * 100000)),
                top=int(math.floor((sheet_height_px - min_y) * 100000)),
                right=int(math.ceil(max_x * 100000)),
                bottom=int(math.ceil((sheet_height_px - max_y) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
            extras={
                "connection_points": [
                    {
                        "id": "power-port-hotspot",
                        "kind": "connection",
                        "role": "ratsnest-anchor",
                        "point": [connection_point[0], connection_point[1]],
                        "source_kind": "power_port_hotspot",
                    }
                ]
            },
        )
