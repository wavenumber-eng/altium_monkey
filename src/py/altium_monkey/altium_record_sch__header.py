"""Schematic record model for SchRecordType.HEADER."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager

from .altium_record_types import SchGraphicalObject, SchRecordType
from .altium_serializer import AltiumSerializer, CaseMode, Fields
from .altium_sch_record_helpers import detect_case_mode_method_from_uppercase_fields


class AltiumSchHeader(SchGraphicalObject):
    """
    HEADER record.

    Document header containing sheet settings and font definitions.
    Typically the first record in a SchDoc file.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sheet_size: int = 0  # 0=A4, 1=A3, etc.
        self.sheet_orientation: int = 0  # 0=Landscape, 1=Portrait
        self.grid_size: int = 100000  # 10 mil default
        self.snap_grid_size: int = 100000
        self.visible_grid_size: int = 100000
        self.show_grid: bool = True
        self.snap_to_grid: bool = True
        self.use_custom_sheet: bool = False
        self.custom_x: int = 0
        self.custom_y: int = 0
        self.custom_x_zones: int = 0
        self.custom_y_zones: int = 0
        self.custom_margin_width: int = 0
        self.title_block_on: bool = False
        self.document_name: str = ""
        # Track field presence
        self._has_sheet_size: bool = False
        self._has_sheet_orientation: bool = False
        self._has_grid_size: bool = False
        self._has_snap_grid_size: bool = False
        self._has_visible_grid_size: bool = False
        self._has_show_grid: bool = False
        self._has_snap_to_grid: bool = False
        self._has_use_custom_sheet: bool = False
        self._has_custom_x: bool = False
        self._has_custom_y: bool = False
        self._has_custom_x_zones: bool = False
        self._has_custom_y_zones: bool = False
        self._has_custom_margin_width: bool = False
        self._has_title_block_on: bool = False
        self._has_document_name: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HEADER

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        s = AltiumSerializer()

        # Sheet settings
        self.sheet_size, self._has_sheet_size = s.read_int(
            record, Fields.SHEET_SIZE, default=0
        )
        self.sheet_orientation, self._has_sheet_orientation = s.read_int(
            record, Fields.SHEET_ORIENTATION, default=0
        )

        # Grid settings
        self.grid_size, self._has_grid_size = s.read_int(
            record, Fields.GRID_SIZE, default=100000
        )
        self.snap_grid_size, self._has_snap_grid_size = s.read_int(
            record, Fields.SNAP_GRID_SIZE, default=100000
        )
        self.visible_grid_size, self._has_visible_grid_size = s.read_int(
            record, Fields.VISIBLE_GRID_SIZE, default=100000
        )
        self.show_grid, self._has_show_grid = s.read_bool(
            record, Fields.SHOW_GRID, default=True
        )
        self.snap_to_grid, self._has_snap_to_grid = s.read_bool(
            record, Fields.SNAP_TO_GRID, default=True
        )

        # Custom sheet settings
        self.use_custom_sheet, self._has_use_custom_sheet = s.read_bool(
            record, Fields.USE_CUSTOM_SHEET, default=False
        )
        self.custom_x, self._has_custom_x = s.read_int(
            record, Fields.CUSTOM_X, default=0
        )
        self.custom_y, self._has_custom_y = s.read_int(
            record, Fields.CUSTOM_Y, default=0
        )
        self.custom_x_zones, self._has_custom_x_zones = s.read_int(
            record, Fields.CUSTOM_X_ZONES, default=0
        )
        self.custom_y_zones, self._has_custom_y_zones = s.read_int(
            record, Fields.CUSTOM_Y_ZONES, default=0
        )
        self.custom_margin_width, self._has_custom_margin_width = s.read_int(
            record, Fields.CUSTOM_MARGIN_WIDTH, default=0
        )
        self.title_block_on, self._has_title_block_on = s.read_bool(
            record, Fields.TITLE_BLOCK_ON, default=False
        )
        self.document_name, self._has_document_name = s.read_str(
            record, Fields.DOCUMENT_NAME, default=""
        )

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Sheet settings
        s.write_int(record, Fields.SHEET_SIZE, self.sheet_size, raw)
        s.write_int(record, Fields.SHEET_ORIENTATION, self.sheet_orientation, raw)

        # Grid settings
        s.write_int(record, Fields.GRID_SIZE, self.grid_size, raw)
        s.write_int(record, Fields.SNAP_GRID_SIZE, self.snap_grid_size, raw)
        s.write_int(record, Fields.VISIBLE_GRID_SIZE, self.visible_grid_size, raw)
        s.write_bool(record, Fields.SHOW_GRID, self.show_grid, raw)
        s.write_bool(record, Fields.SNAP_TO_GRID, self.snap_to_grid, raw)

        # Custom sheet settings (only if enabled)
        if self._has_use_custom_sheet or self.use_custom_sheet:
            s.write_bool(record, Fields.USE_CUSTOM_SHEET, self.use_custom_sheet, raw)
        if self.use_custom_sheet:
            s.write_int(record, Fields.CUSTOM_X, self.custom_x, raw)
            s.write_int(record, Fields.CUSTOM_Y, self.custom_y, raw)
            s.write_int(record, Fields.CUSTOM_X_ZONES, self.custom_x_zones, raw)
            s.write_int(record, Fields.CUSTOM_Y_ZONES, self.custom_y_zones, raw)
            s.write_int(
                record, Fields.CUSTOM_MARGIN_WIDTH, self.custom_margin_width, raw
            )
        if self._has_title_block_on or self.title_block_on:
            s.write_bool(record, Fields.TITLE_BLOCK_ON, self.title_block_on, raw)
        if self._has_document_name or self.document_name:
            s.write_str(record, Fields.DOCUMENT_NAME, self.document_name, raw)
        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def __repr__(self) -> str:
        return (
            f"<AltiumSchHeader size={self.sheet_size} orient={self.sheet_orientation}>"
        )
