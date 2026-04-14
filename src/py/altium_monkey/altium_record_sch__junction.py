"""Schematic record model for SchRecordType.JUNCTION."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import SchGraphicalObject, SchRecordType
from .altium_serializer import AltiumSerializer


class AltiumSchJunction(SchGraphicalObject):
    """
    Wire junction record.
    
    Wire junction dots (indicate T-junction or 4-way connection).
    Very simple - just a dot at a location.
    """

    def __init__(self) -> None:
        super().__init__()
        self.size: int = 0
        self.locked: bool = True
        self._has_size: bool = False
        self._has_locked: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.JUNCTION

    def parse_from_record(
        self,
       record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)

        s = AltiumSerializer()
        self.size, self._has_size = s.read_int(record, 'Size', default=0)
        self.locked, self._has_locked = s.read_bool(record, 'Locked', default=True)

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        size_field = 'Size'
        locked_field = 'Locked'

        if self._raw_record is not None:
            if 'SIZE' in record:
                size_field = 'SIZE'
            elif 'LOCKED' in record:
                locked_field = 'LOCKED'
            elif ('Location.X' not in record) and ('Color' not in record):
                size_field = 'SIZE'
                locked_field = 'LOCKED'

        if self._has_size or ((self._raw_record is None) and (self.size != 0)):
            record[size_field] = str(self.size)

        if self._has_locked or ((self._raw_record is None) and (not self.locked)):
            record[locked_field] = 'T' if self.locked else 'F'

        return record


