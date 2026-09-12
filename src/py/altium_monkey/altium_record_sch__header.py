"""Schematic FileHeader pseudo-record model."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager

from .altium_record_types import Primitive, SchRecordType
from .altium_serializer import AltiumSerializer


class AltiumSchHeader(Primitive):
    """Typed schematic stream preamble."""

    def __init__(self) -> None:
        super().__init__()
        self.header: str = ""
        self.weight: int = 0
        self.minor_version: int = 0
        self.unique_id: str = ""
        self._capture_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HEADER

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        del font_manager
        super().parse_from_record(record)
        serializer = AltiumSerializer()
        self.header, _ = serializer.read_str(record, "HEADER", default="")
        self.weight, _ = serializer.read_int(record, "Weight", default=0)
        self.minor_version, _ = serializer.read_int(record, "MinorVersion", default=0)
        self.unique_id, _ = serializer.read_str(record, "UniqueID", default="")
        self._capture_source_state()

    def _capture_source_state(self) -> None:
        self._source_header = self.header
        self._source_weight = self.weight
        self._source_minor_version = self.minor_version
        self._source_unique_id = self.unique_id

    def serialize_to_record(self) -> dict[str, Any]:
        record = self._get_base_record() if self._raw_record is not None else {}
        serializer = AltiumSerializer()
        raw = self._raw_record

        self._write_string(
            serializer,
            record,
            "HEADER",
            self.header,
            self._source_header,
            raw,
        )
        self._write_integer(
            serializer,
            record,
            "Weight",
            self.weight,
            self._source_weight,
            raw,
        )
        self._write_integer(
            serializer,
            record,
            "MinorVersion",
            self.minor_version,
            self._source_minor_version,
            raw,
        )
        self._write_string(
            serializer,
            record,
            "UniqueID",
            self.unique_id,
            self._source_unique_id,
            raw,
        )
        if raw is None:
            return self._managed_order(record)
        return record

    @staticmethod
    def _write_string(
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: str,
        value: str,
        source_value: str,
        raw: dict[str, object] | None,
    ) -> None:
        if value:
            serializer.write_str(
                record,
                field,
                value,
                raw,
                force=raw is None or value != source_value,
            )
        elif raw is None or value != source_value:
            serializer.remove_field(record, field)

    @staticmethod
    def _write_integer(
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: str,
        value: int,
        source_value: int,
        raw: dict[str, object] | None,
    ) -> None:
        if value != 0:
            serializer.write_int(
                record,
                field,
                value,
                raw,
                force=raw is None or value != source_value,
            )
        elif raw is None or value != source_value:
            serializer.remove_field(record, field)

    @staticmethod
    def _managed_order(record: dict[str, object]) -> dict[str, object]:
        order = ("RECORD", "HEADER", "Weight", "MinorVersion", "UniqueID")
        known = {name.lower(): name for name in order}
        values: dict[str, tuple[str, object]] = {}
        unknown: dict[str, object] = {}
        for key, value in record.items():
            canonical = known.get(key.lower())
            if canonical is None:
                unknown[key] = value
            else:
                values[canonical] = (key, value)
        result = dict(unknown)
        for name in order:
            if name in values:
                key, value = values[name]
                result[key] = value
        return result

    def __repr__(self) -> str:
        return (
            f"<AltiumSchHeader weight={self.weight} minor_version={self.minor_version}>"
        )
