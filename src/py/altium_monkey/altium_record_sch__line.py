"""Schematic record model for SchRecordType.LINE."""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    CoordPoint,
    LineStyle,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
    color_to_hex,
)
from ._sch_managed_defaults import GRAPHICAL_BORDER_COLOR
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    CornerMilsMixin,
    _LineStyleDirtyMixin,
    detect_case_mode_method_from_dotted_uppercase_fields,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SchSvgRenderContext,
    compute_dash_segments,
)


class AltiumSchLine(_LineStyleDirtyMixin, CornerMilsMixin, SchGraphicalObject):
    """
    Line segment record.

    Single straight line from location to corner.

    Public code should use ``location_mils`` and ``corner_mils`` for geometry
    updates, plus ``line_width`` and ``line_style`` for stroke properties.
    The raw coord fields are internal serializer-facing storage.

    Supports line styles (solid, dashed, dotted, dash-dot) via ``LineStyle``
    and ``LineStyleExt`` fields.
    """

    def __init__(self) -> None:
        super().__init__()
        if self.record_type is SchRecordType.LINE:
            self.color = GRAPHICAL_BORDER_COLOR
            self._apply_nonpersisted_area_color_default()
        self.corner = CoordPoint()
        self.line_width: LineWidth = LineWidth.SMALL
        self._line_style: LineStyle = LineStyle.SOLID
        self._line_style_dirty = False
        self._source_line_style: LineStyle = LineStyle.SOLID
        self.line_style_ext: int = 0  # Raw extended line style field from input records
        # Track which fields were present
        self._has_corner_x: bool = False
        self._has_corner_y: bool = False
        self._has_line_width: bool = False
        self._has_line_style: bool = False
        self._has_line_style_ext: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.LINE

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

        # Parse corner coordinates with presence tracking
        corner_x, corner_x_frac, self._has_corner_x = s.read_coord(
            record, "Corner", "X"
        )
        corner_y, corner_y_frac, self._has_corner_y = s.read_coord(
            record, "Corner", "Y"
        )
        self.corner = CoordPoint(corner_x, corner_y, corner_x_frac, corner_y_frac)

        # Parse line properties
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

        # Altium stores one logical line style, but older/newer ASCII layouts split it
        # across LineStyle and LineStyleExt. The extended field wins if it is larger.
        ignores_line_style = self.record_type is SchRecordType.BUS_ENTRY
        if ignores_line_style:
            line_style_val = 0
            line_style_ext_val = 0
            self._has_line_style = False
            self._has_line_style_ext = False
        else:
            line_style_val, self._has_line_style = s.read_int(
                record, Fields.LINE_STYLE, default=0
            )
            line_style_ext_val, self._has_line_style_ext = s.read_int(
                record,
                Fields.LINE_STYLE_EXT,
                default=0,
            )
        self.line_style_ext = line_style_ext_val

        if line_style_ext_val > line_style_val:
            self.line_style = LineStyle(line_style_ext_val)
        else:
            self.line_style = LineStyle(line_style_val)
        self._source_line_style = self.line_style
        self._line_style_dirty = False

        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()

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
        self._serialize_managed_family_coord(
            record, s, "Corner", "X", self.corner.x, self.corner.x_frac
        )
        self._serialize_managed_family_coord(
            record, s, "Corner", "Y", self.corner.y, self.corner.y_frac
        )

        # Write line properties
        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )

        if raw is None or self._line_style_dirty:
            self._remove_fields_case_insensitively(
                record,
                [Fields.LINE_STYLE.canonical, Fields.LINE_STYLE_EXT.canonical],
            )
            primary_line_style = (
                self.line_style.value
                if self.line_style.value < LineStyle.DASH_DOT.value
                else LineStyle.SOLID.value
            )
            if primary_line_style != LineStyle.SOLID.value:
                s.write_int(
                    record,
                    Fields.LINE_STYLE,
                    primary_line_style,
                    None,
                    force=True,
                )
            if self.line_style != LineStyle.SOLID:
                s.write_int(
                    record,
                    Fields.LINE_STYLE_EXT,
                    self.line_style.value,
                    None,
                    force=True,
                )

        if raw is None or self._area_color_dirty:
            self._remove_fields_case_insensitively(record, ["AreaColor", "AREACOLOR"])

        if self.record_type is not SchRecordType.LINE:
            return record
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
                "LineWidth",
                "LineStyle",
                "Color",
                "LineStyleExt",
                "UniqueID",
            ),
        )

    @property
    def line_style(self) -> LineStyle:
        return super().line_style

    @line_style.setter
    def line_style(self, value: LineStyle) -> None:
        self._set_line_style(value)

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this line.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        x1, y1 = ctx.transform_coord_precise(self.location)
        x2, y2 = ctx.transform_coord_precise(self.corner)

        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        dash_segments = compute_dash_segments(
            x1,
            y1,
            x2,
            y2,
            self.line_style,
            stroke_width_mils,
            self.line_width,
        )

        stroke = (
            color_to_hex(ctx.line_color_override)
            if ctx.line_color_override is not None
            else color_to_hex(self.color)
            if self.color is not None
            else ctx.default_stroke
        )
        stroke = ctx.apply_compile_mask_color(
            stroke, ctx.component_compile_masked is True
        )
        color_text = stroke.strip().lstrip("#")
        if len(color_text) == 6:
            color_raw = (
                int(color_text[0:2], 16)
                | (int(color_text[2:4], 16) << 8)
                | (int(color_text[4:6], 16) << 16)
            )
        else:
            color_raw = int(self.color) if self.color is not None else 0
        pen = make_pen(
            color_raw,
            width=0
            if self.line_width == LineWidth.SMALLEST
            else _geometry_item_length(
                stroke_width_mils * ctx.get_stroke_scale(),
                units_per_px=units_per_px,
            ),
        )
        operations = [
            SchGeometryOp.lines(
                [
                    svg_coord_to_geometry(
                        sx1,
                        sy1,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                    ),
                    svg_coord_to_geometry(
                        sx2,
                        sy2,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                    ),
                ],
                pen=pen,
            )
            for sx1, sy1, sx2, sy2 in dash_segments
        ]

        inflate = float(stroke_width_mils) + 2.0
        min_x = min(float(self.location.x), float(self.corner.x))
        max_x = max(float(self.location.x), float(self.corner.x))
        min_y = min(float(self.location.y), float(self.corner.y))
        max_y = max(float(self.location.y), float(self.corner.y))

        unique_id = cast(str, self.unique_id)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=unique_id,
            kind="line",
            object_id="eLine",
            bounds=SchGeometryBounds(
                left=int(round((min_x - inflate) * 100000)),
                top=int(round((max_y + inflate) * 100000)),
                right=int(round((max_x + inflate) * 100000)),
                bottom=int(round((min_y - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
