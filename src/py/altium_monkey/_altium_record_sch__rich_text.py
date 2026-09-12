"""Private raw-lossless adapters for rich-text schematic records."""

from .altium_record_types import SchGraphicalObject, SchRecordType


class _AltiumSchRichTextDocument(SchGraphicalObject):
    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.RICH_TEXT_DOCUMENT


class _AltiumSchRtfLink(SchGraphicalObject):
    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.RTF_LINK
