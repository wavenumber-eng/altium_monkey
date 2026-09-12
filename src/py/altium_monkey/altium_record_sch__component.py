"""Schematic record model for SchRecordType.COMPONENT."""

import copy

from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryOp, SchGeometryRecord
    from .altium_schdoc import AltiumSchDoc

    from .altium_sch_svg_renderer import SchSvgRenderContext

from ._sch_managed_defaults import RECT_BORDER_COLOR, RECT_FILL_COLOR
from ._sch_source_admission import _SourceAdmission

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
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    FieldDef,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import bound_schematic_owner


def _validate_display_mode_count(value: int) -> None:
    if not 0 <= value <= 0xFF:
        raise ValueError("DisplayModeCount must fit an unsigned byte")


def _validate_unsigned_byte(value: int, field: str) -> None:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{field} must fit an unsigned byte")


def _validate_signed_short(value: int, field: str) -> None:
    if not -(2**15) <= value < 2**15:
        raise ValueError(f"{field} must fit a signed short")


def _ascii_upper(value: str) -> str:
    return "".join(
        chr(ord(character) - 32) if "a" <= character <= "z" else character
        for character in value
    )


def _split_legacy_dblib_name(value: str) -> tuple[str, str] | None:
    marker = ".DBLIB/"
    marker_index = _ascii_upper(value).find(marker)
    if marker_index < 0:
        return None
    library_end = marker_index + len(".DBLIB")
    return value[:library_end], value[marker_index + len(marker) :]


def _normalize_alias_list(value: str) -> str:
    return (
        ",".join(sorted({item for item in value.split(",") if item})) if value else ""
    )


def _is_parent_bound_geometry_child(obj: object) -> bool:
    # Keep in sync with altium_schdoc._is_parent_bound_geometry_child without
    # importing SchDoc-only record classes here. Harness/sheet entries need
    # parent connector/symbol geometry context and must not be drawn as generic
    # component graphics.
    return type(obj).__name__ in (
        "AltiumSchHarnessEntry",
        "AltiumSchSheetEntry",
    )


