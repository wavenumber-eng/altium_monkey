"""Typed ObjectDefinitions-stream record metadata."""

from __future__ import annotations

from uuid import uuid4
from .altium_record_types import Primitive, SchRecordType
from .altium_serializer import (
    AltiumSerializer,
    _remove_dynamic_string_field,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    detect_case_mode_method_from_uppercase_fields,
)


_DYNAMIC_FIELDS = (
    ("definition_id", "ObjectDefinitionId"),
    ("definition_hash", "ObjectDefinitionHash"),
    ("database_table_name", "DatabaseTableName"),
    ("design_item_id", "DesignItemId"),
    ("item_guid", "ItemGUID"),
    ("library_path", "LibraryPath"),
    ("lib_reference", "LibReference"),
    ("revision_guid", "RevisionGUID"),
    ("source_library_name", "SourceLibraryName"),
    ("target_filename", "TargetFileName"),
    ("vault_guid", "VaultGUID"),
)


class AltiumSchObjectDefinition(Primitive):
    """Metadata preceding an ObjectDefinitions child group."""

    def __init__(self) -> None:
        super().__init__()
        self.definition_id = str(uuid4())
        self.definition_hash = ""
        self.database_table_name = ""
        self.design_item_id = ""
        self.item_guid = ""
        self.library_path = ""
        self.lib_reference = ""
        self.revision_guid = ""
        self.source_library_name = ""
        self.target_filename = ""
        self.vault_guid = ""
        self.use_db_table_name = False
        self.use_library_name = False
        self.owner_index = -1
        self.is_not_accessible = False
        self.owner_index_additional_list = False
        self.index_in_sheet = -1
        self.ignore_on_load = False
        self.wiring_diagram_origin_unique_id = ""
        self.is_schematic_block_object = False
        self.unique_id_in_reuse_block = ""
        self._dynamic_sources: dict[str, tuple[str, bool, bool]] = {}
        self._capture_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.OBJECT_DEFINITION

    def parse_from_record(
        self, record: dict[str, object], font_manager: object | None = None
    ) -> None:
        del font_manager
        super().parse_from_record(record)
        serializer = AltiumSerializer(self._detect_case_mode())
        for attribute, field in _DYNAMIC_FIELDS:
            value, present, used_utf8 = read_dynamic_string_field(
                serializer, record, self._record, field, default=""
            )
            setattr(self, attribute, value)
            self._dynamic_sources[attribute] = (value, present, used_utf8)

        not_use_db, _ = serializer.read_bool(record, "NotUseDBTableName", default=False)
        not_use_library, _ = serializer.read_bool(
            record, "NotUseLibraryName", default=False
        )
        self.use_db_table_name = not not_use_db
        self.use_library_name = not not_use_library
        self.owner_index, _ = serializer.read_int(record, "OwnerIndex", default=0)
        self.is_not_accessible, _ = serializer.read_bool(
            record, "IsNotAccesible", default=False
        )
        self.owner_index_additional_list, _ = serializer.read_bool(
            record, "OwnerIndexAdditionalList", default=False
        )
        self.index_in_sheet, _ = serializer.read_int(record, "IndexInSheet", default=0)
        self.ignore_on_load, _ = serializer.read_bool(
            record, "IgnoreOnLoad", default=False
        )
        (
            self.wiring_diagram_origin_unique_id,
            wiring_present,
            wiring_used_utf8,
        ) = read_dynamic_string_field(
            serializer,
            record,
            self._record,
            "WiringDiagramOriginUniqueId",
            default="",
        )
        self._wiring_source = (
            self.wiring_diagram_origin_unique_id,
            wiring_present,
            wiring_used_utf8,
        )
        self.is_schematic_block_object, _ = serializer.read_bool(
            record, "IsSchematicBlockObject", default=False
        )
        self.unique_id_in_reuse_block, _ = serializer.read_str(
            record, "UniqueIDInReuseBlock", default=""
        )
        self._capture_source_state()

    def _capture_source_state(self) -> None:
        self._source_use_db_table_name = self.use_db_table_name
        self._source_use_library_name = self.use_library_name
        self._source_owner_index = self.owner_index
        self._source_is_not_accessible = self.is_not_accessible
        self._source_owner_index_additional_list = self.owner_index_additional_list
        self._source_index_in_sheet = self.index_in_sheet
        self._source_ignore_on_load = self.ignore_on_load
        if not hasattr(self, "_wiring_source"):
            self._wiring_source = (self.wiring_diagram_origin_unique_id, False, False)
        self._source_is_schematic_block_object = self.is_schematic_block_object
        self._source_unique_id_in_reuse_block = self.unique_id_in_reuse_block

    def serialize_to_record(self) -> dict[str, object]:
        record = self._get_base_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        self._write_dynamic_fields(serializer, record)
        self._write_inverted_flags(serializer, record)
        self._write_data_object_fields(serializer, record)
        self._remove_graphical_only_fields(serializer, record)
        if self._raw_record is None:
            return self._managed_order(record)
        return record

    def _write_dynamic_fields(
        self, serializer: AltiumSerializer, record: dict[str, object]
    ) -> None:
        for attribute, field in _DYNAMIC_FIELDS:
            value = str(getattr(self, attribute))
            source, present, used_utf8 = self._dynamic_sources.get(
                attribute, ("", False, False)
            )
            self._write_dynamic_field(
                serializer, record, field, value, source, present, used_utf8
            )

    def _write_inverted_flags(
        self, serializer: AltiumSerializer, record: dict[str, object]
    ) -> None:
        self._write_bool(
            serializer,
            record,
            "NotUseDBTableName",
            not self.use_db_table_name,
            not self._source_use_db_table_name,
        )
        self._write_bool(
            serializer,
            record,
            "NotUseLibraryName",
            not self.use_library_name,
            not self._source_use_library_name,
        )

    def _write_data_object_fields(
        self, serializer: AltiumSerializer, record: dict[str, object]
    ) -> None:
        self._write_int(
            serializer,
            record,
            "OwnerIndex",
            self.owner_index,
            self._source_owner_index,
        )
        self._write_bool(
            serializer,
            record,
            "IsNotAccesible",
            self.is_not_accessible,
            self._source_is_not_accessible,
        )
        self._write_bool(
            serializer,
            record,
            "OwnerIndexAdditionalList",
            self.owner_index_additional_list,
            self._source_owner_index_additional_list,
        )
        self._write_int(
            serializer,
            record,
            "IndexInSheet",
            self.index_in_sheet,
            self._source_index_in_sheet,
        )
        self._write_bool(
            serializer,
            record,
            "IgnoreOnLoad",
            self.ignore_on_load,
            self._source_ignore_on_load,
        )
        wiring_source, wiring_present, wiring_used_utf8 = self._wiring_source
        self._write_dynamic_field(
            serializer,
            record,
            "WiringDiagramOriginUniqueId",
            self.wiring_diagram_origin_unique_id,
            wiring_source,
            wiring_present,
            wiring_used_utf8,
        )
        self._write_bool(
            serializer,
            record,
            "IsSchematicBlockObject",
            self.is_schematic_block_object,
            self._source_is_schematic_block_object,
        )
        self._write_string(
            serializer,
            record,
            "UniqueIDInReuseBlock",
            self.unique_id_in_reuse_block,
            self._source_unique_id_in_reuse_block,
        )

    def _write_dynamic_field(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: str,
        value: str,
        source: str,
        was_present: bool,
        used_utf8: bool,
    ) -> None:
        changed = value != source
        if changed and not value:
            _remove_dynamic_string_field(record, field)
            return
        write_dynamic_string_field(
            serializer,
            record,
            field,
            value,
            raw_record=self._raw_record,
            used_utf8_sidecar=used_utf8,
            was_present=was_present,
            force=changed,
        )

    def _write_bool(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: str,
        value: bool,
        source: bool,
    ) -> None:
        if self._raw_record is not None and value == source:
            return
        if value:
            serializer.write_bool(record, field, value, self._raw_record, force=True)
        else:
            serializer.remove_field(record, field)

    @staticmethod
    def _remove_graphical_only_fields(
        serializer: AltiumSerializer, record: dict[str, object]
    ) -> None:
        for field in (
            "OwnerPartId",
            "OwnerPartDisplayMode",
            "SelectionMemory",
            "UnionIndex",
            "GraphicallyLocked",
            "UniqueID",
            "%UTF8%UniqueID",
        ):
            serializer.remove_field(record, field)

    def _write_int(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: str,
        value: int,
        source: int,
    ) -> None:
        if self._raw_record is not None and value == source:
            return
        if value != 0:
            serializer.write_int(record, field, value, self._raw_record, force=True)
        else:
            serializer.remove_field(record, field)

    def _write_string(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: str,
        value: str,
        source: str,
    ) -> None:
        if self._raw_record is not None and value == source:
            return
        if value:
            serializer.write_str(record, field, value, self._raw_record, force=True)
        else:
            serializer.remove_field(record, field)

    @staticmethod
    def _managed_order(record: dict[str, object]) -> dict[str, object]:
        family_order = tuple(field for _, field in _DYNAMIC_FIELDS[:-1]) + (
            "NotUseDBTableName",
            "NotUseLibraryName",
            "VaultGUID",
        )
        base_order = (
            "OwnerIndex",
            "IsNotAccesible",
            "OwnerIndexAdditionalList",
            "IndexInSheet",
            "IgnoreOnLoad",
            "WiringDiagramOriginUniqueId",
            "IsSchematicBlockObject",
            "UniqueIDInReuseBlock",
        )
        order = ("RECORD",) + family_order + base_order
        names = {name.lower(): name for name in order}
        values: dict[str, tuple[str, object]] = {}
        unknown: dict[str, object] = {}
        for key, value in record.items():
            canonical = names.get(key.lower())
            if canonical is None:
                unknown[key] = value
            else:
                values[canonical] = (key, value)
        result: dict[str, object] = {}
        for name in order:
            if name in values:
                key, value = values[name]
                result[key] = value
        result.update(unknown)
        return result

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields
