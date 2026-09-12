"""Schematic record model for SchRecordType.JUNCTION."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager

from .altium_record_types import SchGraphicalObject, SchRecordType
from ._sch_managed_defaults import GRAPHICAL_FILL_COLOR, JUNCTION_COLOR
from .altium_serializer import AltiumSerializer, CaseMode


class AltiumSchJunction(SchGraphicalObject):
    """
    Wire junction record.

    Wire junction dots (indicate T-junction or 4-way connection).
    Very simple - just a dot at a location.
    """

    def __init__(self) -> None:
        super().__init__()
        self.color = JUNCTION_COLOR
        self.area_color = GRAPHICAL_FILL_COLOR
        self._init_family_dynamic_unique_id()
        self.size: int = 0
        self.locked: bool = True
        self._has_size: bool = False
        self._has_locked: bool = False
        self._source_size: int = self.size
        self._source_locked: bool = self.locked
        self._use_pascal_case = True
        self._capture_graphical_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.JUNCTION

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)

        self._use_pascal_case = any(
            key in record for key in ("Size", "Locked", "Color", "Location.X")
        )
        s = AltiumSerializer(
            CaseMode.PASCALCASE if self._use_pascal_case else CaseMode.UPPERCASE
        )
        self._parse_family_dynamic_unique_id(s, record)
        self.size, self._has_size = s.read_int(record, "Size", default=0)
        self.locked, self._has_locked = s.read_bool(record, "Locked", default=False)
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()
        self._source_size = self.size
        self._source_locked = self.locked

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        s = AltiumSerializer(
            CaseMode.PASCALCASE if self._use_pascal_case else CaseMode.UPPERCASE
        )
        self._serialize_managed_family_color(record, s, "Color", int(self.color or 0))
        self._serialize_managed_family_int(record, s, "Size", self.size)
        self._serialize_managed_family_bool(record, s, "Locked", self.locked)
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Size",
                "Color",
                "Locked",
                "UniqueID",
            ),
        )
