"""Schematic record model for SchRecordType.PIECHART."""

import math
from typing import TYPE_CHECKING

from ._sch_managed_defaults import GRAPHICAL_BORDER_COLOR, GRAPHICAL_FILL_COLOR
from .altium_record_sch__arc import AltiumSchArc
from .altium_record_types import LineWidth, SchRecordType
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_svg_renderer import LINE_WIDTH_MILS, SchSvgRenderContext

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord


class AltiumSchPieChart(AltiumSchArc):
    """
    PIECHART record.

    A filled pie slice (arc with center filled).
    """

    def __init__(self) -> None:
        super().__init__()
        self.unique_id = None
        self.color = GRAPHICAL_BORDER_COLOR
        self.area_color = GRAPHICAL_FILL_COLOR
        self.start_angle = 30.0
        self.end_angle = 330.0
        self.line_width = LineWidth.SMALL
        self.is_solid = True
        self._has_is_solid = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PIECHART

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self.unique_id = None
        serializer = AltiumSerializer()
        if not self._has_end_angle:
            self.end_angle = 0.0
        self.is_solid, self._has_is_solid = serializer.read_bool(
            record, Fields.IS_SOLID, default=False
        )
        self._apply_imported_color_defaults(area_color=True)

    def serialize_to_record(self) -> dict[str, object]:
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        if (
            self._raw_record is not None
            and not self._has_end_angle
            and self.end_angle == 0.0
        ):
            serializer.remove_field(record, Fields.END_ANGLE)
        self._serialize_managed_family_color(
            record, serializer, Fields.AREA_COLOR.canonical, self.area_color or 0
        )
        source_solid, _ = serializer.read_bool(
            self._raw_record or {}, Fields.IS_SOLID, default=False
        )
        if (
            self._raw_record is not None
            and self._has_is_solid
            and self.is_solid == source_solid
        ):
            serializer.write_bool(
                record,
                Fields.IS_SOLID,
                self.is_solid,
                self._raw_record,
                force=True,
            )
        else:
            self._serialize_managed_family_bool(
                record, serializer, Fields.IS_SOLID.canonical, self.is_solid
            )
        self._remove_fields_case_insensitively(record, ["UniqueID", "%UTF8%UniqueID"])
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Radius",
                "Radius_Frac",
                "LineWidth",
                "StartAngle",
                "EndAngle",
                "Color",
                "AreaColor",
                "IsSolid",
            ),
        )

    def _generate_pie_points(
        self, center_x: int, center_y: int, radius: int
    ) -> list[tuple[int, int]]:
        """
        Generate the 101 points for a pie chart polygon.

        Based on PieDrawGraphObject.GetPiePoints():
        - 100 points around the arc from start_angle to end_angle
        - Point 101 is the center point

        Args:
            center_x, center_y: Center coordinates in internal schematic units.
            radius: Radius in internal schematic units.

        Returns:
            List of (x, y) tuples for the polygon points
        """
        from ._altium_record_sch__harness_layout import _float_to_i32, _unchecked_i32

        points: list[tuple[int, int]] = []

        end_angle = float(self.end_angle)
        while end_angle < float(self.start_angle):
            end_angle += 360.0
        angle_step = math.radians((end_angle - float(self.start_angle)) / 99.0)
        angle = math.radians(float(self.start_angle))

        for _index in range(100):
            offset_x = _float_to_i32(radius * math.cos(angle), rounded=True)
            offset_y = _float_to_i32(radius * math.sin(angle), rounded=True)
            points.append(
                (
                    _unchecked_i32(center_x + offset_x),
                    _unchecked_i32(center_y + offset_y),
                )
            )
            angle += angle_step

        points.append((center_x, center_y))

        return points

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build a geometry record for this pie chart.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        from ._altium_record_sch__harness_layout import _unchecked_i32

        center_x = _unchecked_i32(self.location.x * 100_000 + self.location.x_frac)
        center_y = _unchecked_i32(self.location.y * 100_000 + self.location.y_frac)
        radius = _unchecked_i32(self.radius * 100_000 + self.radius_frac)
        points = [
            ctx.transform_point(x / 100_000.0, y / 100_000.0)
            for x, y in self._generate_pie_points(center_x, center_y, radius)
        ]
        geometry_points = [
            svg_coord_to_geometry(
                x_px,
                y_px,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for x_px, y_px in points
        ]
        operations: list[SchGeometryOp] = []
        if self.is_solid:
            operations.append(
                SchGeometryOp.polygons(
                    [geometry_points],
                    brush=make_solid_brush(
                        int(self.area_color) if self.area_color is not None else 0
                    ),
                )
            )
        operations.append(
            SchGeometryOp.polygons(
                [geometry_points],
                pen=make_pen(
                    int(self.color) if self.color is not None else 0,
                    width=0
                    if self.line_width == LineWidth.SMALLEST
                    else _geometry_item_length(
                        LINE_WIDTH_MILS.get(self.line_width, 1.0)
                        * ctx.get_stroke_scale(),
                        units_per_px=units_per_px,
                    ),
                    line_join="pljRound",
                ),
            )
        )

        radius_mils = float(radius) / 100_000.0
        inflate = radius_mils + 2.0
        center_x_mils = float(self.location.x)
        center_y_mils = float(self.location.y)

        unique_id = str(self.unique_id or "")
        if not unique_id:
            record_index = int(getattr(self, "_record_index", 0) or 0)
            unique_id = f"PIE{record_index:05d}"

        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="pie",
            object_id="ePie",
            bounds=SchGeometryBounds(
                left=int(round((center_x_mils - inflate) * 100000)),
                top=int(round((center_y_mils + inflate) * 100000)),
                right=int(round((center_x_mils + inflate) * 100000)),
                bottom=int(round((center_y_mils - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<AltiumSchPieChart at=({self.location.x}, {self.location.y}) "
            f"radius={self.radius}>"
        )
