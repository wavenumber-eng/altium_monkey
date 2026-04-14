"""Schematic record model for SchRecordType.RECTANGLE."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    CoordPoint,
    LineStyle,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
)
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    RectangularBoundsMilsMixin,
    detect_case_mode_method_from_dotted_uppercase_fields,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SEMI_TRANSPARENT_ALPHA,
    SchSvgRenderContext,
)


class AltiumSchRectangle(RectangularBoundsMilsMixin, SchGraphicalObject):
    """
    Rectangle record.

    Rectangle from location to corner with optional fill and stroke.

    Public code should use ``location_mils``, ``corner_mils``, and
    ``bounds_mils`` for geometry updates. Use ``line_width``,
    ``line_style``, ``is_solid``, and ``transparent`` for stroke/fill state.
    The raw coord fields remain internal serializer-facing storage.
    """

    def __init__(self) -> None:
        super().__init__()
        self.corner = CoordPoint()
        self.line_width: LineWidth = LineWidth.SMALLEST
        self.line_style: LineStyle = LineStyle.SOLID
        self.is_solid: bool = True
        self.transparent: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.RECTANGLE

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
        self.corner = CoordPoint(
            int(corner_x),
            int(corner_y),
            int(corner_x_frac),
            int(corner_y_frac),
        )

        # Parse line properties
        line_width_val, _ = s.read_int(record, Fields.LINE_WIDTH, default=0)
        self.line_width = LineWidth(line_width_val)

        # LineStyle vs LineStyleExt: prefer LineStyleExt if present and LineStyle is 0
        line_style_val, _ = s.read_int(record, Fields.LINE_STYLE, default=0)
        line_style_ext, has_ext = s.read_int(record, Fields.LINE_STYLE_EXT, default=0)
        if line_style_val == 0 and has_ext and line_style_ext > 0:
            self.line_style = LineStyle(line_style_ext)
        else:
            self.line_style = LineStyle(line_style_val)

        # Parse boolean properties
        self.is_solid, _ = s.read_bool(record, Fields.IS_SOLID, default=False)
        self.transparent, _ = s.read_bool(record, Fields.TRANSPARENT, default=False)

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record (if present)
        # SchLib uses UPPERCASE, SchDoc uses PascalCase
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Normalize coordinates: Location has smaller values, Corner has larger
        # This matches Altium's ConvertToPositiveSlope behavior
        loc_x, loc_y = self.location.x, self.location.y
        corner_x, corner_y = self.corner.x, self.corner.y
        loc_x_frac, loc_y_frac = self.location.x_frac, self.location.y_frac
        corner_x_frac, corner_y_frac = self.corner.x_frac, self.corner.y_frac

        # Swap X if needed
        if loc_x > corner_x:
            loc_x, corner_x = corner_x, loc_x
            loc_x_frac, corner_x_frac = corner_x_frac, loc_x_frac

        # Swap Y if needed
        if loc_y > corner_y:
            loc_y, corner_y = corner_y, loc_y
            loc_y_frac, corner_y_frac = corner_y_frac, loc_y_frac

        # Write location (overwriting parent's values with normalized)
        # Do not use skip_if_zero for normalized coordinates. The base record may
        # still contain pre-normalization values, so the normalized coordinates
        # must always overwrite them.
        s.write_coord(record, "Location", "X", loc_x, loc_x_frac, raw)
        s.write_coord(record, "Location", "Y", loc_y, loc_y_frac, raw)

        # Write corner - also no skip_if_zero since raw record may have stale values
        s.write_coord(record, "Corner", "X", corner_x, corner_x_frac, raw)
        s.write_coord(record, "Corner", "Y", corner_y, corner_y_frac, raw)

        # Write line properties - skip if default (0 = SMALLEST)
        # Altium's Library Splitter omits LineWidth=0.
        # If raw record omitted LineWidth but caller changed width to non-default
        # (e.g., clean transform setting SMALL), force emission so change persists.
        if raw:
            _, has_line_width = Fields.LINE_WIDTH.find_in_record(raw)
        else:
            has_line_width = False
        line_width_raw = raw if has_line_width else None
        s.write_int(
            record,
            Fields.LINE_WIDTH,
            self.line_width.value,
            line_width_raw,
            skip_if_default=True,
            default=0,
        )

        if self.line_style != LineStyle.SOLID:
            s.write_int(record, Fields.LINE_STYLE_EXT, self.line_style.value, raw)

        # Write boolean properties
        s.write_bool(record, Fields.IS_SOLID, self.is_solid, raw)
        # Only write Transparent if True - Altium's Library Splitter omits Transparent=F
        if self.transparent:
            s.write_bool(record, Fields.TRANSPARENT, self.transparent, raw)
        else:
            s.remove_field(record, Fields.TRANSPARENT)

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
        Build an oracle-aligned geometry record for this rectangle.
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
        left = min(float(self.location.x), float(self.corner.x))
        right = max(float(self.location.x), float(self.corner.x))
        bottom = min(float(self.location.y), float(self.corner.y))
        top = max(float(self.location.y), float(self.corner.y))
        inflate = 2.0

        dash_style_map = {
            LineStyle.SOLID: "pdsSolid",
            LineStyle.DASHED: "pdsDash",
            LineStyle.DOTTED: "pdsDot",
            LineStyle.DASH_DOT: "pdsDashDot",
        }
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
                    brush=make_solid_brush(
                        fill_color_raw,
                        alpha=SEMI_TRANSPARENT_ALPHA if self.transparent else 0xFF,
                    ),
                )
            )

        operations.append(
            SchGeometryOp.rounded_rectangle(
                x1=geo_left,
                y1=geo_top,
                x2=geo_right,
                y2=geo_bottom,
                pen=make_pen(
                    stroke_color_raw,
                    width=pen_width,
                    line_join="pljMiter",
                    dash_style=dash_style_map.get(self.line_style, "pdsSolid"),
                ),
            )
        )

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="rectangle",
            object_id="eRectangle",
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
