"""Schematic record models for implementation-related record types."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryOp, SchGeometryRecord

from .altium_record_types import SchGraphicalObject, SchRecordType
from .altium_serializer import AltiumSerializer, CaseMode, Fields
from .altium_sch_record_helpers import detect_case_mode_method_from_uppercase_fields


def _case_insensitive_record_get(
    record: dict[str, Any], key: str, default: str = ""
) -> str:
    """
    Case-insensitive dict get for schematic implementation records.
    """
    for record_key, value in record.items():
        if record_key.lower() == key.lower():
            return str(value)
    return default


class AltiumSchImplementationList(SchGraphicalObject):
    """
    IMPLEMENTATION_LIST record.

    Container for implementation records (footprint/model references).
    Typically a child of COMPONENT records.

    Attributes:
        children: List of AltiumSchImplementation records owned by this list.
                  Populated by SchDoc._build_implementation_hierarchy().
    """

    def __init__(self) -> None:
        super().__init__()
        self.children: list = []  # AltiumSchImplementation records

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IMPLEMENTATION_LIST

    def serialize_to_record(self) -> dict[str, Any]:
        return super().serialize_to_record()

    def __repr__(self) -> str:
        return "<AltiumSchImplementationList>"


class AltiumSchImplementation(SchGraphicalObject):
    """
    IMPLEMENTATION record.

    Individual implementation reference (footprint, simulation model, etc.).

    DATAFILE FIELDS:
        Altium stores library file references as indexed fields:
        - DatafileCount: Number of library file references (usually 1)
        - ModelDatafileEntity0: Library name without extension (e.g., "100_1_4")
        - ModelDatafileKind0: Library type (e.g., "PCBLib")
        These tell Altium which library file contains the model, resolved
        against project documents, installed libraries, and search paths.
    """

    def __init__(self) -> None:
        super().__init__()
        self.children: list = []
        self.children: list = []
        self.model_name: str = ""
        self.model_type: str = ""
        self.description: str = ""
        self.is_current: bool = False
        self.datafile_count: int = 0
        self.datafile_entity: str = ""  # ModelDatafileEntity0
        self.datafile_kind: str = ""  # ModelDatafileKind0
        # Track field presence
        self._has_model_name: bool = False
        self._has_model_type: bool = False
        self._has_description: bool = False
        self._has_is_current: bool = False
        self._has_datafile_count: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IMPLEMENTATION

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        s = AltiumSerializer()

        self.model_name, self._has_model_name = s.read_str(
            record, Fields.MODEL_NAME, default=""
        )
        self.model_type, self._has_model_type = s.read_str(
            record, Fields.MODEL_TYPE, default=""
        )
        self.description, self._has_description = s.read_str(
            record, Fields.DESCRIPTION, default=""
        )
        self.is_current, self._has_is_current = s.read_bool(
            record, Fields.IS_CURRENT, default=False
        )

        # Datafile fields (indexed, handled directly)
        dc_str = _case_insensitive_record_get(record, "DatafileCount", "")
        if dc_str:
            self.datafile_count = int(dc_str)
            self._has_datafile_count = True
        self.datafile_entity = _case_insensitive_record_get(
            record, "ModelDatafileEntity0", ""
        )
        self.datafile_kind = _case_insensitive_record_get(
            record, "ModelDatafileKind0", ""
        )

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        if self._has_model_name or self.model_name:
            s.write_str(record, Fields.MODEL_NAME, self.model_name, raw)
        if self._has_model_type or self.model_type:
            s.write_str(record, Fields.MODEL_TYPE, self.model_type, raw)
        if self._has_description or self.description:
            s.write_str(record, Fields.DESCRIPTION, self.description, raw)
        if self._has_is_current or self.is_current:
            s.write_bool(record, Fields.IS_CURRENT, self.is_current, raw)

        # Datafile fields
        if self._has_datafile_count or self.datafile_count > 0:
            record["DatafileCount"] = str(self.datafile_count)
        if self.datafile_entity:
            record["ModelDatafileEntity0"] = self.datafile_entity
        if self.datafile_kind:
            record["ModelDatafileKind0"] = self.datafile_kind

        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def to_geometry(
        self,
        _ctx: object | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> "SchGeometryRecord | list[SchGeometryOp]":
        from .altium_sch_geometry_oracle import SchGeometryBounds, SchGeometryRecord

        record = SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="implementation",
            object_id="eImplementation",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=[],
            extras={"error": "export_to_screen_show_failed"},
        )
        if wrap_record:
            return record
        return record.operations

    def __repr__(self) -> str:
        return f"<AltiumSchImplementation '{self.model_name}' type={self.model_type}>"


class AltiumSchMapDefinerList(SchGraphicalObject):
    """
    MAP_DEFINER_LIST record.

    Container for pin mapping definitions.
    """

    def __init__(self) -> None:
        super().__init__()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.MAP_DEFINER_LIST

    def serialize_to_record(self) -> dict[str, Any]:
        return super().serialize_to_record()

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

        record = SchGeometryRecord(
            handle="",
            unique_id="",
            kind="implementationmap",
            object_id="eImplementationMap",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=[],
            extras={"error": "export_to_screen_show_failed"},
        )
        if wrap_record:
            return record
        return record.operations


class AltiumSchMapDefiner(SchGraphicalObject):
    """
    MAP_DEFINER record.

    Individual pin mapping definition.
    """

    def __init__(self) -> None:
        super().__init__()
        self.designator_interface: str = ""
        self.implementation_designators: list[str] = []
        self._has_designator_interface: bool = False
        self._has_implementation_designators: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.MAP_DEFINER

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self.designator_interface = _case_insensitive_record_get(record, "DesIntf", "")
        self._has_designator_interface = ("DesIntf" in self._record) or (
            "DESINTF" in self._record
        )

        self.implementation_designators = []
        count_raw = _case_insensitive_record_get(record, "DesImpCount", "")
        if count_raw:
            self._has_implementation_designators = True
            for index in range(int(count_raw)):
                self.implementation_designators.append(
                    _case_insensitive_record_get(record, f"DesImp{index}", "")
                )
        else:
            self._has_implementation_designators = False

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        if self._has_designator_interface or self.designator_interface:
            self._update_field(
                record, "DESINTF", self.designator_interface, ["DesIntf", "DESINTF"]
            )
        else:
            self._remove_field(record, ["DesIntf", "DESINTF"])

        if self._has_implementation_designators or self.implementation_designators:
            self._update_field(
                record,
                "DESIMPCOUNT",
                len(self.implementation_designators),
                ["DesImpCount", "DESIMPCOUNT"],
            )

            for index, value in enumerate(self.implementation_designators):
                self._update_field(
                    record,
                    f"DESIMP{index}",
                    value,
                    [f"DesImp{index}", f"DESIMP{index}"],
                )

            index = len(self.implementation_designators)
            while (f"DesImp{index}" in record) or (f"DESIMP{index}" in record):
                self._remove_field(record, [f"DesImp{index}", f"DESIMP{index}"])
                index += 1
        else:
            self._remove_field(record, ["DesImpCount", "DESIMPCOUNT"])
            index = 0
            while (f"DesImp{index}" in record) or (f"DESIMP{index}" in record):
                self._remove_field(record, [f"DesImp{index}", f"DESIMP{index}"])
                index += 1

        return record

    def __repr__(self) -> str:
        return "<AltiumSchMapDefiner>"


class AltiumSchImplParams(SchGraphicalObject):
    """
    IMPL_PARAMS record.

    Implementation parameters (parameter list for implementations).
    """

    def __init__(self) -> None:
        super().__init__()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IMPL_PARAMS

    def serialize_to_record(self) -> dict[str, Any]:
        return super().serialize_to_record()

    def __repr__(self) -> str:
        return "<AltiumSchImplParams>"
