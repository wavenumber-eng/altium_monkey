"""Schematic record model for SchRecordType.HARNESS_TYPE."""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_sch__label import AltiumSchLabel
from .altium_record_types import SchRecordType
from .altium_serializer import AltiumSerializer, CaseMode, Fields
from .altium_sch_record_helpers import detect_case_mode_method_from_uppercase_fields


class AltiumSchHarnessType(AltiumSchLabel):
    """
    HARNESS_TYPE record.

    Harness connector type label.
    Inherits all behavior from LABEL.
    Uses OwnerIndexAdditionalList=T for file-order hierarchy.
    """

    def __init__(self) -> None:
        super().__init__()
        # Hierarchy flag - indicates this is a child of the preceding object
        self.owner_index_additional_list: bool = True
        # NotAutoPosition - label position is manually set
        self.not_auto_position: bool = True
        # Index in sheet for harness type (typically -1)
        self.index_in_sheet: int | None = -1
        # Track field presence
        self._has_index_in_sheet: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_TYPE

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse harness type from record.

                Args:
                   record: Source record dictionary
                    font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading
        s = AltiumSerializer()

        owner_additional, _ = s.read_bool(
            record, Fields.OWNER_INDEX_ADDITIONAL_LIST, default=False
        )
        self.owner_index_additional_list = owner_additional
        self.not_auto_position, _ = s.read_bool(
            record, Fields.NOT_AUTO_POSITION, default=False
        )

        # Parse IndexInSheet if present
        index_val, self._has_index_in_sheet = s.read_int(
            record, Fields.INDEX_IN_SHEET, default=-1
        )
        if self._has_index_in_sheet:
            self.index_in_sheet = index_val

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Hierarchy flag - must be present for Altium to attach type to connector
        if self.owner_index_additional_list:
            s.write_bool(record, Fields.OWNER_INDEX_ADDITIONAL_LIST, True, raw)

        # Handle OwnerIndex for harness type objects
        # Parent class (AltiumSchLabel) removes OWNERINDEX in synthesis mode for SchLib,
        # but harness types in SchDoc NEED OwnerIndex for second+ connector groups.
        #
        # Logic:
        # - owner_index == 0: First group, use file order hierarchy (no OWNERINDEX needed)
        # - owner_index > 0: Second+ group, MUST have OWNERINDEX pointing to parent connector
        # Always remove any existing OWNERINDEX first to avoid duplicates
        record.pop("OWNERINDEX", None)
        record.pop("OwnerIndex", None)
        owner_index = cast(int, self.owner_index)
        if owner_index > 0:
            s.write_int(record, Fields.OWNER_INDEX, owner_index, raw)

        # Handle IndexInSheet (typically -1 for harness types)
        # Always remove both cases first to avoid duplicates
        record.pop("INDEXINSHEET", None)
        record.pop("IndexInSheet", None)
        if self.index_in_sheet is not None:
            s.write_int(record, Fields.INDEX_IN_SHEET, self.index_in_sheet, raw)
        if self.not_auto_position:
            s.write_bool(record, Fields.NOT_AUTO_POSITION, True, raw)
        # Remove COLOR if 0 (not present in original harness_example.SchDoc)
        if record.get("COLOR") == "0" or record.get("Color") == "0":
            record.pop("COLOR", None)
            record.pop("Color", None)
        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for a harness connector type label.
        """
        from dataclasses import replace

        geometry_record = super().to_geometry(
            ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        if geometry_record is None:
            return None
        return replace(
            geometry_record,
            kind="harnessconnectortype",
            object_id="eHarnessConnectorType",
        )

    def __repr__(self) -> str:
        return f"<AltiumSchHarnessType '{self.text}'>"
