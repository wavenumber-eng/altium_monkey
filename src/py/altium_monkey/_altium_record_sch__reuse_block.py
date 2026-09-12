"""Private adapter for schematic reuse-block implementation metadata."""

from .altium_record_types import SchGraphicalObject, SchRecordType


class _AltiumSchReuseBlockImplementationInfo(SchGraphicalObject):
    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.REUSE_BLOCK_IMPLEMENTATION_INFO
