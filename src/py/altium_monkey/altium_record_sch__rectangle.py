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
from ._sch_managed_defaults import RECT_BORDER_COLOR, RECT_FILL_COLOR
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    RectangularBoundsMilsMixin,
    _LineStyleDirtyMixin,
    detect_case_mode_method_from_dotted_uppercase_fields,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SEMI_TRANSPARENT_ALPHA,
    SchSvgRenderContext,
)


class AltiumSchRectangle(
    _LineStyleDirtyMixin, RectangularBoundsMilsMixin, SchGraphicalObject
):
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
        if self.record_type is SchRecordType.RECTANGLE:
            self.color = RECT_BORDER_COLOR
            self.area_color = RECT_FILL_COLOR
        self.corner = CoordPoint(50, 50)
        self.line_width: LineWidth = LineWidth.SMALLEST
        self._line_style: LineStyle = LineStyle.SOLID
        self._line_style_dirty = False
        self._source_line_style: LineStyle = LineStyle.SOLID
        self.is_solid: bool = True
        self._transparent: bool = False
        self._transparent_dirty = False

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
        if self.record_type in {
            SchRecordType.TEXT_FRAME,
            SchRecordType.NOTE,
            SchRecordType.COMPILE_MASK,
        }:
            line_style_ext, has_ext = 0, False
        else:
            line_style_ext, has_ext = s.read_int(
                record, Fields.LINE_STYLE_EXT, default=0
            )
        self._line_style = LineStyle(line_style_ext if has_ext else 0)
        self._source_line_style = self.line_style
        self._line_style_dirty = False

        # Parse boolean properties
        if self.record_type is SchRecordType.COMPILE_MASK:
            self.is_solid = True
        else:
            self.is_solid, _ = s.read_bool(record, Fields.IS_SOLID, default=False)
        if self.record_type in {SchRecordType.TEXT_FRAME, SchRecordType.NOTE}:
            self._transparent = False
        elif self.record_type is SchRecordType.COMPILE_MASK:
            self._transparent = True
        else:
            self._transparent, _ = s.read_bool(
                record, Fields.TRANSPARENT, default=False
            )
        self._transparent_dirty = False
        self._apply_imported_color_defaults(area_color=True)

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

        # Existing source coordinates must be overwritten after normalization,
        # but a detached authored origin remains omitted like Param WriteCoord.
        self._serialize_managed_family_coord(
            record, s, "Location", "X", loc_x, loc_x_frac
        )
        self._serialize_managed_family_coord(
            record, s, "Location", "Y", loc_y, loc_y_frac
        )

        # Write corner - also no skip_if_zero since raw record may have stale values
        self._serialize_managed_family_coord(
            record, s, "Corner", "X", corner_x, corner_x_frac
        )
        self._serialize_managed_family_coord(
            record, s, "Corner", "Y", corner_y, corner_y_frac
        )

        # Write line properties - skip if default (0 = SMALLEST)
        # Altium's Library Splitter omits LineWidth=0.
        # If raw record omitted LineWidth but caller changed width to non-default
        # (e.g., clean transform setting SMALL), force emission so change persists.
        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )

        if self.record_type not in {
            SchRecordType.TEXT_FRAME,
            SchRecordType.NOTE,
            SchRecordType.COMPILE_MASK,
        } and (raw is None or self._line_style_dirty):
            s.remove_field(record, Fields.LINE_STYLE)
            s.remove_field(record, Fields.LINE_STYLE_EXT)
            if self.line_style != LineStyle.SOLID:
                s.write_int(
                    record,
                    Fields.LINE_STYLE_EXT,
                    self.line_style.value,
                    None,
                    force=True,
                )

        # Write boolean properties
        if self.record_type is not SchRecordType.COMPILE_MASK:
            self._serialize_managed_family_bool(
                record, s, Fields.IS_SOLID.canonical, self.is_solid
            )
        if self.record_type not in {
            SchRecordType.TEXT_FRAME,
            SchRecordType.NOTE,
            SchRecordType.COMPILE_MASK,
        }:
            self._serialize_managed_family_bool(
                record, s, Fields.TRANSPARENT.canonical, self.transparent
            )

        self._move_geometry_identity_to_end_if_needed(record)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Corner.X",
                "Corner.X_Frac",
                "Corner.Y",
                "Corner.Y_Frac",
                "LineStyleExt",
                "LineWidth",
                "Color",
                "AreaColor",
                "IsSolid",
                "Transparent",
                "UniqueID",
            ),
        )

    @property
    def line_style(self) -> LineStyle:
        return super().line_style

    @line_style.setter
    def line_style(self, value: LineStyle) -> None:
        self._set_line_style(value)

    @property
    def transparent(self) -> bool:
        return self._transparent

    @transparent.setter
    def transparent(self, value: bool) -> None:
        self._transparent = bool(value)
        self._transparent_dirty = True

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
            _geometry_item_length,
            make_rounded_rectangle_operation,
            make_pen,
            make_solid_brush,
            wrap_record_operations,
        )

        x1, y1 = ctx.transform_coord_precise(self.location)
        x2, y2 = ctx.transform_coord_precise(self.corner)
        svg_left = min(float(x1), float(x2))
        svg_right = max(float(x1), float(x2))
        svg_top = min(float(y1), float(y2))
        svg_bottom = max(float(y1), float(y2))
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
            else _geometry_item_length(
                stroke_width_mils * ctx.get_stroke_scale(),
                units_per_px=units_per_px,
            )
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
                make_rounded_rectangle_operation(
                    x1_px=svg_left,
                    y1_px=svg_top,
                    x2_px=svg_right,
                    y2_px=svg_bottom,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                    source_rotation=ctx.rotation,
                    brush=make_solid_brush(
                        fill_color_raw,
                        alpha=SEMI_TRANSPARENT_ALPHA if self.transparent else 0xFF,
                    ),
                )
            )

        operations.append(
            make_rounded_rectangle_operation(
                x1_px=svg_left,
                y1_px=svg_top,
                x2_px=svg_right,
                y2_px=svg_bottom,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
                source_rotation=ctx.rotation,
                pen=make_pen(
                    stroke_color_raw,
                    width=pen_width,
                    line_join="pljMiter",
                    dash_style=dash_style_map.get(self.line_style, "pdsSolid"),
                ),
            )
        )

        unique_id = str(self.unique_id or "")
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="rectangle",
            object_id="eRectangle",
            bounds=SchGeometryBounds(
                left=int(round((left - inflate) * 100000)),
                top=int(round((top + inflate) * 100000)),
                right=int(round((right + inflate) * 100000)),
                bottom=int(round((bottom - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
