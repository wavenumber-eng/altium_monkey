"""Schematic record model for SchRecordType.SHEET_NAME."""

from typing import TYPE_CHECKING
from dataclasses import replace

from .altium_record_sch__label import AltiumSchLabel
from .altium_record_types import SchRecordType

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext


class AltiumSchSheetName(AltiumSchLabel):
    """
    SHEET_NAME record.
    
    Sheet name label on hierarchical sheet symbol.
    Inherits all behavior from LABEL.
    """

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.SHEET_NAME

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        record = super().to_geometry(ctx, document_id=document_id, units_per_px=units_per_px)
        if record is None:
            return None
        return replace(record, kind="sheetname", object_id="eSheetName")

    def __repr__(self) -> str:
        return f"<AltiumSchSheetName '{self.text}'>"