class AltiumSchComponent(SchGraphicalObject):
    """
    COMPONENT record.

    Represents a placed symbol instance in a schematic.
    Acts as a container for pins, primitives, and parameters.
    """

    def __init__(self) -> None:
        super().__init__()

        # Library reference
        self._lib_reference: str = "*"
        self.library_path: str = "*"
        self.source_library_name: str = "*"
        self.component_description: str = ""
        self.utf8_component_description: str = ""  # UTF-8 encoded description variant
        self._dynamic_utf8_fields: set[str] = set()

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
        self._source_part_id_locked: bool = True
        self.pins_moveable: bool = False

        # Colors - Local Colors override (when override_colors=True)
        # Fills = area_color, Lines = color, Pins = pin_color
        self.override_colors: bool = False
        self.color = RECT_BORDER_COLOR  # Lines color (Win32 BGR)
        self.area_color = RECT_FILL_COLOR  # Fills color (Win32 BGR)
        self.pin_color: int = 0x000000  # Pins color (Win32 BGR)

        # Component classification
        self.component_kind: ComponentKind = ComponentKind.STANDARD
        self._source_component_kind: ComponentKind = ComponentKind.STANDARD
        self.component_kind_version2: int | None = None

        # Database and design item references
        self.database_table_name: str = ""
        self.use_db_table_name: bool = True
        self.use_library_name: bool = True
        self._design_item_id: str = ""

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
        self.generic_component_template_guid: str = ""
        self._alias_list: str = ""
        self.has_only_current_part_info: bool = False
        self.key_component_unique_id: str = ""
        self.custom_display_mode_names: list[str] = []
        self._has_custom_display_mode_names: list[bool] = []

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
        self._has_generic_component_template_guid: bool = False
        self._has_alias_list: bool = False
        self._has_has_only_current_part_info: bool = False
        self._has_key_component_unique_id: bool = False
        self._has_all_pin_count: bool = False
        self._has_footprint: bool = False
        self._footprint_at_parse: str = self.footprint
        self._legacy_dblib_source: bool = False
        self._lib_reference_at_parse: str = self.lib_reference
        self._library_path_at_parse: str = self.library_path
        self._source_library_name_at_parse: str = self.source_library_name
        self._database_table_name_at_parse: str = self.database_table_name
        self._sheet_part_filename_at_parse: str = self.sheet_part_filename
        self._target_filename_at_parse: str = self.target_filename
        self._part_count_at_parse: int = self.part_count
        self._current_part_id_at_parse: int = self.current_part_id
        self._display_mode_count_at_parse: int = self.display_mode_count
        self._alias_list_at_parse: str = self.alias_list
        self._capture_component_source_state()

    def _capture_component_source_state(self) -> None:
        self._component_source_state: dict[str, object] = {
            name: getattr(self, name)
            for name in (
                "lib_reference",
                "library_path",
                "source_library_name",
                "component_description",
                "part_count",
                "current_part_id",
                "display_mode",
                "display_mode_count",
                "orientation",
                "is_mirrored",
                "show_hidden_pins",
                "show_hidden_fields",
                "display_field_names",
                "designator_locked",
                "part_id_locked",
                "pins_moveable",
                "override_colors",
                "color",
                "area_color",
                "pin_color",
                "component_kind",
                "database_table_name",
                "use_db_table_name",
                "use_library_name",
                "design_item_id",
                "sheet_part_filename",
                "target_filename",
                "vault_guid",
                "item_guid",
                "revision_guid",
                "symbol_vault_guid",
                "symbol_item_guid",
                "symbol_revision_guid",
                "generic_component_template_guid",
                "alias_list",
                "has_only_current_part_info",
                "key_component_unique_id",
                "all_pin_count",
                "unique_id",
            )
        }
        self._source_custom_display_mode_names = list(self.custom_display_mode_names)

    def _component_changed(self, attribute: str) -> bool:
        return getattr(self, attribute) != self._component_source_state[attribute]

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.COMPONENT

    @property
    def lib_reference(self) -> str:
        return self._lib_reference

    @lib_reference.setter
    def lib_reference(self, value: str) -> None:
        self._lib_reference = str(value).strip()

    @property
    def design_item_id(self) -> str:
        return self._design_item_id

    @design_item_id.setter
    def design_item_id(self, value: str) -> None:
        self._design_item_id = str(value).strip()

    @property
    def alias_list(self) -> str:
        return self._alias_list

    @alias_list.setter
    def alias_list(self, value: str) -> None:
        self._alias_list = _normalize_alias_list(str(value))

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        s = AltiumSerializer()

        # Library reference
        self.lib_reference, self._has_lib_reference = self._read_dynamic_field(
            s, record, Fields.LIB_REFERENCE, ""
        )
        self.library_path, self._has_library_path = self._read_dynamic_field(
            s, record, Fields.LIBRARY_PATH, ""
        )
        self.source_library_name, self._has_source_library_name = (
            self._read_dynamic_field(s, record, Fields.SOURCE_LIBRARY_NAME, "")
        )
        self.component_description, self._has_component_description = s.read_str(
            record, Fields.COMPONENT_DESCRIPTION, default=""
        )
        self._library_path_at_parse = self.library_path
        self.utf8_component_description = ""

        # Multi-part
        self.part_count, self._has_part_count = s.read_int(
            record, Fields.PART_COUNT, default=0
        )
        _validate_signed_short(self.part_count, "PartCount")
        self.current_part_id, self._has_current_part_id = s.read_int(
            record,
            Fields.CURRENT_PART_ID,
            default=0,
        )
        if not self._has_current_part_id:
            self.current_part_id, self._has_current_part_id = s.read_int(
                record,
                "CurrentPartID",
                default=0,
            )
        _validate_signed_short(self.current_part_id, "CurrentPartId")
        self.display_mode, self._has_display_mode = s.read_int(
            record, Fields.DISPLAY_MODE, default=0
        )
        _validate_unsigned_byte(self.display_mode, "DisplayMode")
        self.display_mode_count, self._has_display_mode_count = s.read_int(
            record, Fields.DISPLAY_MODE_COUNT, default=0
        )
        _validate_display_mode_count(self.display_mode_count)
        self._part_count_at_parse = self.part_count
        self._current_part_id_at_parse = self.current_part_id
        self._display_mode_count_at_parse = self.display_mode_count

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
            record, Fields.PART_ID_LOCKED, default=self.designator_locked
        )
        self._source_part_id_locked = self.part_id_locked
        self.pins_moveable, self._has_pins_moveable = s.read_bool(
            record, Fields.PINS_MOVEABLE, default=False
        )

        # Colors - Local Colors override
        self.override_colors, self._has_override_colors = s.read_bool(
            record, Fields.OVERRIDE_COLORS, default=False
        )
        self.color, self._has_color = s.read_color(record, Fields.COLOR, default=0)
        self.area_color, self._has_area_color = s.read_color(
            record, Fields.AREA_COLOR, default=0
        )
        pin_color, self._has_pin_color = s.read_color(
            record, Fields.PIN_COLOR, default=0
        )
        self.pin_color = int(pin_color or 0)
        self._capture_graphical_source_state()

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
        self._source_component_kind = self.component_kind

        # Preserve raw ComponentKindVersion2 value for round-trip
        ckv2 = record.get("ComponentKindVersion2", record.get("COMPONENTKINDVERSION2"))
        self.component_kind_version2 = int(ckv2) if ckv2 is not None else None

        # Database references
        self.database_table_name, self._has_database_table_name = (
            self._read_dynamic_field(s, record, Fields.DATABASE_TABLE_NAME, "")
        )
        split_library = _split_legacy_dblib_name(self.source_library_name)
        if split_library is not None:
            self.source_library_name, self.database_table_name = split_library
            self._legacy_dblib_source = True
        self._source_library_name_at_parse = self.source_library_name
        self._database_table_name_at_parse = self.database_table_name
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
        self.design_item_id, self._has_design_item_id = self._read_dynamic_field(
            s, record, Fields.DESIGN_ITEM_ID, ""
        )
        self.lib_reference = self.lib_reference.strip()
        self.design_item_id = self.design_item_id.strip()
        self._lib_reference_at_parse = self.lib_reference

        # File references
        self.sheet_part_filename, self._has_sheet_part_filename = (
            self._read_dynamic_field(s, record, Fields.SHEET_PART_FILENAME, "")
        )
        if not self._has_sheet_part_filename:
            self.sheet_part_filename, self._has_sheet_part_filename = (
                self._read_dynamic_field(s, record, "SheetPartFilename", "")
            )
        self.target_filename, self._has_target_filename = self._read_dynamic_field(
            s, record, Fields.TARGET_FILENAME, "*"
        )
        if not self._has_target_filename:
            self.target_filename, self._has_target_filename = self._read_dynamic_field(
                s, record, "TargetFilename", "*"
            )
        if self.target_filename == "":
            self.target_filename = "*"
        self._sheet_part_filename_at_parse = self.sheet_part_filename
        self._target_filename_at_parse = self.target_filename

        # GUIDs
        self.vault_guid, self._has_vault_guid = self._read_dynamic_field(
            s, record, Fields.VAULT_GUID, ""
        )
        self.item_guid, self._has_item_guid = self._read_dynamic_field(
            s, record, Fields.ITEM_GUID, ""
        )
        self.revision_guid, self._has_revision_guid = self._read_dynamic_field(
            s, record, Fields.REVISION_GUID, ""
        )
        self.symbol_vault_guid, self._has_symbol_vault_guid = self._read_dynamic_field(
            s, record, Fields.SYMBOL_VAULT_GUID, ""
        )
        self.symbol_item_guid, self._has_symbol_item_guid = self._read_dynamic_field(
            s, record, Fields.SYMBOL_ITEM_GUID, ""
        )
        self.symbol_revision_guid, self._has_symbol_revision_guid = (
            self._read_dynamic_field(s, record, Fields.SYMBOL_REVISION_GUID, "")
        )
        (
            self.generic_component_template_guid,
            self._has_generic_component_template_guid,
        ) = self._read_dynamic_field(s, record, "GenericComponentTemplateGUID", "")
        self.alias_list, self._has_alias_list = self._read_dynamic_field(
            s, record, "AliasList", ""
        )
        self.alias_list = _normalize_alias_list(self.alias_list)
        self._alias_list_at_parse = self.alias_list
        (
            self.has_only_current_part_info,
            self._has_has_only_current_part_info,
        ) = s.read_bool(record, "HasOnlyCurrentPartInfo", default=False)
        self.key_component_unique_id, self._has_key_component_unique_id = (
            self._read_dynamic_field(s, record, "KeyComponentUniqueId", "")
        )
        self.custom_display_mode_names = []
        self._has_custom_display_mode_names: list[bool] = []
        for index in range(self.display_mode_count):
            value, present = self._read_dynamic_field(
                s, record, f"CustomDisplayModeName{index}", ""
            )
            self.custom_display_mode_names.append(value)
            self._has_custom_display_mode_names.append(present)

        # Pin count
        self.all_pin_count, self._has_all_pin_count = s.read_int(
            record, Fields.ALL_PIN_COUNT, default=0
        )
        _validate_signed_short(self.all_pin_count, "AllPinCount")

        # Footprint
        self.footprint, self._has_footprint = s.read_str(
            record, Fields.FOOTPRINT, default=""
        )
        self._footprint_at_parse = self.footprint
        self._capture_component_source_state()

    def _read_dynamic_field(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: FieldDef | str,
        default: str,
    ) -> tuple[str, bool]:
        value, present, used_utf8 = read_dynamic_string_field(
            serializer,
            record,
            self._record,
            field,
            default=default,
        )
        field_name = serializer._get_field_def(field).pascal
        if used_utf8:
            self._dynamic_utf8_fields.add(field_name)
        return value, present

    def _write_dynamic_field(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        field: FieldDef | str,
        value: str,
        *,
        was_present: bool,
        force: bool = False,
    ) -> None:
        field_name = serializer._get_field_def(field).pascal
        write_dynamic_string_field(
            serializer,
            record,
            field,
            value,
            raw_record=self._raw_record,
            used_utf8_sidecar=field_name in self._dynamic_utf8_fields,
            was_present=was_present,
            force=force,
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
        if raw is None or self.lib_reference != self._lib_reference_at_parse:
            self._write_dynamic_field(
                serializer,
                record,
                Fields.LIB_REFERENCE,
                self.lib_reference,
                was_present=self._has_lib_reference,
                force=self.lib_reference != self._lib_reference_at_parse,
            )
        if (
            raw is None
            or self._has_library_path
            or self.library_path != self._library_path_at_parse
        ):
            self._write_dynamic_field(
                serializer,
                record,
                Fields.LIBRARY_PATH,
                self.library_path,
                was_present=self._has_library_path,
                force=self.library_path != self._library_path_at_parse,
            )
        source_library_unchanged = (
            self._legacy_dblib_source
            and self.source_library_name == self._source_library_name_at_parse
            and self.database_table_name == self._database_table_name_at_parse
        )
        if not source_library_unchanged and (
            raw is None
            or self._has_source_library_name
            or self.source_library_name != self._source_library_name_at_parse
        ):
            self._write_dynamic_field(
                serializer,
                record,
                Fields.SOURCE_LIBRARY_NAME,
                self.source_library_name,
                was_present=self._has_source_library_name,
                force=self.source_library_name != self._source_library_name_at_parse,
            )
        if (
            self._has_component_description
            or self.component_description
            or self._component_changed("component_description")
        ):
            serializer.write_str(
                record,
                Fields.COMPONENT_DESCRIPTION,
                self.component_description,
                raw,
                force=self._component_changed("component_description"),
            )
        record.pop("%UTF8%ComponentDescription", None)
        record.pop("%UTF8%COMPONENTDESCRIPTION", None)

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
        if (
            raw is None
            or self._has_part_count
            or self.part_count != self._part_count_at_parse
        ):
            serializer.write_int(
                record,
                Fields.PART_COUNT,
                self.part_count,
                raw,
                force=self._component_changed("part_count"),
            )
        record.pop("CurrentPartID", None)
        record.pop("CURRENTPARTID", None)
        if (
            raw is None
            or self._has_current_part_id
            or self.current_part_id != self._current_part_id_at_parse
        ):
            record[keys["current_part_id"]] = str(self.current_part_id)
        if self._has_display_mode or self.display_mode != 0:
            serializer.write_int(
                record,
                Fields.DISPLAY_MODE,
                self.display_mode,
                raw,
                force=self._component_changed("display_mode"),
            )
        if (
            raw is None
            or self._has_display_mode_count
            or self.display_mode_count != self._display_mode_count_at_parse
        ):
            serializer.write_int(
                record,
                Fields.DISPLAY_MODE_COUNT,
                self.display_mode_count,
                raw,
                force=self._component_changed("display_mode_count"),
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
                record,
                Fields.ORIENTATION,
                self.orientation.value,
                raw,
                force=self._component_changed("orientation"),
            )
        if self._has_is_mirrored or self.is_mirrored:
            serializer.write_bool(
                record,
                Fields.IS_MIRRORED,
                self.is_mirrored,
                raw,
                force=self._component_changed("is_mirrored"),
            )

        if self._has_show_hidden_pins or self.show_hidden_pins:
            serializer.write_bool(
                record,
                Fields.SHOW_HIDDEN_PINS,
                self.show_hidden_pins,
                raw,
                force=self._component_changed("show_hidden_pins"),
            )
        if self._has_show_hidden_fields or self.show_hidden_fields:
            serializer.write_bool(
                record,
                Fields.SHOW_HIDDEN_FIELDS,
                self.show_hidden_fields,
                raw,
                force=self._component_changed("show_hidden_fields"),
            )
        if self._has_display_field_names or self.display_field_names:
            serializer.write_bool(
                record,
                Fields.DISPLAY_FIELD_NAMES,
                self.display_field_names,
                raw,
                force=self._component_changed("display_field_names"),
            )

        if self._has_designator_locked or self.designator_locked:
            serializer.write_bool(
                record,
                Fields.DESIGNATOR_LOCKED,
                self.designator_locked,
                raw,
                force=self._component_changed("designator_locked"),
            )
        should_write_part_lock = any(
            (
                self._has_part_id_locked,
                self.part_id_locked != self._source_part_id_locked,
                self.part_id_locked != self.designator_locked,
                self._raw_record is None,
            )
        )
        if should_write_part_lock:
            serializer.write_bool(
                record,
                Fields.PART_ID_LOCKED,
                self.part_id_locked,
                raw,
                force=(
                    self.part_id_locked != self._source_part_id_locked
                    or self.part_id_locked != self.designator_locked
                ),
            )
        if self._has_pins_moveable or self.pins_moveable:
            serializer.write_bool(
                record,
                Fields.PINS_MOVEABLE,
                self.pins_moveable,
                raw,
                force=self._component_changed("pins_moveable"),
            )

    def _serialize_color_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
        raw: dict[str, Any] | None,
    ) -> None:
        """
        Serialize local color override fields.
        """
        if (
            self._has_override_colors
            or self.override_colors
            or self._component_changed("override_colors")
        ):
            serializer.write_bool(
                record,
                Fields.OVERRIDE_COLORS,
                self.override_colors,
                raw,
                force=self._component_changed("override_colors"),
            )
        self._serialize_component_color(
            record, serializer, raw, Fields.COLOR, "color", self._has_color, 0
        )
        self._serialize_component_color(
            record,
            serializer,
            raw,
            Fields.AREA_COLOR,
            "area_color",
            self._has_area_color,
            0xFFFFFF,
        )
        self._serialize_component_color(
            record,
            serializer,
            raw,
            Fields.PIN_COLOR,
            "pin_color",
            self._has_pin_color,
            0,
        )

    def _serialize_component_color(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        raw: dict[str, object] | None,
        field: FieldDef,
        attribute: str,
        was_present: bool,
        override_default: int,
    ) -> None:
        changed = self._component_changed(attribute)
        if raw is not None and not changed:
            return
        raw_value = getattr(self, attribute)
        value = override_default if raw_value is None else int(raw_value)
        if (
            was_present
            or (self.override_colors and value != override_default)
            or changed
        ):
            serializer.write_color(record, field, value, raw, force=changed)

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
        if (
            self._raw_record is not None
            and self.component_kind == self._source_component_kind
        ):
            return
        if self.component_kind == ComponentKind.STANDARD:
            return

        record.pop("ComponentKindVersion2", None)
        record.pop("COMPONENTKINDVERSION2", None)
        record.pop("ComponentKindVersion3", None)
        record.pop("COMPONENTKINDVERSION3", None)
        if self.component_kind == ComponentKind.JUMPER:
            serializer.write_int(
                record,
                Fields.COMPONENT_KIND,
                ComponentKind.STANDARD.value,
                raw,
                force=True,
            )
            record[keys["component_kind_v2"]] = str(ComponentKind.STANDARD.value)
            record[keys["component_kind_v3"]] = str(ComponentKind.JUMPER.value)
            return
        if self.component_kind == ComponentKind.STANDARD_NO_BOM:
            serializer.write_int(
                record,
                Fields.COMPONENT_KIND,
                ComponentKind.STANDARD.value,
                raw,
                force=True,
            )
            record[keys["component_kind_v2"]] = str(ComponentKind.STANDARD_NO_BOM.value)
            return
        serializer.write_int(
            record,
            Fields.COMPONENT_KIND,
            self.component_kind.value,
            raw,
            force=True,
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
        database_fields_unchanged = (
            self._legacy_dblib_source
            and self.source_library_name == self._source_library_name_at_parse
            and self.database_table_name == self._database_table_name_at_parse
        )
        if not database_fields_unchanged and (
            self._has_database_table_name
            or self.database_table_name
            or self._component_changed("database_table_name")
        ):
            self._write_dynamic_field(
                serializer,
                record,
                Fields.DATABASE_TABLE_NAME,
                self.database_table_name,
                was_present=self._has_database_table_name,
                force=self.database_table_name != self._database_table_name_at_parse,
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

        design_item_changed = (
            self.design_item_id != self._component_source_state["design_item_id"]
        )
        if (self._raw_record is None and self.design_item_id) or design_item_changed:
            self._write_dynamic_field(
                serializer,
                record,
                Fields.DESIGN_ITEM_ID,
                self.design_item_id,
                was_present=self._has_design_item_id,
                force=design_item_changed,
            )

    def _serialize_file_fields(
        self,
        record: dict[str, Any],
        serializer: AltiumSerializer,
    ) -> None:
        """
        Serialize case-sensitive file reference fields.
        """
        if (
            self._raw_record is None
            or self._has_sheet_part_filename
            or self.sheet_part_filename != self._sheet_part_filename_at_parse
        ):
            self._write_dynamic_field(
                serializer,
                record,
                Fields.SHEET_PART_FILENAME,
                self.sheet_part_filename,
                was_present=self._has_sheet_part_filename,
                force=self.sheet_part_filename != self._sheet_part_filename_at_parse,
            )

        if (
            self._raw_record is None
            or self._has_target_filename
            or self.target_filename != self._target_filename_at_parse
        ):
            self._write_dynamic_field(
                serializer,
                record,
                Fields.TARGET_FILENAME,
                self.target_filename,
                was_present=self._has_target_filename,
                force=self.target_filename != self._target_filename_at_parse,
            )

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
            attribute = {
                Fields.VAULT_GUID.pascal: "vault_guid",
                Fields.ITEM_GUID.pascal: "item_guid",
                Fields.REVISION_GUID.pascal: "revision_guid",
                Fields.SYMBOL_VAULT_GUID.pascal: "symbol_vault_guid",
                Fields.SYMBOL_ITEM_GUID.pascal: "symbol_item_guid",
                Fields.SYMBOL_REVISION_GUID.pascal: "symbol_revision_guid",
            }[field.pascal]
            changed = self._component_changed(attribute)
            if present or value or changed:
                self._write_dynamic_field(
                    serializer,
                    record,
                    field,
                    value,
                    was_present=present,
                    force=changed,
                )

    def _serialize_extended_fields(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        raw: dict[str, object] | None,
    ) -> None:
        for attribute, present, value, field in (
            (
                "generic_component_template_guid",
                self._has_generic_component_template_guid,
                self.generic_component_template_guid,
                "GenericComponentTemplateGUID",
            ),
            (
                "key_component_unique_id",
                self._has_key_component_unique_id,
                self.key_component_unique_id,
                "KeyComponentUniqueId",
            ),
        ):
            changed = self._component_changed(attribute)
            if present or value or changed:
                self._write_dynamic_field(
                    serializer,
                    record,
                    field,
                    value,
                    was_present=present,
                    force=changed,
                )
        self._serialize_alias_and_current_part_info(record, serializer, raw)
        self._serialize_custom_display_mode_names(record, serializer)

    def _serialize_alias_and_current_part_info(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        raw: dict[str, object] | None,
    ) -> None:
        normalized_aliases = _normalize_alias_list(self.alias_list)
        if normalized_aliases != self._alias_list_at_parse:
            self._write_dynamic_field(
                serializer,
                record,
                "AliasList",
                normalized_aliases,
                was_present=self._has_alias_list,
                force=True,
            )
        if (
            self._has_has_only_current_part_info
            or self.has_only_current_part_info
            or self._component_changed("has_only_current_part_info")
        ):
            serializer.write_bool(
                record,
                "HasOnlyCurrentPartInfo",
                self.has_only_current_part_info,
                raw,
                force=self._component_changed("has_only_current_part_info"),
            )

    def _serialize_custom_display_mode_names(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
    ) -> None:
        custom_names_changed = (
            self.custom_display_mode_names != self._source_custom_display_mode_names
            or self.display_mode_count
            != self._component_source_state["display_mode_count"]
        )
        if custom_names_changed:
            self._remove_out_of_scope_custom_display_mode_fields(record)
            for index in range(self.display_mode_count):
                value = (
                    self.custom_display_mode_names[index]
                    if index < len(self.custom_display_mode_names)
                    else ""
                )
                source = (
                    self._source_custom_display_mode_names[index]
                    if index < len(self._source_custom_display_mode_names)
                    else ""
                )
                if value and value != source:
                    present = (
                        index < len(self._has_custom_display_mode_names)
                        and self._has_custom_display_mode_names[index]
                    )
                    self._write_dynamic_field(
                        serializer,
                        record,
                        f"CustomDisplayModeName{index}",
                        value,
                        was_present=present,
                        force=True,
                    )

    def _serialize_component_unique_id(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
    ) -> None:
        if self._raw_record is not None and not self._component_changed("unique_id"):
            return
        self._remove_component_field(record, serializer, "UniqueID")
        if self.unique_id:
            serializer.write_str(
                record,
                "UniqueID",
                self.unique_id,
                self._raw_record,
                force=True,
            )

    def _remove_out_of_scope_custom_display_mode_fields(
        self, record: dict[str, object]
    ) -> None:
        for key in tuple(record):
            normalized = key.casefold().removeprefix("%utf8%")
            prefix = "customdisplaymodename"
            if not normalized.startswith(prefix):
                continue
            suffix = normalized.removeprefix(prefix)
            if not suffix.isascii() or not suffix.isdigit():
                continue
            index = int(suffix)
            value = (
                self.custom_display_mode_names[index]
                if index < len(self.custom_display_mode_names)
                else ""
            )
            if index >= self.display_mode_count or not value:
                record.pop(key)

    def serialize_to_record(self) -> dict[str, Any]:
        self.lib_reference = self.lib_reference.strip()
        self.design_item_id = self.design_item_id.strip()
        _validate_signed_short(self.part_count, "PartCount")
        _validate_signed_short(self.current_part_id, "CurrentPartId")
        _validate_signed_short(self.all_pin_count, "AllPinCount")
        _validate_unsigned_byte(self.display_mode, "DisplayMode")
        _validate_display_mode_count(self.display_mode_count)
        if self.component_kind_version2 is not None:
            _validate_unsigned_byte(
                self.component_kind_version2, "ComponentKindVersion2"
            )
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
        self._serialize_file_fields(record, s)
        self._serialize_component_unique_id(record, s)
        self._serialize_guid_fields(record, s, raw)
        self._serialize_extended_fields(record, s, raw)

        # Pin count
        if (
            self._has_all_pin_count
            or self.all_pin_count != 0
            or self._component_changed("all_pin_count")
        ):
            s.write_int(
                record,
                Fields.ALL_PIN_COUNT,
                self.all_pin_count,
                raw,
                force=self._component_changed("all_pin_count"),
            )

        if raw is None or self.footprint != self._footprint_at_parse:
            s.remove_field(record, Fields.FOOTPRINT)

        if self._raw_record is None:
            return self._order_fields_case_insensitively(
                record, self._authored_component_order()
            )
        self._canonicalize_component_default_mutations(record, s)
        return record

    def _canonicalize_component_default_mutations(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
    ) -> None:
        self._remove_component_fields_reset_to_defaults(record, serializer)
        self._remove_standard_component_kind_mutation(record, serializer)
        self._remove_true_inverted_flag_mutations(record, serializer)
        self._remove_empty_custom_display_mode_mutations(record)

    def _remove_component_fields_reset_to_defaults(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
    ) -> None:
        fields: tuple[tuple[str, FieldDef | str, object], ...] = (
            ("lib_reference", Fields.LIB_REFERENCE, ""),
            ("library_path", Fields.LIBRARY_PATH, ""),
            ("source_library_name", Fields.SOURCE_LIBRARY_NAME, ""),
            ("component_description", Fields.COMPONENT_DESCRIPTION, ""),
            ("part_count", Fields.PART_COUNT, 0),
            ("current_part_id", Fields.CURRENT_PART_ID, 0),
            ("display_mode", Fields.DISPLAY_MODE, 0),
            ("display_mode_count", Fields.DISPLAY_MODE_COUNT, 0),
            ("orientation", Fields.ORIENTATION, Rotation90.DEG_0),
            ("is_mirrored", Fields.IS_MIRRORED, False),
            ("show_hidden_pins", Fields.SHOW_HIDDEN_PINS, False),
            ("show_hidden_fields", Fields.SHOW_HIDDEN_FIELDS, False),
            ("display_field_names", Fields.DISPLAY_FIELD_NAMES, False),
            ("designator_locked", Fields.DESIGNATOR_LOCKED, False),
            ("pins_moveable", Fields.PINS_MOVEABLE, False),
            ("override_colors", Fields.OVERRIDE_COLORS, False),
            ("color", Fields.COLOR, 0),
            ("area_color", Fields.AREA_COLOR, 0),
            ("pin_color", Fields.PIN_COLOR, 0),
            ("database_table_name", Fields.DATABASE_TABLE_NAME, ""),
            ("design_item_id", Fields.DESIGN_ITEM_ID, ""),
            ("sheet_part_filename", Fields.SHEET_PART_FILENAME, ""),
            ("vault_guid", Fields.VAULT_GUID, ""),
            ("item_guid", Fields.ITEM_GUID, ""),
            ("revision_guid", Fields.REVISION_GUID, ""),
            ("symbol_vault_guid", Fields.SYMBOL_VAULT_GUID, ""),
            ("symbol_item_guid", Fields.SYMBOL_ITEM_GUID, ""),
            ("symbol_revision_guid", Fields.SYMBOL_REVISION_GUID, ""),
            ("generic_component_template_guid", "GenericComponentTemplateGUID", ""),
            ("alias_list", "AliasList", ""),
            ("has_only_current_part_info", "HasOnlyCurrentPartInfo", False),
            ("key_component_unique_id", "KeyComponentUniqueId", ""),
            ("all_pin_count", Fields.ALL_PIN_COUNT, 0),
            ("unique_id", "UniqueID", ""),
        )
        for attribute, field, default in fields:
            value = getattr(self, attribute)
            if value == default and value != self._component_source_state[attribute]:
                self._remove_component_field(record, serializer, field)

    def _remove_standard_component_kind_mutation(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
    ) -> None:
        if (
            self.component_kind == ComponentKind.STANDARD
            and self.component_kind != self._component_source_state["component_kind"]
        ):
            for field in (
                Fields.COMPONENT_KIND,
                "ComponentKindVersion2",
                "ComponentKindVersion3",
            ):
                self._remove_component_field(record, serializer, field)

    def _remove_true_inverted_flag_mutations(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
    ) -> None:
        for attribute, field in (
            ("use_db_table_name", "NotUseDBTableName"),
            ("use_library_name", "NotUseLibraryName"),
        ):
            if (
                getattr(self, attribute) is True
                and self._component_source_state[attribute] is not True
            ):
                self._remove_component_field(record, serializer, field)

    def _remove_empty_custom_display_mode_mutations(
        self,
        record: dict[str, object],
    ) -> None:
        if self.custom_display_mode_names != self._source_custom_display_mode_names:
            for key in list(record):
                if key.casefold().startswith("customdisplaymodename"):
                    suffix = key[len("CustomDisplayModeName") :]
                    if not suffix.isascii() or not suffix.isdigit():
                        continue
                    index = int(suffix)
                    if (
                        index >= len(self.custom_display_mode_names)
                        or not self.custom_display_mode_names[index]
                    ):
                        record.pop(key)

    @staticmethod
    def _remove_component_field(
        record: dict[str, object],
        serializer: AltiumSerializer,
        field: FieldDef | str,
    ) -> None:
        field_def = serializer._get_field_def(field)
        ordinary = field_def.pascal.casefold()
        sidecar = f"%UTF8%{field_def.pascal}".casefold()
        for key in tuple(record):
            if key.casefold() in {ordinary, sidecar}:
                record.pop(key)

    def _authored_component_order(self) -> tuple[str, ...]:
        custom_names = tuple(
            f"CustomDisplayModeName{index}" for index in range(self.display_mode_count)
        )
        return (
            "RECORD",
            "LibReference",
            "ComponentDescription",
            "PartCount",
            "DisplayModeCount",
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
            "DisplayMode",
            "IsMirrored",
            "Orientation",
            "CurrentPartId",
            "ShowHiddenFields",
            "ShowHiddenPins",
            "LibraryPath",
            "SourceLibraryName",
            "DatabaseTableName",
            "SheetPartFileName",
            "TargetFileName",
            "UniqueID",
            "AreaColor",
            "Color",
            "PinColor",
            "OverideColors",
            "DisplayFieldNames",
            "DesignatorLocked",
            "PartIDLocked",
            "PinsMoveable",
            "AliasList",
            "NotUseLibraryName",
            "NotUseDBTableName",
            "DesignItemId",
            "VaultGUID",
            "ItemGUID",
            "RevisionGUID",
            "SymbolVaultGUID",
            "SymbolItemGUID",
            "SymbolRevisionGUID",
            "GenericComponentTemplateGUID",
            "HasOnlyCurrentPartInfo",
            "AllPinCount",
            "KeyComponentUniqueId",
            "ComponentKind",
            "ComponentKindVersion2",
            "ComponentKindVersion3",
            *custom_names,
        )

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
        pin.is_not_accessible = not self.pins_moveable
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
        implementation.model_datafiles = [("", library_name, "PCBLib")]
        implementation.datafile_count = 1
        implementation.datafile_entity = library_name
        implementation.datafile_kind = "PCBLib"
        implementation._has_datafile_count = True
        implementation.unique_id = generate_unique_id()
        schdoc.add_object(implementation, owner=implementation_list)

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

    def get_pin_hotspot(self, pin_designator: str) -> tuple[float, float]:
        """
        Return a placed pin hotspot in schematic mils.
        """
        for pin in self.pins:
            if getattr(pin, "designator", "") != pin_designator:
                continue
            connection_point = pin.get_hot_spot()
            return (connection_point.x_mils, connection_point.y_mils)
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
        x_mils = getattr(point, "x_mils", None)
        y_mils = getattr(point, "y_mils", None)
        if isinstance(x_mils, int | float) and isinstance(y_mils, int | float):
            return (float(x_mils), float(y_mils))
        x = getattr(point, "x", None)
        y = getattr(point, "y", None)
        if isinstance(x, int | float) and isinstance(y, int | float):
            return (float(x) * 10.0, float(y) * 10.0)
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

    def _resolved_display_mode(self, display_mode: int | None = None) -> int | None:
        if display_mode is None:
            display_mode = getattr(self, "display_mode", None)
        try:
            return int(display_mode) if display_mode is not None else None
        except (TypeError, ValueError):
            return None

    def _display_mode_matches(
        self,
        record: object,
        display_mode: int | None = None,
    ) -> bool:
        resolved_display_mode = self._resolved_display_mode(display_mode)
        if resolved_display_mode is None:
            return True
        if not hasattr(record, "owner_part_display_mode"):
            return True
        record_mode = getattr(record, "owner_part_display_mode", None)
        if record_mode is None:
            record_mode = 0
        return int(record_mode) == resolved_display_mode

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
        source_admission: _SourceAdmission = _SourceAdmission(),
    ) -> list[object]:
        resolved_part_id = self._resolved_part_id(part_id)
        resolved_display_mode = self._resolved_display_mode()
        graphics, children, pin_ids, parameter_ids = self._display_body_source_groups(
            source_admission
        )
        body_records = self._unique_records(
            graphics
            + [
                child
                for child in children
                if getattr(child, "is_not_accessible", False)
                and id(child) not in pin_ids
                and id(child) not in parameter_ids
            ]
        )
        return [
            record
            for record in source_admission.admitted(body_records)
            if self._part_matches(record, resolved_part_id)
            and self._display_mode_matches(record, resolved_display_mode)
        ]

    def _display_body_source_groups(
        self, source_admission: _SourceAdmission
    ) -> tuple[list[object], list[object], set[int], set[int]]:
        children = list(source_admission.children(self, self.children))
        if source_admission.parent_by_source_id is None:
            return (
                list(self.graphics),
                children,
                {id(child) for child in self.pins},
                {id(child) for child in self.parameters},
            )
        projected, kinds = self._projected_geometry_source_children(source_admission)
        return (
            [child for child in projected if kinds[id(child)] == "graphic"],
            children,
            {id(child) for child in projected if kinds[id(child)] == "pin"},
            {id(child) for child in projected if kinds[id(child)] == "param"},
        )

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
            if getattr(record, "record_type", None) in {
                SchRecordType.LABEL,
                SchRecordType.NET_LABEL,
                SchRecordType.SHEET_NAME,
                SchRecordType.FILE_NAME,
            } and (
                getattr(record, "is_hidden", False)
                or not str(getattr(record, "text", "") or "")
            ):
                continue
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
        return self._source_display_body_bounds_mils(
            _SourceAdmission(), part_id=part_id
        )

    def _source_display_body_bounds_mils(
        self, source_admission: _SourceAdmission, *, part_id: int | None = None
    ) -> SchRectMils | None:
        rects: list[SchRectMils] = []
        for record in self._display_body_records(
            part_id=part_id, source_admission=source_admission
        ):
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
        resolved_display_mode = self._resolved_display_mode()
        rects: list[SchRectMils] = []
        for record in self._unique_records(records):
            if not self._part_matches(record, resolved_part_id):
                continue
            if not self._display_mode_matches(record, resolved_display_mode):
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
        child_geometry: list["SchGeometryOp"],
        ctx: object,
        *,
        units_per_px: int,
    ) -> list["SchGeometryOp"]:
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

        get_hot_spot = getattr(pin, "get_hot_spot", None)
        transform_coord_precise = getattr(ctx, "transform_coord_precise", None)
        if not callable(get_hot_spot) or not callable(transform_coord_precise):
            return []
        hot_spot = get_hot_spot()
        hot_spot_px = transform_coord_precise(hot_spot)
        if not isinstance(hot_spot_px, tuple | list) or len(hot_spot_px) < 2:
            return []
        hot_spot_x, hot_spot_y = svg_coord_to_geometry(
            hot_spot_px[0],
            hot_spot_px[1],
            sheet_height_px=float(getattr(ctx, "sheet_height", 0.0) or 0.0),
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

    def _geometry_child_is_eligible(
        self,
        child_kind: str,
        child: object,
        resolved_display_mode: int | None,
    ) -> bool:
        if _is_parent_bound_geometry_child(child):
            return False
        if child_kind == "param":
            return self._parameter_child_is_visible(child)
        if type(child).__name__ == "AltiumSchImageParameter":
            # The managed ImageParameter iterator filter only checks visibility;
            # its model is exported explicitly by the parameter painter.
            return not getattr(child, "is_hidden", False)
        owner_part = getattr(child, "owner_part_id", None)
        if (
            owner_part is not None
            and owner_part > 0
            and owner_part != self.current_part_id
        ):
            return False
        if not self._display_mode_matches(child, resolved_display_mode):
            return False
        return not (
            child_kind == "pin"
            and getattr(child, "is_hidden", False)
            and not self.show_hidden_pins
        )

    def _parameter_child_is_visible(self, child: object) -> bool:
        hidden = getattr(child, "is_hidden", False)
        if hasattr(child, "_to_component_comment_geometry"):
            return not hidden
        return self.show_hidden_fields or not hidden

    @staticmethod
    def _geometry_child_kind(
        child: object,
        pin_ids: set[int],
        parameter_ids: set[int],
    ) -> str:
        if type(child).__name__ == "AltiumSchImageParameter":
            from ._sch_source_projection import _component_bound_field_role

            return (
                "param"
                if _component_bound_field_role(child) == "comment"
                else "graphic"
            )
        if id(child) in pin_ids:
            return "pin"
        if id(child) in parameter_ids:
            return "param"
        return "graphic"

    def _geometry_graphics(self) -> list[object]:
        graphics = list(self.graphics)
        graphic_ids = {id(child) for child in graphics}
        graphics.extend(
            child
            for child in self.children
            if type(child).__name__ == "AltiumSchIeeeSymbol"
            and id(child) not in graphic_ids
        )
        return graphics

    def _geometry_source_children(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> tuple[list[object], dict[int, str]]:
        """Return spatial/field children and their renderer categories."""
        if source_admission.parent_by_source_id is not None:
            return self._projected_geometry_source_children(source_admission)
        graphics = self._geometry_graphics()
        pins = list(self.pins)
        parameters = [
            child
            for child in self.parameters
            if type(child).__name__
            in (
                "AltiumSchDesignator",
                "AltiumSchParameter",
                "AltiumSchImageParameter",
            )
        ]
        paint_child_ids = {id(child) for child in [*graphics, *pins, *parameters]}
        source_children = [
            child for child in self.children if id(child) in paint_child_ids
        ]
        if not source_children:
            # Compatibility for manually assembled legacy components that filled
            # category lists before the structural children collection existed.
            source_children = [*graphics, *pins, *parameters]
        pin_ids = {id(child) for child in pins}
        parameter_ids = {id(child) for child in parameters}
        child_kinds = {
            id(child): self._geometry_child_kind(child, pin_ids, parameter_ids)
            for child in source_children
        }
        return source_children, child_kinds

    def _projected_geometry_source_children(
        self, source_admission: _SourceAdmission
    ) -> tuple[list[object], dict[int, str]]:
        from ._sch_source_projection import _component_bound_field_role
        from .altium_schdoc import COMPONENT_GRAPHIC_CHILD_TYPES

        children: list[object] = []
        kinds: dict[int, str] = {}
        for child in self._projected_and_legacy_geometry_children(source_admission):
            name = type(child).__name__
            if name == "AltiumSchPin":
                kind = "pin"
            elif name in ("AltiumSchDesignator", "AltiumSchParameter"):
                kind = "param"
            elif name == "AltiumSchImageParameter":
                kind = (
                    "param"
                    if _component_bound_field_role(child) == "comment"
                    else "graphic"
                )
            elif isinstance(child, COMPONENT_GRAPHIC_CHILD_TYPES) or name in (
                "AltiumSchIeeeSymbol",
                "_AltiumSchHarnessCavityComponent",
            ):
                kind = "graphic"
            else:
                continue
            children.append(child)
            kinds[id(child)] = kind
        return children, kinds

    def _projected_and_legacy_geometry_children(
        self, source_admission: _SourceAdmission
    ) -> list[object]:
        legacy = self.children or [
            *self._geometry_graphics(),
            *self.pins,
            *self.parameters,
        ]
        return source_admission.children_with_legacy(self, legacy)

    def _ordered_geometry_children(
        self,
        *,
        sort_transparency: bool = True,
        record_filter: frozenset[SchRecordType] | None = None,
        exclude_records: frozenset[SchRecordType] = frozenset(),
        max_sort_work: int | None = None,
        source_admission: _SourceAdmission = _SourceAdmission(),
    ) -> list[tuple[str, object]]:
        from .altium_sch_paint_order import (
            _component_bound_children,
            _sort_transparent_objects,
        )

        source_children, child_kinds = self._geometry_source_children(source_admission)
        source_children = _component_bound_children(
            self,
            list(source_admission.children(self, self.children)) or source_children,
        )
        resolved_display_mode = self._resolved_display_mode()
        eligible_children = [
            child
            for child in source_admission.admitted(source_children)
            if id(child) in child_kinds
            and self._geometry_child_is_eligible(
                child_kinds[id(child)], child, resolved_display_mode
            )
            and (
                record_filter is None
                or getattr(child, "record_type", None) in record_filter
            )
            and getattr(child, "record_type", None) not in exclude_records
        ]
        ordered_children = (
            _sort_transparent_objects(eligible_children, max_work=max_sort_work)
            if sort_transparency
            else eligible_children
        )
        return [(child_kinds[id(child)], child) for child in ordered_children]

    @staticmethod
    def _child_geometry_operations(
        child_kind: str,
        child: object,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int,
    ) -> list["SchGeometryOp"]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            SchGeometryRecord,
            unwrap_record_operations,
        )

        to_geometry = getattr(child, "to_geometry", None)
        if child_kind == "param":
            to_geometry = getattr(child, "_to_component_comment_geometry", to_geometry)
        if not callable(to_geometry):
            return []
        if child_kind == "graphic":
            graphic_record = to_geometry(
                ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if not isinstance(graphic_record, SchGeometryRecord):
                return []
            return unwrap_record_operations(
                graphic_record,
                unique_id=getattr(child, "unique_id", ""),
            )

        raw_child_geometry = to_geometry(
            ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            wrap_record=False,
        )
        if not isinstance(raw_child_geometry, list):
            return []
        return [op for op in raw_child_geometry if isinstance(op, SchGeometryOp)]

    def _geometry_child_context(
        self, ctx: "SchSvgRenderContext", children: list[tuple[str, object]]
    ) -> "SchSvgRenderContext":
        if not self.show_hidden_fields:
            return ctx
        copied = ctx.copy()
        copied._visible_hidden_parameter_ids = frozenset(
            id(child) for kind, child in children if kind == "param"
        )
        return copied

    def _render_child_geometry_operations(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int,
        children: list[tuple[str, object]] | None = None,
        include_component_junctions: bool = True,
    ) -> tuple[list["SchGeometryOp"], list["SchGeometryOp"]]:
        from .altium_sch_geometry_oracle import SchGeometryOp

        child_operations: list[SchGeometryOp] = []
        component_operations: list[SchGeometryOp] = []
        native_multipart_junction_wrappers = self.part_count > 1 and getattr(
            ctx, "native_svg_export", False
        )
        if children is None:
            children = self._ordered_geometry_children(
                source_admission=ctx._source_admission
            )
        children = [
            (kind, child)
            for kind, child in children
            if ctx._source_admission.admits(child)
        ]
        ctx = self._geometry_child_context(ctx, children)
        for child_kind, child in children:
            child_geometry = self._child_geometry_operations(
                child_kind,
                child,
                ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if not child_geometry:
                continue
            if (
                include_component_junctions
                and child_kind == "pin"
                and native_multipart_junction_wrappers
            ):
                component_operations.extend(
                    self._native_export_component_junction_ops(
                        child,
                        child_geometry,
                        ctx,
                        units_per_px=units_per_px,
                    )
                )
            child_operations.append(
                SchGeometryOp.begin_group(
                    getattr(child, "unique_id", ""),
                    render_group_id=ctx.render_group_id(child) or None,
                    render_group_identity=ctx.render_group_identity(child),
                    render_source_id=id(child),
                )
            )
            child_operations.extend(child_geometry)
            child_operations.append(SchGeometryOp.end_group())
        return component_operations, child_operations

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        from ._altium_sch_component_overlay import (
            _component_overlay_group_operations,
            _component_paint_contexts,
        )
        from ._altium_sch_component_variant import (
            _component_variant_geometry_operations,
            _component_variant_junction_operations,
        )
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryRecord,
            wrap_record_operations,
        )

        overlay = ctx._component_overlay_capture
        variant = ctx._component_variant_capture
        primitive_ctx, ctx = _component_paint_contexts(ctx, self)
        if variant is not None:
            component_level_operations, child_operations = (
                _component_variant_geometry_operations(
                    self,
                    primitive_ctx,
                    variant,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
            )
        else:
            component_level_operations, child_operations = (
                self._render_child_geometry_operations(
                    primitive_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
            )
        if overlay is not None:
            child_operations.extend(
                _component_overlay_group_operations(
                    ctx, overlay, units_per_px=units_per_px
                )
            )
        if variant is not None:
            component_level_operations.extend(
                _component_variant_junction_operations(
                    self,
                    ctx,
                    variant,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
            )

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


class AltiumSchHarnessComponent(AltiumSchComponent):
    """V5 harness-layout component, serialized as managed RECORD 106."""

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_COMPONENT

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        geometry = super().to_geometry(
            ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        return replace(
            geometry,
            kind="harness_component",
            object_id="eHarnessComponent",
        )
