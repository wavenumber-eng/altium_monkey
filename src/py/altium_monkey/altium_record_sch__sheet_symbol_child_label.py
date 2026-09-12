"""Shared schematic model for sheet-symbol child labels."""

from typing import TYPE_CHECKING

from .altium_record_sch__label import AltiumSchLabel
from .altium_sch_record_helpers import validate_record_enum_value
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)

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
        self.text = "*"
        self.auto_position: bool = True
        self.text_horz_anchor: int = 0
        self.text_vert_anchor: int = 0
        self._has_is_hidden: bool = False
        self._has_not_auto_position: bool = False
        self._has_text_horz_anchor: bool = False
        self._has_text_vert_anchor: bool = False
        self._source_auto_position: bool = self.auto_position
        self._source_text_horz_anchor: int = self.text_horz_anchor
        self._source_text_vert_anchor: int = self.text_vert_anchor
        self._has_unique_id: bool = False
        self._used_utf8_unique_id: bool = False
        self._source_unique_id: str = str(self.unique_id or "")
        self._capture_label_source_state()

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager=font_manager)
        serializer = AltiumSerializer()
        self.is_hidden, self._has_is_hidden = serializer.read_bool(
            record, Fields.IS_HIDDEN, default=False
        )
        not_auto_position, self._has_not_auto_position = serializer.read_bool(
            record, "NotAutoPosition", default=False
        )
        self.auto_position = not not_auto_position
        self.text_horz_anchor, self._has_text_horz_anchor = serializer.read_int(
            record, "TextHorzAnchor", default=0
        )
        validate_record_enum_value("TextHorzAnchor", self.text_horz_anchor, 3)
        self.text_vert_anchor, self._has_text_vert_anchor = serializer.read_int(
            record, "TextVertAnchor", default=0
        )
        validate_record_enum_value("TextVertAnchor", self.text_vert_anchor, 3)
        unique_id, self._has_unique_id, self._used_utf8_unique_id = (
            read_dynamic_string_field(
                serializer,
                record,
                self._record,
                "UniqueID",
                default="",
            )
        )
        self.unique_id = unique_id or None
        self.url = ""
        self._source_auto_position = self.auto_position
        self._source_text_horz_anchor = self.text_horz_anchor
        self._source_text_vert_anchor = self.text_vert_anchor
        self._source_unique_id = str(self.unique_id or "")

    def serialize_to_record(self) -> dict[str, object]:
        validate_record_enum_value("TextHorzAnchor", self.text_horz_anchor, 3)
        validate_record_enum_value("TextVertAnchor", self.text_vert_anchor, 3)
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record
        self._write_visibility_and_position(serializer, record, raw)
        self._write_dynamic_identity(serializer, record, raw)
        self._remove_unsupported_url(record)
        if raw is None:
            return self._authored_managed_order(record)
        return record

    def _write_visibility_and_position(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        self._serialize_managed_family_bool(
            record, serializer, Fields.IS_HIDDEN.canonical, self.is_hidden
        )
        self._serialize_managed_family_bool(
            record, serializer, "NotAutoPosition", not self.auto_position
        )
        self._serialize_managed_family_int(
            record, serializer, "TextHorzAnchor", self.text_horz_anchor
        )
        self._serialize_managed_family_int(
            record, serializer, "TextVertAnchor", self.text_vert_anchor
        )

    def _write_dynamic_identity(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        unique_id = str(self.unique_id or "")
        if self._used_utf8_unique_id and raw is not None:
            for key, value in raw.items():
                if key.lower() == "uniqueid":
                    record[key] = value
                    break
        write_dynamic_string_field(
            serializer,
            record,
            "UniqueID",
            unique_id,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_unique_id,
            was_present=self._has_unique_id,
            force=unique_id != self._source_unique_id,
        )

    @staticmethod
    def _remove_unsupported_url(record: dict[str, object]) -> None:
        for key in tuple(record):
            if key.lower() in {"url", "%utf8%url"}:
                record.pop(key)

    @staticmethod
    def _authored_managed_order(record: dict[str, object]) -> dict[str, object]:
        family_order = (
            "RECORD",
            "OwnerIndex",
            "IsNotAccesible",
            "OwnerIndexAdditionalList",
            "IndexInSheet",
            "IgnoreOnLoad",
            "WiringDiagramOriginUniqueId",
            "IsSchematicBlockObject",
            "UniqueIDInReuseBlock",
            "OwnerPartId",
            "OwnerPartDisplayMode",
            "SelectionMemory",
            "UnionIndex",
            "GraphicallyLocked",
            "Location.X",
            "Location.X_Frac",
            "Location.Y",
            "Location.Y_Frac",
            "Orientation",
            "Justification",
            "Color",
            "FontID",
            "IsHidden",
            "Text",
            "%UTF8%Text",
            "IsMirrored",
            "NotAutoPosition",
            "TextHorzAnchor",
            "TextVertAnchor",
            "UniqueID",
            "%UTF8%UniqueID",
        )
        normalized_order = {name.lower(): name for name in family_order}
        family_values: dict[str, tuple[str, object]] = {}
        result: dict[str, object] = {}
        for key, value in record.items():
            normalized = str(key).lower()
            family_name = normalized_order.get(normalized)
            if family_name is None:
                result[key] = value
            else:
                family_values[family_name] = (key, value)
        for family_name in family_order:
            if family_name in family_values:
                key, value = family_values[family_name]
                result[key] = value
        return result
