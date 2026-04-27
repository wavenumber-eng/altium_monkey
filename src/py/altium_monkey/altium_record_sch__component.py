"""Schematic record model for SchRecordType.COMPONENT."""

import copy

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_schdoc import AltiumSchDoc
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_common_enums import ComponentKind
from .altium_component_kind import parse_component_kind
from .altium_sch_enums import PinElectrical, PinItemMode, Rotation90
from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchRectMils,
    SchRecordType,
    TextOrientation,
)
from .altium_serializer import AltiumSerializer, CaseMode, Fields
from .altium_sch_record_helpers import bound_schematic_owner


class AltiumSchComponent(SchGraphicalObject):
    """
    COMPONENT record.

    Represents a placed symbol instance in a schematic.
    Acts as a container for pins, primitives, and parameters.
    """

    def __init__(self) -> None:
        super().__init__()

        # Library reference
        self.lib_reference: str = "*"
        self.library_path: str = "*"
        self.source_library_name: str = "*"
        self.component_description: str = ""
        self.utf8_component_description: str = ""  # UTF-8 encoded description variant

        # Multi-part symbol support
        self.part_count: int = 1
        self.current_part_id: int = 1
        self.display_mode: int = 0
        self.display_mode_count: int = 1

        # Orientation and mirroring
        self.orientation: Rotation90 = Rotation90.DEG_0
        self.is_mirrored: bool = False

        # Visibility
        self.show_hidden_pins: bool = False
        self.show_hidden_fields: bool = False
        self.display_field_names: bool = False

        # Locking
        self.designator_locked: bool = False
        self.part_id_locked: bool = True
        self.pins_moveable: bool = False

        # Colors - Local Colors override (when override_colors=True)
        # Fills = area_color, Lines = color, Pins = pin_color
        self.override_colors: bool = False
        self.color: int = 0x000000  # Lines color (Win32 BGR)
        self.area_color: int = 0xFFFFFF  # Fills color (Win32 BGR)
        self.pin_color: int = 0x000000  # Pins color (Win32 BGR)

        # Component classification
        self.component_kind: ComponentKind = ComponentKind.STANDARD
        self.component_kind_version2: int | None = None

        # Database and design item references
        self.database_table_name: str = ""
        self.use_db_table_name: bool = True
        self.use_library_name: bool = True
        self.design_item_id: str = ""

        # File references
        self.sheet_part_filename: str = "*"
        self.target_filename: str = "*"

        # GUIDs for vault/revision tracking
        self.vault_guid: str = ""
        self.item_guid: str = ""
        self.revision_guid: str = ""
        self.symbol_vault_guid: str = ""
        self.symbol_item_guid: str = ""
        self.symbol_revision_guid: str = ""

        # Pin count
        self.all_pin_count: int = 0

        # Footprint reference
        self.footprint: str = ""

        # Children (populated during parsing)
        self.pins: list = []
        self.parameters: list = []
        self.graphics: list = []  # Graphical primitives (rectangles, lines, etc.)
        self.children: list = []  # Direct child records in schematic file order

        # Presence tracking
        self._has_lib_reference: bool = False
        self._has_library_path: bool = False
        self._has_source_library_name: bool = False
        self._has_component_description: bool = False
        self._has_part_count: bool = False
        self._has_current_part_id: bool = False
        self._has_display_mode: bool = False
        self._has_display_mode_count: bool = False
        self._has_orientation: bool = False
        self._has_is_mirrored: bool = False
        self._has_show_hidden_pins: bool = False
        self._has_show_hidden_fields: bool = False
        self._has_display_field_names: bool = False
        self._has_designator_locked: bool = False
        self._has_part_id_locked: bool = False
        self._has_pins_moveable: bool = False
        self._has_override_colors: bool = False
        self._has_color: bool = False
        self._has_area_color: bool = False
        self._has_pin_color: bool = False
        self._has_component_kind: bool = False
        self._has_database_table_name: bool = False
        self._has_use_db_table_name: bool = False
        self._has_use_library_name: bool = False
        self._has_design_item_id: bool = False
        self._has_sheet_part_filename: bool = False
        self._has_target_filename: bool = False
        self._has_vault_guid: bool = False
        self._has_item_guid: bool = False
        self._has_revision_guid: bool = False
        self._has_symbol_vault_guid: bool = False
        self._has_symbol_item_guid: bool = False
        self._has_symbol_revision_guid: bool = False
        self._has_all_pin_count: bool = False
        self._has_footprint: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.COMPONENT

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        s = AltiumSerializer()

        # Library reference
        self.lib_reference, self._has_lib_reference = s.read_str(
            record, Fields.LIB_REFERENCE, default="*"
        )
        self.library_path, self._has_library_path = s.read_str(
            record, Fields.LIBRARY_PATH, default="*"
        )
        self.source_library_name, self._has_source_library_name = s.read_str(
            record, Fields.SOURCE_LIBRARY_NAME, default="*"
        )
        self.component_description, self._has_component_description = s.read_str(
            record, Fields.COMPONENT_DESCRIPTION, default=""
        )
        # UTF8 variant is accessed directly (special prefix)
        self.utf8_component_description = record.get("%UTF8%ComponentDescription", "")

        # Multi-part
        self.part_count, self._has_part_count = s.read_int(
            record, Fields.PART_COUNT, default=1
        )
        self.current_part_id, self._has_current_part_id = s.read_int(
            record,
            Fields.CURRENT_PART_ID,
            default=1,
        )
        if not self._has_current_part_id:
            self.current_part_id, self._has_current_part_id = s.read_int(
                record,
                "CurrentPartID",
                default=1,
            )
        self.display_mode, self._has_display_mode = s.read_int(
            record, Fields.DISPLAY_MODE, default=0
        )
        self.display_mode_count, self._has_display_mode_count = s.read_int(
            record, Fields.DISPLAY_MODE_COUNT, default=1
        )

        # Orientation
        orient_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = Rotation90(orient_val)
        self.is_mirrored, self._has_is_mirrored = s.read_bool(
            record, Fields.IS_MIRRORED, default=False
        )

        # Visibility
        self.show_hidden_pins, self._has_show_hidden_pins = s.read_bool(
            record, Fields.SHOW_HIDDEN_PINS, default=False
        )
        self.show_hidden_fields, self._has_show_hidden_fields = s.read_bool(
            record, Fields.SHOW_HIDDEN_FIELDS, default=False
        )
        self.display_field_names, self._has_display_field_names = s.read_bool(
            record, Fields.DISPLAY_FIELD_NAMES, default=False
        )

        # Locking
        self.designator_locked, self._has_designator_locked = s.read_bool(
            record, Fields.DESIGNATOR_LOCKED, default=False
        )
        self.part_id_locked, self._has_part_id_locked = s.read_bool(
            record, Fields.PART_ID_LOCKED, default=True
        )
        self.pins_moveable, self._has_pins_moveable = s.read_bool(
            record, Fields.PINS_MOVEABLE, default=False
        )

        # Colors - Local Colors override
        self.override_colors, self._has_override_colors = s.read_bool(
            record, Fields.OVERRIDE_COLORS, default=False
        )
        self.color, self._has_color = s.read_int(record, Fields.COLOR, default=0)
        self.area_color, self._has_area_color = s.read_int(
            record, Fields.AREA_COLOR, default=0xFFFFFF
        )
        self.pin_color, self._has_pin_color = s.read_int(
            record, Fields.PIN_COLOR, default=0
        )

        # Component kind - use shared versioned-field parsing helper
        # Check if any version field is present (check both case variants)
        self._has_component_kind = (
            Fields.COMPONENT_KIND.pascal in record
            or Fields.COMPONENT_KIND.upper in record
            or "COMPONENTKINDVERSION2" in record
            or "COMPONENTKINDVERSION3" in record
            or "ComponentKindVersion2" in record
            or "ComponentKindVersion3" in record
        )
        self.component_kind = parse_component_kind(record)

        # Preserve raw ComponentKindVersion2 value for round-trip
        ckv2 = record.get("ComponentKindVersion2", record.get("COMPONENTKINDVERSION2"))
        self.component_kind_version2 = int(ckv2) if ckv2 is not None else None

        # Database references
        self.database_table_name, self._has_database_table_name = s.read_str(
            record, Fields.DATABASE_TABLE_NAME, default=""
        )
        not_use_db_table_name, has_not_use_db_table_name = s.read_bool(
            record,
            "NotUseDBTableName",
            default=False,
        )
        if has_not_use_db_table_name:
            self.use_db_table_name = not not_use_db_table_name
            self._has_use_db_table_name = True
        else:
            self.use_db_table_name, self._has_use_db_table_name = s.read_bool(
                record,
                Fields.USE_DB_TABLE_NAME,
                default=True,
            )
        not_use_library_name, has_not_use_library_name = s.read_bool(
            record,
            "NotUseLibraryName",
            default=False,
        )
        if has_not_use_library_name:
            self.use_library_name = not not_use_library_name
            self._has_use_library_name = True
        else:
            self.use_library_name, self._has_use_library_name = s.read_bool(
                record,
                Fields.USE_LIBRARY_NAME,
                default=True,
            )
        self.design_item_id, self._has_design_item_id = s.read_str(
            record, Fields.DESIGN_ITEM_ID, default=""
        )

        # File references
        self.sheet_part_filename, self._has_sheet_part_filename = s.read_str(
            record,
            Fields.SHEET_PART_FILENAME,
            default="*",
        )
        if not self._has_sheet_part_filename:
            self.sheet_part_filename, self._has_sheet_part_filename = s.read_str(
                record,
                "SheetPartFilename",
                default="*",
            )
        self.target_filename, self._has_target_filename = s.read_str(
            record,
            Fields.TARGET_FILENAME,
            default="*",
        )
        if not self._has_target_filename:
            self.target_filename, self._has_target_filename = s.read_str(
                record,
                "TargetFilename",
                default="*",
            )
        if self.target_filename == "":
            self.target_filename = "*"

        # GUIDs
        self.vault_guid, self._has_vault_guid = s.read_str(
            record, Fields.VAULT_GUID, default=""
        )
        self.item_guid, self._has_item_guid = s.read_str(
            record, Fields.ITEM_GUID, default=""
        )
        self.revision_guid, self._has_revision_guid = s.read_str(
            record, Fields.REVISION_GUID, default=""
        )
        self.symbol_vault_guid, self._has_symbol_vault_guid = s.read_str(
            record, Fields.SYMBOL_VAULT_GUID, default=""
        )
        self.symbol_item_guid, self._has_symbol_item_guid = s.read_str(
            record, Fields.SYMBOL_ITEM_GUID, default=""
        )
        self.symbol_revision_guid, self._has_symbol_revision_guid = s.read_str(
            record, Fields.SYMBOL_REVISION_GUID, default=""
        )

        # Pin count
        self.all_pin_count, self._has_all_pin_count = s.read_int(
            record, Fields.ALL_PIN_COUNT, default=0
        )

        # Footprint
        self.footprint, self._has_footprint = s.read_str(
            record, Fields.FOOTPRINT, default=""
        )

    @staticmethod
    def _component_case_keys(mode: CaseMode) -> dict[str, str]:
        """
        Resolve case-sensitive field aliases used by component serialization.
        """
        return {
            "current_part_id": Fields.CURRENT_PART_ID.get_name(mode),
            "component_kind_v2": "ComponentKindVersion2"
            if mode == CaseMode.PASCALCASE
            else "COMPONENTKINDVERSION2",
            "component_kind_v3": "ComponentKindVersion3"
            if mode == CaseMode.PASCALCASE
            else "COMPONENTKINDVERSION3",
            "not_use_db_table_name": "NotUseDBTableName"
            if mode == CaseMode.PASCALCASE
            else "NOTUSEDBTABLENAME",
            "not_use_library_name": "NotUseLibraryName"
            if mode == CaseMode.PASCALCASE
            else "NOTUSELIBRARYNAME",
            "sheet_part_filename": Fields.SHEET_PART_FILENAME.get_name(mode),
            "target_filename": Fields.TARGET_FILENAME.get_name(mode),
        }

    def _serialize_library_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
    ) -> None:
        """
        Serialize the library identity fields.
        """
        if self._has_lib_reference or self.lib_reference != "*":
            serializer.write_str(record, Fields.LIB_REFERENCE, self.lib_reference, raw)
        if self._has_library_path or self.library_path != "*":
            serializer.write_str(record, Fields.LIBRARY_PATH, self.library_path, raw)
        if self._has_source_library_name or self.source_library_name != "*":
            serializer.write_str(
                record, Fields.SOURCE_LIBRARY_NAME, self.source_library_name, raw
            )
        if self._has_component_description or self.component_description:
            serializer.write_str(
                record, Fields.COMPONENT_DESCRIPTION, self.component_description, raw
            )
        if self.utf8_component_description:
            record["%UTF8%ComponentDescription"] = self.utf8_component_description

    def _serialize_multipart_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
        keys: dict[str, str],
    ) -> None:
        """
        Serialize multi-part component fields.
        """
        if self._has_part_count or self.part_count != 1:
            serializer.write_int(record, Fields.PART_COUNT, self.part_count, raw)
        record.pop("CurrentPartID", None)
        record.pop("CURRENTPARTID", None)
        if self._has_current_part_id or self.current_part_id != 1:
            record[keys["current_part_id"]] = str(self.current_part_id)
        if self._has_display_mode or self.display_mode != 0:
            serializer.write_int(record, Fields.DISPLAY_MODE, self.display_mode, raw)
        if self._has_display_mode_count or self.display_mode_count != 1:
            serializer.write_int(
                record, Fields.DISPLAY_MODE_COUNT, self.display_mode_count, raw
            )

    def _serialize_visibility_and_locking(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
    ) -> None:
        """
        Serialize orientation, visibility, and locking flags.
        """
        if self._has_orientation or self.orientation != Rotation90.DEG_0:
            serializer.write_int(
                record, Fields.ORIENTATION, self.orientation.value, raw
            )
        if self._has_is_mirrored or self.is_mirrored:
            serializer.write_bool(record, Fields.IS_MIRRORED, self.is_mirrored, raw)

        if self._has_show_hidden_pins or self.show_hidden_pins:
            serializer.write_bool(
                record, Fields.SHOW_HIDDEN_PINS, self.show_hidden_pins, raw
            )
        if self._has_show_hidden_fields or self.show_hidden_fields:
            serializer.write_bool(
                record, Fields.SHOW_HIDDEN_FIELDS, self.show_hidden_fields, raw
            )
        if self._has_display_field_names or self.display_field_names:
            serializer.write_bool(
                record, Fields.DISPLAY_FIELD_NAMES, self.display_field_names, raw
            )

        if self._has_designator_locked or self.designator_locked:
            serializer.write_bool(
                record, Fields.DESIGNATOR_LOCKED, self.designator_locked, raw
            )
        if self._has_part_id_locked or not self.part_id_locked:
            serializer.write_bool(
                record, Fields.PART_ID_LOCKED, self.part_id_locked, raw
            )
        if self._has_pins_moveable or self.pins_moveable:
            serializer.write_bool(record, Fields.PINS_MOVEABLE, self.pins_moveable, raw)

    def _serialize_color_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
    ) -> None:
        """
        Serialize local color override fields.
        """
        if self._has_override_colors or self.override_colors:
            serializer.write_bool(
                record,
                Fields.OVERRIDE_COLORS,
                self.override_colors,
                raw,
                force=self.override_colors,
            )
        if self._has_color or (self.override_colors and self.color != 0):
            serializer.write_int(
                record,
                Fields.COLOR,
                self.color,
                raw,
                force=self.override_colors and self.color != 0,
            )
        if self._has_area_color or (
            self.override_colors and self.area_color != 0xFFFFFF
        ):
            serializer.write_int(
                record,
                Fields.AREA_COLOR,
                self.area_color,
                raw,
                force=self.override_colors and self.area_color != 0xFFFFFF,
            )
        if self._has_pin_color or (self.override_colors and self.pin_color != 0):
            serializer.write_int(
                record,
                Fields.PIN_COLOR,
                self.pin_color,
                raw,
                force=self.override_colors and self.pin_color != 0,
            )

    def _serialize_component_kind_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
        keys: dict[str, str],
    ) -> None:
        """
        Serialize ComponentKind with versioned fallback fields.
        """
        if not (
            self._has_component_kind or self.component_kind != ComponentKind.STANDARD
        ):
            return

        record.pop("ComponentKindVersion2", None)
        record.pop("COMPONENTKINDVERSION2", None)
        record.pop("ComponentKindVersion3", None)
        record.pop("COMPONENTKINDVERSION3", None)
        if self.component_kind == ComponentKind.JUMPER:
            serializer.write_int(
                record, Fields.COMPONENT_KIND, ComponentKind.STANDARD.value, raw
            )
            record[keys["component_kind_v2"]] = str(ComponentKind.STANDARD.value)
            record[keys["component_kind_v3"]] = str(ComponentKind.JUMPER.value)
            return
        if self.component_kind == ComponentKind.STANDARD_NO_BOM:
            serializer.write_int(
                record, Fields.COMPONENT_KIND, ComponentKind.STANDARD.value, raw
            )
            record[keys["component_kind_v2"]] = str(ComponentKind.STANDARD_NO_BOM.value)
            return
        serializer.write_int(
            record, Fields.COMPONENT_KIND, self.component_kind.value, raw
        )

    def _serialize_database_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
        keys: dict[str, str],
    ) -> None:
        """
        Serialize database and design-item references.
        """
        if self._has_database_table_name or self.database_table_name:
            serializer.write_str(
                record, Fields.DATABASE_TABLE_NAME, self.database_table_name, raw
            )
        if self._has_use_db_table_name or not self.use_db_table_name:
            record[keys["not_use_db_table_name"]] = (
                "T" if not self.use_db_table_name else "F"
            )
        record.pop("UseDBTableName", None)
        record.pop("USEDBTABLENAME", None)

        if self._has_use_library_name or not self.use_library_name:
            record[keys["not_use_library_name"]] = (
                "T" if not self.use_library_name else "F"
            )
        record.pop("UseLibraryName", None)
        record.pop("USELIBRARYNAME", None)

        if self._has_design_item_id or self.design_item_id:
            serializer.write_str(
                record, Fields.DESIGN_ITEM_ID, self.design_item_id, raw
            )

    def _serialize_file_fields(
        self, record: dict[str, Any], keys: dict[str, str]
    ) -> None:
        """
        Serialize case-sensitive file reference fields.
        """
        record.pop("SheetPartFilename", None)
        record.pop("SHEETPARTFILENAME", None)
        if self._has_sheet_part_filename or self.sheet_part_filename != "*":
            record[keys["sheet_part_filename"]] = self.sheet_part_filename

        record.pop("TargetFilename", None)
        record.pop("TARGETFILENAME", None)
        if self._has_target_filename or self.target_filename != "*":
            record[keys["target_filename"]] = self.target_filename

    def _serialize_guid_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
    ) -> None:
        """
        Serialize vault and revision GUIDs.
        """
        for present, value, field in (
            (self._has_vault_guid, self.vault_guid, Fields.VAULT_GUID),
            (self._has_item_guid, self.item_guid, Fields.ITEM_GUID),
            (self._has_revision_guid, self.revision_guid, Fields.REVISION_GUID),
            (
                self._has_symbol_vault_guid,
                self.symbol_vault_guid,
                Fields.SYMBOL_VAULT_GUID,
            ),
            (
                self._has_symbol_item_guid,
                self.symbol_item_guid,
                Fields.SYMBOL_ITEM_GUID,
            ),
            (
                self._has_symbol_revision_guid,
                self.symbol_revision_guid,
                Fields.SYMBOL_REVISION_GUID,
            ),
        ):
            if present or value:
                serializer.write_str(record, field, value, raw)

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record
        keys = self._component_case_keys(mode)

        self._serialize_library_fields(record, s, raw)
        self._serialize_multipart_fields(record, s, raw, keys)
        self._serialize_visibility_and_locking(record, s, raw)
        self._serialize_color_fields(record, s, raw)
        self._serialize_component_kind_fields(record, s, raw, keys)
        self._serialize_database_fields(record, s, raw, keys)
        self._serialize_file_fields(record, keys)
        self._serialize_guid_fields(record, s, raw)

        # Pin count
        if self._has_all_pin_count or self.all_pin_count != 0:
            s.write_int(record, Fields.ALL_PIN_COUNT, self.all_pin_count, raw)

        # Footprint
        if self._has_footprint or self.footprint:
            s.write_str(record, Fields.FOOTPRINT, self.footprint, raw)

        return record

    def _bound_schematic_owner(self) -> object | None:
        return bound_schematic_owner(self)

    def _require_bound_schdoc(self) -> "AltiumSchDoc":
        from .altium_schdoc import AltiumSchDoc

        owner = self._bound_schematic_owner()
        if not isinstance(owner, AltiumSchDoc):
            raise ValueError(
                "Component mutation requires the component to be bound to an AltiumSchDoc"
            )
        return owner

    def _find_designator_record(self) -> object | None:
        from .altium_record_sch__designator import AltiumSchDesignator

        for param in self.parameters:
            if isinstance(param, AltiumSchDesignator):
                return param
        return None

    def _find_named_parameter(self, name: str) -> object | None:
        from .altium_record_sch__parameter import AltiumSchParameter

        target = name.upper()
        for param in self.parameters:
            if not isinstance(param, AltiumSchParameter):
                continue
            if (param.name or "").upper() == target:
                return param
        return None

    def set_part_count(self, count: int) -> "AltiumSchComponent":
        """
        Set the logical multipart count for this placed component.
        """
        resolved = int(count)
        if resolved < 1:
            raise ValueError("part_count must be >= 1")
        if self.current_part_id > resolved:
            raise ValueError(
                f"current_part_id {self.current_part_id} exceeds part_count {resolved}"
            )
        self.part_count = resolved
        self._has_part_count = True
        return self

    def set_current_part(self, part_id: int) -> "AltiumSchComponent":
        """
        Select the active logical part for this placed component.
        """
        resolved = int(part_id)
        if resolved < 1:
            raise ValueError("current_part_id must be >= 1")
        if resolved > self.part_count:
            raise ValueError(
                f"current_part_id {resolved} exceeds part_count {self.part_count}"
            )
        self.current_part_id = resolved
        self._has_current_part_id = True
        return self

    def _resolve_owner_part_id(self, owner_part_id: int | None) -> int | None:
        """
        Normalize child owner-part routing for inline multipart authoring.
        """
        if owner_part_id is None:
            return self.current_part_id
        resolved = int(owner_part_id)
        if resolved in (0, -1):
            return None
        if resolved < 1:
            raise ValueError("owner_part_id must be >= 1, 0, -1, or None")
        if resolved > self.part_count:
            raise ValueError(
                f"owner_part_id {resolved} exceeds part_count {self.part_count}"
            )
        return resolved

    def add_pin(
        self,
        designator: str,
        name: str = "",
        x: int = 0,
        y: int = 0,
        orientation: Rotation90 | int = Rotation90.DEG_0,
        electrical: PinElectrical | int = PinElectrical.INPUT,
        length: int = 200,
        font: str = "Arial",
        font_size: int = 10,
        show_name: bool = True,
        show_designator: bool = True,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a pin in component-local mil coordinates.

        Prefer `Rotation90` and `PinElectrical` enum values for orientation and
        electrical type. Raw integer values are accepted for compatibility.
        """
        from .altium_record_sch__pin import AltiumSchPin
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        schdoc = self._require_bound_schdoc()
        pin = AltiumSchPin()
        pin.designator = designator
        pin.name = name
        pin.location = CoordPoint.from_mils(x, y)
        pin.orientation = Rotation90(int(orientation))
        pin.length = int(round(length / 10.0))
        pin._length_mils = float(length)
        pin.unique_id = generate_unique_id()
        pin._source_is_binary = False
        pin.electrical = (
            electrical
            if isinstance(electrical, PinElectrical)
            else PinElectrical(int(electrical))
        )
        pin.show_name = show_name
        pin.show_designator = show_designator
        pin.color = 0x000000
        pin.name_settings.font_name = font
        pin.name_settings.font_size = font_size
        pin.designator_settings.font_name = font
        pin.designator_settings.font_size = font_size
        pin.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        settings_pairs = (pin.name_settings, pin.designator_settings)
        for settings in settings_pairs:
            settings.font_id = schdoc.font_manager.get_or_create_font(
                font_name=settings.font_name or "Arial",
                font_size=int(settings.font_size or font_size),
                bold=settings.font_bold,
                italic=settings.font_italic,
            )
            settings.font_mode = PinItemMode.CUSTOM

        transformed_pin = to_schematic_space(pin, self, regenerate_id=False)
        schdoc.add_object(transformed_pin, owner=self)
        self.all_pin_count = len(self.pins)
        self._has_all_pin_count = True
        return transformed_pin

    def add_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: int = 0x000000,
        fill_color: int = 0xFFFFFF,
        is_solid: bool = True,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a rectangle in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__rectangle import AltiumSchRectangle
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        rect = AltiumSchRectangle()
        rect.location = CoordPoint.from_mils(min(x1, x2), min(y1, y2))
        rect.corner = CoordPoint.from_mils(max(x1, x2), max(y1, y2))
        rect.color = color
        rect.area_color = fill_color
        rect.is_solid = is_solid
        rect.line_width = LineWidth(int(line_width))
        rect.unique_id = generate_unique_id()
        rect.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(rect, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: int = 0x000000,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a line in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__line import AltiumSchLine
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        line = AltiumSchLine()
        line.location = CoordPoint.from_mils(x1, y1)
        line.corner = CoordPoint.from_mils(x2, y2)
        line.color = color
        line.line_width = LineWidth(int(line_width))
        line.unique_id = generate_unique_id()
        line.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(line, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_polyline(
        self,
        points: list[tuple[int, int]],
        color: int = 0x000000,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a polyline in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__polyline import AltiumSchPolyline
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        polyline = AltiumSchPolyline()
        polyline.vertices = [CoordPoint.from_mils(x, y) for x, y in points]
        polyline.color = color
        polyline.line_width = LineWidth(int(line_width))
        polyline.unique_id = generate_unique_id()
        polyline.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(polyline, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_arc(
        self,
        cx: int,
        cy: int,
        radius: int,
        start_angle: float = 0.0,
        end_angle: float = 360.0,
        color: int = 0x000000,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add an arc in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__arc import AltiumSchArc
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        arc = AltiumSchArc()
        arc.location = CoordPoint.from_mils(cx, cy)
        arc.radius = round(radius / 10.0)
        arc.start_angle = start_angle
        arc.end_angle = end_angle
        arc.color = color
        arc.line_width = LineWidth(int(line_width))
        arc.unique_id = generate_unique_id()
        arc.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(arc, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_ellipse(
        self,
        cx: int,
        cy: int,
        rx: int,
        ry: int,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        is_solid: bool = False,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add an ellipse in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__ellipse import AltiumSchEllipse
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        ellipse = AltiumSchEllipse()
        ellipse.location = CoordPoint.from_mils(cx, cy)
        ellipse.radius = round(rx / 10.0)
        ellipse.secondary_radius = round(ry / 10.0)
        ellipse.color = color
        ellipse.area_color = area_color
        ellipse.is_solid = is_solid
        ellipse.line_width = LineWidth(int(line_width))
        ellipse.unique_id = generate_unique_id()
        ellipse.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(ellipse, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_polygon(
        self,
        points: list[tuple[int, int]],
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        is_solid: bool = False,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a polygon in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__polygon import AltiumSchPolygon
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        polygon = AltiumSchPolygon()
        polygon.vertices = [CoordPoint.from_mils(x, y) for x, y in points]
        polygon.color = color
        polygon.area_color = area_color
        polygon.is_solid = is_solid
        polygon.line_width = LineWidth(int(line_width))
        polygon.unique_id = generate_unique_id()
        polygon.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(polygon, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_bezier(
        self,
        points: list[tuple[int, int]],
        color: int = 0x000000,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a bezier curve in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__bezier import AltiumSchBezier
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        bezier = AltiumSchBezier()
        bezier.vertices = [CoordPoint.from_mils(x, y) for x, y in points]
        bezier.color = color
        bezier.line_width = LineWidth(int(line_width))
        bezier.unique_id = generate_unique_id()
        bezier.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(bezier, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_round_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        corner_x_radius: int = 10,
        corner_y_radius: int = 10,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        is_solid: bool = True,
        line_width: LineWidth | int = LineWidth.SMALL,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a rounded rectangle in component-local mil coordinates.

        Prefer a `LineWidth` enum value for `line_width`. Raw integer values are
        accepted for compatibility.
        """
        from .altium_record_sch__rounded_rectangle import AltiumSchRoundedRectangle
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        rounded_rect = AltiumSchRoundedRectangle()
        rounded_rect.location = CoordPoint.from_mils(x1, y1)
        rounded_rect.corner = CoordPoint.from_mils(x2, y2)
        rounded_rect.corner_x_radius = round(corner_x_radius / 10.0)
        rounded_rect.corner_y_radius = round(corner_y_radius / 10.0)
        rounded_rect.color = color
        rounded_rect.area_color = area_color
        rounded_rect.is_solid = is_solid
        rounded_rect.line_width = LineWidth(int(line_width))
        rounded_rect.unique_id = generate_unique_id()
        rounded_rect.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(
            rounded_rect,
            self,
            regenerate_id=False,
        )
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def add_label(
        self,
        x: int,
        y: int,
        text: str,
        color: int = 0x000000,
        font_id: int = 1,
        orientation: int = 0,
        owner_part_id: int | None = None,
    ) -> object:
        """
        Add a text label in component-local mil coordinates.
        """
        from .altium_record_sch__label import AltiumSchLabel
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        label = AltiumSchLabel()
        label.location = CoordPoint.from_mils(x, y)
        label.text = text
        label.color = color
        label.font_id = font_id
        label.orientation = TextOrientation(orientation)
        label.unique_id = generate_unique_id()
        label.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed = to_schematic_space(label, self, regenerate_id=False)
        schdoc = self._require_bound_schdoc()
        schdoc.add_object(transformed, owner=self)
        return transformed

    def set_comment(
        self,
        text: str,
        x: int = 0,
        y: int = 50,
        visible: bool = True,
    ) -> object:
        """
        Set or create the Comment parameter using component-local mil coordinates.
        """
        from .altium_record_sch__parameter import AltiumSchParameter
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        schdoc = self._require_bound_schdoc()
        existing = self._find_named_parameter("Comment")
        if isinstance(existing, AltiumSchParameter):
            template = AltiumSchParameter()
            template.location = CoordPoint.from_mils(x, y)
            transformed = to_schematic_space(template, self, regenerate_id=False)
            existing.text = text
            existing.is_hidden = not visible
            existing.location = transformed.location
            existing.owner_part_id = self.current_part_id
            return existing

        param = AltiumSchParameter()
        param.name = "Comment"
        param.text = text
        param.location = CoordPoint.from_mils(x, y)
        param.is_hidden = not visible
        param.unique_id = generate_unique_id()
        param.owner_part_id = self.current_part_id

        transformed_param = to_schematic_space(param, self, regenerate_id=False)
        schdoc.add_object(transformed_param, owner=self)
        return transformed_param

    def add_parameter(
        self,
        name: str,
        value: str,
        x: int = 0,
        y: int = 0,
        visible: bool = False,
        owner_part_id: int | None = None,
    ) -> object | None:
        """
        Add or update a custom parameter using component-local mil coordinates.
        """
        from .altium_record_sch__parameter import AltiumSchParameter
        from .altium_symbol_transform import generate_unique_id, to_schematic_space

        if name.lower() == "description":
            self.component_description = value
            self._has_component_description = True
            return None

        schdoc = self._require_bound_schdoc()
        existing = self._find_named_parameter(name)
        if isinstance(existing, AltiumSchParameter):
            template = AltiumSchParameter()
            template.location = CoordPoint.from_mils(x, y)
            transformed_template = to_schematic_space(
                template,
                self,
                regenerate_id=False,
            )
            existing.text = value
            existing.location = transformed_template.location
            existing.is_hidden = not visible
            existing.owner_part_id = self._resolve_owner_part_id(owner_part_id)
            return existing

        param = AltiumSchParameter()
        param.name = name
        param.text = value
        param.location = CoordPoint.from_mils(x, y)
        param.is_hidden = not visible
        param.unique_id = generate_unique_id()
        param.owner_part_id = self._resolve_owner_part_id(owner_part_id)

        transformed_param = to_schematic_space(param, self, regenerate_id=False)
        schdoc.add_object(transformed_param, owner=self)
        return transformed_param

    def add_footprint(
        self,
        model_name: str,
        description: str = "",
        is_current: bool = True,
        library_name: str = "",
    ) -> object:
        """
        Add a PCB footprint implementation to this placed component.
        """
        from .altium_record_sch__implementation import (
            AltiumSchImplementation,
            AltiumSchImplementationList,
            AltiumSchImplParams,
            AltiumSchMapDefinerList,
        )
        from .altium_symbol_transform import generate_unique_id

        schdoc = self._require_bound_schdoc()
        if not library_name:
            library_name = model_name

        implementation_list = next(
            (
                param
                for param in self.parameters
                if isinstance(param, AltiumSchImplementationList)
            ),
            None,
        )
        if implementation_list is None:
            implementation_list = AltiumSchImplementationList()
            implementation_list.unique_id = generate_unique_id()
            schdoc.add_object(implementation_list, owner=self)

        if is_current:
            for existing in getattr(implementation_list, "children", []):
                if hasattr(existing, "is_current"):
                    existing.is_current = False

        implementation = AltiumSchImplementation()
        implementation.model_name = model_name
        implementation.model_type = "PCBLIB"
        implementation.description = description
        implementation.is_current = is_current
        implementation._has_model_name = True
        implementation._has_model_type = True
        implementation._has_is_current = True
        if description:
            implementation._has_description = True
        implementation.datafile_count = 1
        implementation.datafile_entity = library_name
        implementation.datafile_kind = "PCBLib"
        implementation._has_datafile_count = True
        implementation.unique_id = generate_unique_id()
        schdoc.add_object(implementation, owner=implementation_list)

        map_def_list = AltiumSchMapDefinerList()
        schdoc.add_object(map_def_list, owner=implementation)

        impl_params = AltiumSchImplParams()
        schdoc.add_object(impl_params, owner=implementation)

        self.footprint = model_name
        self._has_footprint = True
        return implementation

    def set_designator_style(
        self,
        x: int | None = None,
        y: int | None = None,
        font_name: str = "Arial",
        font_size: int = 12,
        bold: bool = True,
    ) -> object:
        """
        Set the placed designator record position and font in schematic mils.
        """
        from .altium_record_sch__designator import AltiumSchDesignator
        from .altium_symbol_transform import generate_unique_id

        schdoc = self._require_bound_schdoc()
        designator = self._find_designator_record()
        if not isinstance(designator, AltiumSchDesignator):
            designator = AltiumSchDesignator()
            designator.text = self.lib_reference or "U?"
            designator.unique_id = generate_unique_id()
            schdoc.add_object(designator, owner=self)

        if x is not None and y is not None:
            designator.location = CoordPoint.from_mils(x, y)
        designator.font_id = schdoc.font_manager.get_or_create_font(
            font_name=font_name,
            font_size=font_size,
            bold=bold,
        )
        return designator

    def set_comment_style(
        self,
        x: int | None = None,
        y: int | None = None,
        font_name: str = "Arial",
        font_size: int = 10,
        bold: bool = False,
    ) -> object:
        """
        Set the placed Comment parameter position and font in schematic mils.
        """
        from .altium_record_sch__parameter import AltiumSchParameter
        from .altium_symbol_transform import generate_unique_id

        schdoc = self._require_bound_schdoc()
        comment = self._find_named_parameter("Comment")
        if not isinstance(comment, AltiumSchParameter):
            comment = AltiumSchParameter()
            comment.name = "Comment"
            comment.text = "=Value"
            comment.unique_id = generate_unique_id()
            schdoc.add_object(comment, owner=self)

        if x is not None and y is not None:
            comment.location = CoordPoint.from_mils(x, y)
        comment.font_id = schdoc.font_manager.get_or_create_font(
            font_name=font_name,
            font_size=font_size,
            bold=bold,
        )
        return comment

    def get_pin_hotspot(self, pin_designator: str) -> tuple[int, int]:
        """
        Return a placed pin hotspot in schematic mils.
        """
        for pin in self.pins:
            if getattr(pin, "designator", "") != pin_designator:
                continue
            connection_point = pin.connection_point
            return (connection_point[0] * 10, connection_point[1] * 10)
        raise ValueError(
            f"Pin '{pin_designator}' not found. Available: "
            f"{[getattr(pin, 'designator', '') for pin in self.pins]}"
        )

    add_rounded_rectangle = add_round_rectangle

    @staticmethod
    def _part_matches(record: object, part_id: int | None) -> bool:
        owner_part = getattr(record, "owner_part_id", None)
        if owner_part is None:
            return True
        try:
            owner_part_id = int(owner_part)
        except (TypeError, ValueError):
            return True
        if owner_part_id <= 0:
            return True
        return part_id is None or owner_part_id == part_id

    @staticmethod
    def _point_to_mils(point: object) -> tuple[float, float] | None:
        if point is None:
            return None
        if hasattr(point, "x_mils") and hasattr(point, "y_mils"):
            return (float(point.x_mils), float(point.y_mils))
        if hasattr(point, "x") and hasattr(point, "y"):
            return (float(point.x) * 10.0, float(point.y) * 10.0)
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            return (float(point[0]), float(point[1]))
        return None

    @staticmethod
    def _radius_value_mils(record: object, attr: str) -> float | None:
        public_attr = f"{attr}_mils"
        if hasattr(record, public_attr):
            try:
                return float(getattr(record, public_attr))
            except (TypeError, ValueError):
                return None
        if not hasattr(record, attr):
            return None
        try:
            value = int(getattr(record, attr))
            frac = int(getattr(record, f"{attr}_frac", 0) or 0)
        except (TypeError, ValueError):
            return None
        return value * 10.0 + frac / 10000.0

    @classmethod
    def _pin_record_bounds_mils(cls, record: object) -> SchRectMils | None:
        location = cls._point_to_mils(getattr(record, "location", None))
        get_hot_spot = getattr(record, "get_hot_spot", None)
        if location is None or not callable(get_hot_spot):
            return None
        endpoint = cls._point_to_mils(get_hot_spot())
        if endpoint is None:
            return None
        return SchRectMils.from_corners_mils(
            location[0],
            location[1],
            endpoint[0],
            endpoint[1],
        ).normalized()

    @classmethod
    def _record_bounds_mils(cls, record: object) -> SchRectMils | None:
        bounds = getattr(record, "bounds_mils", None)
        if isinstance(bounds, SchRectMils):
            normalized_bounds = bounds.normalized()
            if normalized_bounds.width_mils <= 0 and normalized_bounds.height_mils <= 0:
                return None
            return normalized_bounds

        pin_bounds = cls._pin_record_bounds_mils(record)
        if pin_bounds is not None:
            return pin_bounds

        points: list[tuple[float, float]] = []
        vertex_points: list[tuple[float, float]] = []
        for vertex in getattr(record, "vertices", []) or []:
            point = cls._point_to_mils(vertex)
            if point is not None:
                vertex_points.append(point)

        if vertex_points:
            points.extend(vertex_points)
        else:
            for attr in ("location", "corner"):
                point = cls._point_to_mils(getattr(record, attr, None))
                if point is not None:
                    points.append(point)

        location = cls._point_to_mils(getattr(record, "location", None))
        radius = cls._radius_value_mils(record, "radius")
        if location is not None and radius is not None:
            secondary_radius = cls._radius_value_mils(record, "secondary_radius")
            if secondary_radius is None:
                secondary_radius = radius
            points.extend(
                [
                    (location[0] - radius, location[1] - secondary_radius),
                    (location[0] + radius, location[1] + secondary_radius),
                ]
            )

        if not points:
            return None
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        rect = SchRectMils.from_corners_mils(
            min(xs),
            min(ys),
            max(xs),
            max(ys),
        ).normalized()
        if rect.width_mils <= 0 and rect.height_mils <= 0:
            return None
        return rect

    @staticmethod
    def _merge_rects(rects: list[SchRectMils]) -> SchRectMils | None:
        if not rects:
            return None
        normalized = [rect.normalized() for rect in rects]
        return SchRectMils.from_corners_mils(
            min(rect.x1_mils for rect in normalized),
            min(rect.y1_mils for rect in normalized),
            max(rect.x2_mils for rect in normalized),
            max(rect.y2_mils for rect in normalized),
        ).normalized()

    def _resolved_part_id(self, part_id: int | None) -> int | None:
        if part_id is None:
            part_id = getattr(self, "current_part_id", None)
        try:
            return int(part_id) if part_id is not None else None
        except (TypeError, ValueError):
            return None

    def _unique_records(self, records: list[object]) -> list[object]:
        result: list[object] = []
        seen: set[int] = set()
        for record in records:
            identity = id(record)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(record)
        return result

    def _display_body_records(
        self,
        *,
        part_id: int | None = None,
    ) -> list[object]:
        resolved_part_id = self._resolved_part_id(part_id)
        pin_ids = {id(pin) for pin in getattr(self, "pins", []) or []}
        parameter_ids = {id(param) for param in getattr(self, "parameters", []) or []}
        body_records = self._unique_records(
            list(getattr(self, "graphics", []) or [])
            + [
                child
                for child in (getattr(self, "children", []) or [])
                if getattr(child, "is_not_accessible", False)
                and id(child) not in pin_ids
                and id(child) not in parameter_ids
            ]
        )
        return [
            record
            for record in body_records
            if self._part_matches(record, resolved_part_id)
        ]

    def display_body_element_ids(
        self,
        *,
        part_id: int | None = None,
    ) -> list[str]:
        """
        Return unique IDs for records that form the visible component body.

        This mirrors :meth:`display_body_bounds_mils` and intentionally excludes
        pins and parameter/designator text.
        """
        result: list[str] = []
        seen: set[str] = set()
        for record in self._display_body_records(part_id=part_id):
            unique_id = str(getattr(record, "unique_id", "") or "").strip()
            if not unique_id or unique_id in seen:
                continue
            seen.add(unique_id)
            result.append(unique_id)
        return result

    def display_body_bounds_mils(
        self,
        *,
        part_id: int | None = None,
    ) -> SchRectMils | None:
        """
        Return schematic mil bounds for the visible component body graphics.

        This intentionally excludes pins and visible parameter text. It is the
        preferred target for variant DNP graphics in downstream viewers.
        """
        rects: list[SchRectMils] = []
        for record in self._display_body_records(part_id=part_id):
            bounds = self._record_bounds_mils(record)
            if bounds is not None:
                rects.append(bounds)
        return self._merge_rects(rects)

    def non_accessible_children_bounds_mils(
        self,
        *,
        part_id: int | None = None,
    ) -> SchRectMils | None:
        """
        Alias for Altium's DNP/non-accessible child bounds concept.
        """
        return self.display_body_bounds_mils(part_id=part_id)

    def non_accessible_children_element_ids(
        self,
        *,
        part_id: int | None = None,
    ) -> list[str]:
        """
        Alias for Altium-style DNP/non-accessible child target records.
        """
        return self.display_body_element_ids(part_id=part_id)

    def full_bounds_mils(
        self,
        *,
        include_pins: bool = True,
        include_parameters: bool = True,
        part_id: int | None = None,
    ) -> SchRectMils | None:
        """
        Return schematic mil bounds for component graphics and optional children.
        """
        records: list[object] = list(getattr(self, "graphics", []) or [])
        if include_pins:
            records.extend(getattr(self, "pins", []) or [])
        parameter_records = [
            param
            for param in (getattr(self, "parameters", []) or [])
            if not getattr(param, "is_hidden", False)
        ]
        if include_parameters:
            records.extend(parameter_records)

        pin_ids = {id(pin) for pin in getattr(self, "pins", []) or []}
        parameter_ids = {id(param) for param in getattr(self, "parameters", []) or []}
        for child in getattr(self, "children", []) or []:
            if child in records:
                continue
            if id(child) in pin_ids or id(child) in parameter_ids:
                continue
            if getattr(child, "is_not_accessible", False):
                records.append(child)

        resolved_part_id = self._resolved_part_id(part_id)
        rects: list[SchRectMils] = []
        for record in self._unique_records(records):
            if not self._part_matches(record, resolved_part_id):
                continue
            bounds = self._record_bounds_mils(record)
            if bounds is not None:
                rects.append(bounds)
        return self._merge_rects(rects)

    def _detect_case_mode(self) -> CaseMode:
        """
        Detect case mode from raw record fields.
        """
        if self._raw_record is None:
            return CaseMode.PASCALCASE  # Default for new records
        saw_uppercase = False
        for key in self._raw_record:
            if key == "RECORD":
                continue
            if any(ch.islower() for ch in key):
                return CaseMode.PASCALCASE
            if any(ch.isalpha() for ch in key) and key.upper() == key:
                saw_uppercase = True
        return CaseMode.UPPERCASE if saw_uppercase else CaseMode.PASCALCASE

    @staticmethod
    def _native_export_component_junction_ops(
        pin: object,
        child_geometry: list[object],
        ctx: object,
        *,
        units_per_px: int,
    ) -> list[object]:
        """
        Extract the pin-hotspot junction pair used by native multipart exports.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            SchGeometryOpKind,
            svg_coord_to_geometry,
        )

        if len(child_geometry) < 2:
            return []

        hot_spot = pin.get_hot_spot()
        hot_spot_px = ctx.transform_coord_precise(hot_spot)
        hot_spot_x, hot_spot_y = svg_coord_to_geometry(
            hot_spot_px[0],
            hot_spot_px[1],
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )

        bounds_keys = ("x1", "y1", "x2", "y2", "corner_x_radius", "corner_y_radius")
        for index in range(len(child_geometry) - 1):
            fill_op = child_geometry[index]
            stroke_op = child_geometry[index + 1]
            if fill_op.kind_str() != SchGeometryOpKind.ROUNDED_RECTANGLE.value:
                continue
            if stroke_op.kind_str() != SchGeometryOpKind.ROUNDED_RECTANGLE.value:
                continue

            fill_payload = fill_op.payload
            stroke_payload = stroke_op.payload
            if any(
                fill_payload.get(key) != stroke_payload.get(key) for key in bounds_keys
            ):
                continue
            if "brush" not in fill_payload or "pen" in fill_payload:
                continue
            if "pen" not in stroke_payload or "brush" in stroke_payload:
                continue

            center_x = (float(fill_payload["x1"]) + float(fill_payload["x2"])) / 2.0
            center_y = (float(fill_payload["y1"]) + float(fill_payload["y2"])) / 2.0
            if abs(center_x - hot_spot_x) > 1e-6 or abs(center_y - hot_spot_y) > 1e-6:
                continue

            return [
                SchGeometryOp(kind=op.kind, payload=copy.deepcopy(op.payload))
                for op in (fill_op, stroke_op)
            ]

        return []

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            unwrap_record_operations,
            wrap_record_operations,
        )

        child_operations: list[SchGeometryOp] = []
        component_level_operations: list[SchGeometryOp] = []
        native_multipart_junction_wrappers = self.part_count > 1 and getattr(
            ctx, "native_svg_export", False
        )
        component_children = sorted(
            [("graphic", child) for child in self.graphics]
            + [("pin", child) for child in self.pins]
            + [("param", child) for child in self.parameters],
            key=lambda item: (
                int(getattr(item[1], "_record_index", 10**9)),
                str(getattr(item[1], "unique_id", "")),
            ),
        )

        for child_kind, child in component_children:
            owner_part = getattr(child, "owner_part_id", None)
            if (
                owner_part is not None
                and owner_part > 0
                and owner_part != self.current_part_id
            ):
                continue
            if (
                child_kind == "pin"
                and getattr(child, "is_hidden", False)
                and not self.show_hidden_pins
            ):
                continue

            to_geometry = getattr(child, "to_geometry", None)
            if not callable(to_geometry):
                continue

            if child_kind == "graphic":
                graphic_record = to_geometry(
                    ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
                child_geometry = unwrap_record_operations(
                    graphic_record,
                    unique_id=getattr(child, "unique_id", ""),
                )
            else:
                child_geometry = to_geometry(
                    ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                    wrap_record=False,
                )

            if not child_geometry:
                continue
            if child_kind == "pin" and native_multipart_junction_wrappers:
                component_level_operations.extend(
                    self._native_export_component_junction_ops(
                        child,
                        child_geometry,
                        ctx,
                        units_per_px=units_per_px,
                    )
                )
            child_operations.append(
                SchGeometryOp.begin_group(getattr(child, "unique_id", ""))
            )
            child_operations.extend(child_geometry)
            child_operations.append(SchGeometryOp.end_group())

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="component",
            object_id="eSchComponent",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=wrap_record_operations(
                self.unique_id,
                [*component_level_operations, *child_operations],
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<AltiumSchComponent '{self.lib_reference}' "
            f"at=({self.location.x}, {self.location.y}) "
            f"parts={self.part_count}>"
        )
