"""Schematic record model for SchRecordType.HARNESS_CONNECTOR."""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_record_sch__harness_entry import AltiumSchHarnessEntry
    from .altium_record_sch__harness_type import AltiumSchHarnessType
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import LineWidth, SchGraphicalObject, SchRecordType
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    bound_schematic_owner,
    detect_case_mode_method_from_uppercase_fields,
    remove_named_entry,
)


class SchHarnessConnectorSide(IntEnum):
    """
    Harness connector side placement.
    """

    LEFT = 0
    RIGHT = 1
    TOP = 2
    BOTTOM = 3


class AltiumSchHarnessConnector(SchGraphicalObject):
    """
    HARNESS_CONNECTOR record.

    Harness connector container for harness entries.
    Location is top-left corner, XSize/YSize define dimensions.
    """

    def __init__(self) -> None:
        super().__init__()
        # Dimensions from SchDataRectangularGroup base class
        self.xsize: int = 80  # Default width in mils
        self.ysize: int = 50  # Default height in mils
        # HarnessConnector-specific fields
        self.side: SchHarnessConnectorSide = SchHarnessConnectorSide.RIGHT
        self.primary_connection_position: int = 0
        self.line_width: LineWidth = LineWidth.SMALL
        # Children (entries and type label) - populated during hierarchy building
        self.children: list = []
        self.entries: list = []  # AltiumSchHarnessEntry objects
        self.type_label: AltiumSchHarnessType | None = None
        # Track field presence
        self._has_xsize: bool = False
        self._has_ysize: bool = False
        self._has_side: bool = False
        self._has_primary_connection_position: bool = False
        self._has_line_width: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_CONNECTOR

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading
        s = AltiumSerializer()

        # Parse XSize/YSize (from SchDataRectangularGroup)
        self.xsize, self._has_xsize = s.read_int(record, Fields.X_SIZE, default=80)
        self.ysize, self._has_ysize = s.read_int(record, Fields.Y_SIZE, default=50)
        side_val, self._has_side = s.read_int(
            record, Fields.HARNESS_CONNECTOR_SIDE, default=0
        )
        self.side = SchHarnessConnectorSide(side_val)

        self.primary_connection_position, self._has_primary_connection_position = (
            s.read_int(record, Fields.PRIMARY_CONNECTION_POSITION, default=0)
        )
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        if self._has_xsize or self.xsize != 0:
            s.write_int(record, Fields.X_SIZE, self.xsize, raw)
        else:
            s.remove_field(record, Fields.X_SIZE)
        if self._has_ysize or self.ysize != 0:
            s.write_int(record, Fields.Y_SIZE, self.ysize, raw)
        else:
            s.remove_field(record, Fields.Y_SIZE)
        if self._has_side or self.side != SchHarnessConnectorSide.LEFT:
            s.write_int(record, Fields.HARNESS_CONNECTOR_SIDE, self.side.value, raw)
        else:
            s.remove_field(record, Fields.HARNESS_CONNECTOR_SIDE)
        if (
            self._has_primary_connection_position
            or self.primary_connection_position != 0
        ):
            s.write_int(
                record,
                Fields.PRIMARY_CONNECTION_POSITION,
                self.primary_connection_position,
                raw,
            )
        else:
            s.remove_field(record, Fields.PRIMARY_CONNECTION_POSITION)
        if self._has_line_width or self.line_width != LineWidth.SMALLEST:
            s.write_int(record, Fields.LINE_WIDTH, self.line_width.value, raw)
        else:
            s.remove_field(record, Fields.LINE_WIDTH)

        # Connector is a root object - remove OWNERINDEX (only children have this)
        record.pop("OWNERINDEX", None)
        record.pop("OwnerIndex", None)
        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this harness connector body.
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

        x, y = ctx.transform_point(self.location.x, self.location.y)
        width = self.xsize * ctx.scale
        height = self.ysize * ctx.scale

        fill_color_raw = (
            int(self.area_color) if self.area_color is not None else 0x000000
        )
        border_color_raw = int(self.color) if self.color is not None else 0x000000
        line_width_map = {0: 0.5, 1: 1.0, 2: 3.0, 3: 5.0}
        border_width = line_width_map.get(self.line_width.value, 1.0)

        polygon_points = self._build_connector_polygon_points(
            x,
            y,
            width,
            height,
            10 * ctx.scale,
            7.5 * ctx.scale,
            height / 3,
        )
        geometry_polygon = [
            svg_coord_to_geometry(
                px,
                py,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for px, py in polygon_points
        ]

        operations = [
            SchGeometryOp.polygons(
                [geometry_polygon],
                brush=make_solid_brush(fill_color_raw, alpha=125),
            ),
            SchGeometryOp.polygons(
                [geometry_polygon],
                pen=make_pen(fill_color_raw),
            ),
        ]

        arc_radius = self._calculate_arc_radius() * ctx.scale
        r = float(arc_radius)
        brace_side = self._get_brace_side()
        pcp = self.primary_connection_position
        cy = y + height * (pcp / self.ysize if self.ysize > 0 else 0.5)
        pen = make_pen(
            border_color_raw,
            width=int(round(border_width * units_per_px)),
        )

        def add_arc(
            center_x: float, center_y: float, start_angle: float, end_angle: float
        ) -> None:
            geometry_center_x, geometry_center_y = svg_coord_to_geometry(
                center_x,
                center_y,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            operations.append(
                SchGeometryOp.arc(
                    center_x=geometry_center_x,
                    center_y=geometry_center_y,
                    width=2 * r * units_per_px,
                    height=2 * r * units_per_px,
                    start_angle=start_angle,
                    end_angle=end_angle,
                    pen=pen,
                )
            )

        def add_line(x1: float, y1: float, x2: float, y2: float) -> None:
            operations.append(
                SchGeometryOp.lines(
                    [
                        svg_coord_to_geometry(
                            x1,
                            y1,
                            sheet_height_px=float(ctx.sheet_height or 0.0),
                            units_per_px=units_per_px,
                        ),
                        svg_coord_to_geometry(
                            x2,
                            y2,
                            sheet_height_px=float(ctx.sheet_height or 0.0),
                            units_per_px=units_per_px,
                        ),
                    ],
                    pen=pen,
                )
            )

        if brace_side == "right":
            edge = x + width
            body_x = edge - 2 * r
            bhh = r
            add_arc(edge, cy + bhh, -90, -180)
            add_arc(edge, cy - bhh, -180, -270)
            add_arc(body_x, y + r, 0, -90)
            add_arc(body_x, y + height - r, -270, 0)
            add_line(edge - r, y + r, edge - r, cy - bhh)
            add_line(edge - r, cy + bhh, edge - r, y + height - r)
        elif brace_side == "left":
            edge = x
            body_x = edge + 2 * r
            bhh = r
            add_arc(edge, cy + bhh, 0, -90)
            add_arc(edge, cy - bhh, -270, 0)
            add_arc(body_x, y + r, -90, -180)
            add_arc(body_x, y + height - r, -180, -270)
            add_line(edge + r, y + r, edge + r, cy - bhh)
            add_line(edge + r, cy + bhh, edge + r, y + height - r)
        elif brace_side == "top":
            arc_cx = x + width * (pcp / self.xsize if self.xsize > 0 else 0.5)
            brace_hw = r
            edge = y
            body_y = edge + 2 * r
            add_arc(arc_cx - brace_hw, edge, -270, 0)
            add_arc(arc_cx + brace_hw, edge, -180, -270)
            add_arc(x + r, body_y, -90, -180)
            add_arc(x + width - r, body_y, 0, -90)
            add_line(x + r, edge + r, arc_cx - brace_hw, edge + r)
            add_line(arc_cx + brace_hw, edge + r, x + width - r, edge + r)
        else:
            arc_cx = x + width * (pcp / self.xsize if self.xsize > 0 else 0.5)
            brace_hw = r
            edge = y + height
            body_y = edge - 2 * r
            add_arc(arc_cx - brace_hw, edge, 0, -90)
            add_arc(arc_cx + brace_hw, edge, -90, -180)
            add_arc(x + r, body_y, -180, -270)
            add_arc(x + width - r, body_y, -270, 0)
            add_line(x + r, edge - r, arc_cx - brace_hw, edge - r)
            add_line(arc_cx + brace_hw, edge - r, x + width - r, edge - r)

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="harnessconnector",
            object_id="eHarnessConnector",
            bounds=SchGeometryBounds(
                left=int(round(float(self.location.x) * 100000)),
                top=int(round(float(self.location.y) * 100000)),
                right=int(
                    round((float(self.location.x) + float(self.xsize) + 15) * 100000)
                ),
                bottom=int(
                    round((float(self.location.y) - float(self.ysize)) * 100000)
                ),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _get_brace_side(self) -> str:
        """
        Determine which side the brace should be on based on entry positions.

        The brace is on the OPPOSITE side of the entries:
        - If entries have side=0 (LEFT): brace is on RIGHT
        - If entries have side=1 (RIGHT): brace is on LEFT

        For TOP/BOTTOM connectors, returns 'top' or 'bottom'.
        """
        if self.side in (SchHarnessConnectorSide.TOP, SchHarnessConnectorSide.BOTTOM):
            return "top" if self.side == SchHarnessConnectorSide.TOP else "bottom"

        # For LEFT/RIGHT connectors, check entry side
        # Default: brace matches connector side (entries on opposite)
        if self.entries:
            first_entry_side = self.entries[0].side
            # Entry side 0 = entries on LEFT, so brace on RIGHT
            # Entry side 1 = entries on RIGHT, so brace on LEFT
            return "right" if first_entry_side == 0 else "left"

        # Fallback: use connector side
        return "right" if self.side == SchHarnessConnectorSide.RIGHT else "left"

    def _calculate_arc_radius(self) -> float:
        """
        Calculate arc radius using native Altium algorithm.

        The radius is based on the minimum distance from the connection point
        to either side, divided by two and clamped to 10 mils.
        """
        is_vertical = self.side in (
            SchHarnessConnectorSide.TOP,
            SchHarnessConnectorSide.BOTTOM,
        )
        size = self.xsize if is_vertical else self.ysize
        num = min(
            self.primary_connection_position, size - self.primary_connection_position
        )
        # Native uses num/2, clamped to max 10 mils
        return min(num / 2, 10)

    def _build_connector_polygon_points(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        corner_radius: float,
        brace_depth: float,
        brace_half_height: float,
    ) -> list[tuple[float, float]]:
        """
        Build polygon points for connector shape with brace.

        Native Altium generates a 15-point polygon directly in screen coordinates.
        This implementation generates points directly in SVG coordinates.

        For RIGHT-side connector:
        - edge = x + width (right edge)
        - corner_x = edge - arc (where curves meet vertical line)
        - brace_center_y = y + pcp (vertical center of brace)
        - Polygon traces: left-top -> corner curves -> vertical -> brace -> vertical -> corner curves -> left-bottom
        """
        # Calculate arc radius using native algorithm
        arc = self._calculate_arc_radius()

        # Determine brace side
        brace_side = self._get_brace_side()

        if brace_side == "right":
            # RIGHT: Brace on right edge
            edge = x + width
            corner_x = edge - arc
            pcp = self.primary_connection_position
            brace_center_y = y + pcp

            return [
                # Top edge
                (x, y),  # Point 0: Far left, top
                (edge - 2 * arc, y),  # Point 1: Start of top-right corner
                # Top-right corner curve (3 points)
                (edge - 1.5 * arc, y + arc / 8),  # Point 2
                (edge - 1.125 * arc, y + arc / 2),  # Point 3
                (corner_x, y + arc),  # Point 4: End of corner, start vertical
                # Upper vertical and brace
                (corner_x, brace_center_y - 0.75 * arc),  # Point 5: Before upper brace
                (
                    corner_x + 0.25 * arc,
                    brace_center_y - arc / 3,
                ),  # Point 6: Brace curve
                (edge - 0.25 * arc, brace_center_y),  # Point 7: Brace tip
                (
                    corner_x + 0.25 * arc,
                    brace_center_y + arc / 3,
                ),  # Point 8: Brace curve
                (corner_x, brace_center_y + 0.75 * arc),  # Point 9: After lower brace
                # Lower vertical and bottom-right corner
                (corner_x, y + height - arc),  # Point 10: End vertical, start corner
                (edge - 1.125 * arc, y + height - arc / 2),  # Point 11: Corner curve
                (edge - 1.5 * arc, y + height - arc / 8),  # Point 12: Corner curve
                (edge - 2 * arc, y + height),  # Point 13: End corner
                # Bottom edge
                (x, y + height),  # Point 14: Far left, bottom
            ]

        elif brace_side == "left":
            # LEFT: Brace on left edge
            edge = x
            corner_x = edge + arc
            pcp = self.primary_connection_position
            brace_center_y = y + pcp

            return [
                # Top edge
                (x + width, y),  # Point 0: Far right, top
                (edge + 2 * arc, y),  # Point 1: Start of top-left corner
                # Top-left corner curve (3 points)
                (edge + 1.5 * arc, y + arc / 8),  # Point 2
                (edge + 1.125 * arc, y + arc / 2),  # Point 3
                (corner_x, y + arc),  # Point 4: End of corner, start vertical
                # Upper vertical and brace
                (corner_x, brace_center_y - 0.75 * arc),  # Point 5: Before upper brace
                (
                    corner_x - 0.25 * arc,
                    brace_center_y - arc / 3,
                ),  # Point 6: Brace curve
                (edge + 0.25 * arc, brace_center_y),  # Point 7: Brace tip
                (
                    corner_x - 0.25 * arc,
                    brace_center_y + arc / 3,
                ),  # Point 8: Brace curve
                (corner_x, brace_center_y + 0.75 * arc),  # Point 9: After lower brace
                # Lower vertical and bottom-left corner
                (corner_x, y + height - arc),  # Point 10: End vertical, start corner
                (edge + 1.125 * arc, y + height - arc / 2),  # Point 11: Corner curve
                (edge + 1.5 * arc, y + height - arc / 8),  # Point 12: Corner curve
                (edge + 2 * arc, y + height),  # Point 13: End corner
                # Bottom edge
                (x + width, y + height),  # Point 14: Far right, bottom
            ]

        elif brace_side == "top":
            # TOP: Brace on top edge (horizontal brace)
            edge = y
            corner_y = edge + arc
            pcp = self.primary_connection_position
            brace_center_x = x + pcp

            return [
                # Left edge
                (x, y + height),  # Point 0: Far left, bottom
                (x, edge + 2 * arc),  # Point 1: Start of top-left corner
                # Top-left corner curve (3 points)
                (x + arc / 8, edge + 1.5 * arc),  # Point 2
                (x + arc / 2, edge + 1.125 * arc),  # Point 3
                (x + arc, corner_y),  # Point 4: End of corner, start horizontal
                # Left horizontal and brace
                (brace_center_x - 0.75 * arc, corner_y),  # Point 5: Before left brace
                (
                    brace_center_x - arc / 3,
                    corner_y - 0.25 * arc,
                ),  # Point 6: Brace curve
                (brace_center_x, edge + 0.25 * arc),  # Point 7: Brace tip
                (
                    brace_center_x + arc / 3,
                    corner_y - 0.25 * arc,
                ),  # Point 8: Brace curve
                (brace_center_x + 0.75 * arc, corner_y),  # Point 9: After right brace
                # Right horizontal and top-right corner
                (x + width - arc, corner_y),  # Point 10: End horizontal, start corner
                (x + width - arc / 2, edge + 1.125 * arc),  # Point 11: Corner curve
                (x + width - arc / 8, edge + 1.5 * arc),  # Point 12: Corner curve
                (x + width, edge + 2 * arc),  # Point 13: End corner
                # Right edge
                (x + width, y + height),  # Point 14: Far right, bottom
            ]

        else:  # bottom
            # BOTTOM: Brace on bottom edge (horizontal brace)
            edge = y + height
            corner_y = edge - arc
            pcp = self.primary_connection_position
            brace_center_x = x + pcp

            return [
                # Left edge
                (x, y),  # Point 0: Far left, top
                (x, edge - 2 * arc),  # Point 1: Start of bottom-left corner
                # Bottom-left corner curve (3 points)
                (x + arc / 8, edge - 1.5 * arc),  # Point 2
                (x + arc / 2, edge - 1.125 * arc),  # Point 3
                (x + arc, corner_y),  # Point 4: End of corner, start horizontal
                # Left horizontal and brace
                (brace_center_x - 0.75 * arc, corner_y),  # Point 5: Before left brace
                (
                    brace_center_x - arc / 3,
                    corner_y + 0.25 * arc,
                ),  # Point 6: Brace curve
                (brace_center_x, edge - 0.25 * arc),  # Point 7: Brace tip
                (
                    brace_center_x + arc / 3,
                    corner_y + 0.25 * arc,
                ),  # Point 8: Brace curve
                (brace_center_x + 0.75 * arc, corner_y),  # Point 9: After right brace
                # Right horizontal and bottom-right corner
                (x + width - arc, corner_y),  # Point 10: End horizontal, start corner
                (x + width - arc / 2, edge - 1.125 * arc),  # Point 11: Corner curve
                (x + width - arc / 8, edge - 1.5 * arc),  # Point 12: Corner curve
                (x + width, edge - 2 * arc),  # Point 13: End corner
                # Right edge
                (x + width, y),  # Point 14: Far right, top
            ]

    def _build_brace_arcs(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        corner_radius: float,
        border_color: str,
        border_width: float,
    ) -> list[str]:
        """
        Build arc paths and lines for brace corners.

        Native Altium uses arc_radius = min(PCP, size-PCP) / 2 for the arc paths.
        The arc geometry matches the polygon corner curves.
        """
        elements = []

        # Calculate arc radius using same algorithm as polygon
        arc_radius = self._calculate_arc_radius()
        r = arc_radius  # Arc radius in mils

        # Brace center Y - uses primary_connection_position
        brace_y_ratio = (
            self.primary_connection_position / self.ysize if self.ysize > 0 else 0.5
        )
        cy = y + height * brace_y_ratio
        # Brace half-height equals arc_radius
        bhh = r

        # Determine brace side based on entry positions
        brace_side = self._get_brace_side()

        if brace_side == "right":
            edge = x + width  # Right edge of connector
            corner_x = edge - r  # Arc corner position

            # Four arcs for corners - native order: lower brace, upper brace, top corner, bottom corner
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{corner_x},{cy + bhh} A{r},{r} 0 0,1 {edge},{cy}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{edge},{cy} A{r},{r} 0 0,1 {corner_x},{cy - bhh}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{corner_x - r},{y} A{r},{r} 0 0,1 {corner_x},{y + r}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{corner_x},{y + height - r} A{r},{r} 0 0,1 {corner_x - r},{y + height}"/>'
            )
            # Connecting lines at corner_x
            elements.append(
                f'<line x1="{corner_x}" y1="{y + r}" x2="{corner_x}" y2="{cy - bhh}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )
            elements.append(
                f'<line x1="{corner_x}" y1="{cy + bhh}" x2="{corner_x}" y2="{y + height - r}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )

        elif brace_side == "left":
            # For LEFT: brace tip is at x (edge), corner is at x + r
            edge = x  # Left edge of connector body
            brace_tip_x = edge  # Brace tip is AT the edge
            corner_x = edge + r  # Corner is r units inside from edge

            # Native order: lower brace, upper brace, top corner, bottom corner
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{brace_tip_x},{cy} A{r},{r} 0 0,1 {corner_x},{cy + bhh}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{corner_x},{cy - bhh} A{r},{r} 0 0,1 {brace_tip_x},{cy}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{corner_x},{y + r} A{r},{r} 0 0,1 {corner_x + r},{y}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{corner_x + r},{y + height} A{r},{r} 0 0,1 {corner_x},{y + height - r}"/>'
            )
            elements.append(
                f'<line x1="{corner_x}" y1="{y + r}" x2="{corner_x}" y2="{cy - bhh}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )
            elements.append(
                f'<line x1="{corner_x}" y1="{cy + bhh}" x2="{corner_x}" y2="{y + height - r}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )

        elif brace_side == "top":
            # TOP uses same arc_radius but applied horizontally
            pcp_x_ratio = (
                self.primary_connection_position / self.xsize if self.xsize > 0 else 0.5
            )
            arc_cx = x + width * pcp_x_ratio  # Brace center X
            brace_hw = r  # Brace half-width equals arc radius
            edge = y  # Top edge
            corner_y = edge + r  # Arc corner position
            brace_tip_y = edge  # Brace tip at edge
            body_top = edge + 2 * r  # Body edge

            # Native order: right brace arc, left brace arc, left corner arc, right corner arc
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{arc_cx},{brace_tip_y} A{r},{r} 0 0,1 {arc_cx - brace_hw},{corner_y}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{arc_cx + brace_hw},{corner_y} A{r},{r} 0 0,1 {arc_cx},{brace_tip_y}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{x},{body_top} A{r},{r} 0 0,1 {x + r},{corner_y}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{x + width - r},{corner_y} A{r},{r} 0 0,1 {x + width},{body_top}"/>'
            )
            # Lines: from left corner to left brace, from right brace to right corner
            elements.append(
                f'<line x1="{x + r}" y1="{corner_y}" x2="{arc_cx - brace_hw}" y2="{corner_y}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )
            elements.append(
                f'<line x1="{arc_cx + brace_hw}" y1="{corner_y}" x2="{x + width - r}" y2="{corner_y}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )

        elif brace_side == "bottom":
            # BOTTOM uses same arc_radius but applied horizontally
            pcp_x_ratio = (
                self.primary_connection_position / self.xsize if self.xsize > 0 else 0.5
            )
            arc_cx = x + width * pcp_x_ratio  # Brace center X
            brace_hw = r  # Brace half-width equals arc radius
            edge = y + height  # Bottom edge
            corner_y = edge - r  # Arc corner position
            brace_tip_y = edge  # Brace tip at edge
            body_bottom = edge - 2 * r  # Body edge

            # Native order: left brace arc, right brace arc, left corner arc, right corner arc
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{arc_cx - brace_hw},{corner_y} A{r},{r} 0 0,1 {arc_cx},{brace_tip_y}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{arc_cx},{brace_tip_y} A{r},{r} 0 0,1 {arc_cx + brace_hw},{corner_y}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{x + r},{corner_y} A{r},{r} 0 0,1 {x},{body_bottom}"/>'
            )
            elements.append(
                f'<path stroke="{border_color}" stroke-width="{border_width}px"  d="M{x + width},{body_bottom} A{r},{r} 0 0,1 {x + width - r},{corner_y}"/>'
            )
            # Lines: from left corner to left brace, from right brace to right corner
            elements.append(
                f'<line x1="{x + r}" y1="{corner_y}" x2="{arc_cx - brace_hw}" y2="{corner_y}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )
            elements.append(
                f'<line x1="{arc_cx + brace_hw}" y1="{corner_y}" x2="{x + width - r}" y2="{corner_y}" stroke="{border_color}" stroke-width="{border_width}px"/>'
            )

        return elements

    def _bound_schematic_owner(self) -> object | None:
        return bound_schematic_owner(self)

    def _notify_owner_structure_changed(self) -> None:
        owner = self._bound_schematic_owner()
        if owner is None:
            return
        sync_hook = getattr(owner, "_sync_harness_connector_group_objects", None)
        if callable(sync_hook):
            sync_hook(self)

    @staticmethod
    def _normalized_entry_name(name: str) -> str:
        return str(name or "").strip().lower()

    def get_entry(self, name: str) -> AltiumSchHarnessEntry | None:
        """
        Return the first harness entry with the given display name.

        Lookup is case-insensitive. Missing names return ``None``.
        """
        normalized_name = self._normalized_entry_name(name)
        for entry in self.entries:
            if (
                self._normalized_entry_name(getattr(entry, "name", ""))
                == normalized_name
            ):
                return entry
        return None

    def add_entry(self, entry: AltiumSchHarnessEntry) -> None:
        """
        Attach a harness entry to this connector.
        """
        from .altium_record_sch__harness_entry import AltiumSchHarnessEntry

        if not isinstance(entry, AltiumSchHarnessEntry):
            raise TypeError("entry must be an AltiumSchHarnessEntry")
        if entry in self.entries:
            raise ValueError("entry is already attached to this harness connector")
        parent = getattr(entry, "parent", None)
        if parent is not None and parent is not self:
            raise ValueError(
                "entry is already attached to a different harness connector"
            )
        entry.parent = self
        self.entries.append(entry)
        self._notify_owner_structure_changed()

    def remove_entry(self, entry: AltiumSchHarnessEntry) -> bool:
        """
        Detach a harness entry from this connector.
        """
        if entry not in self.entries:
            return False
        self.entries.remove(entry)
        self._notify_owner_structure_changed()
        if getattr(entry, "parent", None) is self:
            entry.parent = None
        if hasattr(entry, "_bound_schematic_context"):
            entry._bound_schematic_context = None
        return True

    def remove_entry_by_name(self, name: str) -> bool:
        """
        Remove the first entry whose name matches ``name`` case-insensitively.
        """
        return remove_named_entry(self, name)

    def move_entry(
        self, entry_or_name: AltiumSchHarnessEntry | str, *, index: int
    ) -> None:
        """
        Reorder an existing entry within this connector.
        """
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("index must be an integer")
        if index < 0 or index >= len(self.entries):
            raise IndexError("index is out of range for harness entries")

        if isinstance(entry_or_name, str):
            entry = self.get_entry(entry_or_name)
            if entry is None:
                raise ValueError(f"No harness entry named {entry_or_name!r}")
        else:
            entry = entry_or_name
            if entry not in self.entries:
                raise ValueError("entry is not attached to this harness connector")

        current_index = self.entries.index(entry)
        if current_index == index:
            return
        self.entries.pop(current_index)
        self.entries.insert(index, entry)
        self._notify_owner_structure_changed()

    def set_type_label(self, type_label: AltiumSchHarnessType) -> None:
        """
        Attach or replace the harness type label for this connector.
        """
        from .altium_record_sch__harness_type import AltiumSchHarnessType

        if not isinstance(type_label, AltiumSchHarnessType):
            raise TypeError("type_label must be an AltiumSchHarnessType")
        parent = getattr(type_label, "parent", None)
        if parent is not None and parent is not self:
            raise ValueError(
                "type_label is already attached to a different harness connector"
            )

        existing = self.type_label
        if existing is type_label:
            return
        if existing is not None and existing in self.children:
            self.children.remove(existing)

        type_label.parent = self
        self.type_label = type_label
        if type_label not in self.children:
            self.children.append(type_label)
        self._notify_owner_structure_changed()
        if existing is not None:
            if hasattr(existing, "_bound_schematic_context"):
                existing._bound_schematic_context = None
            if getattr(existing, "parent", None) is self:
                existing.parent = None

    def clear_type_label(self) -> bool:
        """
        Remove the current harness type label when present.
        """
        if self.type_label is None:
            return False
        current_type = self.type_label
        if current_type in self.children:
            self.children.remove(current_type)
        self.type_label = None
        self._notify_owner_structure_changed()
        if getattr(current_type, "parent", None) is self:
            current_type.parent = None
        if hasattr(current_type, "_bound_schematic_context"):
            current_type._bound_schematic_context = None
        return True

    def __repr__(self) -> str:
        side_name = self.side.name.title()
        return f"<AltiumSchHarnessConnector side={side_name} size=({self.xsize}x{self.ysize})>"
