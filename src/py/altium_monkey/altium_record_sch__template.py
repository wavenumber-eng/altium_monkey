"""Schematic record model for SchRecordType.TEMPLATE."""

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from ._sch_managed_defaults import GRAPHICAL_BORDER_COLOR, GRAPHICAL_FILL_COLOR
from .altium_record_types import SchGraphicalObject, SchPrimitive, SchRecordType
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import detect_case_mode_method_from_uppercase_fields


class AltiumSchTemplate(SchGraphicalObject):
    """
    TEMPLATE record.

    A reference to a schematic template file.
    Templates define standard drawing elements like borders and title blocks.
    """

    def __init__(self) -> None:
        super().__init__()
        self.unique_id = None
        self.filename: str = "*.dot"
        self.is_not_accessible = True
        self.color = GRAPHICAL_BORDER_COLOR
        self._area_color = GRAPHICAL_FILL_COLOR
        self._capture_graphical_source_state()
        self._capture_primitive_source_state()
        # Track field presence
        self._has_filename: bool = False
        self._source_filename: str = self.filename

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.TEMPLATE

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        SchPrimitive.parse_from_record(self, record, font_manager)
        self.unique_id = None
        self._apply_imported_graphical_metadata_defaults()
        s = AltiumSerializer()

        # Template filename
        self.filename, self._has_filename = s.read_str(
            record, Fields.FILENAME, default=""
        )
        self._source_filename = self.filename

    def serialize_to_record(self) -> dict[str, Any]:
        record = SchPrimitive.serialize_to_record(self)
        self._serialize_managed_string(
            record,
            Fields.FILENAME.pascal,
            [Fields.FILENAME.pascal, Fields.FILENAME.upper],
            self.filename,
            self._source_filename,
        )
        for key in tuple(record):
            if key.lower() in {"uniqueid", "%utf8%uniqueid"}:
                record.pop(key)
        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        child_records: list | None = None,
        unique_id_override: str | None = None,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry wrapper record for a rendered template.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            unwrap_record_operations,
            wrap_record_operations,
        )

        nested_records = [
            record for record in (child_records or []) if record is not None
        ]
        if not nested_records:
            return None

        unique_id = (
            unique_id_override
            or getattr(self, "unique_id", None)
            or f"TPL{int(getattr(self, '_record_index', 0) or 0):05d}"
        )
        operations: list[SchGeometryOp] = []
        bounds_left = math.inf
        bounds_top = -math.inf
        bounds_right = -math.inf
        bounds_bottom = math.inf

        for child_record in nested_records:
            child_unique_id = str(getattr(child_record, "unique_id", "") or "")
            if not child_unique_id:
                continue
            operations.append(SchGeometryOp.begin_group(child_unique_id))
            operations.extend(unwrap_record_operations(child_record))
            operations.append(SchGeometryOp.end_group())

            child_bounds = getattr(child_record, "bounds", None)
            if child_bounds is None:
                continue
            bounds_left = min(bounds_left, child_bounds.left)
            bounds_top = max(bounds_top, child_bounds.top)
            bounds_right = max(bounds_right, child_bounds.right)
            bounds_bottom = min(bounds_bottom, child_bounds.bottom)

        if not operations:
            return None

        bounds = SchGeometryBounds(
            left=0 if math.isinf(bounds_left) else int(bounds_left),
            top=0 if math.isinf(bounds_top) else int(bounds_top),
            right=0 if math.isinf(bounds_right) else int(bounds_right),
            bottom=0 if math.isinf(bounds_bottom) else int(bounds_bottom),
        )

        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=str(unique_id),
            kind="template",
            object_id="eTemplate",
            bounds=bounds,
            operations=wrap_record_operations(
                str(unique_id), operations, units_per_px=units_per_px
            ),
        )

    def __repr__(self) -> str:
        return f"<AltiumSchTemplate filename='{self.filename}'>"
