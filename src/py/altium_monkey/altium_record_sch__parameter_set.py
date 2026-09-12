"""Schematic record model for SchRecordType.PARAMETER_SET."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._sch_source_admission import _SourceAdmission

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import (
        SchGeometryBounds,
        SchGeometryOp,
        SchGeometryRecord,
    )
    from .altium_sch_svg_renderer import (
        SchSvgRenderContext,
        _ParameterSetRenderBudget,
    )

from .altium_sch_enums import ParameterSetStyle, Rotation90
from .altium_record_types import (
    SchGraphicalObject,
    SchRecordType,
    color_to_hex,
    hex_to_win32_color,
)
from ._sch_managed_defaults import DIRECTIVE_COLOR, GRAPHICAL_FILL_COLOR
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    RotatedLocalPointMixin,
    detect_case_mode_from_uppercase_fields,
)
from .altium_sch_svg_renderer import svg_text
from .altium_text_metrics import measure_gdi_typographic_bounds, measure_text_width
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key


_IMPORTED_DIRECTIVE_COLOR = 0xFFBF00

_GeometryCoord = Callable[[float, float], tuple[float, float]]


@dataclass(frozen=True, slots=True)
class _ParameterSetGeometryState:
    differential_pair: bool
    display_string: str
    imported: bool
    source_color: int
    gated_out: bool
    has_effective_document: bool
    bounds: SchGeometryBounds


def _transform_trace_points(
    record: RotatedLocalPointMixin,
    x: float,
    y: float,
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    return [
        record._transform_local_point(x, y, point_x, point_y)
        for point_x, point_y in points
    ]


class AltiumSchParameterSet(RotatedLocalPointMixin, SchGraphicalObject):
    """Parameter-set directive with ordered child parameters."""

    def __init__(self) -> None:
        super().__init__()
        self.color = DIRECTIVE_COLOR
        self.area_color = GRAPHICAL_FILL_COLOR
        self._init_family_dynamic_unique_id()
        self._geometry_stroke_width_px = 1.0
        self._geometry_tiny_stroke_width_px = 0.33333
        self._geometry_font_size_px = 577.9188842773438 / 64.0
        self.name: str = "Parameter Set"
        self.orientation: Rotation90 = Rotation90.DEG_0
        self.style: ParameterSetStyle = ParameterSetStyle.LARGE
        # DrawObjectInfo state is request-local/editor state and is never serialized.
        self._draw_disabled = False
        self._draw_dimmed = False
        self._draw_compilation_masked = False
        self._draw_editable_in_current_view = True
        # Track field presence
        self._has_name: bool = False
        self._used_utf8_name: bool = False
        self._has_orientation: bool = False
        self._has_style: bool = False
        # Child parameters (populated by hierarchy builder)
        self.parameters: list = []
        self._capture_graphical_source_state()
        self._capture_parameter_set_source_state()

    def _capture_parameter_set_source_state(self) -> None:
        self._source_name = self.name
        self._source_orientation = self.orientation
        self._source_style = self.style

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PARAMETER_SET

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        s = AltiumSerializer()
        r = self._record
        self._parse_family_dynamic_unique_id(s, record)

        # Parameter set name
        self.name, self._has_name, self._used_utf8_name = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.NAME,
            default="",
        )

        # Orientation (0-3)
        orientation_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = Rotation90(orientation_val)

        # Display style
        style_val, self._has_style = s.read_int(record, Fields.STYLE, default=0)
        self.style = ParameterSetStyle(style_val)
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()
        self._capture_parameter_set_source_state()

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        mode = detect_case_mode_from_uppercase_fields(self._raw_record)
        s = AltiumSerializer(mode)
        raw = self._raw_record

        self._serialize_managed_family_color(
            record, s, Fields.COLOR.canonical, int(self.color or 0)
        )

        if self.name != self._source_name and not self.name:
            self._remove_fields_case_insensitively(record, ["Name", "%UTF8%Name"])
        else:
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
        if self._has_orientation or self.orientation != self._source_orientation:
            s.write_int(
                record,
                Fields.ORIENTATION,
                self.orientation.value,
                raw,
                force=self.orientation != self._source_orientation,
            )
        if self._has_style or self.style != self._source_style:
            s.write_int(
                record,
                Fields.STYLE,
                self.style.value,
                raw,
                force=self.style != self._source_style,
            )
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Color",
                "Orientation",
                "Name",
                "Style",
                "UniqueID",
            ),
        )

    def is_differential_pair(self) -> bool:
        """Test the first matching DifferentialPair child's untrimmed text."""
        return self._source_is_differential_pair(_SourceAdmission())

    def _source_is_differential_pair(
        self,
        source_admission: _SourceAdmission,
        budget: "_ParameterSetRenderBudget | None" = None,
    ) -> bool:
        from .altium_record_sch__parameter import AltiumSchParameter

        for param in source_admission.children(self, self.parameters):
            # The managed object-set filter excludes image and designator kinds.
            if type(param) is not AltiumSchParameter:
                continue
            if budget is not None:
                budget.reserve_text_characters(len(param.name))
            if dotnet_ordinal_ignore_case_key(param.name) == "DIFFERENTIALPAIR":
                if budget is not None:
                    budget.reserve_text_characters(len(param.text))
                return dotnet_ordinal_ignore_case_key(param.text) == "TRUE"
        return False

    def _get_display_string(self, ctx: "SchSvgRenderContext | None" = None) -> str:
        """Return the label after the managed evaluator/preference gates."""
        # Child parameters define directives; they are not fallback label text.
        if (
            ctx is None
            or not ctx.owner_document_present
            or not ctx.options.convert_special_strings
            or not self.name.startswith("=")
        ):
            return self.name
        resolved = ctx.substitute_parameters(self.name)
        if not resolved and ctx.options.replace_empty_value_with_name:
            return self.name
        return resolved

    def _rotate_svg_offset(self, dx: float, dy: float) -> tuple[float, float]:
        """
        Rotate a local SVG-space offset using schematic 90-degree orientation.
        """
        match self.orientation:
            case Rotation90.DEG_0:
                return dx, dy
            case Rotation90.DEG_90:
                return dy, -dx
            case Rotation90.DEG_180:
                return -dx, -dy
            case Rotation90.DEG_270:
                return -dy, dx

        return dx, dy

    def _render_standard(self, x: float, y: float) -> list[str]:
        stroke = color_to_hex(self.color)
        fill = stroke
        display_string = self._get_display_string()
        is_tiny = self.style == ParameterSetStyle.TINY

        line_length = 2.0 if is_tiny else 6.0
        circle_radius = 2.0 if is_tiny else 6.0
        circle_diameter = circle_radius * 2.0
        circle_center_offset = 4.0 if is_tiny else 12.0

        line_end_x, line_end_y = self._transform_local_point(x, y, line_length, 0.0)
        circle_center_x, circle_center_y = self._transform_local_point(
            x, y, circle_center_offset, 0.0
        )

        elements = [
            f'<line x1="{x}" y1="{y}" x2="{line_end_x}" y2="{line_end_y}" '
            f'stroke="{stroke}" stroke-width="1px"/>',
            f'<rect x = "{circle_center_x - circle_radius}" y="{circle_center_y - circle_radius}" '
            f'width="{circle_diameter}" height="{circle_diameter}" '
            f'stroke="{stroke}" stroke-width="1px" '
            f'rx="{circle_radius}" ry="{circle_radius}"/>',
        ]

        if is_tiny:
            return elements

        text_width = measure_text_width(display_string, 9.0, "Times New Roman")

        match self.orientation:
            case Rotation90.DEG_0:
                info_x = x + 10.8558
                info_y = y + 4.0
                label_x = x + 20.0
                label_y = y + 4.0
            case Rotation90.DEG_90:
                info_x = x - 1.14418
                info_y = y - 8.0
                label_x = x - text_width / 2.0
                label_y = y - 21.0
            case Rotation90.DEG_180:
                info_x = x - 13.1442
                info_y = y + 4.0
                label_x = x - 20.0 - text_width
                label_y = y + 4.0
            case Rotation90.DEG_270:
                info_x = x - 1.14418
                info_y = y + 16.0
                label_x = x - text_width / 2.0
                label_y = y + 29.0
            case _:
                info_x = x + 10.8558
                info_y = y + 4.0
                label_x = x + 20.0
                label_y = y + 4.0

        elements.append(
            svg_text(
                info_x,
                info_y,
                "i",
                font_size=9,
                font_family="Times New Roman",
                fill=fill,
            )
        )
        if display_string:
            elements.append(
                svg_text(
                    label_x,
                    label_y,
                    display_string,
                    font_size=9,
                    font_family="Times New Roman",
                    fill=fill,
                )
            )
        return elements

    def _render_diffpair(
        self, x: float, y: float, ctx: "SchSvgRenderContext"
    ) -> list[str]:
        """
        Render differential pair directive as zigzag traces.

        Render differential pair directive as two zigzag traces.

        The shadow color is blended against the active sheet area color, so
        different sheet backgrounds can produce different muted shadow tones.

        Args:
            x, y: Transformed location coordinates
            ctx: Render context with sheet_area_color for shadow calculation

        Returns:
            List of SVG line elements
        """
        elements = []

        # Get document area color from context for shadow calculation.
        raw_area_color = getattr(ctx, "sheet_area_color", 0xFFFFFF)
        doc_area_color = 0xFFFFFF if raw_area_color is None else int(raw_area_color)

        # Determine colors based on directive color and document area
        # Main color: directive color or black
        effective_color = self.color if self.color is not None else 0
        main_color = color_to_hex(effective_color) if effective_color else "#000000"

        # Shadow color: calculated using ApplyDark + ModifyColor algorithm
        # This now correctly accounts for different document area colors
        shadow_color = self._derive_diffpair_shadow_color(
            effective_color, doc_area_color
        )

        def trace_segments(
            points: list[tuple[float, float]],
        ) -> list[tuple[float, float, float, float]]:
            return [
                (
                    points[index][0],
                    points[index][1],
                    points[index + 1][0],
                    points[index + 1][1],
                )
                for index in range(len(points) - 1)
            ]

        main_upper = _transform_trace_points(
            self,
            x,
            y,
            [
                (5.0, -7.5),
                (9.0, -7.5),
                (11.0, -9.5),
                (15.0, -9.5),
                (17.0, -7.5),
                (21.0, -7.5),
            ],
        )
        main_lower = _transform_trace_points(
            self,
            x,
            y,
            [
                (5.0, -5.0),
                (9.0, -5.0),
                (11.0, -3.0),
                (15.0, -3.0),
                (17.0, -5.0),
                (21.0, -5.0),
            ],
        )
        main_connector_end = self._transform_local_point(x, y, 5.0, -5.0)
        shadow_upper = [
            (point_x + 1.0, point_y + 1.0) for point_x, point_y in main_upper
        ]
        shadow_lower = [
            (point_x + 1.0, point_y + 1.0) for point_x, point_y in main_lower
        ]
        shadow_connector_end = (
            main_connector_end[0] + 1.0,
            main_connector_end[1] + 1.0,
        )

        # ====================================================================
        # SHADOW LAYER FIRST (muted color, offset +1px in both X and Y)
        # ====================================================================
        # In native SVG, shadow traces start at x+6, main traces at x+5 (1px difference)
        # Shadow Y is +1px from main (in SVG coords where Y increases downward)

        for x1, y1, x2, y2 in trace_segments(shadow_upper):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{shadow_color}" stroke-width="1px"/>'
            )

        for x1, y1, x2, y2 in trace_segments(shadow_lower):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{shadow_color}" stroke-width="1px"/>'
            )

        elements.append(
            f'<line x1="{x}" y1="{y}" x2="{shadow_connector_end[0]}" y2="{shadow_connector_end[1]}" '
            f'stroke="{shadow_color}" stroke-width="0.5px" vector-effect="non-scaling-stroke"/>'
        )

        # ====================================================================
        # MAIN LAYER SECOND (directive color, offset -1px from shadow)
        # ====================================================================
        # Main traces are 1px left and 1px up from shadow in SVG coords

        for x1, y1, x2, y2 in trace_segments(main_upper):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{main_color}" stroke-width="1px"/>'
            )

        for x1, y1, x2, y2 in trace_segments(main_lower):
            elements.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{main_color}" stroke-width="1px"/>'
            )

        elements.append(
            f'<line x1="{x}" y1="{y}" x2="{main_connector_end[0]}" y2="{main_connector_end[1]}" '
            f'stroke="{main_color}" stroke-width="0.5px" vector-effect="non-scaling-stroke"/>'
        )

        return elements

    def _derive_diffpair_shadow_color(
        self, color: int, doc_area_color: int = 0xFFFFFF
    ) -> str:
        """
        Derive muted shadow color for diffpair traces from directive color.

        Implements Altium's exact algorithm from native implementation:
            ColorManager.ModifyColor(64, ColorManager.ApplyDark(color, 128), docAreaColor)

        From native implementation:
        - ApplyDark(color, howMuch): Subtract howMuch from each channel (min 0)
        - ModifyColor(percent, color, bgColor): Blend color toward bgColor by percent%
          result = color + (bgColor - color) * percent / 100

        Args:
            color: Altium BGR color value
            doc_area_color: Document area background color (default white)

        Returns:
            Hex color string for shadow
        """
        # Extract RGB from BGR format
        b = (color >> 16) & 0xFF
        g = (color >> 8) & 0xFF
        r = color & 0xFF

        # Step 1: ApplyDark(color, 128) - subtract 128 from each channel, min 0
        r_dark = max(0, r - 128)
        g_dark = max(0, g - 128)
        b_dark = max(0, b - 128)

        # Document area color (typically white)
        doc_r = doc_area_color & 0xFF
        doc_g = (doc_area_color >> 8) & 0xFF
        doc_b = (doc_area_color >> 16) & 0xFF

        # Step 2: ModifyColor(64, darkColor, docAreaColor)
        # result = darkColor + (docAreaColor - darkColor) * 64 / 100
        r_out = r_dark + (doc_r - r_dark) * 64 // 100
        g_out = g_dark + (doc_g - g_dark) * 64 // 100
        b_out = b_dark + (doc_b - b_dark) * 64 // 100

        return f"#{r_out:02X}{g_out:02X}{b_out:02X}"

    @staticmethod
    def _painter_pen_color(ctx: "SchSvgRenderContext", color_raw: int) -> int:
        from ._altium_record_sch__harness_layout import _harness_state_gray_level
        from .altium_sch_svg_renderer import SchPaintColorMode

        if ctx.options.is_metafile:
            if ctx.options.paint_color_mode is SchPaintColorMode.GRAYSCALE:
                return _harness_state_gray_level(color_raw)
            if ctx.options.paint_color_mode is SchPaintColorMode.MONOCHROME:
                return 0
        if ctx.options.emphasize:
            return 0x0000FF
        return color_raw

    @staticmethod
    def _painter_text_color(ctx: "SchSvgRenderContext", color_raw: int) -> int:
        from ._altium_record_sch__harness_layout import _harness_state_gray_level
        from .altium_sch_svg_renderer import SchPaintColorMode

        if ctx.options.paint_color_mode is SchPaintColorMode.MONOCHROME:
            return 0
        if not ctx.options.is_metafile:
            return color_raw
        if ctx.options.paint_color_mode is SchPaintColorMode.GRAYSCALE:
            return _harness_state_gray_level(color_raw)
        return color_raw

    def _capture_geometry_state(
        self, ctx: SchSvgRenderContext
    ) -> _ParameterSetGeometryState:
        differential_pair = self._source_is_differential_pair(
            ctx._source_admission,
            ctx._parameter_set_work_budget,
        )
        ctx._parameter_set_work_budget.reserve_text_characters(len(self.name))
        display_string = "" if differential_pair else self._get_display_string(ctx)
        imported = ctx._parameter_set_is_imported(self)
        gated_out = ctx.options.is_metafile and not ctx.options.metafile_parameter_sets
        has_document = bool(ctx.owner_document_present)
        self._reserve_geometry_text_work(
            ctx,
            display_string,
            differential_pair=differential_pair,
            imported=imported,
            gated_out=gated_out,
            has_document=has_document,
        )
        bounds = self._geometry_bounds(
            ctx,
            differential_pair=differential_pair,
            display_string=display_string,
            has_document=has_document,
        )
        state = _ParameterSetGeometryState(
            differential_pair,
            display_string,
            imported,
            self._geometry_source_color(ctx, imported),
            gated_out,
            has_document,
            bounds,
        )
        ctx._parameter_set_work_budget.reserve_operations(
            self._geometry_raw_operation_count(ctx, state) + 8
        )
        return state

    def _reserve_geometry_text_work(
        self,
        ctx: SchSvgRenderContext,
        display_string: str,
        *,
        differential_pair: bool,
        imported: bool,
        gated_out: bool,
        has_document: bool,
    ) -> None:
        measure_count = self._geometry_text_measure_count(
            ctx,
            differential_pair=differential_pair,
            gated_out=gated_out,
            has_document=has_document,
        )
        marker_characters = int(
            imported and differential_pair and not gated_out and has_document
        )
        info_characters = int(
            not differential_pair
            and not gated_out
            and self.style != ParameterSetStyle.TINY
            and not ctx.options.optimized
        )
        ctx._parameter_set_work_budget.reserve_text_characters(
            len(display_string) * measure_count + marker_characters + info_characters
        )

    def _geometry_bounds(
        self,
        ctx: SchSvgRenderContext,
        *,
        differential_pair: bool,
        display_string: str,
        has_document: bool,
    ) -> SchGeometryBounds:
        from ._altium_record_sch__harness_layout import _harness_internal_location
        from ._altium_sch_component_bounds import (
            _parameter_set_bounds_from_state,
            _portable_string_bounds,
        )

        return _parameter_set_bounds_from_state(
            _harness_internal_location(self.location),
            self.orientation,
            self.style,
            has_effective_document=has_document,
            differential_pair=differential_pair,
            display_string=display_string,
            measure=lambda location, text, font_id: _portable_string_bounds(
                ctx, location, text, font_id
            ),
            max_text_characters=ctx.options.max_parameter_set_text_characters,
        )

    def _geometry_source_color(self, ctx: SchSvgRenderContext, imported: bool) -> int:
        if imported:
            return _IMPORTED_DIRECTIVE_COLOR
        if ctx.line_color_override is not None:
            return int(ctx.line_color_override)
        return int(self.color) if self.color is not None else 0

    def _geometry_text_measure_count(
        self,
        ctx: SchSvgRenderContext,
        *,
        differential_pair: bool,
        gated_out: bool,
        has_document: bool,
    ) -> int:
        if differential_pair:
            return 0
        bounds_measurements = (
            int(has_document and self.style == ParameterSetStyle.LARGE) * 2
        )
        return bounds_measurements + int(not gated_out)

    def _geometry_raw_operation_count(
        self,
        ctx: SchSvgRenderContext,
        state: _ParameterSetGeometryState,
    ) -> int:
        if state.gated_out or (
            state.differential_pair and not state.has_effective_document
        ):
            return 0
        if state.differential_pair:
            return 7 if state.imported else 6
        visible = self.style != ParameterSetStyle.TINY and not ctx.options.optimized
        return 2 + int(visible) * (1 + int(bool(state.display_string)))

    @staticmethod
    def _geometry_coord(ctx: SchSvgRenderContext, units_per_px: int) -> _GeometryCoord:
        from .altium_sch_geometry_oracle import svg_coord_to_geometry

        sheet_height_px = float(ctx.sheet_height or 0.0)

        def coord(px: float, py: float) -> tuple[float, float]:
            return svg_coord_to_geometry(
                px,
                py,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )

        return coord

    def _geometry_record(
        self,
        document_id: str,
        units_per_px: int,
        state: _ParameterSetGeometryState,
        operations: list[SchGeometryOp],
    ) -> SchGeometryRecord:
        from .altium_sch_geometry_oracle import (
            SchGeometryRecord,
            wrap_record_operations,
        )

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="parameterset",
            object_id="eParameterSet",
            bounds=state.bounds,
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _diffpair_geometry_operations(
        self,
        ctx: SchSvgRenderContext,
        state: _ParameterSetGeometryState,
        coord: _GeometryCoord,
        units_per_px: int,
    ) -> list[SchGeometryOp]:
        from ._altium_record_sch__harness_layout import _harness_drawing_color
        from .altium_sch_geometry_oracle import make_pen

        x, y = ctx.transform_coord_precise(self.location)
        raw_area_color = getattr(ctx, "sheet_area_color", 0xFFFFFF)
        doc_area_color = 0xFFFFFF if raw_area_color is None else int(raw_area_color)
        shadow_color = hex_to_win32_color(
            self._derive_diffpair_shadow_color(state.source_color, doc_area_color)
        )
        main_draw_color = _harness_drawing_color(self, state.source_color, ctx)
        shadow_draw_color = _harness_drawing_color(self, shadow_color, ctx)
        main_pen_color = self._painter_pen_color(ctx, main_draw_color)
        shadow_pen_color = self._painter_pen_color(ctx, shadow_draw_color)
        main_upper, main_lower = self._diffpair_trace_points(x, y)
        main_connector_end = self._transform_local_point(x, y, 5.0, -5.0)
        shadow_upper = [(px + 1.0, py + 1.0) for px, py in main_upper]
        shadow_lower = [(px + 1.0, py + 1.0) for px, py in main_lower]
        shadow_connector_end = (
            main_connector_end[0] + 1.0,
            main_connector_end[1] + 1.0,
        )
        from .altium_sch_geometry_oracle import _geometry_item_length

        trace_width = _geometry_item_length(
            self._geometry_stroke_width_px,
            units_per_px=units_per_px,
        )
        operations = self._diffpair_trace_operations(
            coord,
            (x, y),
            main_connector_end,
            shadow_connector_end,
            main_upper,
            main_lower,
            shadow_upper,
            shadow_lower,
            main_pen=make_pen(main_pen_color, width=trace_width),
            shadow_pen=make_pen(shadow_pen_color, width=trace_width),
            main_connector_pen=make_pen(main_pen_color, width=0),
            shadow_connector_pen=make_pen(shadow_pen_color, width=0),
        )
        if state.imported:
            operations.append(
                self._diffpair_import_marker(
                    ctx,
                    coord,
                    units_per_px,
                    state.source_color,
                )
            )
        return operations

    def _diffpair_trace_points(
        self, x: float, y: float
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        upper = _transform_trace_points(
            self,
            x,
            y,
            [
                (5.0, -7.5),
                (9.0, -7.5),
                (11.0, -9.5),
                (15.0, -9.5),
                (17.0, -7.5),
                (21.0, -7.5),
            ],
        )
        lower = _transform_trace_points(
            self,
            x,
            y,
            [
                (5.0, -5.0),
                (9.0, -5.0),
                (11.0, -3.0),
                (15.0, -3.0),
                (17.0, -5.0),
                (21.0, -5.0),
            ],
        )
        return upper, lower

    @staticmethod
    def _diffpair_trace_operations(
        coord: _GeometryCoord,
        location: tuple[float, float],
        main_connector_end: tuple[float, float],
        shadow_connector_end: tuple[float, float],
        main_upper: list[tuple[float, float]],
        main_lower: list[tuple[float, float]],
        shadow_upper: list[tuple[float, float]],
        shadow_lower: list[tuple[float, float]],
        *,
        main_pen: dict[str, object],
        shadow_pen: dict[str, object],
        main_connector_pen: dict[str, object],
        shadow_connector_pen: dict[str, object],
    ) -> list[SchGeometryOp]:
        from .altium_sch_geometry_oracle import SchGeometryOp

        def points(values: list[tuple[float, float]]) -> list[tuple[float, float]]:
            return [coord(x, y) for x, y in values]

        return [
            SchGeometryOp.lines(points(shadow_upper), pen=shadow_pen),
            SchGeometryOp.lines(points(shadow_lower), pen=shadow_pen),
            SchGeometryOp.lines(
                [coord(*location), coord(*shadow_connector_end)],
                pen=shadow_connector_pen,
            ),
            SchGeometryOp.lines(points(main_upper), pen=main_pen),
            SchGeometryOp.lines(points(main_lower), pen=main_pen),
            SchGeometryOp.lines(
                [coord(*location), coord(*main_connector_end)],
                pen=main_connector_pen,
            ),
        ]

    def _diffpair_import_marker(
        self,
        ctx: SchSvgRenderContext,
        coord: _GeometryCoord,
        units_per_px: int,
        color_raw: int,
    ) -> SchGeometryOp:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_font_payload,
            make_solid_brush,
        )

        offsets = {
            Rotation90.DEG_0: (13.0, 9.0),
            Rotation90.DEG_90: (-6.0, 16.0),
            Rotation90.DEG_180: (-13.0, -3.0),
            Rotation90.DEG_270: (6.0, -10.0),
        }
        dx, dy = offsets[self.orientation]
        x, y = ctx.transform_point(
            self.location.x + self.location.x_frac / 100_000.0 + dx,
            self.location.y + self.location.y_frac / 100_000.0 + dy,
        )
        font_size_px = ctx._pt_to_px(6.0, "Times New Roman", False, False)
        width = measure_text_width("c", font_size_px, "Times New Roman")
        geometry_x, geometry_y = coord(x - width / 2.0, y)
        return SchGeometryOp.string(
            x=geometry_x,
            y=geometry_y,
            text="c",
            font=make_font_payload(
                name="Times New Roman",
                size_px=font_size_px,
                units_per_px=units_per_px,
                rotation=0.0,
            ),
            brush=make_solid_brush(self._painter_text_color(ctx, color_raw)),
        )

    def _standard_geometry_operations(
        self,
        ctx: SchSvgRenderContext,
        state: _ParameterSetGeometryState,
        coord: _GeometryCoord,
        units_per_px: int,
    ) -> list[SchGeometryOp]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            _geometry_item_length,
            make_pen,
        )

        x, y = ctx.transform_coord_precise(self.location)
        is_tiny = self.style == ParameterSetStyle.TINY
        radius = 2.0 if is_tiny else 6.0
        center_offset = 4.0 if is_tiny else 12.0
        line_length = 2.0 if is_tiny else 6.0
        stroke_width_px = (
            self._geometry_tiny_stroke_width_px
            if is_tiny
            else self._geometry_stroke_width_px
        )
        pen = make_pen(
            self._painter_pen_color(ctx, state.source_color),
            width=_geometry_item_length(
                stroke_width_px,
                units_per_px=units_per_px,
            ),
        )
        line_end = self._transform_local_point(x, y, line_length, 0.0)
        circle_center = self._transform_local_point(x, y, center_offset, 0.0)
        operations = [
            SchGeometryOp.lines([coord(x, y), coord(*line_end)], pen=pen),
            self._standard_circle_operation(
                coord,
                circle_center,
                radius,
                units_per_px,
                pen,
                imported=state.imported,
            ),
        ]
        text_operations = self._standard_text_operations(
            ctx,
            state.display_string,
            coord,
            units_per_px,
            x,
            y,
            source_color=state.source_color,
            visible=not is_tiny and not ctx.options.optimized,
        )
        operations.extend(text_operations)
        return operations

    @staticmethod
    def _standard_circle_operation(
        coord: _GeometryCoord,
        center: tuple[float, float],
        radius: float,
        units_per_px: int,
        pen: dict[str, object],
        *,
        imported: bool,
    ) -> SchGeometryOp:
        from .altium_sch_geometry_oracle import SchGeometryOp, _geometry_item_length

        center_x, center_y = coord(*center)
        radius_units = _geometry_item_length(radius, units_per_px=units_per_px)
        if imported:
            return SchGeometryOp.arc(
                center_x=center_x,
                center_y=center_y,
                width=_geometry_item_length(radius * 2.0, units_per_px=units_per_px),
                height=_geometry_item_length(radius * 2.0, units_per_px=units_per_px),
                start_angle=-40.0,
                end_angle=-320.0,
                pen=pen,
            )
        return SchGeometryOp.rounded_rectangle_from_item(
            center_x=center_x,
            center_y=center_y,
            half_width=radius_units,
            half_height=radius_units,
            corner_x_radius=radius_units,
            corner_y_radius=radius_units,
            pen=pen,
        )

    def _standard_text_operations(
        self,
        ctx: SchSvgRenderContext,
        display_string: str,
        coord: _GeometryCoord,
        units_per_px: int,
        x: float,
        y: float,
        *,
        source_color: int,
        visible: bool,
    ) -> list[SchGeometryOp]:
        from ._altium_record_sch__harness_layout import _utf16_code_unit_prefix
        from .altium_sch_geometry_oracle import make_solid_brush

        label_font = ctx.get_font_info(ctx._horizontal_system_font_id)
        label_size = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix(display_string, 8192),
            ctx.get_font_size_for_width(ctx._horizontal_system_font_id),
            label_font[0],
            bold=label_font[2],
            italic=label_font[3],
        )
        if not visible:
            return []
        info_font = ctx.get_font_info(1)
        info_size = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix("i", 8192),
            ctx.get_font_size_for_width(1),
            info_font[0],
            bold=info_font[2],
            italic=info_font[3],
        )
        positions = self._standard_text_positions(x, y, info_size, label_size)
        brush = make_solid_brush(self._painter_text_color(ctx, source_color))
        operations = [
            self._standard_text_operation(
                coord, units_per_px, positions[0], "i", info_font, brush
            )
        ]
        if display_string:
            operations.append(
                self._standard_text_operation(
                    coord,
                    units_per_px,
                    positions[1],
                    display_string,
                    label_font,
                    brush,
                )
            )
        return operations

    def _standard_text_positions(
        self,
        x: float,
        y: float,
        info_size: tuple[float, float],
        label_size: tuple[float, float],
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        info_width, info_height = info_size
        label_width, label_height = label_size
        managed_label_width = int(label_width * 100_000) / 100_000
        label_half_height = (int(label_height * 100_000) // 2) / 100_000
        positions = {
            Rotation90.DEG_0: (
                (x + 12.0 - info_width / 2.0, y + 5.0 - info_height),
                (x + 20.0, y + label_half_height - label_height),
            ),
            Rotation90.DEG_90: (
                (x - info_width / 2.0, y - 7.0 - info_height),
                (x - label_width / 2.0, y - 20.0 - label_height),
            ),
            Rotation90.DEG_180: (
                (x - 12.0 - info_width / 2.0, y + 5.0 - info_height),
                (
                    x - 20.0 - managed_label_width,
                    y + label_half_height - label_height,
                ),
            ),
            Rotation90.DEG_270: (
                (x - info_width / 2.0, y + 17.0 - info_height),
                (x - label_width / 2.0, y + 20.0),
            ),
        }
        return positions[self.orientation]

    @staticmethod
    def _standard_text_operation(
        coord: _GeometryCoord,
        units_per_px: int,
        position: tuple[float, float],
        text: str,
        font: tuple[str, float, bool, bool, bool],
        brush: dict[str, object],
    ) -> SchGeometryOp:
        from .altium_sch_geometry_oracle import SchGeometryOp, make_font_payload

        name, size_px, bold, italic, underline = font
        x, y = coord(*position)
        return SchGeometryOp.string(
            x=x,
            y=y,
            text=text,
            font=make_font_payload(
                name=name,
                size_px=size_px,
                units_per_px=units_per_px,
                rotation=0.0,
                underline=underline,
                italic=italic,
                bold=bold,
            ),
            brush=brush,
        )

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> SchGeometryRecord:
        state = self._capture_geometry_state(ctx)
        coord = self._geometry_coord(ctx, units_per_px)
        operations: list[SchGeometryOp] = []
        if not state.gated_out:
            if state.differential_pair and state.has_effective_document:
                operations = self._diffpair_geometry_operations(
                    ctx, state, coord, units_per_px
                )
            elif not state.differential_pair:
                operations = self._standard_geometry_operations(
                    ctx, state, coord, units_per_px
                )
        return self._geometry_record(document_id, units_per_px, state, operations)

    def __repr__(self) -> str:
        return (
            f"<AltiumSchParameterSet name='{self.name}' "
            f"style={self.style.name} orientation={self.orientation.value * 90}deg>"
        )
