"""Schematic record model for SchRecordType.ROUND_RECTANGLE."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
)
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    _coord_scalar_to_native_units,
    CornerXRadiusMilsMixin,
    CornerYRadiusMilsMixin,
    RectangularBoundsMilsMixin,
    detect_case_mode_method_from_dotted_uppercase_fields,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SchSvgRenderContext,
)


class AltiumSchRoundedRectangle(
    RectangularBoundsMilsMixin,
    CornerXRadiusMilsMixin,
    CornerYRadiusMilsMixin,
    SchGraphicalObject,
):
    """
    Rounded rectangle record.

    Rectangle with rounded corners.

    Public code should use ``location_mils``, ``corner_mils``, and
    ``bounds_mils`` for geometry updates. Use ``corner_x_radius_mils`` and
    ``corner_y_radius_mils`` for rounded-corner updates, plus ``line_width``
    and ``is_solid`` for stroke/fill state. The raw coord-style corner-radius
    fields remain internal serializer-facing storage.
    """

    def __init__(self) -> None:
        super().__init__()
        self.corner = CoordPoint()
        self.corner_x_radius: int = 20
        self.corner_x_radius_frac: int = 0
        self.corner_y_radius: int = 20
        self.corner_y_radius_frac: int = 0
        self.line_width: LineWidth = LineWidth.SMALLEST
        # Note: RoundRectangle does NOT support LineStyle, LineStyleExt, or Transparent
        # (per native file format implementation) - always uses SOLID lines and opaque fill
        self.is_solid: bool = True
        # Track presence for conditional serialization
        self._has_corner_x_radius: bool = False
        self._has_corner_y_radius: bool = False
        self._has_line_width: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.ROUND_RECTANGLE

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse from a record.
        """
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()

        # Parse corner coordinates
        corner_x, corner_x_frac, _ = s.read_coord(record, "Corner", "X")
        corner_y, corner_y_frac, _ = s.read_coord(record, "Corner", "Y")
        self.corner = CoordPoint(corner_x, corner_y, corner_x_frac, corner_y_frac)

        # Parse corner radii using the native coord-style whole/fraction form.
        self.corner_x_radius, self.corner_x_radius_frac, self._has_corner_x_radius = (
            s.read_coord(
                record,
                "CornerXRadius",
            )
        )
        if not self._has_corner_x_radius:
            self.corner_x_radius = 0
            self.corner_x_radius_frac = 0
        self.corner_y_radius, self.corner_y_radius_frac, self._has_corner_y_radius = (
            s.read_coord(
                record,
                "CornerYRadius",
            )
        )
        if not self._has_corner_y_radius:
            self.corner_y_radius = 0
            self.corner_y_radius_frac = 0

        # Parse line width with presence tracking
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)
        # Note: RoundRectangle does NOT support LineStyle/LineStyleExt/Transparent

        # Parse is_solid
        self.is_solid, _ = s.read_bool(record, Fields.IS_SOLID, default=False)

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Write corner coordinates
        s.write_coord(record, "Corner", "X", self.corner.x, self.corner.x_frac, raw)
        s.write_coord(record, "Corner", "Y", self.corner.y, self.corner.y_frac, raw)

        # Write corner radii using the native coord-style whole/fraction form.
        s.write_coord(
            record,
            "CornerXRadius",
            "",
            self.corner_x_radius,
            self.corner_x_radius_frac,
            raw,
        )
        s.write_coord(
            record,
            "CornerYRadius",
            "",
            self.corner_y_radius,
            self.corner_y_radius_frac,
            raw,
        )

        # Write line width - skip if default (0 = SMALLEST)
        s.write_int(
            record,
            Fields.LINE_WIDTH,
            self.line_width.value,
            raw,
            skip_if_default=True,
            default=0,
        )
        # Note: RoundRectangle does NOT serialize LineStyle/LineStyleExt/Transparent
        # even if those stale fields were present in a parsed raw record.
        s.remove_field(record, Fields.LINE_STYLE)
        s.remove_field(record, Fields.LINE_STYLE_EXT)
        s.remove_field(record, Fields.TRANSPARENT)

        # Write is_solid
        s.write_bool(record, Fields.IS_SOLID, self.is_solid, raw)

        return record

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this rounded rectangle.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        x1, y1 = ctx.transform_coord_precise(self.location)
        x2, y2 = ctx.transform_coord_precise(self.corner)
        svg_left = min(float(x1), float(x2))
        svg_right = max(float(x1), float(x2))
        svg_top = min(float(y1), float(y2))
        svg_bottom = max(float(y1), float(y2))
        geo_left, geo_top = svg_coord_to_geometry(
            svg_left,
            svg_top,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        geo_right, geo_bottom = svg_coord_to_geometry(
            svg_right,
            svg_bottom,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        width_units = abs(float(x2) - float(x1))
        height_units = abs(float(y2) - float(y1))
        corner_x_units = _coord_scalar_to_native_units(
            self.corner_x_radius,
            self.corner_x_radius_frac,
        )
        corner_y_units = _coord_scalar_to_native_units(
            self.corner_y_radius,
            self.corner_y_radius_frac,
        )
        fill_radius_x_units = min(corner_x_units, width_units / 2.0)
        fill_radius_y_units = min(corner_y_units, height_units / 2.0)
        stroke_radius_x_units = min(
            corner_x_units,
            width_units / 2.0,
        )
        stroke_radius_y_units = min(
            corner_y_units,
            height_units / 2.0,
        )
        fill_radius_x_px = fill_radius_x_units * ctx.scale
        fill_radius_y_px = fill_radius_y_units * ctx.scale
        stroke_radius_x_px = stroke_radius_x_units * ctx.scale
        stroke_radius_y_px = stroke_radius_y_units * ctx.scale
        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        pen_width = (
            0
            if self.line_width == LineWidth.SMALLEST
            else int(round(stroke_width_mils * units_per_px))
        )
        fill_color_raw = (
            int(ctx.area_color_override)
            if ctx.area_color_override is not None
            else int(self.area_color)
            if self.area_color is not None
            else 0
        )
        stroke_color_raw = (
            int(ctx.line_color_override)
            if ctx.line_color_override is not None
            else int(self.color or 0)
        )

        operations: list[SchGeometryOp] = []
        if self.is_solid:
            operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=geo_left,
                    y1=geo_top,
                    x2=geo_right,
                    y2=geo_bottom,
                    corner_x_radius=int(round(fill_radius_x_px * units_per_px)),
                    corner_y_radius=int(round(fill_radius_y_px * units_per_px)),
                    brush=make_solid_brush(fill_color_raw),
                )
            )

        operations.append(
            SchGeometryOp.rounded_rectangle(
                x1=geo_left,
                y1=geo_top,
                x2=geo_right,
                y2=geo_bottom,
                corner_x_radius=int(round(stroke_radius_x_px * units_per_px)),
                corner_y_radius=int(round(stroke_radius_y_px * units_per_px)),
                pen=make_pen(
                    stroke_color_raw,
                    width=pen_width,
                    line_join="pljRound",
                ),
            )
        )

        left = min(float(self.location.x), float(self.corner.x))
        right = max(float(self.location.x), float(self.corner.x))
        bottom = min(float(self.location.y), float(self.corner.y))
        top = max(float(self.location.y), float(self.corner.y))
        inflate = stroke_width_mils + 2.0

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="roundrectangle",
            object_id="eRoundRectangle",
            bounds=SchGeometryBounds(
                left=int(round((left - inflate) * 100000)),
                top=int(round((top + inflate) * 100000)),
                right=int(round((right + inflate) * 100000)),
                bottom=int(round((bottom - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
