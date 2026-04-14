"""Schematic record model for SchRecordType.BUS."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_sch__wire import AltiumSchWire
from .altium_record_types import SchRecordType


class AltiumSchBus(AltiumSchWire):
    """
    BUS record.
    
    Represents a bus (multi-signal) connection.
    Inherits all behavior from WIRE.
    """

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.BUS


    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for a bus path.
        """
        return super().to_geometry(
            ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            kind="bus",
            object_id="eBus",
            default_color_raw=0x000000,
            stroke_width_mils_override=3.0,
            junction_color_raw=0x800000,
            junction_size_px=6.0,
        )

    def __repr__(self) -> str:
        legacy_vertices = getattr(self, 'vertices', None)
        vertex_count = len(legacy_vertices) if legacy_vertices is not None else len(self.points)
        return f"<AltiumSchBus vertices={vertex_count}>"

