"""Schematic record model for SchRecordType.PIECHART."""

import math
from typing import TYPE_CHECKING, cast

from .altium_record_sch__arc import AltiumSchArc
from .altium_record_types import LineWidth, SchRecordType, color_to_hex
from .altium_sch_svg_renderer import LINE_WIDTH_MILS, SchSvgRenderContext, svg_polygon

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryRecord


class AltiumSchPieChart(AltiumSchArc):
    """
    PIECHART record.
    
    A filled pie slice (arc with center filled).
    """

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PIECHART


    def _generate_pie_points(self, cx: float, cy: float, r: float) -> list[tuple[float, float]]:
        """
        Generate the 101 points for a pie chart polygon.
        
        Based on PieDrawGraphObject.GetPiePoints():
        - 100 points around the arc from start_angle to end_angle
        - Point 101 is the center point
        
        Args:
            cx, cy: Center coordinates (SVG, already transformed)
            r: Radius (already scaled)
        
        Returns:
            List of (x, y) tuples for the polygon points
        """
        num_arc_points = 100
        points = []

        # Handle wrap-around (e.g., start=270, end=90 should go through 360)
        angle_diff = self.end_angle - self.start_angle
        if angle_diff < 0:
            angle_diff += 360

        # Generate arc points
        for i in range(num_arc_points):
            # Interpolate angle from start to end
            t = i / (num_arc_points - 1) if num_arc_points > 1 else 0
            angle_deg = self.start_angle + t * angle_diff
            angle_rad = math.radians(angle_deg)

            # Calculate point on arc
            # Note: SVG Y is inverted, so we negate the sin component
            x = cx + r * math.cos(angle_rad)
            y = cy - r * math.sin(angle_rad)
            points.append((x, y))

        # Add center point as the last point
        points.append((cx, cy))

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
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        cx, cy = ctx.transform_coord_precise(self.location)
        cx, cy = round(cx, 3), round(cy, 3)
        radius = cast(int, self.radius)
        radius_px = round(radius * ctx.scale, 3)
        points = self._generate_pie_points(cx, cy, radius_px)
        geometry_points = [
            svg_coord_to_geometry(
                x_px,
                y_px,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for x_px, y_px in points
        ]
        operations = [
            SchGeometryOp.polygons(
                [geometry_points],
                pen=make_pen(
                    int(self.color) if self.color is not None else 0,
                    width=0 if self.line_width == LineWidth.SMALLEST else int(round(LINE_WIDTH_MILS.get(self.line_width, 1.0) * units_per_px)),
                    line_join="pljRound",
                ),
            )
        ]

        inflate = float(radius) + 2.0
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

