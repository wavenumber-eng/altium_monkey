"""Schematic record model for SchRecordType.BUS_ENTRY."""

from typing import TYPE_CHECKING

from .altium_record_sch__line import AltiumSchLine
from .altium_record_types import SchRecordType

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext


class AltiumSchBusEntry(AltiumSchLine):
    """
    BUS_ENTRY record.
    
    Represents a bus entry point (wire-to-bus connection).
    Inherits all behavior from LINE.
    """

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.BUS_ENTRY

    def serialize_to_record(self) -> dict:
        record = super().serialize_to_record()
        record.pop('LineStyle', None)
        record.pop('LINESTYLE', None)
        record.pop('LineStyleExt', None)
        record.pop('LINESTYLEEXT', None)
        return record

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for a bus entry.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )
        from .altium_sch_svg_renderer import DEFAULT_LINE_WIDTH, LINE_WIDTH_MILS

        svg_start = tuple(float(v) for v in ctx.transform_coord_precise(self.location))
        svg_end = tuple(float(v) for v in ctx.transform_coord_precise(self.corner))
        geometry_points = [
            svg_coord_to_geometry(
                svg_start[0],
                svg_start[1],
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            ),
            svg_coord_to_geometry(
                svg_end[0],
                svg_end[1],
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            ),
        ]

        stroke_width_mils = float(LINE_WIDTH_MILS.get(self.line_width, DEFAULT_LINE_WIDTH))
        inflate = max(stroke_width_mils, 1.0)
        min_x = min(float(self.location.x), float(self.corner.x)) - inflate
        max_x = max(float(self.location.x), float(self.corner.x)) + inflate
        min_y = min(float(self.location.y), float(self.corner.y)) - inflate
        max_y = max(float(self.location.y), float(self.corner.y)) + inflate

        color_raw = int(self.color) if self.color is not None else 0
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="busentry",
            object_id="eBusEntry",
            bounds=SchGeometryBounds(
                left=int(round(min_x * 100000)),
                top=int(round(max_y * 100000)),
                right=int(round(max_x * 100000)),
                bottom=int(round(min_y * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                [
                    SchGeometryOp.lines(
                        geometry_points,
                        pen=make_pen(color_raw, width=int(round(stroke_width_mils * units_per_px))),
                    )
                ],
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<AltiumSchBusEntry from=({self.location.x}, {self.location.y}) "
            f"to=({self.corner.x}, {self.corner.y})>"
        )

