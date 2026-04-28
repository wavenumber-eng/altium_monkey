"""Shared schematic model for sheet-symbol child labels."""

from typing import TYPE_CHECKING

from .altium_record_sch__label import AltiumSchLabel
from .altium_serializer import AltiumSerializer, Fields

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager


class AltiumSchSheetSymbolChildLabel(AltiumSchLabel):
    """
    Common behavior for SHEET_NAME and FILE_NAME child labels.

    Unlike standalone LABEL records, these children persist IsHidden in Altium
    records and need to preserve explicit true and false values.
    """

    def __init__(self) -> None:
        super().__init__()
        self._has_is_hidden: bool = False

    def parse_from_record(
        self,
        record: dict,
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager=font_manager)
        serializer = AltiumSerializer()
        self.is_hidden, self._has_is_hidden = serializer.read_bool(
            record, Fields.IS_HIDDEN, default=False
        )

    def serialize_to_record(self) -> dict:
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record
        if self._has_is_hidden or self.is_hidden:
            serializer.write_bool(
                record,
                Fields.IS_HIDDEN,
                self.is_hidden,
                raw,
                force=self.is_hidden,
            )
        else:
            serializer.remove_field(record, Fields.IS_HIDDEN)
        return record
