"""Schematic implementation-link record models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .altium_record_types import (
    MAX_INDEXED_ITEMS_PER_RECORD,
    SchGraphicalObject,
    SchPrimitive,
    SchRecordType,
)
from .altium_serializer import (
    AltiumSerializer,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import detect_case_mode_method_from_uppercase_fields

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryOp, SchGeometryRecord


MAX_IMPLEMENTATION_DATAFILES = 32_767


@dataclass(frozen=True)
class _DynamicSource:
    present: bool = False
    used_utf8: bool = False
    value: str = ""


def _remove_fields(record: dict[str, object], *names: str) -> None:
    normalized = {name.lower() for name in names}
    for key in tuple(record):
        if key.lower() in normalized:
            record.pop(key)


def _remove_identity(record: dict[str, object]) -> None:
    _remove_fields(record, "UniqueID", "%UTF8%UniqueID")


def _read_dynamic(
    serializer: AltiumSerializer,
    record: dict[str, object],
    view: Mapping[str, object],
    field: str,
) -> tuple[str, _DynamicSource]:
    value, present, used_utf8 = read_dynamic_string_field(
        serializer,
        record,
        view,
        field,
        default="",
    )
    return value, _DynamicSource(present, used_utf8, value)


def _write_dynamic(
    serializer: AltiumSerializer,
    record: dict[str, object],
    raw: dict[str, object] | None,
    field: str,
    value: str,
    source: _DynamicSource,
) -> None:
    if source.used_utf8 and raw is not None:
        for key, fallback in raw.items():
            if key.lower() == field.lower():
                record[key] = fallback
                break
    write_dynamic_string_field(
        serializer,
        record,
        field,
        value,
        raw_record=raw,
        used_utf8_sidecar=source.used_utf8,
        was_present=source.present,
        force=value != source.value,
    )


def _validate_indexed_count(field: str, value: int, limit: int) -> None:
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    if value > limit:
        raise ValueError(f"{field} exceeds the {limit} item limit")


def _model_datafile_index(field: str) -> int | None:
    normalized = field.lower().removeprefix("%utf8%")
    for prefix in (
        "modeldatafileentity",
        "modeldatafilekind",
        "modeldatafile",
    ):
        suffix = normalized.removeprefix(prefix)
        if suffix != normalized and suffix.isascii() and suffix.isdigit():
            return int(suffix)
    return None


def _desimp_index(field: str) -> int | None:
    normalized = field.lower()
    suffix = normalized.removeprefix("desimp")
    if suffix == normalized or not suffix.isascii() or not suffix.isdigit():
        return None
    return int(suffix)


def _plain_dynamic_field(field: str) -> str:
    return field.lower().removeprefix("%utf8%")


def _append_named_values(
    result: dict[str, object],
    values: list[tuple[str, object]],
    names: tuple[str, ...],
) -> None:
    for name in names:
        for key, value in values:
            if _plain_dynamic_field(key) == name:
                result[key] = value


def _append_model_datafile_values(
    result: dict[str, object], values: list[tuple[str, object]]
) -> None:
    for key, value in values:
        if _plain_dynamic_field(key).startswith("modeldatafile"):
            result[key] = value


class _ImplementationWrapper(SchGraphicalObject):
    """Shared serialization contract for synthetic implementation wrappers."""

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        if self.owner_part_id is None:
            self.owner_part_id = 0
        self.unique_id = None

    def serialize_to_record(self) -> dict[str, object]:
        record = super().serialize_to_record()
        if self.owner_part_id == 0:
            _remove_fields(record, "OwnerPartId")
        _remove_identity(record)
        return record


class AltiumSchImplementationList(_ImplementationWrapper):
    """Wrapper synthesized around component implementations."""

    def __init__(self) -> None:
        super().__init__()
        self.unique_id = None
        self.children: list[AltiumSchImplementation] = []

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IMPLEMENTATION_LIST

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        return super().parse_from_record(record, font_manager)

    def serialize_to_record(self) -> dict[str, object]:
        return super().serialize_to_record()

    def __repr__(self) -> str:
        return "<AltiumSchImplementationList>"


class AltiumSchImplementation(SchPrimitive):
    """Model or footprint implementation reference."""

    def __init__(self) -> None:
        super().__init__()
        self.owner_part_id = None
        self.description: str = ""
        self.use_component_library: bool = False
        self.model_name: str = ""
        self.model_type: str = ""
        self.model_vault_guid: str = ""
        self.model_item_guid: str = ""
        self.model_revision_guid: str = ""
        self.is_current: bool = False
        self.integrated_model: bool = False
        self.database_model: bool = False
        self.model_location: str = ""
        self.model_datafiles: list[tuple[str, str, str]] = []

        # Compatibility view of the first indexed link.
        self.datafile_count: int = 0
        self.datafile_entity: str = ""
        self.datafile_kind: str = ""

        self.map = AltiumSchMapDefinerList()
        self.map.parent = self
        self.children: list[SchPrimitive] = [self.map]

        self._has_model_name = False
        self._has_model_type = False
        self._has_description = False
        self._has_is_current = False
        self._has_datafile_count = False
        self._description_source = _DynamicSource()
        self._vault_source = _DynamicSource()
        self._item_source = _DynamicSource()
        self._revision_source = _DynamicSource()
        self._identity_source = _DynamicSource(value=str(self.unique_id or ""))
        self._location_source = _DynamicSource()
        self._datafile_sources: list[
            tuple[_DynamicSource, _DynamicSource, _DynamicSource]
        ] = []
        self._source_use_component_library = False
        self._source_model_name = ""
        self._source_model_type = ""
        self._source_is_current = False
        self._source_integrated_model = False
        self._source_database_model = False
        self._source_model_datafiles: tuple[tuple[str, str, str], ...] = ()
        self._source_legacy_datafile = (0, "", "")

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IMPLEMENTATION

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        view = self._record
        self.description, self._description_source = _read_dynamic(
            serializer, record, view, "Description"
        )
        self._has_description = self._description_source.present
        use_component_library, _ = serializer.read_bool(
            record, "UseComponentLibrary", default=False
        )
        datalinks_locked, _ = serializer.read_bool(
            record, "DatalinksLocked", default=False
        )
        database_locked, _ = serializer.read_bool(
            record, "DatabaseDatalinksLocked", default=False
        )
        self.use_component_library = (
            use_component_library or datalinks_locked or database_locked
        )
        self.model_name, self._has_model_name = serializer.read_str(
            record, "ModelName", default=""
        )
        self.model_type, self._has_model_type = serializer.read_str(
            record, "ModelType", default=""
        )
        self.is_current, self._has_is_current = serializer.read_bool(
            record, "IsCurrent", default=False
        )
        self.integrated_model, _ = serializer.read_bool(
            record, "IntegratedModel", default=False
        )
        self.database_model, _ = serializer.read_bool(
            record, "DatabaseModel", default=False
        )
        self.model_vault_guid, self._vault_source = _read_dynamic(
            serializer, record, view, "ModelVaultGUID"
        )
        self.model_item_guid, self._item_source = _read_dynamic(
            serializer, record, view, "ModelItemGUID"
        )
        self.model_revision_guid, self._revision_source = _read_dynamic(
            serializer, record, view, "ModelRevisionGUID"
        )
        unique_id, self._identity_source = _read_dynamic(
            serializer, record, view, "UniqueID"
        )
        self.unique_id = unique_id or None
        self.model_location, self._location_source = _read_dynamic(
            serializer, record, view, "ModelLocation"
        )
        self.model_datafiles = []
        self._datafile_sources = []
        if not self.model_location:
            self._read_datafiles(serializer, record, view)
        self._sync_legacy_datafile_view()
        self._capture_source_state()

    def _read_datafiles(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        view: Mapping[str, object],
    ) -> None:
        count, self._has_datafile_count = serializer.read_int(
            record, "DatafileCount", default=0
        )
        _validate_indexed_count("DatafileCount", count, MAX_IMPLEMENTATION_DATAFILES)
        for index in range(count):
            location, location_source = _read_dynamic(
                serializer, record, view, f"ModelDatafile{index}"
            )
            entity, entity_source = _read_dynamic(
                serializer, record, view, f"ModelDatafileEntity{index}"
            )
            kind, kind_source = _read_dynamic(
                serializer, record, view, f"ModelDatafileKind{index}"
            )
            self.model_datafiles.append((location, entity or self.model_name, kind))
            self._datafile_sources.append((location_source, entity_source, kind_source))

    def _sync_legacy_datafile_view(self) -> None:
        self.datafile_count = len(self.model_datafiles)
        if self.model_datafiles:
            _, self.datafile_entity, self.datafile_kind = self.model_datafiles[0]
        else:
            self.datafile_entity = ""
            self.datafile_kind = ""

    def _capture_source_state(self) -> None:
        self._source_use_component_library = self.use_component_library
        self._source_model_name = self.model_name
        self._source_model_type = self.model_type
        self._source_is_current = self.is_current
        self._source_integrated_model = self.integrated_model
        self._source_database_model = self.database_model
        self._source_model_datafiles = tuple(self.model_datafiles)
        self._source_legacy_datafile = (
            self.datafile_count,
            self.datafile_entity,
            self.datafile_kind,
        )

    def _effective_datafiles(self) -> list[tuple[str, str, str]]:
        legacy = (self.datafile_count, self.datafile_entity, self.datafile_kind)
        if legacy == self._source_legacy_datafile:
            return list(self.model_datafiles)
        _validate_indexed_count(
            "DatafileCount", self.datafile_count, MAX_IMPLEMENTATION_DATAFILES
        )
        result = list(self.model_datafiles[: self.datafile_count])
        while len(result) < self.datafile_count:
            result.append(("", self.model_name, ""))
        if result:
            location, _, _ = result[0]
            result[0] = (
                location,
                self.datafile_entity or self.model_name,
                self.datafile_kind,
            )
        return result

    def serialize_to_record(self) -> dict[str, object]:
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record
        _write_dynamic(
            serializer,
            record,
            raw,
            "Description",
            self.description,
            self._description_source,
        )
        self._write_library_flags(serializer, record, raw)
        self._write_ordinary_fields(serializer, record, raw)
        self._write_datafiles(serializer, record, raw)
        for field, value, source in (
            ("ModelVaultGUID", self.model_vault_guid, self._vault_source),
            ("ModelItemGUID", self.model_item_guid, self._item_source),
            ("ModelRevisionGUID", self.model_revision_guid, self._revision_source),
        ):
            _write_dynamic(serializer, record, raw, field, value, source)
        self._write_boolean(
            serializer,
            record,
            raw,
            "IsCurrent",
            self.is_current,
            self._source_is_current,
        )
        self._write_boolean(
            serializer,
            record,
            raw,
            "IntegratedModel",
            self.integrated_model,
            self._source_integrated_model,
        )
        self._write_boolean(
            serializer,
            record,
            raw,
            "DatabaseModel",
            self.database_model,
            self._source_database_model,
        )
        _write_dynamic(
            serializer,
            record,
            raw,
            "UniqueID",
            str(self.unique_id or ""),
            self._identity_source,
        )
        return self._authored_order(record) if raw is None else record

    def _write_library_flags(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        if (
            raw is not None
            and self.use_component_library == self._source_use_component_library
        ):
            return
        for field in (
            "UseComponentLibrary",
            "DatalinksLocked",
            "DatabaseDatalinksLocked",
        ):
            if self.use_component_library:
                serializer.write_bool(record, field, True, raw, force=True)
            else:
                serializer.remove_field(record, field)

    def _write_ordinary_fields(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        for field, value, present, source in (
            (
                "ModelName",
                self.model_name,
                self._has_model_name,
                self._source_model_name,
            ),
            (
                "ModelType",
                self.model_type,
                self._has_model_type,
                self._source_model_type,
            ),
        ):
            if present or value or value != source:
                serializer.write_str(record, field, value, raw, force=value != source)
            else:
                serializer.remove_field(record, field)

    def _write_datafiles(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        links = self._effective_datafiles()
        links_changed = tuple(links) != self._source_model_datafiles
        location_changed = self.model_location != self._location_source.value
        if (
            raw is not None
            and self.model_location
            and not links_changed
            and not location_changed
        ):
            return
        _remove_fields(record, "ModelLocation", "%UTF8%ModelLocation")
        self._write_datafile_count(serializer, record, raw, links, links_changed)
        self._write_datafile_values(serializer, record, raw, links)
        if links_changed or location_changed:
            self._remove_stale_datafiles(record, len(links))

    def _write_datafile_count(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
        links: list[tuple[str, str, str]],
        links_changed: bool,
    ) -> None:
        if links or self._has_datafile_count or links_changed:
            serializer.write_int(
                record, "DatafileCount", len(links), raw, force=links_changed
            )
        else:
            serializer.remove_field(record, "DatafileCount")

    def _write_datafile_values(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
        links: list[tuple[str, str, str]],
    ) -> None:
        for index, (location, entity, kind) in enumerate(links):
            sources = self._datafile_source(index)
            for suffix, value, source in (
                ("", location, sources[0]),
                ("Entity", entity, sources[1]),
                ("Kind", kind, sources[2]),
            ):
                _write_dynamic(
                    serializer,
                    record,
                    raw,
                    f"ModelDatafile{suffix}{index}",
                    value,
                    source,
                )

    def _datafile_source(
        self, index: int
    ) -> tuple[_DynamicSource, _DynamicSource, _DynamicSource]:
        if index < len(self._datafile_sources):
            return self._datafile_sources[index]
        return (_DynamicSource(), _DynamicSource(), _DynamicSource())

    @staticmethod
    def _remove_stale_datafiles(record: dict[str, object], first_stale: int) -> None:
        for key in tuple(record):
            index = _model_datafile_index(key)
            if index is not None and index >= first_stale:
                record.pop(key)

    @staticmethod
    def _write_boolean(
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
        field: str,
        value: bool,
        source: bool,
    ) -> None:
        if value:
            serializer.write_bool(record, field, True, raw, force=value != source)
        elif value != source:
            serializer.remove_field(record, field)

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    @staticmethod
    def _authored_order(record: dict[str, object]) -> dict[str, object]:
        before_links = (
            "description",
            "usecomponentlibrary",
            "modelname",
            "modeltype",
            "datafilecount",
            "modelvaultguid",
            "modelitemguid",
            "modelrevisionguid",
        )
        after_links = (
            "iscurrent",
            "datalinkslocked",
            "databasedatalinkslocked",
            "integratedmodel",
            "databasemodel",
            "uniqueid",
        )
        values = list(record.items())
        ordered_names = set(before_links + after_links)
        result = {
            key: value
            for key, value in values
            if _plain_dynamic_field(key) not in ordered_names
            and not _plain_dynamic_field(key).startswith("modeldatafile")
        }
        _append_named_values(result, values, before_links)
        _append_model_datafile_values(result, values)
        _append_named_values(result, values, after_links)
        return result

    def to_geometry(
        self,
        _ctx: object | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord | list[SchGeometryOp]":
        from .altium_sch_geometry_oracle import SchGeometryBounds, SchGeometryRecord

        result = SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="implementation",
            object_id="eImplementation",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=[],
            extras={"error": "export_to_screen_show_failed"},
        )
        return result if wrap_record else result.operations

    def __repr__(self) -> str:
        return f"<AltiumSchImplementation '{self.model_name}' type={self.model_type}>"


class AltiumSchMapDefinerList(SchPrimitive):
    """Compatibility name for the managed ImplementationMap."""

    def __init__(self) -> None:
        super().__init__()
        self.owner_part_id = None
        self.unique_id = None
        self.children: list[AltiumSchMapDefiner] = []

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.MAP_DEFINER_LIST

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self.unique_id = None

    def add_map_definer(self, child: "AltiumSchMapDefiner") -> bool:
        if not isinstance(child, AltiumSchMapDefiner):
            raise TypeError("child must be an AltiumSchMapDefiner")
        if self.find_map_definer(child.designator_interface) is not None:
            return False
        child.parent = self
        self.children.append(child)
        return True

    def remove_map_definer(self, child: "AltiumSchMapDefiner") -> bool:
        for index, candidate in enumerate(self.children):
            if candidate is child:
                self.children.pop(index)
                child.parent = None
                return True
        return False

    def find_map_definer(
        self, designator_interface: str
    ) -> "AltiumSchMapDefiner | None":
        normalized = designator_interface.lower()
        return next(
            (
                child
                for child in self.children
                if child.designator_interface.lower() == normalized
            ),
            None,
        )

    def serialize_to_record(self) -> dict[str, object]:
        record = super().serialize_to_record()
        _remove_identity(record)
        return record

    def __repr__(self) -> str:
        return "<AltiumSchMapDefinerList>"

    def to_geometry(
        self,
        _ctx: object | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord | list[SchGeometryOp]":
        from .altium_sch_geometry_oracle import SchGeometryBounds, SchGeometryRecord

        result = SchGeometryRecord(
            handle="",
            unique_id="",
            kind="implementationmap",
            object_id="eImplementationMap",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=[],
            extras={"error": "export_to_screen_show_failed"},
        )
        return result if wrap_record else result.operations


class AltiumSchMapDefiner(SchPrimitive):
    """Pin-designator mapping entry."""

    def __init__(self) -> None:
        super().__init__()
        self.owner_part_id = None
        self.unique_id = None
        self.designator_interface: str = ""
        self.implementation_designators: list[str] = []
        self._designator_source = _DynamicSource()
        self._source_implementation_designators: tuple[str, ...] = ()
        self._has_implementation_designators = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.MAP_DEFINER

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self.unique_id = None
        serializer = AltiumSerializer()
        self.designator_interface, self._designator_source = _read_dynamic(
            serializer, record, self._record, "DesIntf"
        )
        count, self._has_implementation_designators = serializer.read_int(
            record, "DesImpCount", default=0
        )
        _validate_indexed_count("DesImpCount", count, MAX_INDEXED_ITEMS_PER_RECORD)
        self.implementation_designators = [
            serializer.read_str(record, f"DesImp{index}", default="")[0]
            for index in range(count)
        ]
        self._source_implementation_designators = tuple(self.implementation_designators)

    def serialize_to_record(self) -> dict[str, object]:
        _validate_indexed_count(
            "DesImpCount",
            len(self.implementation_designators),
            MAX_INDEXED_ITEMS_PER_RECORD,
        )
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record
        self._write_design_interface(serializer, record, raw)
        self._write_design_implementations(serializer, record, raw)
        _remove_identity(record)
        return record

    def _write_design_interface(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        if raw is None or self.designator_interface != self._designator_source.value:
            _remove_fields(record, "%UTF8%DesIntf")
            if self.designator_interface:
                serializer.write_str(
                    record,
                    "DesIntf",
                    self.designator_interface,
                    raw,
                    force=True,
                )
            else:
                serializer.remove_field(record, "DesIntf")

    def _write_design_implementations(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        changed = tuple(self.implementation_designators) != (
            self._source_implementation_designators
        )
        if (
            self.implementation_designators
            or self._has_implementation_designators
            or changed
        ):
            serializer.write_int(
                record,
                "DesImpCount",
                len(self.implementation_designators),
                raw,
                force=changed,
            )
        else:
            serializer.remove_field(record, "DesImpCount")
        for index, value in enumerate(self.implementation_designators):
            serializer.write_str(record, f"DesImp{index}", value, raw, force=changed)
        if changed:
            self._remove_stale_design_implementations(record)

    def _remove_stale_design_implementations(self, record: dict[str, object]) -> None:
        first_stale = len(self.implementation_designators)
        for key in tuple(record):
            index = _desimp_index(key)
            if index is not None and index >= first_stale:
                record.pop(key)

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def __repr__(self) -> str:
        return "<AltiumSchMapDefiner>"


class AltiumSchImplParams(_ImplementationWrapper):
    """Compatibility name for the managed ParameterList wrapper."""

    def __init__(self) -> None:
        super().__init__()
        self.unique_id = None

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IMPL_PARAMS

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        return super().parse_from_record(record, font_manager)

    def serialize_to_record(self) -> dict[str, object]:
        return super().serialize_to_record()

    def __repr__(self) -> str:
        return "<AltiumSchImplParams>"
