"""Private adapters for schematic high-level-code records."""

from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol
from .altium_record_types import SchRecordType


class _AltiumSchHighLevelCodeSymbol(AltiumSchSheetSymbol):
    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HIGH_LEVEL_CODE_SYMBOL
