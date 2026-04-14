"""Schematic record model for SchRecordType.HYPERLINK."""

from .altium_record_sch__label import AltiumSchLabel
from .altium_record_types import SchRecordType


class AltiumSchHyperlink(AltiumSchLabel):
    """
    HYPERLINK record.
    
    Hyperlink annotation with clickable text.
    Inherits all behavior from LABEL.
    """

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HYPERLINK

    def __repr__(self) -> str:
        return f"<AltiumSchHyperlink '{self.text}'>"
